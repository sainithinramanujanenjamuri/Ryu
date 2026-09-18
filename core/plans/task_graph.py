"""Task Graph and Task Node contracts for Plan Versioning.

spec §16 (TaskGraph & PlanDelta), PLAN-001 — Phase 2
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

ALLOWED_TASK_STATES = frozenset({"pending", "in_flight", "completed", "failed", "cancelled"})


@dataclass
class TaskNode:
    """Represents an executable node within a Space's Task Graph."""
    id: str
    capability: str
    params: dict[str, Any] = field(default_factory=dict)
    optional: bool = False
    state: str = "pending"

    def __post_init__(self) -> None:
        if self.state not in ALLOWED_TASK_STATES:
            raise ValueError(
                f"Invalid task state '{self.state}'; must be one of {ALLOWED_TASK_STATES}"
            )


@dataclass
class TaskGraph:
    """Versioned DAG of tasks scoped to a specific Space."""
    space_id: str
    plan_version: int
    nodes: list[TaskNode] = field(default_factory=list)

    def get_node(self, node_id: str) -> TaskNode | None:
        """Find a node by its ID."""
        for n in self.nodes:
            if n.id == node_id:
                return n
        return None
