"""Team Builder: Maps Task Graph nodes to Agent and Worker roles.

spec §4 (Team Builder), §16 (task.assigned), ORCH-004 — Phase 4
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol

from ryu.pulse_bus.pulse import Pulse, Severity

from core.orchestrator.goal_analyzer import GoalSpec
from core.plans.task_graph import TaskGraph
from core.resources.identity import ResourceIdentity


class PulsePublisher(Protocol):
    def publish(self, pulse: Pulse) -> Pulse: ...


@dataclass(frozen=True)
class TaskAssignment:
    """Assignment metadata for an individual task node."""

    task_id: str
    assignee_id: str
    role: str
    skills: list[str]
    capabilities: list[str]
    resource_requirements: list[ResourceIdentity] = field(default_factory=list)
    plan_version: int = 1


@dataclass
class AssignmentTable:
    """Authoritative or proposed team assignment mapping for a Space plan."""

    space_id: str
    plan_version: int
    assignments: dict[str, TaskAssignment] = field(default_factory=dict)

    def get_assignment(self, task_id: str) -> TaskAssignment | None:
        return self.assignments.get(task_id)


class TeamBuilder:
    """Maps TaskGraph nodes to Agent and Worker roles.

    Invariants:
    - Never directly allocates resources or mints leases (Law 2).
    - If `GoalSpec.single_agent_eligible` is True, assigns one Agent for the whole graph.
    - Emits `task.assigned` Pulses with `plan_version`.
    """

    def __init__(self, bus: PulsePublisher | None = None) -> None:
        self.bus = bus

    def build_team(self, task_graph: TaskGraph, goal_spec: GoalSpec) -> AssignmentTable:
        """Construct assignments for each node in the task graph."""
        assignments: dict[str, TaskAssignment] = {}
        single_agent = goal_spec.single_agent_eligible

        for node in task_graph.nodes:
            if single_agent:
                assignee = f"agent-single-{task_graph.space_id}"
                role = "general_agent"
            else:
                assignee, role = self._determine_role(node.capability)

            # Identify hardware resource requirements (e.g. GPU)
            res_reqs: list[ResourceIdentity] = []
            if "gpu" in node.capability.lower() or "cuda" in str(node.params).lower():
                res_reqs.append(
                    ResourceIdentity(
                        resource_type="gpu",
                        provider_id=node.params.get("gpu_provider", "node-1"),
                        instance_id=node.params.get("gpu_instance", "cuda-0"),
                    )
                )

            assignment = TaskAssignment(
                task_id=node.id,
                assignee_id=assignee,
                role=role,
                skills=[node.capability],
                capabilities=[node.capability],
                resource_requirements=res_reqs,
                plan_version=task_graph.plan_version,
            )
            assignments[node.id] = assignment

            # Emit task.assigned Pulse
            if self.bus is not None:
                pulse = Pulse(
                    id=f"pulse-task-assigned-{node.id}-{task_graph.plan_version}",
                    space_id=task_graph.space_id,
                    type="task.assigned",
                    severity=Severity.INFO,
                    source="team_builder",
                    correlation_id=f"corr-{task_graph.space_id}-{node.id}",
                    payload={
                        "task_id": node.id,
                        "assignee_id": assignee,
                        "plan_version": task_graph.plan_version,
                    },
                    timestamp=datetime.now(timezone.utc),
                )
                self.bus.publish(pulse)

        return AssignmentTable(
            space_id=task_graph.space_id,
            plan_version=task_graph.plan_version,
            assignments=assignments,
        )

    def _determine_role(self, capability: str) -> tuple[str, str]:
        cap_lower = capability.lower()
        if "gpu" in cap_lower or "cuda" in cap_lower or "train" in cap_lower:
            return "worker-gpu-1", "gpu_specialist"
        if "fs" in cap_lower or "file" in cap_lower:
            return "worker-fs-1", "file_operator"
        if "net" in cap_lower or "http" in cap_lower:
            return "worker-net-1", "network_operator"
        if "code" in cap_lower:
            return "worker-code-1", "code_analyst"
        return "agent-general-1", "generalist"
