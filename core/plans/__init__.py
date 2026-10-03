"""RYU AI Plan Versioning & TaskGraph Package — Phase 2 Space Kernel.

Enforces single-writer CAS on plan_version and in-flight supersede resolution.
spec §16 (docs/Architecture §16)
"""

from __future__ import annotations

from core.plans.delta import DeltaOp, PlanDelta
from core.plans.inflight_resolve import resolve_inflight_node
from core.plans.plan_store import InMemoryPlanStore, PlanStore, PlanStoreProtocol
from core.plans.postgres_plan_store import PostgresPlanStore
from core.plans.serialization import (
    deserialize_task_graph_json,
    serialize_task_graph_json,
    task_graph_from_dict,
    task_graph_to_dict,
    task_node_from_dict,
    task_node_to_dict,
)
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
    "InMemoryPlanStore",
    "MissingDependencyError",
    "PlanConflictError",
    "PlanDelta",
    "PlanStore",
    "PlanStoreProtocol",
    "PostgresPlanStore",
    "TaskGraph",
    "TaskGraphError",
    "TaskNode",
    "TaskNotFoundError",
    "TaskState",
    "deserialize_task_graph_json",
    "resolve_inflight_node",
    "serialize_task_graph_json",
    "task_graph_from_dict",
    "task_graph_to_dict",
    "task_node_from_dict",
    "task_node_to_dict",
]


