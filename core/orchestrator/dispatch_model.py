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

from core.plans.delta import PlanDelta
from core.plans.task_graph import (
    TaskGraph,
    TaskNode,
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


class WorkerInvokerProtocol(Protocol):
    """Dependency inversion protocol for invoking capability workers (AGENTS.md §7)."""

    def invoke(self, request: Any) -> Any: ...


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
