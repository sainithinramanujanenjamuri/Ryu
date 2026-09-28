"""Dispatcher protocols, lifecycle contracts, and deterministic scheduling models.

spec §4 (Space Orchestrator), §16 (TaskGraph & Dispatch), DISPATCH-001..005, ADR-0041
Phase 12: Autonomous Plan Execution & Task Dispatch Engine
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Protocol

from ryu.pulse_bus.pulse import Pulse, Severity

from core.capabilities.admission import CapabilityRequest, CapabilityResponse
from core.plans.delta import PlanDelta
from core.plans.task_graph import (
    TaskGraph,
    TaskNode,
    TaskNotFoundError,
    TaskState,
)
from core.resources.identity import ResourceIdentity
from core.resources.lease import Lease
from core.resources.manager import ResourceAcquisitionResult, ResourceManager


class CrossSpaceViolationError(Exception):
    """Raised when an operation attempts cross-space boundary traversal (SCCA Law 1, SPACE-001)."""


class DispatchAction(str, Enum):
    """Deterministic actions emitted by the Dispatcher for a TaskNode."""

    DISPATCH = "dispatch"
    AWAIT_DEPENDENCIES = "await_dependencies"
    AWAIT_ADMISSION = "await_admission"
    AWAIT_LEASE = "await_lease"
    AWAIT_APPROVAL = "await_approval"
    TASK_COMPLETED = "task_completed"
    TASK_FAILED = "task_failed"
    TASK_BLOCKED = "task_blocked"
    PLAN_CONVERGED = "plan_converged"
    PLAN_FAILED = "plan_failed"


@dataclass(frozen=True)
class DispatchDecision:
    """Deterministic dispatch decision for a specific TaskNode."""

    task_id: str
    action: DispatchAction
    space_id: str
    plan_version: int
    reason: str
    capability: str | None = None
    idempotency_key: str | None = None


@dataclass(frozen=True)
class DispatchAttempt:
    """Immutable record of an individual task execution dispatch attempt."""

    idempotency_key: str
    space_id: str
    plan_version: int
    task_id: str
    attempt: int
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    status: str = "dispatched"


def compute_dispatch_idempotency_key(
    space_id: str,
    plan_version: int,
    task_id: str,
    attempt: int = 1,
) -> str:
    """Deterministically compute an immutable idempotency key for a dispatch attempt.

    Invariant: The same (space_id, plan_version, task_id, attempt) tuple always produces
    the identical SHA-256 token, protecting against duplicate dispatch across redeliveries.
    """
    raw = f"{space_id}:{plan_version}:{task_id}:{attempt}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class VerifiedExecutionEvidence:
    """Verified evidence proving capability execution (DISPATCH-004, ADR-0041).

    Execution evidence is not limited to file artifacts; it encompasses
    verified file artifacts, structured task outputs, signed tool outputs,
    and non-zero duration execution metrics with exit code 0.
    """

    task_id: str
    evidence_type: str  # "artifact" | "structured_output" | "signed_telemetry" | "exit_code"
    verified: bool
    sha256: str | None = None
    exit_code: int = 0
    duration_seconds: float = 0.0
    output_payload: dict[str, Any] = field(default_factory=dict)
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TaskExecutionRequest:
    """Request contract submitted by Dispatcher to WorkerInvokerProtocol (Phase 12.4)."""

    request_id: str
    space_id: str
    task_id: str
    plan_id: str
    plan_version: int
    attempt: int
    capability: str
    lease_token: str = ""
    arguments: dict[str, Any] = field(default_factory=dict)
    worker_id: str = ""
    idempotency_key: str = ""
    is_tainted: bool = False
    timeout_seconds: float = 30.0
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True)
class TaskExecutionResult:
    """Result contract returned by WorkerInvokerProtocol to Dispatcher (Phase 12.4)."""

    request_id: str
    status: str  # "ok" | "failed" | "timeout" | "violation" | "cancelled" | "denied"
    task_id: str
    space_id: str
    plan_version: int
    output_data: Any = None
    artifacts: list[Any] = field(default_factory=list)
    taint: bool = False
    duration_seconds: float = 0.0
    error: str | None = None
    error_class: str | None = None
    logs: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def is_success(self) -> bool:
        return self.status == "ok" and self.error is None


class WorkerInvokerProtocol(Protocol):
    """Dependency inversion protocol for invoking capability workers (AGENTS.md §7)."""

    def invoke(self, request: TaskExecutionRequest) -> TaskExecutionResult: ...


class GoalEvaluatorProtocol(Protocol):
    """Dependency inversion protocol for evaluating plan evidence against GoalSpec."""

    def evaluate(self, goal_spec: Any, evidence: list[VerifiedExecutionEvidence]) -> Any: ...


class TaskDispatcherProtocol(Protocol):
    """Protocol governing deterministic TaskGraph evaluation and dispatch scheduling."""

    def evaluate_plan(self, task_graph: TaskGraph, space_id: str) -> list[DispatchDecision]: ...

    def get_ready_decisions(
        self, task_graph: TaskGraph, space_id: str
    ) -> list[DispatchDecision]: ...


class SpaceKernelAuthorityProtocol(Protocol):
    """Protocol decoupling Dispatcher from concrete SpaceKernel (AGENTS.md §7)."""

    @property
    def space_id(self) -> str: ...

    def get_plan_version(self) -> int: ...

    def get_task_graph(self, version: int | None = None) -> TaskGraph: ...

    def commit_plan_delta(
        self, delta: PlanDelta, proposal_id: str | None = None
    ) -> tuple[bool, int, str | None]: ...

    def propose_task_transition(
        self,
        task_id: str,
        to_state: str,
        expected_plan_version: int,
        from_state: str | None = None,
        reason: str = "",
        error: str | None = None,
        result_ref: str | None = None,
        proposal_id: str | None = None,
    ) -> tuple[bool, int, str | None]: ...

    def request_capability(
        self,
        request: CapabilityRequest,
        is_tainted: bool = False,
        approval: Any | None = None,
    ) -> CapabilityResponse: ...


@dataclass(frozen=True)
class TaskPipelineResult:
    """Outcome of coordinating pre-dispatch admission and resource leasing (Phase 12.3)."""

    task_id: str
    space_id: str
    plan_version: int
    admitted: bool
    leased: bool
    terminal_state: str
    capability_response: CapabilityResponse | None = None
    lease: Lease | None = None
    lease_token: str | None = None
    queue_position: int | None = None
    error: str | None = None
    reason: str = ""


@dataclass(frozen=True)
class DispatchExecutionResult:
    """Consolidated outcome of dispatching and executing a task via WorkerInvoker (Phase 12.4)."""

    task_id: str
    space_id: str
    plan_version: int
    status: str  # "ok" | "failed" | "timeout" | "violation" | "cancelled" | "denied" | "cas_failed" | "rejected"
    terminal_state: str  # "observing" | "failed" | "timed_out" | "cancelled" | "leased" | "blocked"
    execution_result: TaskExecutionResult | None = None
    lease_token: str | None = None
    output_data: Any = None
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    taint: bool = False
    duration_seconds: float = 0.0
    error: str | None = None
    reason: str = ""
    cached: bool = False


class DeterministicDispatcher:
    """Deterministic DAG evaluator and dispatch decision engine (Phase 12.1, ADR-0041).

    Constitutional Invariants:
    - SCCA Law 1 (Space-Centric): Strictly verified against space_id.
    - SCCA Law 2 (Capabilities Requested): Issues decisions; never grants capabilities.
    - AGENTS.md §7 (Deterministic Core Independence): Zero imports of workers/agents/channels.
    - Bounded & Deterministic: Kahn's DAG traversal, cycle rejection, tie-breaking by task ID.
    """

    def __init__(self) -> None:
        self._tracked_attempts: dict[str, DispatchAttempt] = {}

    def evaluate_plan(self, task_graph: TaskGraph, space_id: str) -> list[DispatchDecision]:
        """Evaluate the entire TaskGraph and return decisions for every task in deterministic order.

        Raises:
            CrossSpaceViolationError: if task_graph space does not match requested space.
            MissingDependencyError: if a task depends on an unknown task.
            GraphCycleError: if the graph contains circular dependencies.
        """
        if task_graph.space_id != space_id:
            raise CrossSpaceViolationError(
                f"Cross-space dispatch rejected: target space '{task_graph.space_id}' "
                f"does not match context '{space_id}'."
            )

        task_graph.validate_dependencies()
        decisions: list[DispatchDecision] = []

        # Sort nodes topologically for deterministic evaluation
        ordered_nodes = task_graph.topological_sort()

        for node in ordered_nodes:
            decision = self._evaluate_node(node, task_graph, space_id)
            decisions.append(decision)

        return decisions

    def get_ready_decisions(
        self, task_graph: TaskGraph, space_id: str
    ) -> list[DispatchDecision]:
        """Return only actionable DISPATCH decisions for currently ready tasks."""
        all_decisions = self.evaluate_plan(task_graph, space_id)
        return [d for d in all_decisions if d.action == DispatchAction.DISPATCH]

    def record_attempt(self, attempt: DispatchAttempt) -> bool:
        """Record an in-flight dispatch attempt for idempotency deduplication.

        Returns:
            True if recorded (first time seen), False if duplicate attempt already exists.
        """
        if attempt.idempotency_key in self._tracked_attempts:
            return False
        self._tracked_attempts[attempt.idempotency_key] = attempt
        return True

    def is_attempt_tracked(self, idempotency_key: str) -> bool:
        """Check if an attempt is currently tracked."""
        return idempotency_key in self._tracked_attempts

    def _evaluate_node(
        self, node: TaskNode, task_graph: TaskGraph, space_id: str
    ) -> DispatchDecision:
        norm_state = node.state.lower()

        if norm_state == "completed":
            return DispatchDecision(
                task_id=node.id,
                action=DispatchAction.TASK_COMPLETED,
                space_id=space_id,
                plan_version=task_graph.plan_version,
                reason="Task is already completed",
                capability=node.capability,
            )

        if norm_state in ("failed", "escalated"):
            return DispatchDecision(
                task_id=node.id,
                action=DispatchAction.TASK_FAILED,
                space_id=space_id,
                plan_version=task_graph.plan_version,
                reason=f"Task is in {norm_state} state: {node.error or 'failure'}",
                capability=node.capability,
            )

        if norm_state in ("blocked", "timed_out"):
            return DispatchDecision(
                task_id=node.id,
                action=DispatchAction.TASK_BLOCKED,
                space_id=space_id,
                plan_version=task_graph.plan_version,
                reason=f"Task is {norm_state}: {node.error or 'blocked'}",
                capability=node.capability,
            )

        # Check dependencies
        all_deps_completed = True
        failed_dep: str | None = None
        for dep_id in node.dependencies:
            parent = task_graph.get_node(dep_id)
            if parent is None:
                all_deps_completed = False
                break
            if parent.state == "completed":
                continue
            if parent.optional and parent.state in ("failed", "cancelled"):
                continue
            if parent.state in ("failed", "escalated", "blocked"):
                failed_dep = dep_id
            all_deps_completed = False
            break

        if failed_dep:
            return DispatchDecision(
                task_id=node.id,
                action=DispatchAction.TASK_BLOCKED,
                space_id=space_id,
                plan_version=task_graph.plan_version,
                reason=f"Prerequisite dependency '{failed_dep}' failed or blocked",
                capability=node.capability,
            )

        if not all_deps_completed:
            return DispatchDecision(
                task_id=node.id,
                action=DispatchAction.AWAIT_DEPENDENCIES,
                space_id=space_id,
                plan_version=task_graph.plan_version,
                reason="Prerequisite dependencies are still in-flight or pending",
                capability=node.capability,
            )

        # All dependencies completed: ready to dispatch
        idempotency_key = compute_dispatch_idempotency_key(
            space_id=space_id,
            plan_version=task_graph.plan_version,
            task_id=node.id,
            attempt=node.attempt,
        )

        return DispatchDecision(
            task_id=node.id,
            action=DispatchAction.DISPATCH,
            space_id=space_id,
            plan_version=task_graph.plan_version,
            reason="All prerequisite dependencies completed; task is ready for dispatch",
            capability=node.capability,
            idempotency_key=idempotency_key,
        )

    def create_transition_delta(
        self,
        space_id: str,
        task_id: str,
        to_state: str,
        expected_plan_version: int,
        from_state: str | None = None,
        reason: str = "",
        error: str | None = None,
        result_ref: str | None = None,
        delta_id: str | None = None,
    ) -> PlanDelta:
        """Construct a validated PlanDelta for a task lifecycle transition."""
        ops = [
            {
                "op": "transition",
                "target_node_id": task_id,
                "to_state": to_state,
                "from_state": from_state,
                "reason": reason,
                "error": error,
                "result_ref": result_ref,
            }
        ]
        kwargs: dict[str, Any] = {
            "space_id": space_id,
            "base_version": expected_plan_version,
            "resulting_version": expected_plan_version + 1,
            "ops": ops,
        }
        if delta_id:
            kwargs["delta_id"] = delta_id
        return PlanDelta(**kwargs)

    def propose_transition(
        self,
        kernel: SpaceKernelAuthorityProtocol,
        task_id: str,
        to_state: str,
        expected_plan_version: int,
        from_state: str | None = None,
        reason: str = "",
        error: str | None = None,
        result_ref: str | None = None,
    ) -> tuple[bool, int, str | None]:
        """Propose a task state transition to SpaceKernel via PlanDelta CAS.

        The Dispatcher NEVER directly mutates the PlanStore. SpaceKernel validates authority.
        """
        delta = self.create_transition_delta(
            space_id=kernel.space_id,
            task_id=task_id,
            to_state=to_state,
            expected_plan_version=expected_plan_version,
            from_state=from_state,
            reason=reason,
            error=error,
            result_ref=result_ref,
        )
        return kernel.commit_plan_delta(delta, proposal_id=task_id)

    def rebase_and_propose_transition(
        self,
        kernel: SpaceKernelAuthorityProtocol,
        task_id: str,
        to_state: str,
        max_rebases: int = 3,
        from_state: str | None = None,
        reason: str = "",
        error: str | None = None,
        result_ref: str | None = None,
    ) -> tuple[bool, int]:
        """Attempt transition with bounded rebase if concurrent deltas supersede plan version (ADR-0003)."""
        current_ver = kernel.get_plan_version()
        success, new_ver, _ = self.propose_transition(
            kernel=kernel,
            task_id=task_id,
            to_state=to_state,
            expected_plan_version=current_ver,
            from_state=from_state,
            reason=reason,
            error=error,
            result_ref=result_ref,
        )
        if success:
            return True, new_ver

        for _ in range(max_rebases):
            latest_ver = kernel.get_plan_version()
            graph = kernel.get_task_graph()
            node = graph.get_node(task_id)
            if node is None:
                return False, latest_ver
            if from_state and node.state.lower() != from_state.lower():
                return False, latest_ver
            success, rebased_ver, _ = self.propose_transition(
                kernel=kernel,
                task_id=task_id,
                to_state=to_state,
                expected_plan_version=latest_ver,
                from_state=from_state,
                reason=f"{reason} (rebased)",
                error=error,
                result_ref=result_ref,
            )
            if success:
                return True, rebased_ver

        return False, kernel.get_plan_version()

    def request_task_admission(
        self,
        kernel: SpaceKernelAuthorityProtocol,
        task_id: str,
        expected_plan_version: int | None = None,
        is_tainted: bool = False,
        approval: Any | None = None,
        budget: float = 0.0,
        timeout: float = 30.0,
    ) -> tuple[bool, CapabilityResponse, int]:
        """Request capability admission for a task through SpaceKernel AdmissionController (Phase 12.3).

        SCCA Lifecycle Transitions:
            - If task in PENDING (with all dependencies completed): PENDING -> READY via CAS
            - If task in READY: READY -> ADMISSION_PENDING via CAS
            - If task in BLOCKED (with approval/policy resolution): BLOCKED -> ADMISSION_PENDING via CAS
            - If task already ADMITTED: returns admitted=True immediately
            - Evaluates kernel.request_capability()
            - If admitted: ADMISSION_PENDING -> ADMITTED via CAS
            - If denied: ADMISSION_PENDING -> BLOCKED via CAS

        Returns:
            (admitted: bool, response: CapabilityResponse, resulting_plan_version: int)
        """
        graph = kernel.get_task_graph()
        node = graph.get_node(task_id)
        if node is None:
            raise TaskNotFoundError(
                f"Task '{task_id}' not found in space '{kernel.space_id}'"
            )

        cur_version = kernel.get_plan_version()
        if expected_plan_version is not None and cur_version != expected_plan_version:
            return (
                False,
                CapabilityResponse(
                    status="denied",
                    error="plan_version_mismatch",
                    cost=0.0,
                ),
                cur_version,
            )

        norm_state = node.state.lower()
        if norm_state == TaskState.ADMITTED.value:
            return (
                True,
                CapabilityResponse(status="ok", result={"already_admitted": True}),
                cur_version,
            )

        # Handle PENDING state: check dependencies then transition to READY
        if norm_state == TaskState.PENDING.value:
            for dep_id in node.dependencies:
                dep_node = graph.get_node(dep_id)
                if dep_node is None or dep_node.state.lower() != TaskState.COMPLETED.value:
                    return (
                        False,
                        CapabilityResponse(
                            status="denied",
                            error=f"unresolved_dependency: {dep_id}",
                            cost=0.0,
                        ),
                        cur_version,
                    )
            ok, new_ver, err = kernel.propose_task_transition(
                task_id=task_id,
                to_state=TaskState.READY.value,
                expected_plan_version=cur_version,
                from_state=TaskState.PENDING.value,
                reason="Dependencies satisfied; marking task ready",
            )
            if not ok:
                return (
                    False,
                    CapabilityResponse(status="denied", error=f"cas_failed: {err}"),
                    new_ver,
                )
            cur_version = new_ver
            norm_state = TaskState.READY.value

        # Handle READY state: transition to ADMISSION_PENDING
        if norm_state == TaskState.READY.value:
            ok, new_ver, err = kernel.propose_task_transition(
                task_id=task_id,
                to_state=TaskState.ADMISSION_PENDING.value,
                expected_plan_version=cur_version,
                from_state=TaskState.READY.value,
                reason="Requesting capability admission",
            )
            if not ok:
                return (
                    False,
                    CapabilityResponse(
                        status="denied",
                        error=f"cas_failed: {err}",
                        cost=0.0,
                    ),
                    new_ver,
                )
            cur_version = new_ver
            norm_state = TaskState.ADMISSION_PENDING.value

        # Handle BLOCKED state: transition to ADMISSION_PENDING if re-evaluating
        elif norm_state == TaskState.BLOCKED.value:
            ok, new_ver, err = kernel.propose_task_transition(
                task_id=task_id,
                to_state=TaskState.ADMISSION_PENDING.value,
                expected_plan_version=cur_version,
                from_state=TaskState.BLOCKED.value,
                reason="Re-evaluating blocked task for admission",
            )
            if not ok:
                return (
                    False,
                    CapabilityResponse(
                        status="denied",
                        error=f"cas_failed: {err}",
                        cost=0.0,
                    ),
                    new_ver,
                )
            cur_version = new_ver
            norm_state = TaskState.ADMISSION_PENDING.value

        elif norm_state != TaskState.ADMISSION_PENDING.value:
            return (
                False,
                CapabilityResponse(
                    status="denied",
                    error=f"invalid_task_state_for_admission: {node.state}",
                    cost=0.0,
                ),
                cur_version,
            )

        # Build formal CapabilityRequest matching contracts
        idempotency_key = compute_dispatch_idempotency_key(
            space_id=kernel.space_id,
            plan_version=cur_version,
            task_id=task_id,
            attempt=node.attempt,
        )
        req = CapabilityRequest(
            requester_id=task_id,
            space_id=kernel.space_id,
            capability=node.capability,
            params=dict(node.params),
            timeout=timeout,
            budget=budget,
            idempotency_key=idempotency_key,
        )

        # Authoritative kernel admission evaluation
        response = kernel.request_capability(
            request=req,
            is_tainted=is_tainted,
            approval=approval,
        )

        if response.status == "ok":
            ok, new_ver, err = kernel.propose_task_transition(
                task_id=task_id,
                to_state=TaskState.ADMITTED.value,
                expected_plan_version=cur_version,
                from_state=TaskState.ADMISSION_PENDING.value,
                reason="Capability admitted by kernel AdmissionController",
            )
            if not ok:
                return (
                    False,
                    CapabilityResponse(
                        status="denied",
                        error=f"cas_failed: {err}",
                        cost=0.0,
                    ),
                    new_ver,
                )
            return True, response, new_ver

        # Admission denied: transition to BLOCKED via CAS
        err_msg = response.error or "admission_denied"
        ok, new_ver, _ = kernel.propose_task_transition(
            task_id=task_id,
            to_state=TaskState.BLOCKED.value,
            expected_plan_version=cur_version,
            from_state=TaskState.ADMISSION_PENDING.value,
            reason=f"Admission denied: {err_msg}",
            error=err_msg,
        )
        return False, response, new_ver

    def acquire_task_lease(
        self,
        kernel: SpaceKernelAuthorityProtocol,
        resource_mgr: ResourceManager,
        task_id: str,
        identity: ResourceIdentity,
        expected_plan_version: int | None = None,
        units: int = 1,
        duration_seconds: float = 60.0,
        priority: int = 0,
        scope: str = "execution",
    ) -> tuple[bool, ResourceAcquisitionResult, int]:
        """Request resource lease allocation for an admitted task through ResourceManager (Phase 12.3).

        SCCA Lifecycle Transitions:
            - If task in ADMITTED: ADMITTED -> LEASE_PENDING via CAS
            - If task already LEASED: returns leased=True immediately
            - Evaluates resource_mgr.acquire()
            - If granted: LEASE_PENDING -> LEASED via CAS (with rollback on CAS failure)
            - If contested (queued): remains in LEASE_PENDING
            - If denied: LEASE_PENDING -> FAILED via CAS

        Returns:
            (leased: bool, acquisition: ResourceAcquisitionResult, resulting_plan_version: int)
        """
        graph = kernel.get_task_graph()
        node = graph.get_node(task_id)
        if node is None:
            raise TaskNotFoundError(
                f"Task '{task_id}' not found in space '{kernel.space_id}'"
            )

        cur_version = kernel.get_plan_version()
        if expected_plan_version is not None and cur_version != expected_plan_version:
            return (
                False,
                ResourceAcquisitionResult(
                    granted=False,
                    reason=f"plan_version_mismatch: expected {expected_plan_version}, current {cur_version}",
                ),
                cur_version,
            )

        norm_state = node.state.lower()
        if norm_state == TaskState.LEASED.value:
            return True, ResourceAcquisitionResult(granted=True, cached=True), cur_version

        if norm_state == TaskState.ADMITTED.value:
            ok, new_ver, err = kernel.propose_task_transition(
                task_id=task_id,
                to_state=TaskState.LEASE_PENDING.value,
                expected_plan_version=cur_version,
                from_state=TaskState.ADMITTED.value,
                reason="Requesting resource lease allocation",
            )
            if not ok:
                return (
                    False,
                    ResourceAcquisitionResult(granted=False, reason=f"cas_failed: {err}"),
                    new_ver,
                )
            cur_version = new_ver
        elif norm_state != TaskState.LEASE_PENDING.value:
            return (
                False,
                ResourceAcquisitionResult(
                    granted=False,
                    reason=f"invalid_task_state_for_lease: {node.state}",
                ),
                cur_version,
            )

        # Idempotency token computation (ADR-0006)
        idempotency_key = compute_dispatch_idempotency_key(
            space_id=kernel.space_id,
            plan_version=cur_version,
            task_id=task_id,
            attempt=node.attempt,
        )

        acq = resource_mgr.acquire(
            space_id=kernel.space_id,
            requester_id=task_id,
            identity=identity,
            units=units,
            duration_seconds=duration_seconds,
            priority=priority,
            idempotency_key=idempotency_key,
            scope=scope,
        )

        if acq.granted and acq.lease is not None:
            # Transition to LEASED via CAS
            ok, new_ver, err = kernel.propose_task_transition(
                task_id=task_id,
                to_state=TaskState.LEASED.value,
                expected_plan_version=cur_version,
                from_state=TaskState.LEASE_PENDING.value,
                reason=f"Resource lease granted: {acq.lease.lease_token}",
                result_ref=acq.lease.lease_token,
            )
            if not ok:
                # ROLLBACK PROTECTION:
                # If plan CAS fails due to concurrent plan update or version mismatch,
                # immediately release acquired lease to prevent hardware resource leaks.
                try:
                    resource_mgr.release(
                        space_id=kernel.space_id,
                        requester_id=task_id,
                        lease_token=acq.lease.lease_token,
                    )
                except Exception:
                    pass
                return (
                    False,
                    ResourceAcquisitionResult(
                        granted=False,
                        reason=f"cas_failed_on_leased_transition: {err} (lease released)",
                    ),
                    new_ver,
                )
            return True, acq, new_ver

        if not acq.granted:
            if acq.queue_position is not None:
                # Contested: task stays in LEASE_PENDING waiting for capacity
                return False, acq, cur_version

            # Resource acquisition denied (e.g. overcapacity, unregistered resource)
            reason_str = acq.reason or "resource_acquisition_denied"
            ok, new_ver, _ = kernel.propose_task_transition(
                task_id=task_id,
                to_state=TaskState.FAILED.value,
                expected_plan_version=cur_version,
                from_state=TaskState.LEASE_PENDING.value,
                reason=f"Resource acquisition failed: {reason_str}",
                error=reason_str,
            )
            return False, acq, new_ver

        return False, acq, cur_version

    def release_task_lease(
        self,
        kernel: SpaceKernelAuthorityProtocol,
        resource_mgr: ResourceManager,
        task_id: str,
        lease_token: str,
    ) -> bool:
        """Release a held lease explicitly through ResourceManager."""
        return resource_mgr.release(
            space_id=kernel.space_id,
            requester_id=task_id,
            lease_token=lease_token,
        )

    def coordinate_admission_and_lease(
        self,
        kernel: SpaceKernelAuthorityProtocol,
        resource_mgr: ResourceManager,
        task_id: str,
        resource_identity: ResourceIdentity,
        expected_plan_version: int | None = None,
        units: int = 1,
        duration_seconds: float = 60.0,
        priority: int = 0,
        is_tainted: bool = False,
        approval: Any | None = None,
        budget: float = 0.0,
        timeout: float = 30.0,
        scope: str = "execution",
    ) -> TaskPipelineResult:
        """Execute the coordinated Admission Control + Fractional Lease pipeline (Phase 12.3).

        SCCA Execution Sequence:
            READY -> ADMISSION_PENDING -> ADMITTED -> LEASE_PENDING -> LEASED

        TERMINAL GUARANTEES:
            - If admission fails: terminal state is BLOCKED or FAILED. Leases = 0.
            - If resource contested: terminal state is LEASE_PENDING.
            - If lease denied: terminal state is FAILED.
            - If lease granted and CAS succeeds: terminal state is LEASED.
            - Under NO circumstance does this method invoke workers, tools, or sandboxes.
        """
        # Step 1: Admission Control
        admitted, adm_resp, pver = self.request_task_admission(
            kernel=kernel,
            task_id=task_id,
            expected_plan_version=expected_plan_version,
            is_tainted=is_tainted,
            approval=approval,
            budget=budget,
            timeout=timeout,
        )

        if not admitted:
            graph = kernel.get_task_graph()
            node = graph.get_node(task_id)
            term_state = node.state if node else TaskState.BLOCKED.value
            return TaskPipelineResult(
                task_id=task_id,
                space_id=kernel.space_id,
                plan_version=pver,
                admitted=False,
                leased=False,
                terminal_state=term_state,
                capability_response=adm_resp,
                error=adm_resp.error,
                reason=f"Admission denied: {adm_resp.error}",
            )

        # Step 2: Resource Lease Acquisition
        leased, acq_res, pver2 = self.acquire_task_lease(
            kernel=kernel,
            resource_mgr=resource_mgr,
            task_id=task_id,
            identity=resource_identity,
            expected_plan_version=pver,
            units=units,
            duration_seconds=duration_seconds,
            priority=priority,
            scope=scope,
        )

        if leased and acq_res.lease is not None:
            return TaskPipelineResult(
                task_id=task_id,
                space_id=kernel.space_id,
                plan_version=pver2,
                admitted=True,
                leased=True,
                terminal_state=TaskState.LEASED.value,
                capability_response=adm_resp,
                lease=acq_res.lease,
                lease_token=acq_res.lease.lease_token,
                reason="Task admitted and resource lease granted",
            )

        if acq_res.queue_position is not None:
            return TaskPipelineResult(
                task_id=task_id,
                space_id=kernel.space_id,
                plan_version=pver2,
                admitted=True,
                leased=False,
                terminal_state=TaskState.LEASE_PENDING.value,
                capability_response=adm_resp,
                queue_position=acq_res.queue_position,
                reason=f"Resource contested; queued at position {acq_res.queue_position}",
            )

        # Resource denied or lease acquisition failed
        graph = kernel.get_task_graph()
        node = graph.get_node(task_id)
        term_state = node.state if node else TaskState.FAILED.value
        return TaskPipelineResult(
            task_id=task_id,
            space_id=kernel.space_id,
            plan_version=pver2,
            admitted=True,
            leased=False,
            terminal_state=term_state,
            capability_response=adm_resp,
            error=acq_res.reason,
            reason=f"Resource acquisition failed: {acq_res.reason}",
        )

    def dispatch_task(
        self,
        kernel: SpaceKernelAuthorityProtocol,
        resource_mgr: ResourceManager,
        task_id: str,
        invoker: WorkerInvokerProtocol,
        expected_plan_version: int | None = None,
        is_tainted: bool = False,
        timeout_seconds: float = 30.0,
    ) -> DispatchExecutionResult:
        """Dispatch an admitted and leased task to a capability worker (Phase 12.4).

        SCCA Lifecycle Transitions:
            LEASED -> DISPATCHED -> RUNNING -> OBSERVING (or FAILED / TIMED_OUT / CANCELLED)

        Mandatory Boundaries:
            - Task must be in LEASED state.
            - Lease must be active, valid, and bound to kernel.space_id and task_id.
            - Plan version must match expected_plan_version.
            - Idempotency deduplication: duplicate execution requests are contained.
            - Invoker executes sandboxed worker.
            - All termination paths release the acquired lease in ResourceManager.
        """
        graph = kernel.get_task_graph()
        node = graph.get_node(task_id)
        if node is None:
            raise TaskNotFoundError(
                f"Task '{task_id}' not found in space '{kernel.space_id}'"
            )

        cur_version = kernel.get_plan_version()
        effective_version = expected_plan_version if expected_plan_version is not None else cur_version
        idempotency_key = compute_dispatch_idempotency_key(
            space_id=kernel.space_id,
            plan_version=effective_version,
            task_id=task_id,
            attempt=node.attempt,
        )

        if self.is_attempt_tracked(idempotency_key):
            # Duplicate execution request is contained idempotently (ADR-0041)
            return DispatchExecutionResult(
                task_id=task_id,
                space_id=kernel.space_id,
                plan_version=cur_version,
                status="ok",
                terminal_state=node.state,
                cached=True,
                lease_token=node.result_ref,
                reason="Duplicate dispatch request idempotently deduplicated",
            )

        if expected_plan_version is not None and cur_version != expected_plan_version:
            return DispatchExecutionResult(
                task_id=task_id,
                space_id=kernel.space_id,
                plan_version=cur_version,
                status="rejected",
                terminal_state=node.state,
                error="plan_version_mismatch",
                reason=f"Expected plan version {expected_plan_version}, current {cur_version}",
            )

        norm_state = node.state.lower()
        if norm_state != TaskState.LEASED.value:
            return DispatchExecutionResult(
                task_id=task_id,
                space_id=kernel.space_id,
                plan_version=cur_version,
                status="rejected",
                terminal_state=node.state,
                error="invalid_task_state_for_dispatch",
                reason=f"Task '{task_id}' is in state '{node.state}'; expected 'leased'",
            )

        lease_token = node.result_ref
        if not lease_token:
            return DispatchExecutionResult(
                task_id=task_id,
                space_id=kernel.space_id,
                plan_version=cur_version,
                status="rejected",
                terminal_state=node.state,
                error="missing_lease_token",
                reason="Task is in leased state but has no lease token result_ref",
            )

        # Validate mandatory lease existence and ownership (Step 4)
        lease = resource_mgr.get_lease(lease_token)
        if lease is None:
            kernel.propose_task_transition(
                task_id=task_id,
                to_state=TaskState.FAILED.value,
                expected_plan_version=cur_version,
                from_state=TaskState.LEASED.value,
                reason=f"Execution rejected: lease {lease_token} not found",
                error="lease_not_found",
            )
            return DispatchExecutionResult(
                task_id=task_id,
                space_id=kernel.space_id,
                plan_version=kernel.get_plan_version(),
                status="rejected",
                terminal_state=TaskState.FAILED.value,
                error="lease_not_found",
                reason=f"Lease {lease_token} not found in ResourceManager",
            )

        if lease.space_id != kernel.space_id:
            kernel.propose_task_transition(
                task_id=task_id,
                to_state=TaskState.FAILED.value,
                expected_plan_version=cur_version,
                from_state=TaskState.LEASED.value,
                reason=f"Cross-space lease access rejected: lease in {lease.space_id}, task in {kernel.space_id}",
                error="cross_space_lease",
            )
            return DispatchExecutionResult(
                task_id=task_id,
                space_id=kernel.space_id,
                plan_version=kernel.get_plan_version(),
                status="rejected",
                terminal_state=TaskState.FAILED.value,
                error="cross_space_lease",
                reason=f"Lease belongs to space {lease.space_id}, caller in {kernel.space_id}",
            )

        if lease.requester_id != task_id:
            kernel.propose_task_transition(
                task_id=task_id,
                to_state=TaskState.FAILED.value,
                expected_plan_version=cur_version,
                from_state=TaskState.LEASED.value,
                reason=f"Wrong task lease: lease held by {lease.requester_id}, task is {task_id}",
                error="wrong_task_lease",
            )
            return DispatchExecutionResult(
                task_id=task_id,
                space_id=kernel.space_id,
                plan_version=kernel.get_plan_version(),
                status="rejected",
                terminal_state=TaskState.FAILED.value,
                error="wrong_task_lease",
                reason=f"Lease held by {lease.requester_id}, task is {task_id}",
            )

        now = resource_mgr.clock.now()
        if not lease.is_valid(now):
            err_reason = "lease_expired" if now >= lease.expiry else f"lease_{lease.state.value}"
            kernel.propose_task_transition(
                task_id=task_id,
                to_state=TaskState.FAILED.value,
                expected_plan_version=cur_version,
                from_state=TaskState.LEASED.value,
                reason=f"Execution rejected: lease {lease_token} is invalid ({err_reason})",
                error=err_reason,
            )
            return DispatchExecutionResult(
                task_id=task_id,
                space_id=kernel.space_id,
                plan_version=kernel.get_plan_version(),
                status="rejected",
                terminal_state=TaskState.FAILED.value,
                error=err_reason,
                reason=f"Lease {lease_token} is invalid ({err_reason})",
            )


        self.record_attempt(
            DispatchAttempt(
                idempotency_key=idempotency_key,
                space_id=kernel.space_id,
                plan_version=cur_version,
                task_id=task_id,
                attempt=node.attempt,
                status="dispatched",
            )
        )

        # Transition LEASED -> DISPATCHED via CAS
        ok, new_ver, err = kernel.propose_task_transition(
            task_id=task_id,
            to_state=TaskState.DISPATCHED.value,
            expected_plan_version=cur_version,
            from_state=TaskState.LEASED.value,
            reason="Dispatching task to worker invoker",
        )
        if not ok:
            return DispatchExecutionResult(
                task_id=task_id,
                space_id=kernel.space_id,
                plan_version=new_ver,
                status="cas_failed",
                terminal_state=node.state,
                error=f"cas_failed: {err}",
            )
        cur_version = new_ver

        # Transition DISPATCHED -> RUNNING via CAS
        ok, new_ver, err = kernel.propose_task_transition(
            task_id=task_id,
            to_state=TaskState.RUNNING.value,
            expected_plan_version=cur_version,
            from_state=TaskState.DISPATCHED.value,
            reason="Worker execution started",
        )
        if not ok:
            return DispatchExecutionResult(
                task_id=task_id,
                space_id=kernel.space_id,
                plan_version=new_ver,
                status="cas_failed",
                terminal_state=node.state,
                error=f"cas_failed: {err}",
            )
        cur_version = new_ver

        # Publish task.started pulse (if bus available)
        bus = getattr(kernel, "bus", None)
        if bus is not None:
            bus.publish(
                Pulse(
                    id=f"pulse-start-{kernel.space_id}-{task_id}-{node.attempt}",
                    space_id=kernel.space_id,
                    type="task.started",
                    severity=Severity.INFO,
                    source="dispatcher",
                    timestamp=datetime.now(timezone.utc),
                    payload={
                        "task_id": task_id,
                        "plan_version": cur_version,
                    },
                    taint=is_tainted,
                    correlation_id=f"corr-{kernel.space_id}-{task_id}",
                )
            )

        req_id = f"req-{task_id}-{node.attempt}-{cur_version}"
        task_req = TaskExecutionRequest(
            request_id=req_id,
            space_id=kernel.space_id,
            task_id=task_id,
            plan_id=f"plan-{kernel.space_id}",
            plan_version=cur_version,
            attempt=node.attempt,
            capability=node.capability,
            lease_token=lease_token,
            arguments=dict(node.params),
            idempotency_key=idempotency_key,
            is_tainted=is_tainted,
            timeout_seconds=timeout_seconds,
        )

        try:
            exec_res = invoker.invoke(task_req)
        except Exception as exc:
            exec_res = TaskExecutionResult(
                request_id=req_id,
                status="failed",
                task_id=task_id,
                space_id=kernel.space_id,
                plan_version=cur_version,
                error=str(exc),
                error_class="terminal.invalid_params",
            )

        terminal_status = exec_res.status
        if exec_res.is_success:
            ok, new_ver, _ = kernel.propose_task_transition(
                task_id=task_id,
                to_state=TaskState.OBSERVING.value,
                expected_plan_version=cur_version,
                from_state=TaskState.RUNNING.value,
                reason="Worker execution succeeded; observing artifacts",
                result_ref=exec_res.request_id,
            )
            terminal_state = TaskState.OBSERVING.value
            cur_version = new_ver
        elif exec_res.status == "timeout":
            err_msg = exec_res.error or "Execution timed out"
            ok, new_ver, _ = kernel.propose_task_transition(
                task_id=task_id,
                to_state=TaskState.TIMED_OUT.value,
                expected_plan_version=cur_version,
                from_state=TaskState.RUNNING.value,
                reason=err_msg,
                error=err_msg,
            )
            terminal_state = TaskState.TIMED_OUT.value
            cur_version = new_ver
            if bus is not None:
                bus.publish(
                    Pulse(
                        id=f"pulse-fail-{kernel.space_id}-{task_id}-{cur_version}",
                        space_id=kernel.space_id,
                        type="task.failed",
                        severity=Severity.ERROR,
                        source="dispatcher",
                        timestamp=datetime.now(timezone.utc),
                        payload={
                            "task_id": task_id,
                            "error_class": exec_res.error_class or "transient.timeout",
                            "message": err_msg,
                            "plan_version": cur_version,
                        },
                        taint=exec_res.taint,
                        correlation_id=f"corr-{kernel.space_id}-{task_id}",
                    )
                )
        elif exec_res.status == "cancelled":
            err_msg = exec_res.error or "Execution cancelled"
            ok, new_ver, _ = kernel.propose_task_transition(
                task_id=task_id,
                to_state=TaskState.CANCELLED.value,
                expected_plan_version=cur_version,
                from_state=TaskState.RUNNING.value,
                reason=err_msg,
                error=err_msg,
            )
            terminal_state = TaskState.CANCELLED.value
            cur_version = new_ver
        else:
            err_msg = exec_res.error or f"Worker execution {exec_res.status}"
            ok, new_ver, _ = kernel.propose_task_transition(
                task_id=task_id,
                to_state=TaskState.FAILED.value,
                expected_plan_version=cur_version,
                from_state=TaskState.RUNNING.value,
                reason=err_msg,
                error=err_msg,
            )
            terminal_state = TaskState.FAILED.value
            cur_version = new_ver
            if bus is not None:
                bus.publish(
                    Pulse(
                        id=f"pulse-fail-{kernel.space_id}-{task_id}-{cur_version}",
                        space_id=kernel.space_id,
                        type="task.failed",
                        severity=Severity.ERROR,
                        source="dispatcher",
                        timestamp=datetime.now(timezone.utc),
                        payload={
                            "task_id": task_id,
                            "error_class": exec_res.error_class or "terminal.invalid_params",
                            "message": err_msg,
                            "plan_version": cur_version,
                        },
                        taint=exec_res.taint,
                        correlation_id=f"corr-{kernel.space_id}-{task_id}",
                    )
                )

        try:
            resource_mgr.release(
                space_id=kernel.space_id,
                requester_id=task_id,
                lease_token=lease_token,
            )
        except Exception:
            pass

        return DispatchExecutionResult(
            task_id=task_id,
            space_id=kernel.space_id,
            plan_version=cur_version,
            status=terminal_status,
            terminal_state=terminal_state,
            execution_result=exec_res,
            lease_token=lease_token,
            output_data=exec_res.output_data,
            artifacts=list(exec_res.artifacts),
            taint=exec_res.taint,
            duration_seconds=exec_res.duration_seconds,
            error=exec_res.error,
            reason=f"Dispatch completed: status={terminal_status}, terminal_state={terminal_state}",
        )

    def execute_task_pipeline(
        self,
        kernel: SpaceKernelAuthorityProtocol,
        resource_mgr: ResourceManager,
        task_id: str,
        resource_identity: ResourceIdentity,
        invoker: WorkerInvokerProtocol,
        expected_plan_version: int | None = None,
        units: int = 1,
        duration_seconds: float = 60.0,
        priority: int = 0,
        is_tainted: bool = False,
        approval: Any | None = None,
        budget: float = 0.0,
        timeout: float = 30.0,
        scope: str = "execution",
    ) -> DispatchExecutionResult:
        """Execute end-to-end task execution pipeline from READY through OBSERVING (Phase 12.4).

        Pipeline:
            READY -> ADMISSION_PENDING -> ADMITTED -> LEASE_PENDING -> LEASED -> DISPATCHED -> RUNNING -> OBSERVING
        """
        pipe_res = self.coordinate_admission_and_lease(
            kernel=kernel,
            resource_mgr=resource_mgr,
            task_id=task_id,
            resource_identity=resource_identity,
            expected_plan_version=expected_plan_version,
            units=units,
            duration_seconds=duration_seconds,
            priority=priority,
            is_tainted=is_tainted,
            approval=approval,
            budget=budget,
            timeout=timeout,
            scope=scope,
        )

        if not pipe_res.leased or pipe_res.terminal_state != TaskState.LEASED.value:
            return DispatchExecutionResult(
                task_id=task_id,
                space_id=kernel.space_id,
                plan_version=pipe_res.plan_version,
                status="rejected",
                terminal_state=pipe_res.terminal_state,
                error=pipe_res.error,
                reason=pipe_res.reason,
            )

        return self.dispatch_task(
            kernel=kernel,
            resource_mgr=resource_mgr,
            task_id=task_id,
            invoker=invoker,
            expected_plan_version=pipe_res.plan_version,
            is_tainted=is_tainted,
            timeout_seconds=timeout,
        )
