"""RYU AI Plan Versioning & TaskGraph Package — Phase 2 Space Kernel.

Enforces single-writer CAS on plan_version and in-flight supersede resolution.
spec §16 (docs/Architecture §16)
"""

from __future__ import annotations

from core.plans.delta import DeltaOp, PlanDelta
from core.plans.inflight_resolve import resolve_inflight_node
from core.plans.plan_store import PlanStore
from core.plans.task_graph import (
    ALLOWED_TASK_STATES,
    GraphCycleError,
    IllegalStateTransitionError,
    MissingDependencyError,
    PlanConflictError,
    TaskGraph,
    TaskGraphError,
    TaskNode,
    TaskNotFoundError,
    TaskState,
)

__all__ = [
    "ALLOWED_TASK_STATES",
    "DeltaOp",
    "GraphCycleError",
    "IllegalStateTransitionError",
    "MissingDependencyError",
    "PlanConflictError",
    "PlanDelta",
    "PlanStore",
    "TaskGraph",
    "TaskGraphError",
    "TaskNode",
    "TaskNotFoundError",
    "TaskState",
    "resolve_inflight_node",
]

