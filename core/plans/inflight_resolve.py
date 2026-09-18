"""In-flight node resolution on Plan supersede.

Decides per-node whether in-flight tasks finish, checkpoint, or cancel when
a PlanDelta updates the authoritative plan_version.

spec §16 (TaskGraph & PlanDelta), PLAN-004 — Phase 2
"""

from __future__ import annotations

from typing import Literal

from core.plans.delta import PlanDelta
from core.plans.task_graph import TaskNode

ResolutionAction = Literal["finish", "checkpoint", "cancel"]


def resolve_inflight_node(node: TaskNode, delta: PlanDelta) -> ResolutionAction:
    """
    Determine the resolution action for an in-flight node affected by a PlanDelta.

    Rules (docs/Architecture §16):
    - cancel: op removes the node outright.
    - checkpoint: op targets the node (reassign/rollback) and work is paused/checkpointed.
    - finish: no matching op targets the node; allowed to complete on original version.
    """
    for op in delta.ops:
        target = op.get("target_node_id")
        if target == node.id:
            op_type = op.get("op")
            if op_type == "remove":
                return "cancel"
            if op_type in ("reassign", "rollback"):
                return "checkpoint"
    return "finish"

