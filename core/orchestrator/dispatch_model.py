"""Dispatcher protocols, lifecycle contracts, and deterministic scheduling models.

spec §4 (Space Orchestrator), §16 (TaskGraph & Dispatch), DISPATCH-001..005, ADR-0041
Phase 12: Autonomous Plan Execution & Task Dispatch Engine
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from core.orchestrator.execution_state import ExecutionAttemptStore as ExecutionAttemptStoreType
    from core.orchestrator.execution_state import ConvergenceStateStore as ConvergenceStateStoreType
else:
    ExecutionAttemptStoreType = Any
    ConvergenceStateStoreType = Any

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
from core.space.memory_protocol import (
    AdaptationLayerProtocol,
    ExperienceHint,
    ExperienceObserverProtocol,
    TaskExecutionOutcome,
)


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


class EvidenceStatus(str, Enum):
    """Explicit lifecycle status for execution evidence (Phase 12.5)."""

    UNSEEN = "unseen"
    COLLECTED = "collected"
    VERIFIED = "verified"
    INVALID = "invalid"
    TAMPERED = "tampered"
    MISMATCHED = "mismatched"
    MISSING = "missing"
    UNTRUSTED = "untrusted"


class EvidenceType(str, Enum):
    """Standardized classification of task execution evidence (Phase 12.5)."""

    ARTIFACT = "artifact"
    STRUCTURED_OUTPUT = "structured_output"
    SIGNED_TOOL_OUTPUT = "signed_tool_output"
    PROCESS_EXIT = "process_exit"
    TELEMETRY = "telemetry"


@dataclass(frozen=True)
class VerifiedExecutionEvidence:
    """Verified evidence proving capability execution (DISPATCH-004, ADR-0041, Phase 12.5).

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
    evidence_id: str = ""
    status: str = "verified"
    space_id: str = ""
    plan_version: int = 1
    attempt: int = 1
    path: str | None = None
    source: str = "worker"
    tainted: bool = False
    error: str | None = None

    @property
    def is_verified(self) -> bool:
        return self.verified and self.status == EvidenceStatus.VERIFIED.value


@dataclass(frozen=True)
class EvidenceVerificationResult:
    """Aggregated outcome of verifying all evidence for a task execution (Phase 12.5)."""

    task_id: str
    space_id: str
    plan_version: int
    attempt: int
    is_valid: bool
    status: EvidenceStatus
    evidence_items: list[VerifiedExecutionEvidence] = field(default_factory=list)
    failure_reasons: list[str] = field(default_factory=list)
    tainted: bool = False


@dataclass(frozen=True)
class TaskCompletionResult:
    """Outcome of observing, evaluating, and completing a task (Phase 12.5)."""

    task_id: str
    space_id: str
    plan_version: int
    status: str  # "completed" | "failed" | "cas_failed" | "rejected"
    terminal_state: str  # "completed" | "failed" | etc.
    completed: bool
    verification: EvidenceVerificationResult | None = None
    unblocked_tasks: list[str] = field(default_factory=list)
    blocked_tasks: list[str] = field(default_factory=list)
    cached: bool = False
    error: str | None = None
    reason: str = ""


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

    @property
    def exit_code(self) -> int:
        if isinstance(self.details, dict):
            return int(self.details.get("exit_code", 0))
        return 0


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

    def verify_space_identity(self, incoming_space_id: str) -> None: ...

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

    def __init__(
        self,
        attempt_store: "ExecutionAttemptStoreType | None" = None,
        experience_observer: ExperienceObserverProtocol | None = None,
    ) -> None:
        self._tracked_attempts: dict[str, DispatchAttempt] = {}
        self._attempt_store = attempt_store
        self.experience_observer = experience_observer
        # Pre-populate in-memory cache from durable store if provided
        if self._attempt_store is not None:
            self._preload_tracked_attempts()

    def _preload_tracked_attempts(self) -> None:
        """Pre-populate _tracked_attempts from durable store at startup (RECOVERY-001).

        Restores idempotency protection across process restarts. Only loads
        records that are not yet 'recovered' or 'failed' (still relevant).
        """
        # Import here to avoid circular imports; store is protocol-typed
        try:
            from core.orchestrator.execution_state import ExecutionAttemptRecord
            interrupted = self._attempt_store.get_interrupted_attempts(crash_detection_window_seconds=0.0)  # type: ignore[union-attr]
            for record in interrupted:
                attempt = DispatchAttempt(
                    idempotency_key=record.idempotency_key,
                    space_id=record.space_id,
                    plan_version=record.plan_version,
                    task_id=record.task_id,
                    attempt=record.attempt_number,
                    status=record.status,
                )
                self._tracked_attempts[record.idempotency_key] = attempt
        except Exception:
            pass  # Degrade gracefully if store unavailable at startup

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

        Also persists to the durable ExecutionAttemptStore if one was provided
        at construction time, ensuring idempotency protection survives restart (RECOVERY-001).

        Returns:
            True if recorded (first time seen), False if duplicate attempt already exists.
        """
        if attempt.idempotency_key in self._tracked_attempts:
            return False
        self._tracked_attempts[attempt.idempotency_key] = attempt
        # Persist durably if store available
        if self._attempt_store is not None:
            try:
                from core.orchestrator.execution_state import ExecutionAttemptRecord
                record = ExecutionAttemptRecord(
                    attempt_id=attempt.idempotency_key,
                    idempotency_key=attempt.idempotency_key,
                    space_id=attempt.space_id,
                    task_id=attempt.task_id,
                    plan_version=attempt.plan_version,
                    attempt_number=attempt.attempt,
                    capability="",  # filled by caller if needed
                    status=attempt.status,
                    started_at=attempt.created_at,
                )
                self._attempt_store.save_attempt(record)
            except Exception:
                pass  # Store failure must not block dispatch (Law 6 — log externally)
        return True

    def update_attempt_status(
        self,
        idempotency_key: str,
        status: str,
        *,
        failure_class: str | None = None,
        failure_message: str | None = None,
        exit_code: int | None = None,
        artifact_sha256: str | None = None,
    ) -> None:
        """Update the status of a tracked attempt in both memory and durable store (RECOVERY-001)."""
        if idempotency_key in self._tracked_attempts:
            old = self._tracked_attempts[idempotency_key]
            from dataclasses import replace as dc_replace
            self._tracked_attempts[idempotency_key] = dc_replace(old, status=status)
        if self._attempt_store is not None:
            try:
                now = datetime.now(timezone.utc)
                self._attempt_store.update_attempt_status(
                    idempotency_key,
                    status,
                    completed_at=now if status in ("completed", "failed", "recovered") else None,
                    failure_class=failure_class,
                    failure_message=failure_message,
                    exit_code=exit_code,
                    artifact_sha256=artifact_sha256,
                )
            except Exception:
                pass  # Store failure must not block execution (Law 6 — log externally)

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
            exec_res = replace(exec_res, plan_version=new_ver)
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
            exec_res = replace(exec_res, plan_version=new_ver)
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
            exec_res = replace(exec_res, plan_version=new_ver)
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
            exec_res = replace(exec_res, plan_version=new_ver)
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

    @staticmethod
    def _is_safe_artifact_path(
        raw_path: str,
        base_dir: Path | None,
        space_id: str,
    ) -> tuple[bool, Path | None, str | None]:
        """Verify artifact path safety, prohibiting directory traversal or escaping space root.

        Enforces:
        - No parent traversal '..' in any path component
        - Resolves strictly inside base_dir/space_id (or base_dir) if base_dir provided
        """
        clean_path = raw_path.replace("\\", "/").strip()
        parts = clean_path.split("/")
        if ".." in parts:
            return False, None, "Path traversal forbidden ('..' detected)"

        candidate = Path(raw_path)
        if base_dir is not None:
            space_root = (base_dir / space_id).resolve()
            if candidate.is_absolute():
                resolved = candidate.resolve()
            else:
                resolved = (space_root / raw_path).resolve()

            # Ensure resolved path is within space_root or base_dir
            try:
                resolved.relative_to(space_root)
            except ValueError:
                try:
                    resolved.relative_to(base_dir.resolve())
                except ValueError:
                    return False, None, f"Artifact path escapes space sandbox boundary: {resolved}"
            return True, resolved, None
        return True, candidate, None

    @staticmethod
    def _verify_artifact_sha256(
        file_path: Path,
        expected_hash: str | None,
    ) -> tuple[bool, str, str | None]:
        """Verify the cryptographic SHA-256 hash of a physical file."""
        if not file_path.is_file():
            return False, "", f"Artifact file does not exist on disk: {file_path}"
        try:
            hasher = hashlib.sha256()
            with open(file_path, "rb") as f:
                while chunk := f.read(65536):
                    hasher.update(chunk)
            actual_hash = hasher.hexdigest()
            if expected_hash and actual_hash.lower() != expected_hash.lower():
                return (
                    False,
                    actual_hash,
                    f"Artifact SHA-256 mismatch: expected {expected_hash}, got {actual_hash}",
                )
            return True, actual_hash, None
        except Exception as exc:
            return False, "", f"Failed to read artifact file for hashing: {exc}"

    def verify_execution_evidence(
        self,
        space_id: str,
        task_node: TaskNode,
        execution_result: TaskExecutionResult,
        plan_version: int,
        base_dir: Path | None = None,
        replay_mode: bool = False,
    ) -> EvidenceVerificationResult:
        """Deterministically verify all execution evidence collected for a task (Phase 12.5).

        Verifies:
        - Space identity binding (Law 1, SPACE-001)
        - Task identity binding
        - Plan version binding
        - Worker execution status (ok, exit_code 0)
        - Artifact existence, path containment, and SHA-256 integrity
        - Structured output contract validation
        - Telemetry and non-zero duration
        - Taint tracking containment
        """
        evidence_items: list[VerifiedExecutionEvidence] = []
        failure_reasons: list[str] = []
        is_tampered = False
        is_mismatched = False
        is_missing = False

        # 1. Space Identity Binding
        if execution_result.space_id != space_id:
            failure_reasons.append(
                f"Cross-space evidence rejected: result space '{execution_result.space_id}' does not match '{space_id}'"
            )
            is_mismatched = True

        # 2. Task Identity Binding
        if execution_result.task_id != task_node.id:
            failure_reasons.append(
                f"Cross-task evidence rejected: result task '{execution_result.task_id}' does not match '{task_node.id}'"
            )
            is_mismatched = True

        # 3. Plan Version Binding
        if execution_result.plan_version != plan_version:
            failure_reasons.append(
                f"Plan version mismatch: result version {execution_result.plan_version} does not match current plan version {plan_version}"
            )
            is_mismatched = True

        # 4. Execution Attempt Binding
        result_attempt = (
            execution_result.details.get("attempt")
            if isinstance(execution_result.details, dict)
            else None
        )
        if result_attempt is not None and result_attempt != task_node.attempt:
            failure_reasons.append(
                f"Execution attempt mismatch: result attempt {result_attempt} does not match task attempt {task_node.attempt}"
            )
            is_mismatched = True

        # 5. Worker Execution Status
        if not execution_result.is_success:
            err = execution_result.error or f"Worker execution status is '{execution_result.status}'"
            failure_reasons.append(f"Worker execution failed: {err}")

        # 5. Process Exit Status & Telemetry
        exit_code = (
            execution_result.details.get("exit_code", 0)
            if isinstance(execution_result.details, dict)
            else 0
        )
        if exit_code != 0 and execution_result.is_success:
            failure_reasons.append(
                f"Worker reported success but process exit code was non-zero ({exit_code})"
            )

        duration_sec = float(execution_result.duration_seconds)
        if duration_sec < 0.0:
            failure_reasons.append(f"Invalid execution duration ({duration_sec}s)")

        # Record process execution evidence
        evidence_items.append(
            VerifiedExecutionEvidence(
                task_id=task_node.id,
                evidence_type=EvidenceType.PROCESS_EXIT.value,
                verified=(len(failure_reasons) == 0),
                exit_code=exit_code,
                duration_seconds=duration_sec,
                evidence_id=f"ev-proc-{task_node.id}-{execution_result.request_id}",
                status=(
                    EvidenceStatus.VERIFIED.value
                    if not failure_reasons
                    else EvidenceStatus.INVALID.value
                ),
                space_id=space_id,
                plan_version=plan_version,
                attempt=task_node.attempt,
                tainted=execution_result.taint,
            )
        )

        # 6. Structured Output Verification
        required_keys = task_node.params.get("required_output_keys", [])
        if required_keys:
            if not isinstance(execution_result.output_data, dict):
                failure_reasons.append(
                    "Task required structured output dictionary, but output_data is None or non-dict"
                )
                is_missing = True
            else:
                missing_keys = [
                    k for k in required_keys if k not in execution_result.output_data
                ]
                if missing_keys:
                    failure_reasons.append(
                        f"Structured output missing required keys: {missing_keys}"
                    )
                    is_missing = True

        if execution_result.output_data is not None:
            output_dict = (
                execution_result.output_data
                if isinstance(execution_result.output_data, dict)
                else {"data": execution_result.output_data}
            )
            evidence_items.append(
                VerifiedExecutionEvidence(
                    task_id=task_node.id,
                    evidence_type=EvidenceType.STRUCTURED_OUTPUT.value,
                    verified=(len(failure_reasons) == 0),
                    output_payload=output_dict,
                    duration_seconds=duration_sec,
                    evidence_id=f"ev-struct-{task_node.id}-{execution_result.request_id}",
                    status=(
                        EvidenceStatus.VERIFIED.value
                        if not failure_reasons
                        else EvidenceStatus.INVALID.value
                    ),
                    space_id=space_id,
                    plan_version=plan_version,
                    attempt=task_node.attempt,
                    tainted=execution_result.taint,
                )
            )

        # 7. Artifact Verification
        required_artifacts = task_node.params.get("required_artifacts", [])
        raw_artifacts = list(execution_result.artifacts or [])

        # Check required artifacts presence
        if required_artifacts:
            declared_names = set()
            for art in raw_artifacts:
                if isinstance(art, dict):
                    declared_names.add(art.get("name") or art.get("path") or "")
                elif isinstance(art, str):
                    declared_names.add(art)
                elif hasattr(art, "name"):
                    declared_names.add(getattr(art, "name"))
            for req_name in required_artifacts:
                if req_name not in declared_names:
                    failure_reasons.append(
                        f"Required artifact '{req_name}' was not produced by worker"
                    )
                    is_missing = True

        for idx, art in enumerate(raw_artifacts):
            art_dict: dict[str, Any] = {}
            if isinstance(art, dict):
                art_dict = dict(art)
            elif hasattr(art, "to_dict"):
                art_dict = art.to_dict()
            elif hasattr(art, "__dict__"):
                art_dict = dict(art.__dict__)
            elif isinstance(art, str):
                art_dict = {"path": art, "name": art}

            art_space = art_dict.get("space_id")
            if art_space and art_space != space_id:
                failure_reasons.append(
                    f"Cross-space artifact rejected: artifact space '{art_space}' does not match task space '{space_id}'"
                )
                is_mismatched = True

            art_path = art_dict.get("path") or art_dict.get("name") or ""
            expected_sha = art_dict.get("sha256")

            # Check safe path and path containment
            safe, resolved_path, path_err = self._is_safe_artifact_path(
                art_path, base_dir, space_id
            )
            if not safe:
                failure_reasons.append(f"Artifact security violation: {path_err}")
                is_tampered = True
                evidence_items.append(
                    VerifiedExecutionEvidence(
                        task_id=task_node.id,
                        evidence_type=EvidenceType.ARTIFACT.value,
                        verified=False,
                        path=art_path,
                        sha256=expected_sha,
                        evidence_id=f"ev-art-{task_node.id}-{idx}",
                        status=EvidenceStatus.TAMPERED.value,
                        space_id=space_id,
                        plan_version=plan_version,
                        attempt=task_node.attempt,
                        tainted=execution_result.taint,
                        error=path_err,
                    )
                )
                continue

            # In replay mode: verify without physical filesystem operations
            if replay_mode:
                if not expected_sha:
                    failure_reasons.append(
                        f"Artifact '{art_path}' lacks SHA-256 hash in replay mode"
                    )
                    is_missing = True
                evidence_items.append(
                    VerifiedExecutionEvidence(
                        task_id=task_node.id,
                        evidence_type=EvidenceType.ARTIFACT.value,
                        verified=bool(expected_sha),
                        path=str(resolved_path) if resolved_path else art_path,
                        sha256=expected_sha,
                        evidence_id=f"ev-art-{task_node.id}-{idx}",
                        status=(
                            EvidenceStatus.VERIFIED.value
                            if expected_sha
                            else EvidenceStatus.MISSING.value
                        ),
                        space_id=space_id,
                        plan_version=plan_version,
                        attempt=task_node.attempt,
                        tainted=execution_result.taint,
                    )
                )
            else:
                # Live mode: verify physical file and SHA-256
                if resolved_path and resolved_path.is_file():
                    valid_hash, actual_hash, hash_err = self._verify_artifact_sha256(
                        resolved_path, expected_sha
                    )
                    if not valid_hash:
                        failure_reasons.append(f"Artifact integrity failure: {hash_err}")
                        is_tampered = True
                        evidence_items.append(
                            VerifiedExecutionEvidence(
                                task_id=task_node.id,
                                evidence_type=EvidenceType.ARTIFACT.value,
                                verified=False,
                                path=str(resolved_path),
                                sha256=actual_hash,
                                evidence_id=f"ev-art-{task_node.id}-{idx}",
                                status=EvidenceStatus.TAMPERED.value,
                                space_id=space_id,
                                plan_version=plan_version,
                                attempt=task_node.attempt,
                                tainted=execution_result.taint,
                                error=hash_err,
                            )
                        )
                    else:
                        evidence_items.append(
                            VerifiedExecutionEvidence(
                                task_id=task_node.id,
                                evidence_type=EvidenceType.ARTIFACT.value,
                                verified=True,
                                path=str(resolved_path),
                                sha256=actual_hash,
                                evidence_id=f"ev-art-{task_node.id}-{idx}",
                                status=EvidenceStatus.VERIFIED.value,
                                space_id=space_id,
                                plan_version=plan_version,
                                attempt=task_node.attempt,
                                tainted=execution_result.taint,
                            )
                        )
                else:
                    # File does not exist on disk
                    if art_path in required_artifacts or (expected_sha and base_dir is not None):
                        failure_reasons.append(
                            f"Declared artifact not found on disk: {resolved_path or art_path}"
                        )
                        is_missing = True
                    evidence_items.append(
                        VerifiedExecutionEvidence(
                            task_id=task_node.id,
                            evidence_type=EvidenceType.ARTIFACT.value,
                            verified=False,
                            path=str(resolved_path) if resolved_path else art_path,
                            sha256=expected_sha,
                            evidence_id=f"ev-art-{task_node.id}-{idx}",
                            status=EvidenceStatus.MISSING.value,
                            space_id=space_id,
                            plan_version=plan_version,
                            attempt=task_node.attempt,
                            tainted=execution_result.taint,
                            error=f"Artifact not found on disk: {resolved_path or art_path}",
                        )
                    )

        is_valid = len(failure_reasons) == 0
        overall_status = EvidenceStatus.VERIFIED
        if not is_valid:
            if is_tampered:
                overall_status = EvidenceStatus.TAMPERED
            elif is_mismatched:
                overall_status = EvidenceStatus.MISMATCHED
            elif is_missing:
                overall_status = EvidenceStatus.MISSING
            else:
                overall_status = EvidenceStatus.INVALID

        tainted = execution_result.taint or bool(task_node.params.get("is_tainted"))

        return EvidenceVerificationResult(
            task_id=task_node.id,
            space_id=space_id,
            plan_version=plan_version,
            attempt=task_node.attempt,
            is_valid=is_valid,
            status=overall_status,
            evidence_items=evidence_items,
            failure_reasons=failure_reasons,
            tainted=tainted,
        )

    def unblock_dependencies(
        self,
        kernel: SpaceKernelAuthorityProtocol,
        completed_task_id: str,
        expected_plan_version: int | None = None,
        max_retries: int = 3,
    ) -> tuple[bool, int, list[str], list[str], str | None]:
        """Inspect the TaskGraph and transition eligible dependent tasks to READY via PlanDelta (Phase 12.5).

        Invariants:
        - A dependent task enters READY if and only if ALL its upstream dependencies are COMPLETED
          (or if an optional dependency is completed/failed/cancelled).
        - Direct TaskGraph mutation is forbidden; all transitions commit atomically via SpaceKernel CAS.
        """
        for retry in range(max_retries):
            cur_version = kernel.get_plan_version()
            if expected_plan_version is not None and retry == 0:
                cur_version = expected_plan_version

            graph = kernel.get_task_graph(cur_version)
            unblock_ops: list[dict[str, Any]] = []
            unblocked_ids: list[str] = []

            for node in graph.nodes:
                if completed_task_id not in node.dependencies:
                    continue
                if node.state not in ("pending", "blocked"):
                    continue

                all_satisfied = True
                for dep_id in node.dependencies:
                    parent = graph.get_node(dep_id)
                    if parent is None:
                        all_satisfied = False
                        break
                    if parent.state == TaskState.COMPLETED.value:
                        continue
                    if parent.optional and parent.state in (
                        TaskState.COMPLETED.value,
                        TaskState.FAILED.value,
                        TaskState.CANCELLED.value,
                    ):
                        continue
                    all_satisfied = False
                    break

                if all_satisfied:
                    unblock_ops.append(
                        {
                            "op": "transition",
                            "target_node_id": node.id,
                            "to_state": TaskState.READY.value,
                            "from_state": node.state,
                            "reason": f"Prerequisites completed (unblocked by '{completed_task_id}')",
                        }
                    )
                    unblocked_ids.append(node.id)

            if not unblock_ops:
                return True, cur_version, [], [], None

            delta = PlanDelta(
                space_id=kernel.space_id,
                base_version=cur_version,
                resulting_version=cur_version + 1,
                ops=unblock_ops,
            )
            ok, new_ver, winning_id = kernel.commit_plan_delta(
                delta, proposal_id=f"unblock-{completed_task_id}-{retry}"
            )
            if ok:
                return True, new_ver, unblocked_ids, [], None

        return (
            False,
            kernel.get_plan_version(),
            [],
            [],
            "CAS conflict retries exhausted during dependency unblocking",
        )

    def handle_failed_dependencies(
        self,
        kernel: SpaceKernelAuthorityProtocol,
        failed_task_id: str,
        expected_plan_version: int | None = None,
        max_retries: int = 3,
    ) -> tuple[bool, int, list[str], str | None]:
        """Inspect the TaskGraph and transition affected dependent tasks to BLOCKED via PlanDelta (Phase 12.5).

        Invariants:
        - Downstream tasks requiring a failed non-optional dependency cannot become READY.
        - Mandatory downstream tasks transition PENDING -> BLOCKED atomically via SpaceKernel CAS.
        - SCCA Law 6: failures are contained and surfaced upward.
        """
        for retry in range(max_retries):
            cur_version = kernel.get_plan_version()
            if expected_plan_version is not None and retry == 0:
                cur_version = expected_plan_version

            graph = kernel.get_task_graph(cur_version)
            failed_node = graph.get_node(failed_task_id)
            if failed_node is not None and failed_node.optional:
                # If the failed task itself was optional, its failure does not block downstream tasks
                return True, cur_version, [], None

            block_ops: list[dict[str, Any]] = []
            blocked_ids: list[str] = []

            for node in graph.nodes:
                if failed_task_id not in node.dependencies:
                    continue
                if node.optional:
                    # Optional dependent node is not blocked
                    continue
                if node.state in ("pending", "ready"):
                    block_ops.append(
                        {
                            "op": "transition",
                            "target_node_id": node.id,
                            "to_state": TaskState.BLOCKED.value,
                            "from_state": node.state,
                            "reason": f"Upstream mandatory dependency '{failed_task_id}' failed",
                            "error": f"Upstream mandatory dependency '{failed_task_id}' failed",
                        }
                    )
                    blocked_ids.append(node.id)

            if not block_ops:
                return True, cur_version, [], None

            delta = PlanDelta(
                space_id=kernel.space_id,
                base_version=cur_version,
                resulting_version=cur_version + 1,
                ops=block_ops,
            )
            ok, new_ver, winning_id = kernel.commit_plan_delta(
                delta, proposal_id=f"block-{failed_task_id}-{retry}"
            )
            if ok:
                return True, new_ver, blocked_ids, None

        return (
            False,
            kernel.get_plan_version(),
            [],
            "CAS conflict retries exhausted during dependency blocking",
        )

    def observe_and_evaluate_task(
        self,
        kernel: SpaceKernelAuthorityProtocol,
        task_id: str,
        execution_result: TaskExecutionResult,
        expected_plan_version: int | None = None,
        base_dir: Path | None = None,
        replay_mode: bool = False,
        max_retries: int = 3,
    ) -> TaskCompletionResult:
        """Observe, verify, and complete a task with atomic CAS transitions and dependency unblocking (Phase 12.5).

        Lifecycle:
            OBSERVING -> EVALUATING -> COMPLETED (or FAILED)
                      -> Unblock downstream READY tasks (or block on failure)
        """
        kernel.verify_space_identity(kernel.space_id)

        for retry in range(max_retries):
            cur_version = kernel.get_plan_version()
            if expected_plan_version is not None and retry == 0:
                cur_version = expected_plan_version

            graph = kernel.get_task_graph(cur_version)
            node = graph.get_node(task_id)
            if node is None:
                return TaskCompletionResult(
                    task_id=task_id,
                    space_id=kernel.space_id,
                    plan_version=cur_version,
                    status="rejected",
                    terminal_state="unknown",
                    completed=False,
                    error=f"Task '{task_id}' not found in plan",
                )

            # Idempotency check: if already terminal
            if node.state == TaskState.COMPLETED.value:
                return TaskCompletionResult(
                    task_id=task_id,
                    space_id=kernel.space_id,
                    plan_version=cur_version,
                    status="completed",
                    terminal_state=TaskState.COMPLETED.value,
                    completed=True,
                    cached=True,
                    reason=f"Task '{task_id}' is already in completed state",
                )
            if node.state == TaskState.FAILED.value:
                return TaskCompletionResult(
                    task_id=task_id,
                    space_id=kernel.space_id,
                    plan_version=cur_version,
                    status="failed",
                    terminal_state=TaskState.FAILED.value,
                    completed=False,
                    cached=True,
                    error=node.error or "Task already in failed state",
                    reason=f"Task '{task_id}' is already in failed state",
                )

            observed_version = cur_version

            # If node is in RUNNING, transition to OBSERVING first
            if node.state == TaskState.RUNNING.value:
                ok, cur_version, err = kernel.propose_task_transition(
                    task_id=task_id,
                    to_state=TaskState.OBSERVING.value,
                    expected_plan_version=cur_version,
                    from_state=TaskState.RUNNING.value,
                    reason="Transitioning to observing for evidence collection",
                    result_ref=execution_result.request_id,
                )
                if not ok:
                    continue  # Retry CAS loop
                observed_version = cur_version

            # Transition OBSERVING -> EVALUATING
            if node.state in (TaskState.OBSERVING.value, TaskState.RUNNING.value):
                ok, cur_version, err = kernel.propose_task_transition(
                    task_id=task_id,
                    to_state=TaskState.EVALUATING.value,
                    expected_plan_version=cur_version,
                    from_state=TaskState.OBSERVING.value,
                    reason="Evaluating task execution evidence",
                )
                if not ok:
                    continue  # Retry CAS loop
            elif node.state != TaskState.EVALUATING.value:
                return TaskCompletionResult(
                    task_id=task_id,
                    space_id=kernel.space_id,
                    plan_version=cur_version,
                    status="rejected",
                    terminal_state=node.state,
                    completed=False,
                    error=f"Task '{task_id}' is in state '{node.state}', expected 'observing' or 'evaluating'",
                )

            # Now node is in EVALUATING
            # Verify execution evidence against the plan version under which the task executed
            ver_res = self.verify_execution_evidence(
                space_id=kernel.space_id,
                task_node=node,
                execution_result=execution_result,
                plan_version=observed_version,
                base_dir=base_dir,
                replay_mode=replay_mode,
            )

            bus = getattr(kernel, "bus", None)

            if ver_res.is_valid:
                # Transition EVALUATING -> COMPLETED
                ok, cur_version, err = kernel.propose_task_transition(
                    task_id=task_id,
                    to_state=TaskState.COMPLETED.value,
                    expected_plan_version=cur_version,
                    from_state=TaskState.EVALUATING.value,
                    reason="Execution evidence verified",
                    result_ref=execution_result.request_id,
                )
                if not ok:
                    continue  # Retry CAS loop

                # Publish task.completed pulse
                if bus is not None:
                    bus.publish(
                        Pulse(
                            id=f"pulse-complete-{kernel.space_id}-{task_id}-{cur_version}",
                            space_id=kernel.space_id,
                            type="task.completed",
                            severity=Severity.INFO,
                            source="dispatcher",
                            timestamp=datetime.now(timezone.utc),
                            payload={
                                "task_id": task_id,
                                "result_ref": execution_result.request_id,
                                "plan_version": cur_version,
                            },
                            taint=ver_res.tainted,
                            correlation_id=f"corr-{kernel.space_id}-{task_id}",
                        )
                    )

                # Unblock downstream dependencies via CAS
                unblock_ok, cur_version, unblocked, _, err = self.unblock_dependencies(
                    kernel=kernel,
                    completed_task_id=task_id,
                    expected_plan_version=cur_version,
                )

                # Report verified outcome to experience observer (ADAPT-001)
                if self.experience_observer is not None and not replay_mode:
                    try:
                        self.experience_observer.observe_task_outcome(
                            TaskExecutionOutcome(
                                task_id=task_id,
                                space_id=kernel.space_id,
                                plan_version=cur_version,
                                capability=node.capability,
                                params=dict(node.params),
                                status="completed",
                                exit_code=execution_result.exit_code,
                                duration_seconds=execution_result.duration_seconds,
                                result_ref=execution_result.request_id,
                                dependencies=tuple(node.dependencies),
                                taint=ver_res.tainted,
                                completed_at=datetime.now(timezone.utc),
                            )
                        )
                    except Exception:
                        pass  # Experience observation failure must never fail the task

                return TaskCompletionResult(
                    task_id=task_id,
                    space_id=kernel.space_id,
                    plan_version=cur_version,
                    status="completed",
                    terminal_state=TaskState.COMPLETED.value,
                    completed=True,
                    verification=ver_res,
                    unblocked_tasks=unblocked,
                    reason="Task evidence verified; completed and dependencies unblocked",
                )
            else:
                # Transition EVALUATING -> FAILED
                fail_msg = (
                    "; ".join(ver_res.failure_reasons)
                    or "Execution evidence verification failed"
                )
                ok, cur_version, err = kernel.propose_task_transition(
                    task_id=task_id,
                    to_state=TaskState.FAILED.value,
                    expected_plan_version=cur_version,
                    from_state=TaskState.EVALUATING.value,
                    reason=fail_msg,
                    error=fail_msg,
                )
                if not ok:
                    continue  # Retry CAS loop

                # Publish task.failed pulse
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
                                "error_class": "terminal.evidence_verification_failed",
                                "message": fail_msg,
                                "plan_version": cur_version,
                            },
                            taint=ver_res.tainted,
                            correlation_id=f"corr-{kernel.space_id}-{task_id}",
                        )
                    )

                # Block downstream dependencies via CAS
                block_ok, cur_version, blocked, err = self.handle_failed_dependencies(
                    kernel=kernel,
                    failed_task_id=task_id,
                    expected_plan_version=cur_version,
                )

                # Report failure outcome to experience observer (ADAPT-001)
                if self.experience_observer is not None and not replay_mode:
                    try:
                        self.experience_observer.observe_task_outcome(
                            TaskExecutionOutcome(
                                task_id=task_id,
                                space_id=kernel.space_id,
                                plan_version=cur_version,
                                capability=node.capability,
                                params=dict(node.params),
                                status="failed",
                                exit_code=execution_result.exit_code,
                                duration_seconds=execution_result.duration_seconds,
                                error_class="terminal.evidence_verification_failed",
                                error_message=fail_msg,
                                dependencies=tuple(node.dependencies),
                                taint=ver_res.tainted,
                                completed_at=datetime.now(timezone.utc),
                            )
                        )
                    except Exception:
                        pass  # Experience observation failure must never fail the task

                return TaskCompletionResult(
                    task_id=task_id,
                    space_id=kernel.space_id,
                    plan_version=cur_version,
                    status="failed",
                    terminal_state=TaskState.FAILED.value,
                    completed=False,
                    verification=ver_res,
                    blocked_tasks=blocked,
                    error=fail_msg,
                    reason=f"Evidence verification failed: {fail_msg}",
                )

        return TaskCompletionResult(
            task_id=task_id,
            space_id=kernel.space_id,
            plan_version=kernel.get_plan_version(),
            status="cas_failed",
            terminal_state="unknown",
            completed=False,
            error="CAS conflict retries exhausted during task observation and evaluation",
        )

    def execute_task_full_pipeline(
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
        base_dir: Path | None = None,
        replay_mode: bool = False,
    ) -> TaskCompletionResult:
        """Execute full end-to-end task execution pipeline from READY through COMPLETED & DAG unblocking (Phase 12.5).

        Pipeline:
            READY -> ADMISSION_PENDING -> ADMITTED -> LEASE_PENDING -> LEASED
                  -> DISPATCHED -> RUNNING -> OBSERVING
                  -> EVALUATING -> COMPLETED / FAILED
                  -> Unblock / Block DAG dependencies
        """
        dispatch_res = self.execute_task_pipeline(
            kernel=kernel,
            resource_mgr=resource_mgr,
            task_id=task_id,
            resource_identity=resource_identity,
            invoker=invoker,
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

        if (
            dispatch_res.terminal_state != TaskState.OBSERVING.value
            or dispatch_res.execution_result is None
        ):
            return TaskCompletionResult(
                task_id=task_id,
                space_id=kernel.space_id,
                plan_version=dispatch_res.plan_version,
                status=dispatch_res.status,
                terminal_state=dispatch_res.terminal_state,
                completed=False,
                error=dispatch_res.error,
                reason=dispatch_res.reason,
            )

        return self.observe_and_evaluate_task(
            kernel=kernel,
            task_id=task_id,
            execution_result=dispatch_res.execution_result,
            expected_plan_version=dispatch_res.plan_version,
            base_dir=base_dir,
            replay_mode=replay_mode,
        )


# ---------------------------------------------------------------------------
# Phase 12.6 — Convergence Engine / Plan Reconciliation
# ---------------------------------------------------------------------------


class GoalEvaluationStatus(str, Enum):
    """Formal verdict of goal evaluation against plan evidence (Phase 12.6, spec §10.1)."""

    SATISFIED = "satisfied"
    UNSATISFIED = "unsatisfied"
    INCONCLUSIVE = "inconclusive"


@dataclass(frozen=True)
class GoalEvaluationResult:
    """Strongly-typed, immutable evaluation verdict (Phase 12.6, spec §10.1).

    Invariants:
    - status is always one of GoalEvaluationStatus (SATISFIED | UNSATISFIED | INCONCLUSIVE).
    - confidence is in [0.0, 1.0]; 0.0 means 'no signal', 1.0 means 'certain'.
    - missing_criteria is an exhaustive list of unmet criteria; empty when status=SATISFIED.
    - GoalEvaluationResult carries ZERO plan mutation authority.
    """

    status: GoalEvaluationStatus
    confidence: float
    reasoning: str
    missing_criteria: list[str] = field(default_factory=list)
    evaluated_tasks: int = 0
    satisfied_tasks: int = 0
    evidence_count: int = 0

    def __post_init__(self) -> None:
        if not (0.0 <= self.confidence <= 1.0):
            raise ValueError(
                f"GoalEvaluationResult.confidence must be in [0.0, 1.0]; got {self.confidence}"
            )

    @property
    def is_satisfied(self) -> bool:
        return self.status == GoalEvaluationStatus.SATISFIED


class DeterministicGoalEvaluator:
    """LLM-free, evidence-bound goal evaluator (Phase 12.6, spec §10.2).

    Evaluates plan completion by examining task terminal states, artifact SHA-256
    presence, structured output fields, exit codes, and telemetry — all without
    calling an external LLM or mutating any authoritative state.

    Invariants:
    - Zero plan mutation authority: evaluates only; writes nothing.
    - No imports from workers/, agents/, llm/, memory/, channels/ (AGENTS.md §7).
    - Deterministic: same inputs always produce identical GoalEvaluationResult.
    - Replay-safe: can be called during REPLAY_MODE without side effects.
    """

    def evaluate(
        self,
        goal_spec: Any,
        evidence: list[VerifiedExecutionEvidence],
    ) -> GoalEvaluationResult:
        """Evaluate whether the provided evidence satisfies goal_spec.

        Args:
            goal_spec: Any object with 'objective', 'constraints', 'required_capabilities'
                       attributes or dict keys. Uses duck-typing for provider independence.
            evidence:  list of VerifiedExecutionEvidence collected during task execution.

        Returns:
            GoalEvaluationResult with SATISFIED / UNSATISFIED / INCONCLUSIVE verdict.
        """
        if not evidence:
            return GoalEvaluationResult(
                status=GoalEvaluationStatus.INCONCLUSIVE,
                confidence=0.0,
                reasoning="No execution evidence collected; cannot evaluate goal satisfaction.",
                missing_criteria=["execution_evidence"],
                evaluated_tasks=0,
                satisfied_tasks=0,
                evidence_count=0,
            )

        # Collect constraints from goal_spec (duck-type safe)
        constraints: list[str] = []
        if hasattr(goal_spec, "constraints"):
            constraints = list(goal_spec.constraints or [])
        elif isinstance(goal_spec, dict):
            constraints = list(goal_spec.get("constraints") or [])

        # Evaluate evidence items
        verified_count = sum(1 for e in evidence if e.verified and e.status == EvidenceStatus.VERIFIED.value)
        tainted_count = sum(1 for e in evidence if e.tainted)
        failed_count = sum(1 for e in evidence if not e.verified)
        total = len(evidence)

        missing: list[str] = []
        failure_reasons: list[str] = []

        # Rule 1: All evidence must be verified (no TAMPERED, INVALID, MISSING items)
        if failed_count > 0:
            missing.append("all_evidence_verified")
            failure_reasons.append(
                f"{failed_count}/{total} evidence item(s) failed verification"
            )

        # Rule 2: No tainted evidence (unless constraints explicitly allow it)
        allow_taint = "allow_taint" in constraints
        if tainted_count > 0 and not allow_taint:
            missing.append("no_tainted_evidence")
            failure_reasons.append(
                f"{tainted_count} tainted evidence item(s) detected"
            )

        # Rule 3: At least one artifact-type evidence with SHA-256 or exit code 0
        has_artifact = any(
            e.evidence_type in ("artifact", EvidenceType.ARTIFACT.value) and e.sha256
            for e in evidence
        )
        has_process_exit = any(
            e.exit_code == 0 and e.duration_seconds > 0.0
            for e in evidence
        )
        has_structured = any(
            e.evidence_type in ("structured_output", EvidenceType.STRUCTURED_OUTPUT.value)
            and e.output_payload
            for e in evidence
        )

        if not (has_artifact or has_process_exit or has_structured):
            missing.append("substantive_execution_evidence")
            failure_reasons.append(
                "No artifact SHA-256, successful process exit, or structured output found"
            )

        # Rule 4: Constraint violations check (deterministic keyword scan)
        for constraint in constraints:
            if constraint.startswith("require_artifact:"):
                required_path = constraint.split(":", 1)[1].strip()
                found = any(
                    (e.path or "").endswith(required_path) and e.verified
                    for e in evidence
                )
                if not found:
                    missing.append(f"required_artifact:{required_path}")
                    failure_reasons.append(
                        f"Required artifact '{required_path}' not found in evidence"
                    )
            elif constraint.startswith("require_exit_code:"):
                try:
                    required_code = int(constraint.split(":", 1)[1].strip())
                    found = any(e.exit_code == required_code for e in evidence)
                    if not found:
                        missing.append(f"required_exit_code:{required_code}")
                        failure_reasons.append(
                            f"Required exit code {required_code} not found in evidence"
                        )
                except (ValueError, IndexError):
                    pass

        if missing:
            confidence = max(0.0, (verified_count / total) * 0.5) if total else 0.0
            return GoalEvaluationResult(
                status=GoalEvaluationStatus.UNSATISFIED,
                confidence=round(confidence, 4),
                reasoning=(
                    f"Goal UNSATISFIED: {'; '.join(failure_reasons)}. "
                    f"Verified {verified_count}/{total} evidence items."
                ),
                missing_criteria=missing,
                evaluated_tasks=total,
                satisfied_tasks=verified_count,
                evidence_count=total,
            )

        confidence = min(1.0, verified_count / total) if total else 0.0
        return GoalEvaluationResult(
            status=GoalEvaluationStatus.SATISFIED,
            confidence=round(confidence, 4),
            reasoning=(
                f"Goal SATISFIED: All {verified_count}/{total} evidence items verified. "
                f"No tainted items, constraints satisfied."
            ),
            missing_criteria=[],
            evaluated_tasks=total,
            satisfied_tasks=verified_count,
            evidence_count=total,
        )

    def evaluate_from_task_graph(
        self,
        goal_spec: Any,
        task_graph: TaskGraph,
        evidence: list[VerifiedExecutionEvidence],
    ) -> GoalEvaluationResult:
        """Evaluate goal satisfaction from both TaskGraph state and collected evidence (Phase 12.6).

        This dual-source evaluation checks task terminal states in addition to
        artifact-level evidence, providing a higher-confidence verdict.
        """
        # All non-optional tasks must be COMPLETED
        incomplete: list[str] = []
        failed_tasks: list[str] = []
        for node in task_graph.nodes:
            if node.state == TaskState.COMPLETED.value:
                continue
            if node.optional and node.state in (
                TaskState.FAILED.value,
                TaskState.CANCELLED.value,
            ):
                continue
            if node.state in (TaskState.FAILED.value, TaskState.ESCALATED.value):
                failed_tasks.append(node.id)
            else:
                incomplete.append(node.id)

        if failed_tasks:
            return GoalEvaluationResult(
                status=GoalEvaluationStatus.UNSATISFIED,
                confidence=0.0,
                reasoning=f"Goal UNSATISFIED: tasks in terminal FAILED state: {failed_tasks}",
                missing_criteria=[f"task:{tid}" for tid in failed_tasks],
                evaluated_tasks=len(task_graph.nodes),
                satisfied_tasks=sum(
                    1 for n in task_graph.nodes if n.state == TaskState.COMPLETED.value
                ),
                evidence_count=len(evidence),
            )

        if incomplete:
            return GoalEvaluationResult(
                status=GoalEvaluationStatus.INCONCLUSIVE,
                confidence=0.0,
                reasoning=f"Goal INCONCLUSIVE: tasks still in-flight: {incomplete}",
                missing_criteria=[f"task:{tid}" for tid in incomplete],
                evaluated_tasks=len(task_graph.nodes),
                satisfied_tasks=sum(
                    1 for n in task_graph.nodes if n.state == TaskState.COMPLETED.value
                ),
                evidence_count=len(evidence),
            )

        # All tasks complete — defer to evidence-level evaluation
        return self.evaluate(goal_spec, evidence)


class ConvergenceDecision(str, Enum):
    """Deterministic convergence action produced by ConvergenceEngine (Phase 12.6, spec §11.1)."""

    CONTINUE = "continue"    # Next ready tasks in the DAG are scheduled.
    RETRY = "retry"          # Transient failure; task retried under bounded backoff (≤ 3).
    REPLAN = "replan"        # Structural failure; PlanReconciler proposes a PlanDelta.
    ESCALATE = "escalate"    # Budgets exhausted; human intervention requested.
    ABORT = "abort"          # Terminal unrecoverable violation; Space enters failure.


@dataclass(frozen=True)
class ConvergenceProposal:
    """Strongly-typed, immutable convergence proposal (Phase 12.6, spec §11.1).

    The ConvergenceEngine produces a ConvergenceProposal; it never directly mutates
    the plan. All plan changes must flow: Proposal → PlanDelta → SpaceKernel CAS.

    Invariants:
    - Immutable after creation (frozen=True).
    - decision is always a ConvergenceDecision enum member.
    - task_id may be None for plan-level decisions (ABORT, ESCALATE).
    - retry_attempt is bounded: 0 <= retry_attempt <= MAX_RETRY_BUDGET (3).
    - replan_attempt is bounded: 0 <= replan_attempt <= MAX_REPLAN_BUDGET (3).
    - plan_delta is only populated for REPLAN decisions.
    - plan_delta MUST be committed via SpaceKernel.commit_plan_delta(); ConvergenceEngine
      has zero authority to apply it directly.
    """

    decision: ConvergenceDecision
    space_id: str
    plan_version: int
    reasoning: str
    task_id: str | None = None
    retry_attempt: int = 0
    replan_attempt: int = 0
    plan_delta: PlanDelta | None = None
    evaluation: GoalEvaluationResult | None = None
    escalation_reason: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    # Failure fingerprint for loop-detection (SHA-256 of space+task+error)
    failure_fingerprint: str = ""

    # Phase 13: Experiential adaptation provenance & advisory hints (ADAPT-003, ADAPT-004)
    adaptation_hints: tuple[ExperienceHint, ...] = field(default_factory=tuple)
    counterfactual_recommendation: str = ""
    source_experience_id: str = ""


def _compute_failure_fingerprint(space_id: str, task_id: str, error: str) -> str:
    """Deterministic SHA-256 failure fingerprint for loop-detection (Phase 12.6)."""
    raw = f"{space_id}:{task_id}:{error}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


class ConvergenceEngine:
    """Closes the autonomous execution loop by evaluating plan state and proposing convergence actions.

    Phase 12.6 — spec §11, ADR-0041 §2B

    Authority Boundaries (AGENTS.md §6):
    - ConvergenceEngine CANNOT directly mutate plans, spaces, leases, approvals, or secrets.
    - GoalEvaluator CANNOT mutate PlanStore or TaskGraph.
    - LLM output (when injected via GoalEvaluatorProtocol) is a proposal input only, never authority.
    - Only authoritative path: Proposal → PlanDelta → SpaceKernel.commit_plan_delta() → PlanStore CAS.

    Invariants:
    - MAX_RETRY_BUDGET = 3: task retries; escalates after 3 transient failures.
    - MAX_REPLAN_BUDGET = 3: plan replans; escalates after 3 structural failures.
    - Failure fingerprinting prevents the same failure from triggering unbounded distinct replans.
    - REPLAY_MODE: produces identical proposals from identical pulse sequences; no side effects.
    - LLM cannot override convergence budgets regardless of output content.
    """

    MAX_RETRY_BUDGET = 3
    MAX_REPLAN_BUDGET = 3

    def __init__(
        self,
        space_id: str,
        reconciler: Any,  # PlanReconciler — protocol-typed to avoid tight coupling
        goal_evaluator: GoalEvaluatorProtocol | None = None,
        replay_mode: bool = False,
        state_store: "ConvergenceStateStoreType | None" = None,
        adaptation_layer: AdaptationLayerProtocol | None = None,
    ) -> None:
        self.space_id = space_id
        self.reconciler = reconciler
        self.goal_evaluator = goal_evaluator or DeterministicGoalEvaluator()
        self.replay_mode = replay_mode
        self._state_store = state_store
        self.adaptation_layer = adaptation_layer
        # Bounded counters — keyed per task_id (in-memory cache; backed by durable store if provided)
        self._retry_counts: dict[str, int] = {}
        self._replan_counts: dict[str, int] = {}
        # Failure fingerprint registry for loop detection
        self._seen_fingerprints: set[str] = set()

    # ── Durable state helpers (RECOVERY-002, RECOVERY-003) ───────────────────

    def _get_retry_count(self, task_id: str) -> int:
        """Return retry count, loading from durable store if not yet cached."""
        if task_id not in self._retry_counts:
            if self._state_store is not None:
                try:
                    rec = self._state_store.load_state(self.space_id, task_id)
                    self._retry_counts[task_id] = rec.retry_count
                    self._replan_counts[task_id] = rec.replan_count
                    for fp in rec.failure_fingerprints:
                        self._seen_fingerprints.add(fp)
                except Exception:
                    self._retry_counts[task_id] = 0
            else:
                self._retry_counts[task_id] = 0
        return self._retry_counts[task_id]

    def _increment_retry(self, task_id: str, failure_class: str = "") -> int:
        """Increment retry counter and persist to durable store (RECOVERY-002)."""
        # Load first if not cached
        current = self._get_retry_count(task_id)
        new_count = current + 1
        self._retry_counts[task_id] = new_count
        if self._state_store is not None:
            try:
                self._state_store.increment_retry(
                    self.space_id, task_id, failure_class or None
                )
            except Exception:
                pass  # Degrade gracefully; in-memory count is still correct
        return new_count

    def _get_replan_count(self, task_id: str) -> int:
        """Return replan count, loading from durable store if not yet cached."""
        if task_id not in self._replan_counts:
            # Trigger load via _get_retry_count (loads all at once)
            self._get_retry_count(task_id)
            self._replan_counts.setdefault(task_id, 0)
        return self._replan_counts[task_id]

    def _increment_replan(self, task_id: str) -> int:
        """Increment replan counter and persist to durable store (RECOVERY-002)."""
        current = self._get_replan_count(task_id)
        new_count = current + 1
        self._replan_counts[task_id] = new_count
        if self._state_store is not None:
            try:
                self._state_store.increment_replan(self.space_id, task_id)
            except Exception:
                pass
        return new_count

    def _add_fingerprint(self, task_id: str, fingerprint: str) -> None:
        """Record failure fingerprint in memory and durable store (RECOVERY-003)."""
        self._seen_fingerprints.add(fingerprint)
        if self._state_store is not None:
            try:
                self._state_store.add_fingerprint(self.space_id, task_id, fingerprint)
            except Exception:
                pass

    def _has_fingerprint(self, fingerprint: str) -> bool:
        """Check if fingerprint was seen (in-memory; preloaded from store at first access)."""
        return fingerprint in self._seen_fingerprints

    def evaluate_and_propose(
        self,
        kernel: SpaceKernelAuthorityProtocol,
        goal_spec: Any,
        evidence: list[VerifiedExecutionEvidence],
        failed_task_id: str | None = None,
        error_class: str = "",
        error_message: str = "",
    ) -> ConvergenceProposal:
        """Evaluate plan state and produce a deterministic ConvergenceProposal.

        This is the primary entry point. The caller (SpaceOrchestrator or runtime loop)
        is responsible for acting on the proposal via SpaceKernel CAS.

        Args:
            kernel:          SpaceKernel authority (read-only query; no mutations).
            goal_spec:       Human goal specification (GoalSpec or compatible duck-type).
            evidence:        Collected VerifiedExecutionEvidence from completed tasks.
            failed_task_id:  Task that just failed, if any (None for non-failure calls).
            error_class:     Failure taxonomy class (e.g. "transient.timeout").
            error_message:   Human-readable failure message.

        Returns:
            ConvergenceProposal with decision + optional plan_delta for REPLAN.
        """
        kernel.verify_space_identity(self.space_id)
        plan_version = kernel.get_plan_version()
        task_graph = kernel.get_task_graph()

        # ── 1. Failure path ──────────────────────────────────────────────────
        if failed_task_id is not None:
            return self._handle_failure(
                kernel=kernel,
                plan_version=plan_version,
                task_graph=task_graph,
                failed_task_id=failed_task_id,
                error_class=error_class,
                error_message=error_message,
            )

        # ── 2. Non-failure: evaluate goal satisfaction ───────────────────────
        eval_result = self.goal_evaluator.evaluate(goal_spec, evidence)

        if eval_result.status == GoalEvaluationStatus.SATISFIED:
            return ConvergenceProposal(
                decision=ConvergenceDecision.CONTINUE,
                space_id=self.space_id,
                plan_version=plan_version,
                reasoning=f"Goal SATISFIED with confidence {eval_result.confidence:.2%}. "
                          f"Plan convergence complete.",
                evaluation=eval_result,
            )

        if eval_result.status == GoalEvaluationStatus.INCONCLUSIVE:
            # Still tasks in-flight; continue execution
            return ConvergenceProposal(
                decision=ConvergenceDecision.CONTINUE,
                space_id=self.space_id,
                plan_version=plan_version,
                reasoning="Goal evaluation inconclusive; continuing task execution.",
                evaluation=eval_result,
            )

        # UNSATISFIED — check if any tasks are still running or ready
        active_states = {TaskState.READY.value, TaskState.RUNNING.value,
                         TaskState.DISPATCHED.value, TaskState.OBSERVING.value,
                         TaskState.EVALUATING.value}
        still_running = [n for n in task_graph.nodes if n.state in active_states]
        if still_running:
            return ConvergenceProposal(
                decision=ConvergenceDecision.CONTINUE,
                space_id=self.space_id,
                plan_version=plan_version,
                reasoning=f"Tasks still in-flight ({len(still_running)}); continuing.",
                evaluation=eval_result,
            )

        # All tasks finished but goal unsatisfied → REPLAN
        return self._propose_replan(
            kernel=kernel,
            plan_version=plan_version,
            eval_result=eval_result,
            task_id=None,
            reason="Goal UNSATISFIED after plan exhaustion",
        )

    def _handle_failure(
        self,
        kernel: SpaceKernelAuthorityProtocol,
        plan_version: int,
        task_graph: TaskGraph,
        failed_task_id: str,
        error_class: str,
        error_message: str,
    ) -> ConvergenceProposal:
        """Handle a task failure via bounded retry → replan → escalate chain (SCCA Law 6)."""
        fingerprint = _compute_failure_fingerprint(
            self.space_id, failed_task_id, error_class
        )

        # ── Terminal errors: escalate immediately (no retries) ───────────────
        terminal_classes = {
            "terminal.permission_denied",
            "terminal.budget_exceeded",
            "terminal.security_violation",
            "terminal.space_terminated",
        }
        if error_class in terminal_classes:
            return ConvergenceProposal(
                decision=ConvergenceDecision.ESCALATE,
                space_id=self.space_id,
                plan_version=plan_version,
                task_id=failed_task_id,
                reasoning=f"Terminal error '{error_class}' for task '{failed_task_id}': "
                          f"{error_message}. Immediate human escalation required.",
                escalation_reason=f"{error_class}: {error_message}",
                failure_fingerprint=fingerprint,
            )

        # ── Abort on unrecoverable violations ───────────────────────────────
        if error_class.startswith("violation."):
            return ConvergenceProposal(
                decision=ConvergenceDecision.ABORT,
                space_id=self.space_id,
                plan_version=plan_version,
                task_id=failed_task_id,
                reasoning=f"Unrecoverable violation '{error_class}' for task '{failed_task_id}'. "
                          f"Aborting space execution.",
                failure_fingerprint=fingerprint,
            )

        # ── Transient errors: bounded retry ──────────────────────────────────
        if error_class.startswith("transient."):
            current_retries = self._get_retry_count(failed_task_id)
            if current_retries < self.MAX_RETRY_BUDGET:
                new_count = self._increment_retry(failed_task_id, error_class)
                return ConvergenceProposal(
                    decision=ConvergenceDecision.RETRY,
                    space_id=self.space_id,
                    plan_version=plan_version,
                    task_id=failed_task_id,
                    retry_attempt=new_count,
                    reasoning=(
                        f"Transient failure '{error_class}' for task '{failed_task_id}' "
                        f"(attempt {new_count}/{self.MAX_RETRY_BUDGET}). Scheduling retry."
                    ),
                    failure_fingerprint=fingerprint,
                )
            else:
                # Retry budget exhausted → fall through to replan
                reason = (
                    f"Retry budget exhausted ({self.MAX_RETRY_BUDGET} attempts) "
                    f"for task '{failed_task_id}' on '{error_class}'."
                )
                return self._propose_replan(
                    kernel=kernel,
                    plan_version=plan_version,
                    eval_result=None,
                    task_id=failed_task_id,
                    reason=reason,
                    fingerprint=fingerprint,
                )

        # ── Unknown / structural failures: replan ────────────────────────────
        return self._propose_replan(
            kernel=kernel,
            plan_version=plan_version,
            eval_result=None,
            task_id=failed_task_id,
            reason=f"Structural failure '{error_class}': {error_message}",
            fingerprint=fingerprint,
        )

    def _propose_replan(
        self,
        kernel: SpaceKernelAuthorityProtocol,
        plan_version: int,
        eval_result: GoalEvaluationResult | None,
        task_id: str | None,
        reason: str,
        fingerprint: str = "",
    ) -> ConvergenceProposal:
        """Propose a REPLAN via PlanDelta or escalate if replan budget is exhausted."""
        replan_key = task_id or "__plan__"
        current_replans = self._get_replan_count(replan_key)

        # Infinite-loop prevention: if same fingerprint seen twice, escalate
        if fingerprint and self._has_fingerprint(fingerprint):
            return ConvergenceProposal(
                decision=ConvergenceDecision.ESCALATE,
                space_id=self.space_id,
                plan_version=plan_version,
                task_id=task_id,
                reasoning=(
                    f"Failure fingerprint '{fingerprint}' repeated — infinite loop detected. "
                    f"Escalating to human operator."
                ),
                escalation_reason=f"Infinite loop guard triggered: {reason}",
                replan_attempt=current_replans,
                failure_fingerprint=fingerprint,
                evaluation=eval_result,
            )

        if current_replans >= self.MAX_REPLAN_BUDGET:
            return ConvergenceProposal(
                decision=ConvergenceDecision.ESCALATE,
                space_id=self.space_id,
                plan_version=plan_version,
                task_id=task_id,
                reasoning=(
                    f"Replan budget exhausted ({self.MAX_REPLAN_BUDGET} replans) for "
                    f"'{replan_key}'. Escalating to human operator. Reason: {reason}"
                ),
                escalation_reason=f"Replan budget exhausted: {reason}",
                replan_attempt=current_replans,
                failure_fingerprint=fingerprint,
                evaluation=eval_result,
            )

        # Record fingerprint and increment replan counter
        if fingerprint:
            self._add_fingerprint(replan_key, fingerprint)
        new_replan_count = self._increment_replan(replan_key)

        # Phase 13: Query advisory adaptation hints (ADAPT-002, ADAPT-003)
        adaptation_hints: list[ExperienceHint] = []
        counterfactual_rec = ""
        source_exp_id = ""
        suggested_alt_cap = ""

        if self.adaptation_layer is not None and not self.replay_mode:
            try:
                hint_query: dict[str, Any] = {
                    "task_id": task_id or "__plan__",
                    "error_class": reason,
                    "fingerprint": fingerprint,
                }
                if task_id:
                    try:
                        cur_node = kernel.get_task_graph().get_node(task_id)
                        if cur_node is not None:
                            hint_query["capability"] = cur_node.capability
                    except Exception:
                        pass

                hints = self.adaptation_layer.generate_hints(
                    space_id=self.space_id,
                    situation_hint=hint_query,
                    limit=5,
                )
                adaptation_hints = list(hints)
                for h in adaptation_hints:
                    if h.suggested_alternative_capability and not suggested_alt_cap:
                        suggested_alt_cap = h.suggested_alternative_capability
                    if h.counterfactual_summary and not counterfactual_rec:
                        counterfactual_rec = h.counterfactual_summary
                    if h.experience_id and not source_exp_id:
                        source_exp_id = h.experience_id
            except Exception:
                pass  # Advisory layer failure must never block convergence

        # Build PlanDelta for REPLAN — uses "rollback" op (legal in ALLOWED_OPS).
        # "rollback" is the correct structural recovery op for replanning.
        # The Dispatcher does NOT commit this; the caller must pass it to SpaceKernel.
        replan_ops: list[dict[str, Any]] = []
        payload_data: dict[str, Any] = {
            "reason": reason,
            "replan_attempt": new_replan_count,
            "failure_fingerprint": fingerprint,
        }
        if suggested_alt_cap:
            payload_data["suggested_alternative"] = suggested_alt_cap
        if counterfactual_rec:
            payload_data["counterfactual_recommendation"] = counterfactual_rec
        if source_exp_id:
            payload_data["source_experience_id"] = source_exp_id

        if task_id is not None:
            replan_ops.append(
                {
                    "op": "rollback",
                    "target_node_id": task_id,
                    "payload": payload_data,
                }
            )
        else:
            payload_data["missing_criteria"] = (
                eval_result.missing_criteria if eval_result else []
            )
            replan_ops.append(
                {
                    "op": "rollback",
                    "target_node_id": "__plan__",
                    "payload": payload_data,
                }
            )

        plan_delta = PlanDelta(
            space_id=self.space_id,
            base_version=plan_version,
            resulting_version=plan_version + 1,
            ops=replan_ops,
            delta_id=f"replan-{replan_key}-attempt-{new_replan_count}",
        )

        reasoning_msg = (
            f"REPLAN proposed (attempt {new_replan_count}/{self.MAX_REPLAN_BUDGET}). "
            f"Reason: {reason}"
        )
        if counterfactual_rec:
            reasoning_msg += f" [Adaptation: {counterfactual_rec[:100]}]"

        return ConvergenceProposal(
            decision=ConvergenceDecision.REPLAN,
            space_id=self.space_id,
            plan_version=plan_version,
            task_id=task_id,
            reasoning=reasoning_msg,
            replan_attempt=new_replan_count,
            plan_delta=plan_delta,
            evaluation=eval_result,
            failure_fingerprint=fingerprint,
            adaptation_hints=tuple(adaptation_hints),
            counterfactual_recommendation=counterfactual_rec,
            source_experience_id=source_exp_id,
        )

    def apply_proposal(
        self,
        proposal: ConvergenceProposal,
        kernel: SpaceKernelAuthorityProtocol,
    ) -> tuple[bool, int, str | None]:
        """Apply a ConvergenceProposal's PlanDelta via SpaceKernel CAS (Phase 12.6).

        This is the ONLY method that touches the authoritative plan state.
        For CONTINUE / RETRY / ESCALATE / ABORT decisions, no CAS is performed here.
        For REPLAN decisions, the plan_delta is submitted to SpaceKernel.commit_plan_delta().

        Returns:
            (success: bool, new_plan_version: int, error: str | None)
        """
        kernel.verify_space_identity(self.space_id)

        if proposal.decision != ConvergenceDecision.REPLAN:
            # Non-replan decisions don't mutate the plan
            return True, proposal.plan_version, None

        if proposal.plan_delta is None:
            return (
                False,
                proposal.plan_version,
                "REPLAN decision has no PlanDelta to apply",
            )

        # Bounded rebase loop: at most MAX_REPLAN_BUDGET CAS attempts
        delta = proposal.plan_delta
        for attempt in range(self.reconciler.max_rebases if self.reconciler else 3):
            try:
                ok, new_ver, err = kernel.commit_plan_delta(
                    delta, proposal_id=delta.delta_id
                )
                if ok:
                    return True, new_ver, None
            except Exception as exc:
                return False, kernel.get_plan_version(), f"PlanDelta commit failed: {exc}"
            # Rebase stale delta onto latest version
            latest = kernel.get_plan_version()
            delta = PlanDelta(
                space_id=self.space_id,
                base_version=latest,
                resulting_version=latest + 1,
                ops=delta.ops,
                delta_id=f"{delta.delta_id}-rebase-{attempt + 1}",
            )

        return False, kernel.get_plan_version(), "CAS rebase limit exceeded during REPLAN"

    def is_plan_converged(self, task_graph: TaskGraph) -> bool:
        """Return True if every non-optional task in the graph is in a terminal state (Phase 12.6)."""
        terminal = {
            TaskState.COMPLETED.value,
            TaskState.FAILED.value,
            TaskState.CANCELLED.value,
            TaskState.ESCALATED.value,
        }
        for node in task_graph.nodes:
            if node.state not in terminal and not node.optional:
                return False
        return True

    def is_plan_succeeded(self, task_graph: TaskGraph) -> bool:
        """Return True if every non-optional task reached COMPLETED (goal achieved, Phase 12.6)."""
        for node in task_graph.nodes:
            if node.optional:
                continue
            if node.state != TaskState.COMPLETED.value:
                return False
        return True

    def reset_task_budgets(self, task_id: str) -> None:
        """Reset retry/replan budgets for a task after a successful replan (Phase 12.6).

        Called by the orchestration loop when a REPLAN is accepted and the task
        is given a fresh identity in the new plan version.
        """
        self._retry_counts.pop(task_id, None)
        self._replan_counts.pop(task_id, None)
