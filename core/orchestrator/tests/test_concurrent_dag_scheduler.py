"""Unit and integration test suite for ConcurrentDAGScheduler (Phase 15.4, Finding F-04).

Covers:
- SCHED-001: Bounded concurrent worker pool & per-space concurrency limits (Slices A, B, ADV-SCHED-01)
- SCHED-002: Optimistic Plan CAS rebase and lease rollback on exhaustion (Slices E, H, ADV-SCHED-03..05, 16)
- SCHED-003: Deterministic prioritization & multi-space fair sharing (Slices C, F, ADV-SCHED-02)
- SCHED-004: Atomic budget pre-reservation and reconciliation (Slice G, ADV-SCHED-06)
- SCHED-005: Terminal transition exclusivity and idempotency deduplication (Slices I, ADV-SCHED-09, 14)
- Slices D, J, K, L: Diamond DAG parallel execution, cascading unblock, crash recovery, graceful drain.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from ryu.pulse_bus.pulse import Pulse

from core.capabilities.admission import AdmissionController
from core.orchestrator.dispatch_model import (
    CrossSpaceViolationError,
    DeterministicDispatcher,
    TaskExecutionRequest,
    TaskExecutionResult,
    compute_dispatch_idempotency_key,
)
from core.orchestrator.execution_state import (
    ExecutionAttemptRecord,
    InMemoryExecutionAttemptStore,
)
from core.orchestrator.scheduler import (
    ConcurrentDAGScheduler,
    SchedulerConfig,
    TaskCandidate,
)
from core.plans.delta import PlanDelta
from core.plans.task_graph import TaskGraph, TaskNode, TaskState
from core.resources.identity import Resource, ResourceIdentity
from core.resources.manager import ResourceManager
from core.resources.store import InMemoryResourceStore
from core.space.kernel import SpaceKernel


# ---------------------------------------------------------------------------
# Test Fixtures & Mock Invoker
# ---------------------------------------------------------------------------


class SpyPulseBus:
    def __init__(self) -> None:
        self.published: list[Pulse] = []
        self._lock = threading.Lock()

    def publish(self, pulse: Pulse) -> Pulse:
        with self._lock:
            self.published.append(pulse)
        return pulse

    def find_by_type(self, pulse_type: str) -> list[Pulse]:
        with self._lock:
            return [p for p in self.published if p.type == pulse_type]


class ControlledWorkerInvoker:
    """Mock invoker with controllable delay, concurrency tracking, and failure injection."""

    def __init__(self, delay_seconds: float = 0.0) -> None:
        self.delay_seconds = delay_seconds
        self.invoked_requests: list[TaskExecutionRequest] = []
        self.concurrent_count = 0
        self.max_observed_concurrency = 0
        self._lock = threading.Lock()
        self.fail_tasks: set[str] = set()
        self.hang_tasks: set[str] = set()

    def invoke(self, request: TaskExecutionRequest) -> TaskExecutionResult:
        with self._lock:
            self.invoked_requests.append(request)
            self.concurrent_count += 1
            if self.concurrent_count > self.max_observed_concurrency:
                self.max_observed_concurrency = self.concurrent_count

        try:
            if request.task_id in self.fail_tasks:
                return TaskExecutionResult(
                    request_id=request.request_id,
                    status="failed",
                    task_id=request.task_id,
                    space_id=request.space_id,
                    plan_version=request.plan_version,
                    error="Injected worker failure",
                    error_class="transient.worker_error",
                )

            if request.task_id in self.hang_tasks:
                time.sleep(10.0)

            if self.delay_seconds > 0:
                time.sleep(self.delay_seconds)

            return TaskExecutionResult(
                request_id=request.request_id,
                status="ok",
                task_id=request.task_id,
                space_id=request.space_id,
                plan_version=request.plan_version,
                duration_seconds=self.delay_seconds,
                details={"exit_code": 0},
            )
        finally:
            with self._lock:
                self.concurrent_count -= 1


def _create_env(
    space_id: str,
    budget: float = 100.0,
    policy: str = "hard_stop",
    invoker_delay: float = 0.0,
    capacity: int = 10,
) -> tuple[SpaceKernel, ResourceManager, ControlledWorkerInvoker, ResourceIdentity, SpyPulseBus]:
    bus = SpyPulseBus()
    kernel = SpaceKernel(
        space_id=space_id,
        owner_id="owner-test",
        bus=bus,
        budget=budget,
        budget_policy=policy,
    )
    res_mgr = ResourceManager(bus=bus, store=InMemoryResourceStore())
    ident = ResourceIdentity("cpu", f"host-{space_id}", "core-0")
    res_mgr.register_resource(Resource(identity=ident, space_id=space_id, total_capacity=capacity))
    invoker = ControlledWorkerInvoker(delay_seconds=invoker_delay)
    return kernel, res_mgr, invoker, ident, bus


def _commit_tasks(kernel: SpaceKernel, nodes: list[dict[str, Any]]) -> int:
    cur = kernel.get_plan_version()
    delta = PlanDelta(
        space_id=kernel.space_id,
        base_version=cur,
        resulting_version=cur + 1,
        ops=nodes,
    )
    ok, new_ver, err = kernel.commit_plan_delta(delta)
    assert ok, f"commit_plan_delta failed: {err}"
    return new_ver


# ---------------------------------------------------------------------------
# 1. Config Validation Tests
# ---------------------------------------------------------------------------


def test_config_validation_bounds() -> None:
    """SCHED-001: Configuration bounds must be validated strictly fail-closed."""
    # Valid default config
    cfg = SchedulerConfig()
    assert cfg.max_concurrent_workers == 4
    assert cfg.per_space_concurrency == 2

    # Zero or negative workers rejected
    with pytest.raises(ValueError, match="max_concurrent_workers must be at least 1"):
        SchedulerConfig(max_concurrent_workers=0)

    # Excessive workers rejected (> 64)
    with pytest.raises(ValueError, match="exceeds maximum allowable bound"):
        SchedulerConfig(max_concurrent_workers=100)

    # Per-space > max_concurrent rejected
    with pytest.raises(ValueError, match="cannot exceed max_concurrent_workers"):
        SchedulerConfig(max_concurrent_workers=4, per_space_concurrency=8)

    # Ready queue capacity bounded
    with pytest.raises(ValueError, match="ready_queue_capacity must be at least 1"):
        SchedulerConfig(ready_queue_capacity=0)


# ---------------------------------------------------------------------------
# 2. Deterministic Candidate Prioritization (SCHED-003, Slice C)
# ---------------------------------------------------------------------------


def test_candidate_priority_sorting() -> None:
    """Slice C: TaskCandidate sorts by (-priority, topological_depth, task_id)."""
    c1 = TaskCandidate("s1", "task-z", priority=10, topological_depth=2, plan_version=1, capability="test")
    c2 = TaskCandidate("s1", "task-a", priority=10, topological_depth=1, plan_version=1, capability="test")
    c3 = TaskCandidate("s1", "task-b", priority=20, topological_depth=3, plan_version=1, capability="test")
    c4 = TaskCandidate("s1", "task-c", priority=10, topological_depth=1, plan_version=1, capability="test")

    candidates = [c1, c2, c3, c4]
    candidates.sort(key=lambda c: c.sort_key())

    # c3 has highest priority (20) -> first
    assert candidates[0].task_id == "task-b"
    # c2 and c4 have priority 10, depth 1 -> tie-break by task_id: 'task-a' before 'task-c'
    assert candidates[1].task_id == "task-a"
    assert candidates[2].task_id == "task-c"
    # c1 has priority 10, depth 2 -> last
    assert candidates[3].task_id == "task-z"


# ---------------------------------------------------------------------------
# 3. Bounded Concurrency: Global & Per-Space (SCHED-001, Slices A & B, ADV-01)
# ---------------------------------------------------------------------------


def test_bounded_worker_pool_limit() -> None:
    """Slice A / ADV-SCHED-01: Global concurrent workers never exceed max_concurrent_workers."""
    kernel, res_mgr, invoker, ident, _ = _create_env("sp-bound", invoker_delay=0.1, capacity=20)
    # Add 12 independent tasks
    ops = [
        {"op": "add", "target_node_id": f"t-{i}", "capability": "python.eval_sandboxed", "state": "ready"}
        for i in range(12)
    ]
    _commit_tasks(kernel, ops)

    cfg = SchedulerConfig(max_concurrent_workers=3, per_space_concurrency=3)
    scheduler = ConcurrentDAGScheduler(config=cfg)
    scheduler.register_space("sp-bound", kernel, res_mgr, invoker, ident)

    try:
        # Step and let workers run
        for _ in range(5):
            scheduler.step()
            time.sleep(0.02)

        # Invariant: At no point did active workers exceed max_concurrent_workers (3)
        assert invoker.max_observed_concurrency <= 3
        scheduler.run_until_converged("sp-bound", timeout=5.0)
        assert scheduler.is_space_converged("sp-bound")
    finally:
        scheduler.shutdown()


def test_per_space_concurrency_limit() -> None:
    """Slice B: A single Space cannot consume more than per_space_concurrency slots."""
    kernel, res_mgr, invoker, ident, _ = _create_env("sp-limit", invoker_delay=0.1, capacity=20)
    ops = [
        {"op": "add", "target_node_id": f"t-{i}", "capability": "python.eval_sandboxed", "state": "ready"}
        for i in range(8)
    ]
    _commit_tasks(kernel, ops)

    cfg = SchedulerConfig(max_concurrent_workers=8, per_space_concurrency=2)
    scheduler = ConcurrentDAGScheduler(config=cfg)
    scheduler.register_space("sp-limit", kernel, res_mgr, invoker, ident)

    try:
        for _ in range(5):
            scheduler.step()
            time.sleep(0.02)

        # Invariant: Concurrency never exceeded per_space_concurrency (2)
        assert invoker.max_observed_concurrency <= 2
        scheduler.run_until_converged("sp-limit", timeout=5.0)
        assert scheduler.is_space_converged("sp-limit")
    finally:
        scheduler.shutdown()


# ---------------------------------------------------------------------------
# 4. Diamond DAG Parallel Execution & Cascading Unblock (Slices D & J)
# ---------------------------------------------------------------------------


def test_diamond_dag_concurrent_execution() -> None:
    """Slice D / Slice J: Diamond DAG A -> (B, C) -> D executes B and C concurrently."""
    kernel, res_mgr, invoker, ident, _ = _create_env("sp-diamond", invoker_delay=0.05, capacity=10)

    # Build Diamond DAG: A -> (B, C) -> D
    ops = [
        {"op": "add", "target_node_id": "task-A", "capability": "python.eval_sandboxed", "state": "ready"},
        {"op": "add", "target_node_id": "task-B", "capability": "python.eval_sandboxed", "state": "pending", "dependencies": ["task-A"]},
        {"op": "add", "target_node_id": "task-C", "capability": "python.eval_sandboxed", "state": "pending", "dependencies": ["task-A"]},
        {"op": "add", "target_node_id": "task-D", "capability": "python.eval_sandboxed", "state": "pending", "dependencies": ["task-B", "task-C"]},
    ]
    _commit_tasks(kernel, ops)

    cfg = SchedulerConfig(max_concurrent_workers=4, per_space_concurrency=4)
    scheduler = ConcurrentDAGScheduler(config=cfg)
    scheduler.register_space("sp-diamond", kernel, res_mgr, invoker, ident)

    try:
        converged = scheduler.run_until_converged("sp-diamond", max_cycles=50, timeout=10.0)
        assert converged is True
        assert scheduler.is_space_succeeded("sp-diamond")

        graph = kernel.get_task_graph()
        assert graph.get_node("task-A").state == TaskState.COMPLETED.value
        assert graph.get_node("task-B").state == TaskState.COMPLETED.value
        assert graph.get_node("task-C").state == TaskState.COMPLETED.value
        assert graph.get_node("task-D").state == TaskState.COMPLETED.value

        # Invariant: B and C ran in parallel -> peak concurrency >= 2
        assert invoker.max_observed_concurrency >= 2
    finally:
        scheduler.shutdown()


# ---------------------------------------------------------------------------
# 5. Concurrent Completion CAS Rebase (SCHED-002, Slice E, ADV-03..05)
# ---------------------------------------------------------------------------


def test_concurrent_completion_cas_rebase() -> None:
    """Slice E / ADV-SCHED-05: Concurrent tasks completing simultaneously rebase and commit."""
    kernel, res_mgr, invoker, ident, bus = _create_env("sp-cas", invoker_delay=0.05, capacity=10)

    ops = [
        {"op": "add", "target_node_id": "task-parallel-1", "capability": "python.eval_sandboxed", "state": "ready"},
        {"op": "add", "target_node_id": "task-parallel-2", "capability": "python.eval_sandboxed", "state": "ready"},
    ]
    _commit_tasks(kernel, ops)

    cfg = SchedulerConfig(max_concurrent_workers=2, per_space_concurrency=2, max_cas_rebases=3)
    scheduler = ConcurrentDAGScheduler(config=cfg, bus=bus)
    scheduler.register_space("sp-cas", kernel, res_mgr, invoker, ident)

    try:
        converged = scheduler.run_until_converged("sp-cas", max_cycles=50, timeout=5.0)
        assert converged is True

        graph = kernel.get_task_graph()
        assert graph.get_node("task-parallel-1").state == TaskState.COMPLETED.value
        assert graph.get_node("task-parallel-2").state == TaskState.COMPLETED.value
    finally:
        scheduler.shutdown()


# ---------------------------------------------------------------------------
# 6. Multi-Space Fair Sharing (SCHED-003, Slice F, ADV-02)
# ---------------------------------------------------------------------------


def test_multi_space_fair_sharing() -> None:
    """Slice F / ADV-SCHED-02: Round-robin scheduling prevents starvation between spaces."""
    k1, rm1, inv1, id1, _ = _create_env("sp-bulk", invoker_delay=0.05, capacity=20)
    k2, rm2, inv2, id2, _ = _create_env("sp-small", invoker_delay=0.05, capacity=20)

    # Space 1 has 10 tasks, Space 2 has 2 tasks
    _commit_tasks(k1, [
        {"op": "add", "target_node_id": f"bulk-{i}", "capability": "python.eval_sandboxed", "state": "ready"}
        for i in range(10)
    ])
    _commit_tasks(k2, [
        {"op": "add", "target_node_id": f"small-{i}", "capability": "python.eval_sandboxed", "state": "ready"}
        for i in range(2)
    ])

    cfg = SchedulerConfig(max_concurrent_workers=4, per_space_concurrency=2)
    scheduler = ConcurrentDAGScheduler(config=cfg)
    scheduler.register_space("sp-bulk", k1, rm1, inv1, id1)
    scheduler.register_space("sp-small", k2, rm2, inv2, id2)

    try:
        # First step should schedule from both spaces (round robin)
        dispatched = scheduler.step()
        assert dispatched >= 3  # Takes up to 2 from sp-bulk, up to 2 from sp-small

        # Space 2's tasks must complete without being blocked behind all 10 tasks of Space 1
        scheduler.drain(timeout=5.0)
        assert scheduler.is_space_converged("sp-small")
        assert scheduler.is_space_succeeded("sp-small")
    finally:
        scheduler.shutdown()


# ---------------------------------------------------------------------------
# 7. Atomic Budget Pre-Reservation (SCHED-004, Slice G, ADV-06)
# ---------------------------------------------------------------------------


def test_atomic_budget_prereservation_and_hard_stop() -> None:
    """Slice G / ADV-SCHED-06: Atomic budget pre-reservation enforces hard_stop across concurrency."""
    bus = SpyPulseBus()
    kernel = SpaceKernel(
        space_id="sp-budget",
        owner_id="owner-test",
        bus=bus,
        budget=100.0,
        budget_policy="hard_stop",
    )
    res_mgr = ResourceManager(bus=bus, store=InMemoryResourceStore())
    ident = ResourceIdentity("cpu", "host-bud", "core-0")
    res_mgr.register_resource(Resource(identity=ident, space_id="sp-budget", total_capacity=10))
    invoker = ControlledWorkerInvoker(delay_seconds=0.02)

    # Add 2 tasks, each requesting 70 budget. Total required = 140 > 100.
    ops = [
        {"op": "add", "target_node_id": "b-task-1", "capability": "python.eval_sandboxed", "state": "ready"},
        {"op": "add", "target_node_id": "b-task-2", "capability": "python.eval_sandboxed", "state": "ready"},
    ]
    _commit_tasks(kernel, ops)

    cfg = SchedulerConfig(max_concurrent_workers=2, per_space_concurrency=2)
    scheduler = ConcurrentDAGScheduler(config=cfg, bus=bus)
    scheduler.register_space("sp-budget", kernel, res_mgr, invoker, ident, default_budget=70.0)

    try:
        scheduler.run_until_converged("sp-budget", max_cycles=20, timeout=5.0)

        graph = kernel.get_task_graph()
        # Exactly one task should succeed, and the other should be blocked / denied due to budget exhaustion
        n1 = graph.get_node("b-task-1")
        n2 = graph.get_node("b-task-2")

        states = {n1.state, n2.state}
        assert TaskState.COMPLETED.value in states
        assert TaskState.BLOCKED.value in states or TaskState.FAILED.value in states
    finally:
        scheduler.shutdown()


# ---------------------------------------------------------------------------
# 8. Duplicate Dispatch Deduplication (SCHED-005, Slice I, ADV-14)
# ---------------------------------------------------------------------------


def test_duplicate_dispatch_deduplication() -> None:
    """Slice I / ADV-SCHED-14: Attempting to dispatch an already-dispatched task is contained."""
    kernel, res_mgr, invoker, ident, _ = _create_env("sp-dedup", invoker_delay=0.1, capacity=5)
    _commit_tasks(kernel, [
        {"op": "add", "target_node_id": "dup-task-1", "capability": "python.eval_sandboxed", "state": "ready"}
    ])

    attempt_store = InMemoryExecutionAttemptStore()
    scheduler = ConcurrentDAGScheduler(attempt_store=attempt_store)
    scheduler.register_space("sp-dedup", kernel, res_mgr, invoker, ident)

    try:
        # First step dispatches the candidate
        d1 = scheduler.step()
        assert d1 == 1

        # Second step immediately without waiting: candidate discovery skips active task
        d2 = scheduler.step()
        assert d2 == 0

        scheduler.drain(timeout=5.0)
        assert scheduler.is_space_converged("sp-dedup")
    finally:
        scheduler.shutdown()


def test_durable_race_safe_idempotency_claim() -> None:
    """Critical Check #1: Two concurrent scheduler execution attempts for same logical task
    and same idempotency key guarantee:
      - exactly one durable execution attempt wins
      - exactly one worker execution starts
      - losing caller observes existing attempt
      - no duplicate worker spawn
    """
    kernel, res_mgr, invoker, ident, _ = _create_env("sp-claim-race", invoker_delay=0.05, capacity=5)
    _commit_tasks(kernel, [
        {"op": "add", "target_node_id": "race-task-1", "capability": "python.eval_sandboxed", "state": "ready"}
    ])

    attempt_store = InMemoryExecutionAttemptStore()
    scheduler = ConcurrentDAGScheduler(attempt_store=attempt_store)
    scheduler.register_space("sp-claim-race", kernel, res_mgr, invoker, ident)

    candidates = scheduler.discover_eligible_candidates("sp-claim-race")
    assert len(candidates) == 1
    cand = candidates[0]

    results: list[bool] = []
    barrier = threading.Barrier(2)

    def attempt_dispatch() -> None:
        barrier.wait()
        win = scheduler.dispatch_candidate("sp-claim-race", cand)
        results.append(win)

    t1 = threading.Thread(target=attempt_dispatch)
    t2 = threading.Thread(target=attempt_dispatch)

    t1.start()
    t2.start()
    t1.join()
    t2.join()

    try:
        # Exactly one winner and one loser
        assert results.count(True) == 1
        assert results.count(False) == 1

        # Check attempt store: exactly one attempt record exists
        idempotency_key = compute_dispatch_idempotency_key(
            space_id=cand.space_id,
            plan_version=cand.plan_version,
            task_id=cand.task_id,
            attempt=cand.attempt,
        )
        rec = attempt_store.get_attempt(idempotency_key)
        assert rec is not None
        assert rec.task_id == "race-task-1"

        # Losing caller observing existing attempt can verify claim rejection
        assert attempt_store.claim_attempt(
            ExecutionAttemptRecord(
                attempt_id="third-caller",
                idempotency_key=idempotency_key,
                space_id=cand.space_id,
                task_id=cand.task_id,
                plan_version=cand.plan_version,
                attempt_number=cand.attempt,
                capability=cand.capability,
                status="dispatched",
            )
        ) is False

        scheduler.drain(timeout=5.0)

        # Exactly one worker execution ran (invoked requests list length is exactly 1)
        assert len(invoker.invoked_requests) == 1
    finally:
        scheduler.shutdown()


# ---------------------------------------------------------------------------
# 9. Hardware Lease Contention & Rollback (Slice H, ADV-07)
# ---------------------------------------------------------------------------


def test_worker_failure_and_lease_release() -> None:
    """Slice H / ADV-SCHED-07: Injected worker failure transitions to FAILED and releases lease."""
    kernel, res_mgr, invoker, ident, _ = _create_env("sp-fail", invoker_delay=0.01, capacity=1)
    _commit_tasks(kernel, [
        {"op": "add", "target_node_id": "fail-task", "capability": "python.eval_sandboxed", "state": "ready"}
    ])

    invoker.fail_tasks.add("fail-task")

    scheduler = ConcurrentDAGScheduler()
    scheduler.register_space("sp-fail", kernel, res_mgr, invoker, ident)

    try:
        scheduler.run_until_converged("sp-fail", max_cycles=10, timeout=5.0)

        graph = kernel.get_task_graph()
        node = graph.get_node("fail-task")
        assert node.state == TaskState.FAILED.value

        # Invariant: Resource was released back to capacity 1
        res = res_mgr.get_resource(ident)
        assert res.available_capacity == 1
    finally:
        scheduler.shutdown()


# ---------------------------------------------------------------------------
# 10. Cross-Space Boundary Enforcement (SCCA Law 1, ADV-08)
# ---------------------------------------------------------------------------


def test_cross_space_isolation_enforced() -> None:
    """ADV-SCHED-08: Registering kernel with mismatched space_id raises CrossSpaceViolationError."""
    kernel, res_mgr, invoker, ident, _ = _create_env("sp-real")
    scheduler = ConcurrentDAGScheduler()

    with pytest.raises(CrossSpaceViolationError, match="does not match registered space_id"):
        scheduler.register_space("sp-imposter", kernel, res_mgr, invoker, ident)


# ---------------------------------------------------------------------------
# 11. Graceful Shutdown & Drain (Slice L)
# ---------------------------------------------------------------------------


def test_graceful_shutdown_and_drain() -> None:
    """Slice L: drain() and shutdown() wait for active tasks without orphan threads."""
    kernel, res_mgr, invoker, ident, _ = _create_env("sp-drain", invoker_delay=0.05, capacity=5)
    _commit_tasks(kernel, [
        {"op": "add", "target_node_id": f"dr-{i}", "capability": "python.eval_sandboxed", "state": "ready"}
        for i in range(4)
    ])

    scheduler = ConcurrentDAGScheduler()
    scheduler.register_space("sp-drain", kernel, res_mgr, invoker, ident)

    with scheduler:
        # Scheduler runs in background thread
        time.sleep(0.1)

    # When context exits, shutdown completes and all tasks finish
    assert scheduler.is_space_converged("sp-drain")
    assert scheduler.get_active_task_count() == 0


# ---------------------------------------------------------------------------
# 12. Additional Adversarial & Edge Cases (ADV-SCHED-03, 04, 09..16)
# ---------------------------------------------------------------------------


def test_adv_sched_03_stale_task_state_rebase() -> None:
    """ADV-SCHED-03: Stale plan version rebases up to max_cas_rebases during transition."""
    kernel, res_mgr, invoker, ident, _ = _create_env("sp-rebase-stale")
    _commit_tasks(kernel, [
        {"op": "add", "target_node_id": "rebase-t1", "capability": "python.eval_sandboxed", "state": "ready"}
    ])

    old_ver = kernel.get_plan_version()

    # Bumping plan version concurrently
    _commit_tasks(kernel, [
        {"op": "add", "target_node_id": "concurrent-t2", "capability": "python.eval_sandboxed", "state": "pending"}
    ])
    new_ver = kernel.get_plan_version()
    assert new_ver > old_ver

    # propose_task_transition with old_ver should succeed via optimistic rebase
    ok, res_ver, err = kernel.propose_task_transition(
        task_id="rebase-t1",
        to_state=TaskState.ADMISSION_PENDING.value,
        expected_plan_version=old_ver,
        from_state=TaskState.READY.value,
        reason="Testing optimistic rebase",
        max_rebases=3,
    )
    assert ok is True
    assert res_ver == new_ver + 1


def test_adv_sched_04_concurrent_cas_storm() -> None:
    """ADV-SCHED-04: High parallelism storm of 8 tasks committing transitions in parallel."""
    kernel, res_mgr, invoker, ident, _ = _create_env("sp-storm", invoker_delay=0.02, capacity=20)
    task_count = 8
    _commit_tasks(kernel, [
        {"op": "add", "target_node_id": f"storm-{i}", "capability": "python.eval_sandboxed", "state": "ready"}
        for i in range(task_count)
    ])

    cfg = SchedulerConfig(max_concurrent_workers=task_count, per_space_concurrency=task_count, max_cas_rebases=5)
    scheduler = ConcurrentDAGScheduler(config=cfg)
    scheduler.register_space("sp-storm", kernel, res_mgr, invoker, ident)

    try:
        converged = scheduler.run_until_converged("sp-storm", max_cycles=50, timeout=10.0)
        assert converged is True

        graph = kernel.get_task_graph()
        for i in range(task_count):
            assert graph.get_node(f"storm-{i}").state == TaskState.COMPLETED.value
    finally:
        scheduler.shutdown()


def test_adv_sched_09_terminal_state_overwrite_rejected() -> None:
    """ADV-SCHED-09 / SCHED-005: Transition out of terminal state is strictly rejected."""
    kernel, res_mgr, invoker, ident, _ = _create_env("sp-term")
    _commit_tasks(kernel, [
        {"op": "add", "target_node_id": "term-t1", "capability": "python.eval_sandboxed", "state": "ready"}
    ])

    scheduler = ConcurrentDAGScheduler()
    scheduler.register_space("sp-term", kernel, res_mgr, invoker, ident)
    scheduler.run_until_converged("sp-term", max_cycles=10, timeout=3.0)
    scheduler.shutdown()

    graph = kernel.get_task_graph()
    assert graph.get_node("term-t1").state == TaskState.COMPLETED.value

    from core.plans.task_graph import IllegalStateTransitionError

    cur2 = kernel.get_plan_version()
    # Attempt illegal transition from COMPLETED to READY
    with pytest.raises(IllegalStateTransitionError):
        kernel.propose_task_transition(
            task_id="term-t1",
            to_state=TaskState.READY.value,
            expected_plan_version=cur2,
            reason="Illegal resurrect attempt",
        )


def test_adv_sched_10_circular_dependency_rejected() -> None:
    """ADV-SCHED-10: Graph with cycle cannot be topologically sequenced."""
    from core.plans.task_graph import GraphCycleError
    kernel, res_mgr, invoker, ident, _ = _create_env("sp-cycle")

    cur = kernel.get_plan_version()
    delta = PlanDelta(
        space_id="sp-cycle",
        base_version=cur,
        resulting_version=cur + 1,
        ops=[
            {"op": "add", "target_node_id": "node-A", "capability": "test", "state": "ready", "dependencies": ["node-B"]},
            {"op": "add", "target_node_id": "node-B", "capability": "test", "state": "ready", "dependencies": ["node-A"]},
        ],
    )
    kernel.commit_plan_delta(delta)
    graph = kernel.get_task_graph()
    assert graph.has_cycles() is True
    with pytest.raises(GraphCycleError):
        graph.validate_dependencies()


def test_adv_sched_11_ready_queue_capacity_limit() -> None:
    """ADV-SCHED-11: Candidate discovery strictly honors ready_queue_capacity bound."""
    kernel, res_mgr, invoker, ident, _ = _create_env("sp-qcap")
    _commit_tasks(kernel, [
        {"op": "add", "target_node_id": f"qc-{i}", "capability": "test", "state": "ready"}
        for i in range(20)
    ])

    cfg = SchedulerConfig(ready_queue_capacity=5)
    scheduler = ConcurrentDAGScheduler(config=cfg)
    scheduler.register_space("sp-qcap", kernel, res_mgr, invoker, ident)

    candidates = scheduler.discover_eligible_candidates("sp-qcap")
    assert len(candidates) == 5


def test_adv_sched_13_fractional_resource_contention() -> None:
    """ADV-SCHED-13: Overcommitted resource capacity queues or denies cleanly without leak."""
    kernel, res_mgr, invoker, ident, _ = _create_env("sp-contested", invoker_delay=0.1, capacity=1)
    # Add 2 tasks, resource capacity is only 1
    _commit_tasks(kernel, [
        {"op": "add", "target_node_id": "res-1", "capability": "python.eval_sandboxed", "state": "ready"},
        {"op": "add", "target_node_id": "res-2", "capability": "python.eval_sandboxed", "state": "ready"},
    ])

    cfg = SchedulerConfig(max_concurrent_workers=2, per_space_concurrency=2)
    scheduler = ConcurrentDAGScheduler(config=cfg)
    scheduler.register_space("sp-contested", kernel, res_mgr, invoker, ident)

    try:
        # Step once: both candidates submitted, but ResourceManager only gives lease to one
        scheduler.step()
        time.sleep(0.02)
        # Max concurrent worker execution is at most 1 because total capacity is 1
        assert invoker.concurrent_count <= 1

        scheduler.run_until_converged("sp-contested", max_cycles=30, timeout=5.0)
        assert scheduler.is_space_converged("sp-contested")
    finally:
        scheduler.shutdown()

