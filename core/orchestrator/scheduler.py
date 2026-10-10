"""Concurrent DAG Scheduler and Bounded Execution Engine.

spec §4 (Space Orchestrator), §16 (TaskGraph & Dispatch), SCHED-001..005, ADR-0048
Phase 15.4: Concurrent DAG Scheduling and Bounded Execution Engine

Authority and Architecture:
- Subordinates strictly to SpaceKernel for plan mutations and CAS transitions.
- Subordinates strictly to ResourceManager for hardware leases and quotas.
- Subordinates strictly to AdmissionController for capability admission and budget.
- Subordinates strictly to Core Boundary Rule (no imports from workers/, agents/, etc.).
- Coordinates deterministic task execution across concurrent threads bounded by
  SchedulerConfig invariants.
"""

from __future__ import annotations

import concurrent.futures
import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.orchestrator.dispatch_model import (
    CrossSpaceViolationError,
    DeterministicDispatcher,
    SpaceKernelAuthorityProtocol,
    TaskCompletionResult,
    WorkerInvokerProtocol,
    compute_dispatch_idempotency_key,
)
from core.orchestrator.execution_state import (
    ExecutionAttemptRecord,
    ExecutionAttemptStore,
)
from core.plans.task_graph import TaskGraph, TaskState
from core.resources.identity import ResourceIdentity
from core.resources.manager import ResourceManager

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 1. Configuration & Data Contracts (SCHED-001, SCHED-002)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SchedulerConfig:
    """Immutable configuration contract for ConcurrentDAGScheduler (SCHED-001).

    Enforces deterministic upper bounds to prevent runaway concurrency, thread
    exhaustion, and resource starvation.
    """

    max_concurrent_workers: int = 4
    per_space_concurrency: int = 2
    ready_queue_capacity: int = 1000
    max_cas_rebases: int = 3
    default_task_timeout: float = 60.0
    poll_interval_seconds: float = 0.05

    def __post_init__(self) -> None:
        if self.max_concurrent_workers < 1:
            raise ValueError("max_concurrent_workers must be at least 1")
        if self.max_concurrent_workers > 64:
            raise ValueError(
                f"max_concurrent_workers ({self.max_concurrent_workers}) exceeds maximum allowable bound (64)"
            )
        if self.per_space_concurrency < 1:
            raise ValueError("per_space_concurrency must be at least 1")
        if self.per_space_concurrency > self.max_concurrent_workers:
            raise ValueError(
                f"per_space_concurrency ({self.per_space_concurrency}) cannot exceed "
                f"max_concurrent_workers ({self.max_concurrent_workers})"
            )
        if self.ready_queue_capacity < 1:
            raise ValueError("ready_queue_capacity must be at least 1")
        if self.ready_queue_capacity > 100000:
            raise ValueError(
                f"ready_queue_capacity ({self.ready_queue_capacity}) exceeds allowable bound (100000)"
            )
        if self.max_cas_rebases < 0:
            raise ValueError("max_cas_rebases must be non-negative")
        if self.default_task_timeout <= 0.0:
            raise ValueError("default_task_timeout must be positive")


@dataclass(frozen=True)
class TaskCandidate:
    """Immutable representation of a task eligible for scheduling execution."""

    space_id: str
    task_id: str
    priority: int
    topological_depth: int
    plan_version: int
    capability: str
    attempt: int = 1

    def sort_key(self) -> tuple[int, int, str]:
        """Deterministic sorting key: (-priority, topological_depth, task_id).

        Prioritizes high-priority tasks first, then tasks with lowest dependency
        depth (to unblock branches early), and finally breaks ties by task_id.
        """
        return (-self.priority, self.topological_depth, self.task_id)


@dataclass
class SpaceRegistration:
    """Runtime context for an active Space managed by the scheduler."""

    space_id: str
    kernel: SpaceKernelAuthorityProtocol
    resource_mgr: ResourceManager
    invoker: WorkerInvokerProtocol
    resource_identity: ResourceIdentity
    base_dir: Path | None = None
    default_budget: float = 0.0
    default_timeout: float = 60.0
    concurrency_limit: int | None = None
    active_tasks: set[str] = field(default_factory=set)


# ---------------------------------------------------------------------------
# 2. Concurrent DAG Scheduler Engine (SCHED-001..005, ADR-0048)
# ---------------------------------------------------------------------------


class ConcurrentDAGScheduler:
    """Bounded, concurrent, deterministic task scheduler for SCCA Spaces.

    Subordinates strictly to SpaceKernel, ResourceManager, and AdmissionController.
    Coordinates parallel task execution across a strictly bounded thread pool while
    preserving single-writer CAS plan invariants, hardware lease integrity, and
    multi-space fair share scheduling.
    """

    def __init__(
        self,
        config: SchedulerConfig | None = None,
        dispatcher: DeterministicDispatcher | None = None,
        attempt_store: ExecutionAttemptStore | None = None,
        bus: Any | None = None,
    ) -> None:
        self.config = config or SchedulerConfig()
        self.dispatcher = dispatcher or DeterministicDispatcher(attempt_store=attempt_store)
        self.attempt_store = attempt_store
        self.bus = bus

        self._lock = threading.RLock()
        self._spaces: dict[str, SpaceRegistration] = {}
        self._space_keys: list[str] = []
        self._round_robin_idx: int = 0

        # Bounded worker thread pool (SCHED-001)
        self._executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=self.config.max_concurrent_workers,
            thread_name_prefix="ryu-sched-worker",
        )
        self._active_futures: dict[concurrent.futures.Future[Any], tuple[str, str]] = {}
        self._running = False
        self._bg_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._wake_event = threading.Event()

    # -----------------------------------------------------------------------
    # Space Lifecycle Management (SCCA Law 1)
    # -----------------------------------------------------------------------

    def register_space(
        self,
        space_id: str,
        kernel: SpaceKernelAuthorityProtocol,
        resource_mgr: ResourceManager,
        invoker: WorkerInvokerProtocol,
        resource_identity: ResourceIdentity,
        base_dir: Path | None = None,
        default_budget: float = 0.0,
        default_timeout: float | None = None,
        concurrency_limit: int | None = None,
    ) -> None:
        """Register a Space for concurrent execution coordination."""
        if kernel.space_id != space_id:
            raise CrossSpaceViolationError(
                f"Kernel space_id '{kernel.space_id}' does not match registered space_id '{space_id}'"
            )

        with self._lock:
            reg = SpaceRegistration(
                space_id=space_id,
                kernel=kernel,
                resource_mgr=resource_mgr,
                invoker=invoker,
                resource_identity=resource_identity,
                base_dir=base_dir,
                default_budget=default_budget,
                default_timeout=default_timeout or self.config.default_task_timeout,
                concurrency_limit=concurrency_limit,
            )
            self._spaces[space_id] = reg
            if space_id not in self._space_keys:
                self._space_keys.append(space_id)
            self._wake_event.set()

    def unregister_space(self, space_id: str) -> None:
        """Unregister a Space. Does not cancel running tasks, but halts new dispatch."""
        with self._lock:
            self._spaces.pop(space_id, None)
            if space_id in self._space_keys:
                self._space_keys.remove(space_id)

    def get_registered_spaces(self) -> list[str]:
        """Return list of registered Space IDs."""
        with self._lock:
            return list(self._space_keys)

    def get_active_task_count(self, space_id: str | None = None) -> int:
        """Return count of active tasks currently running in workers."""
        with self._lock:
            if space_id is not None:
                reg = self._spaces.get(space_id)
                return len(reg.active_tasks) if reg else 0
            return len(self._active_futures)

    # -----------------------------------------------------------------------
    # Candidate Discovery & Prioritization (SCHED-001, SCHED-003)
    # -----------------------------------------------------------------------

    def discover_eligible_candidates(self, space_id: str) -> list[TaskCandidate]:
        """Discover and deterministically sort all eligible tasks in a Space.

        A task is eligible if:
        1. It is in READY state (or PENDING with all dependencies completed).
        2. It is not currently active in a worker thread.
        """
        with self._lock:
            reg = self._spaces.get(space_id)
            if reg is None:
                return []

            graph = reg.kernel.get_task_graph()
            cur_version = reg.kernel.get_plan_version()
            active = reg.active_tasks

        # Compute topological depth for each node in graph
        depths: dict[str, int] = {}
        for node in graph.nodes:
            self._compute_depth(node.id, graph, depths, visited=set())

        ready_nodes = list(graph.get_ready_tasks())
        for node in graph.nodes:
            if node.state in (TaskState.LEASE_PENDING.value, TaskState.ADMISSION_PENDING.value):
                if node not in ready_nodes:
                    ready_nodes.append(node)

        candidates: list[TaskCandidate] = []
        for node in ready_nodes:
            if node.id in active:
                continue

            # Idempotency deduplication check (SCHED-005, ADR-0041)
            idempotency_key = compute_dispatch_idempotency_key(
                space_id=space_id,
                plan_version=cur_version,
                task_id=node.id,
                attempt=node.attempt,
            )
            if self.dispatcher.is_attempt_tracked(idempotency_key):
                continue

            node_priority = getattr(node, "priority", None)
            if node_priority is None:
                node_priority = node.params.get("priority", 0) if isinstance(node.params, dict) else 0

            candidates.append(
                TaskCandidate(
                    space_id=space_id,
                    task_id=node.id,
                    priority=int(node_priority),
                    topological_depth=depths.get(node.id, 0),
                    plan_version=cur_version,
                    capability=node.capability,
                    attempt=node.attempt,
                )
            )

        # Enforce ready queue capacity limit (SCHED-001)
        if len(candidates) > self.config.ready_queue_capacity:
            candidates = candidates[: self.config.ready_queue_capacity]

        # Deterministic sorting: (-priority, topological_depth, task_id)
        candidates.sort(key=lambda c: c.sort_key())
        return candidates

    def _compute_depth(
        self,
        node_id: str,
        graph: TaskGraph,
        depths: dict[str, int],
        visited: set[str],
    ) -> int:
        if node_id in depths:
            return depths[node_id]
        if node_id in visited:
            return 0  # Cycle guard (Kahn's algorithm in graph already validates)

        visited.add(node_id)
        node = graph.get_node(node_id)
        if not node or not node.dependencies:
            depths[node_id] = 0
            return 0

        max_parent_depth = 0
        for parent_id in node.dependencies:
            p_depth = self._compute_depth(parent_id, graph, depths, visited)
            if p_depth > max_parent_depth:
                max_parent_depth = p_depth

        depth = max_parent_depth + 1
        depths[node_id] = depth
        return depth

    # -----------------------------------------------------------------------
    # Scheduling Cycle & Multi-Space Fairness (SCHED-001, SCHED-003)
    # -----------------------------------------------------------------------

    def step(self) -> int:
        """Execute a single deterministic scheduling cycle across registered Spaces.

        Uses round-robin / fair-share iteration across active Spaces to prevent
        starvation (SCHED-003) while respecting global and per-space bounds (SCHED-001).

        Returns:
            Number of newly dispatched task futures.
        """
        # Cleanup completed futures first
        self._prune_completed_futures()

        with self._lock:
            total_active = len(self._active_futures)
            available_global = self.config.max_concurrent_workers - total_active
            if available_global <= 0 or not self._space_keys:
                return 0

            space_count = len(self._space_keys)
            dispatched_count = 0

            # Round-Robin iteration starting from last index
            for offset in range(space_count):
                if available_global <= 0:
                    break

                curr_idx = (self._round_robin_idx + offset) % space_count
                space_id = self._space_keys[curr_idx]
                reg = self._spaces.get(space_id)
                if not reg:
                    continue

                per_space_limit = (
                    reg.concurrency_limit
                    if reg.concurrency_limit is not None
                    else self.config.per_space_concurrency
                )
                per_space_limit = min(per_space_limit, self.config.max_concurrent_workers)

                space_active = len(reg.active_tasks)
                space_slots = per_space_limit - space_active
                if space_slots <= 0:
                    continue

                candidates = self.discover_eligible_candidates(space_id)
                if not candidates:
                    continue

                to_dispatch = candidates[: min(space_slots, available_global)]
                for cand in to_dispatch:
                    if self._dispatch_candidate(reg, cand):
                        dispatched_count += 1
                        available_global -= 1
                        if available_global <= 0:
                            break

            # Advance round robin index
            self._round_robin_idx = (self._round_robin_idx + 1) % space_count
            return dispatched_count

    def dispatch_candidate(self, space_id: str, candidate: TaskCandidate) -> bool:
        """Explicitly dispatch a task candidate with durable attempt registration."""
        reg = self._spaces.get(space_id)
        if not reg:
            raise CrossSpaceViolationError(
                f"Space '{space_id}' not registered in scheduler"
            )
        return self._dispatch_candidate(reg, candidate)

    def _dispatch_candidate(self, reg: SpaceRegistration, cand: TaskCandidate) -> bool:
        """Submit an eligible task candidate to the worker thread pool.

        Enforces durable race-safe idempotency via ExecutionAttemptStore claim
        before spawning worker thread (ADR-0042, ADR-0048, SCHED-001).

        Returns:
            True if candidate was successfully claimed and dispatched.
            False if attempt was already claimed by a concurrent caller / duplicate.
        """
        # Record dispatch attempt in ExecutionAttemptStore if configured (ADR-0042)
        idempotency_key = compute_dispatch_idempotency_key(
            space_id=cand.space_id,
            plan_version=cand.plan_version,
            task_id=cand.task_id,
            attempt=cand.attempt,
        )
        if self.attempt_store is not None:
            attempt_rec = ExecutionAttemptRecord(
                attempt_id=f"att-{cand.task_id}-{cand.attempt}-{int(time.time() * 1000)}",
                idempotency_key=idempotency_key,
                space_id=cand.space_id,
                task_id=cand.task_id,
                plan_version=cand.plan_version,
                attempt_number=cand.attempt,
                capability=cand.capability,
                status="dispatched",
            )
            try:
                if hasattr(self.attempt_store, "claim_attempt"):
                    claimed = self.attempt_store.claim_attempt(attempt_rec)
                else:
                    existing = self.attempt_store.get_attempt(idempotency_key)
                    if existing is not None:
                        claimed = False
                    else:
                        self.attempt_store.save_attempt(attempt_rec)
                        claimed = True
                if not claimed:
                    # Race lost / duplicate execution attempt already claimed!
                    logger.debug(
                        f"Task {cand.task_id} dispatch attempt {idempotency_key} "
                        "already claimed in ExecutionAttemptStore; skipping duplicate."
                    )
                    return False
            except Exception as exc:
                logger.warning(
                    f"Error checking ExecutionAttemptStore for {idempotency_key}: {exc}"
                )

        with self._lock:
            reg.active_tasks.add(cand.task_id)

        # Submit worker execution to bounded thread pool
        future = self._executor.submit(
            self._execute_task_pipeline_wrapper,
            reg=reg,
            cand=cand,
        )
        with self._lock:
            self._active_futures[future] = (cand.space_id, cand.task_id)

        return True


    def _execute_task_pipeline_wrapper(
        self,
        reg: SpaceRegistration,
        cand: TaskCandidate,
    ) -> TaskCompletionResult:
        """Execute full task pipeline within worker thread with rollback safety."""
        task_id = cand.task_id
        space_id = cand.space_id

        try:
            comp_res = self.dispatcher.execute_task_full_pipeline(
                kernel=reg.kernel,
                resource_mgr=reg.resource_mgr,
                task_id=task_id,
                resource_identity=reg.resource_identity,
                invoker=reg.invoker,
                expected_plan_version=None,
                budget=reg.default_budget,
                timeout=reg.default_timeout,
                base_dir=reg.base_dir,
            )
            if self.attempt_store is not None:
                try:
                    status = "completed" if comp_res.completed else "failed"
                    idempotency_key = compute_dispatch_idempotency_key(
                        space_id=cand.space_id,
                        plan_version=cand.plan_version,
                        task_id=cand.task_id,
                        attempt=cand.attempt,
                    )
                    self.attempt_store.update_attempt_status(
                        idempotency_key,
                        status=status,
                        failure_message=comp_res.error,
                    )
                except Exception:
                    pass
            return comp_res
        except Exception as exc:
            logger.exception(f"Unhandled exception during task {task_id} execution: {exc}")
            if self.attempt_store is not None:
                try:
                    idempotency_key = compute_dispatch_idempotency_key(
                        space_id=cand.space_id,
                        plan_version=cand.plan_version,
                        task_id=cand.task_id,
                        attempt=cand.attempt,
                    )
                    self.attempt_store.update_attempt_status(
                        idempotency_key,
                        status="failed",
                        failure_message=str(exc),
                    )
                except Exception:
                    pass
            # Ensure task enters FAILED state if unhandled error occurred
            try:
                cur_ver = reg.kernel.get_plan_version()
                reg.kernel.propose_task_transition(
                    task_id=task_id,
                    to_state=TaskState.FAILED.value,
                    expected_plan_version=cur_ver,
                    reason=f"Scheduler worker thread unhandled exception: {exc}",
                    error=str(exc),
                    max_rebases=self.config.max_cas_rebases,
                )
            except Exception:
                pass
            return TaskCompletionResult(
                task_id=task_id,
                space_id=space_id,
                plan_version=reg.kernel.get_plan_version(),
                status="failed",
                terminal_state=TaskState.FAILED.value,
                completed=False,
                error=str(exc),
                reason=f"Worker execution crashed: {exc}",
            )
        finally:
            with self._lock:
                reg.active_tasks.discard(task_id)
            self._wake_event.set()

    def _prune_completed_futures(self) -> None:
        """Clean up completed or errored worker futures from active table."""
        with self._lock:
            done = [f for f in self._active_futures if f.done()]
            for f in done:
                space_id, task_id = self._active_futures.pop(f)
                reg = self._spaces.get(space_id)
                if reg:
                    reg.active_tasks.discard(task_id)

    # -----------------------------------------------------------------------
    # Convergence & Drain Utilities (Phase 12.6, SCHED-005)
    # -----------------------------------------------------------------------

    def drain(self, timeout: float = 10.0) -> None:
        """Wait for all currently active task futures to complete."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._lock:
                self._prune_completed_futures()
                if not self._active_futures:
                    return
            time.sleep(0.01)

    def is_space_converged(self, space_id: str) -> bool:
        """Return True if every non-optional task in the Space is terminal."""
        with self._lock:
            reg = self._spaces.get(space_id)
            if not reg:
                return True
            graph = reg.kernel.get_task_graph()
            return self.dispatcher.is_plan_converged(graph)

    def is_space_succeeded(self, space_id: str) -> bool:
        """Return True if every non-optional task reached COMPLETED."""
        with self._lock:
            reg = self._spaces.get(space_id)
            if not reg:
                return False
            graph = reg.kernel.get_task_graph()
            return self.dispatcher.is_plan_succeeded(graph)

    def run_until_converged(
        self,
        space_id: str | None = None,
        max_cycles: int = 100,
        timeout: float = 10.0,
    ) -> bool:
        """Drive scheduling steps and worker drain until target Space converges.

        Args:
            space_id: Optional specific Space ID to wait for, or None for all spaces.
            max_cycles: Maximum scheduling passes before aborting.
            timeout: Maximum wall-clock seconds before returning.

        Returns:
            True if all checked spaces converged, False if timed out or max_cycles exceeded.
        """
        deadline = time.monotonic() + timeout
        cycles = 0

        while time.monotonic() < deadline and cycles < max_cycles:
            cycles += 1
            self.step()

            # Check convergence
            with self._lock:
                target_spaces = [space_id] if space_id else list(self._space_keys)
                all_converged = True
                for s in target_spaces:
                    if not self.is_space_converged(s):
                        all_converged = False
                        break

                if all_converged and not self._active_futures:
                    return True

            time.sleep(0.01)

        self.drain(timeout=max(0.1, deadline - time.monotonic()))
        with self._lock:
            target_spaces = [space_id] if space_id else list(self._space_keys)
            return all(self.is_space_converged(s) for s in target_spaces)

    # -----------------------------------------------------------------------
    # Background Coordination Loop
    # -----------------------------------------------------------------------

    def start(self) -> None:
        """Start the background coordinator thread."""
        with self._lock:
            if self._running:
                return
            self._running = True
            self._stop_event.clear()
            self._bg_thread = threading.Thread(
                target=self._run_loop,
                name="ryu-concurrent-dag-scheduler",
                daemon=True,
            )
            self._bg_thread.start()

    def stop(self, wait: bool = True, timeout: float = 5.0) -> None:
        """Stop the background coordinator thread and wait for drain."""
        with self._lock:
            if not self._running:
                return
            self._running = False
            self._stop_event.set()
            self._wake_event.set()

        if wait and self._bg_thread and self._bg_thread.is_alive():
            self._bg_thread.join(timeout=timeout)

        if wait:
            self.drain(timeout=timeout)

    def _run_loop(self) -> None:
        """Internal background loop driving scheduling passes."""
        while not self._stop_event.is_set():
            try:
                self.step()
            except Exception as exc:
                logger.exception(f"Error in scheduler step: {exc}")

            self._wake_event.wait(timeout=self.config.poll_interval_seconds)
            self._wake_event.clear()

    def shutdown(self, wait: bool = True) -> None:
        """Shut down the scheduler and worker thread pool."""
        self.stop(wait=wait)
        self._executor.shutdown(wait=wait)

    def __enter__(self) -> ConcurrentDAGScheduler:
        self.start()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.shutdown(wait=True)
