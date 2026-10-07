"""Task Graph and Task Node contracts for Plan Versioning and Execution Dispatch.

spec §16 (TaskGraph & PlanDelta), PLAN-001 — Phase 2
Phase 12: DISPATCH-001, DISPATCH-002, DISPATCH-003, ADR-0041
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class TaskGraphError(Exception):
    """Base exception for TaskGraph validation and execution."""


class IllegalStateTransitionError(TaskGraphError):
    """Raised when an unauthorized or illegal state transition is attempted."""


class MissingDependencyError(TaskGraphError):
    """Raised when a task depends on an unknown or non-existent task ID."""


class GraphCycleError(TaskGraphError):
    """Raised when a circular dependency or cycle is detected in a TaskGraph."""


class TaskNotFoundError(TaskGraphError):
    """Raised when a requested task ID is not found in the TaskGraph."""


class PlanConflictError(TaskGraphError):
    """Raised when a PlanDelta CAS commit fails due to version mismatch or conflict."""


class TaskState(str, Enum):
    """Deterministic TaskNode execution lifecycle states (Phase 12, ADR-0041)."""

    # Primary 11-stage progression
    PENDING = "pending"
    READY = "ready"
    ADMISSION_PENDING = "admission_pending"
    ADMITTED = "admitted"
    LEASE_PENDING = "lease_pending"
    LEASED = "leased"
    DISPATCHED = "dispatched"
    RUNNING = "running"
    OBSERVING = "observing"
    EVALUATING = "evaluating"
    COMPLETED = "completed"

    # Failure / exceptional states
    BLOCKED = "blocked"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    RETRY_PENDING = "retry_pending"
    ESCALATED = "escalated"
    CANCELLED = "cancelled"

    # Backward compatibility alias
    IN_FLIGHT = "in_flight"


ALLOWED_TASK_STATES = frozenset(s.value for s in TaskState)

LEGAL_TRANSITIONS: dict[str, frozenset[str]] = {
    "pending": frozenset({"ready", "blocked", "cancelled"}),
    "ready": frozenset({"admission_pending", "blocked", "cancelled"}),
    "admission_pending": frozenset({"admitted", "blocked", "failed", "cancelled"}),
    "admitted": frozenset({"lease_pending", "blocked", "cancelled"}),
    "lease_pending": frozenset({"leased", "blocked", "failed", "cancelled"}),
    "leased": frozenset({"dispatched", "cancelled", "failed"}),
    "dispatched": frozenset({"running", "in_flight", "failed", "timed_out", "cancelled"}),
    "running": frozenset({"observing", "completed", "failed", "timed_out", "cancelled"}),
    "in_flight": frozenset({"observing", "completed", "failed", "timed_out", "cancelled"}),
    "observing": frozenset({"evaluating", "completed", "failed"}),
    "evaluating": frozenset({"completed", "failed", "escalated"}),
    "retry_pending": frozenset({"ready", "admission_pending", "escalated", "cancelled"}),
    "blocked": frozenset({"ready", "admission_pending", "failed", "cancelled", "escalated"}),
    "failed": frozenset({"retry_pending", "escalated", "ready", "pending"}),
    "timed_out": frozenset({"retry_pending", "failed", "escalated", "ready", "pending"}),
    "completed": frozenset(),
    "cancelled": frozenset(),
    "escalated": frozenset({"retry_pending", "cancelled", "ready", "pending"}),
}


@dataclass
class TaskNode:
    """Represents an executable node within a Space's Task Graph."""

    id: str
    capability: str
    params: dict[str, Any] = field(default_factory=dict)
    optional: bool = False
    state: str = "pending"
    dependencies: list[str] = field(default_factory=list)
    attempt: int = 1
    result_ref: str | None = None
    error: str | None = None

    def __post_init__(self) -> None:
        norm_state = str(self.state).lower().strip()
        if norm_state not in ALLOWED_TASK_STATES:
            raise ValueError(
                f"Invalid task state '{self.state}'; must be one of {ALLOWED_TASK_STATES}"
            )
        self.state = norm_state
        # Deduplicate dependencies while preserving order
        self.dependencies = list(dict.fromkeys(self.dependencies))

    def transition_to(self, target_state: str | TaskState, reason: str | None = None) -> None:
        """Deterministically transition node to a target state adhering to LEGAL_TRANSITIONS."""
        norm_target = (
            target_state.value if isinstance(target_state, TaskState) else str(target_state)
        ).lower().strip()

        if norm_target not in ALLOWED_TASK_STATES:
            raise ValueError(
                f"Invalid task state '{target_state}'; must be one of {ALLOWED_TASK_STATES}"
            )

        allowed = LEGAL_TRANSITIONS.get(self.state, frozenset())
        if norm_target not in allowed:
            raise IllegalStateTransitionError(
                f"Illegal state transition from '{self.state}' to '{norm_target}' for task '{self.id}'"
            )

        self.state = norm_target
        if reason and norm_target in ("failed", "blocked", "timed_out", "escalated"):
            self.error = reason


@dataclass
class TaskGraph:
    """Versioned DAG of tasks scoped to a specific Space (Phase 12, ADR-0041)."""

    space_id: str
    plan_version: int
    nodes: list[TaskNode] = field(default_factory=list)

    def get_node(self, node_id: str) -> TaskNode | None:
        """Find a node by its ID."""
        for n in self.nodes:
            if n.id == node_id:
                return n
        return None

    def validate_dependencies(self) -> None:
        """Verify graph integrity: missing dependencies, self-dependencies, and cycles.

        Raises:
            MissingDependencyError: if a task depends on an unknown task ID.
            GraphCycleError: if a self-dependency or circular dependency is detected.
        """
        node_ids = {n.id for n in self.nodes}

        # Check unknown dependencies and self-loops
        for node in self.nodes:
            for dep_id in node.dependencies:
                if dep_id == node.id:
                    raise GraphCycleError(
                        f"Self-dependency detected: Task '{node.id}' cannot depend on itself."
                    )
                if dep_id not in node_ids:
                    raise MissingDependencyError(
                        f"Missing dependency: Task '{node.id}' depends on unknown task '{dep_id}'."
                    )

        # Cycle detection via Kahn's algorithm
        in_degree: dict[str, int] = {n.id: len(n.dependencies) for n in self.nodes}
        dependents: dict[str, list[str]] = {n.id: [] for n in self.nodes}
        for n in self.nodes:
            for dep in n.dependencies:
                dependents[dep].append(n.id)

        queue = deque([nid for nid, deg in in_degree.items() if deg == 0])
        visited_count = 0

        while queue:
            curr = queue.popleft()
            visited_count += 1
            for child in dependents[curr]:
                in_degree[child] -= 1
                if in_degree[child] == 0:
                    queue.append(child)

        if visited_count != len(self.nodes):
            raise GraphCycleError(
                f"Cycle detected in TaskGraph for space '{self.space_id}': "
                f"only {visited_count}/{len(self.nodes)} nodes could be topologically sequenced."
            )

    def has_cycles(self) -> bool:
        """Return True if the graph contains any cycles."""
        try:
            self.validate_dependencies()
            return False
        except GraphCycleError:
            return True
        except MissingDependencyError:
            return False

    def get_ready_tasks(self) -> list[TaskNode]:
        """Identify all tasks eligible for execution.

        Rule: A task is READY if:
        1. Current state is 'pending' or 'ready'.
        2. All upstream dependency tasks have state == 'completed'
           (or if optional upstream dependency is 'completed', 'failed', or 'cancelled').
        """
        self.validate_dependencies()
        ready_tasks: list[TaskNode] = []

        for node in self.nodes:
            if node.state not in ("pending", "ready"):
                continue

            all_deps_satisfied = True
            for dep_id in node.dependencies:
                parent = self.get_node(dep_id)
                if parent is None:
                    all_deps_satisfied = False
                    break
                if parent.state == "completed":
                    continue
                if parent.optional and parent.state in ("failed", "cancelled"):
                    continue
                # Prerequisite unfulfilled
                all_deps_satisfied = False
                break

            if all_deps_satisfied:
                ready_tasks.append(node)

        # Deterministic order by ID
        ready_tasks.sort(key=lambda n: n.id)
        return ready_tasks

    def topological_sort(self) -> list[TaskNode]:
        """Return deterministic topological ordering of all nodes."""
        self.validate_dependencies()
        in_degree: dict[str, int] = {n.id: len(n.dependencies) for n in self.nodes}
        dependents: dict[str, list[str]] = {n.id: [] for n in self.nodes}
        for n in self.nodes:
            for dep in n.dependencies:
                dependents[dep].append(n.id)

        # Sort initial nodes by ID for deterministic tie-breaking
        ready_keys = sorted([nid for nid, deg in in_degree.items() if deg == 0])
        queue = deque(ready_keys)
        ordered: list[TaskNode] = []

        while queue:
            # Sort current queue level deterministically
            curr_id = queue.popleft()
            node = self.get_node(curr_id)
            if node:
                ordered.append(node)

            children = sorted(dependents[curr_id])
            for child in children:
                in_degree[child] -= 1
                if in_degree[child] == 0:
                    queue.append(child)

        return ordered

    def is_completed(self) -> bool:
        """Return True if all mandatory nodes are completed."""
        if not self.nodes:
            return False
        for n in self.nodes:
            if not n.optional and n.state != "completed":
                return False
            if n.optional and n.state not in ("completed", "failed", "cancelled"):
                return False
        return True

    def has_failures(self) -> bool:
        """Return True if any mandatory node has permanently failed or escalated."""
        for n in self.nodes:
            if not n.optional and n.state in ("failed", "escalated"):
                return True
        return False

