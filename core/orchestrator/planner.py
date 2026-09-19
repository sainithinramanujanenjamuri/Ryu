"""Planner: Generates proposed versioned Task Graphs from Goal Specs.

spec §4 (Planner), §16 (TaskGraph), ORCH-003 — Phase 4
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.orchestrator.goal_analyzer import GoalSpec
from core.plans.task_graph import TaskGraph, TaskNode


@dataclass(frozen=True)
class ProposedPlan:
    """A proposed, uncommitted plan produced by the Planner.

    Constitutional Invariant:
    PROPOSED PLAN != AUTHORITATIVE PLAN.
    This proposal has no authority until submitted and committed via the Space Kernel's Plan CAS.
    """

    space_id: str
    proposed_version: int
    task_graph: TaskGraph
    metadata: dict[str, Any]


class Planner:
    """Produces versioned Task Graphs from Goal Specs without mutating authoritative state."""

    def __init__(self) -> None:
        pass

    def plan_goal(self, goal_spec: GoalSpec) -> ProposedPlan:
        """Construct a proposed TaskGraph for a GoalSpec."""
        nodes: list[TaskNode] = []

        # If explicit steps provided in metadata, use them
        explicit_steps = goal_spec.metadata.get("steps")
        if explicit_steps and isinstance(explicit_steps, list):
            for idx, step in enumerate(explicit_steps):
                step_id = step.get("id", f"task-{idx + 1}")
                cap = step.get("capability", "general.compute")
                optional = bool(step.get("optional", False))
                params = dict(step.get("params", {}))
                nodes.append(
                    TaskNode(
                        id=step_id,
                        capability=cap,
                        params=params,
                        optional=optional,
                        state="pending",
                    )
                )
        elif goal_spec.single_agent_eligible:
            # Single-agent node
            cap = (
                goal_spec.required_capabilities[0]
                if goal_spec.required_capabilities
                else "general.compute"
            )
            nodes.append(
                TaskNode(
                    id=f"task-single-{goal_spec.goal_id}",
                    capability=cap,
                    params={"objective": goal_spec.objective},
                    optional=False,
                    state="pending",
                )
            )
        else:
            # Multi-node graph mapped to required capabilities
            for idx, cap in enumerate(goal_spec.required_capabilities):
                node_id = f"task-{idx + 1}-{cap.replace('.', '-')}"
                # Mark later analysis/reporting nodes as optional for degraded mode testing
                is_optional = "report" in cap or "metrics" in cap
                nodes.append(
                    TaskNode(
                        id=node_id,
                        capability=cap,
                        params={"objective": f"Execute {cap} for {goal_spec.objective}"},
                        optional=is_optional,
                        state="pending",
                    )
                )

        task_graph = TaskGraph(
            space_id=goal_spec.space_id,
            plan_version=1,
            nodes=nodes,
        )

        return ProposedPlan(
            space_id=goal_spec.space_id,
            proposed_version=1,
            task_graph=task_graph,
            metadata={"goal_id": goal_spec.goal_id},
        )
