"""Unit tests for TeamBuilder.

spec §4 (Team Builder), §16 (task.assigned), ORCH-004 — Phase 4
"""

from __future__ import annotations

from ryu.pulse_bus.bus import PulseBus
from ryu.pulse_bus.pulse import Pulse

from core.orchestrator.goal_analyzer import GoalSpec
from core.orchestrator.team_builder import TeamBuilder
from core.plans.task_graph import TaskGraph, TaskNode


class SpyPulseBus(PulseBus):
    def __init__(self) -> None:
        super().__init__()
        self.published: list[Pulse] = []

    def publish(self, pulse: Pulse) -> Pulse:
        self.published.append(pulse)
        return super().publish(pulse)


def test_team_builder_single_agent_assignment() -> None:
    bus = SpyPulseBus()
    tb = TeamBuilder(bus=bus)

    spec = GoalSpec(
        goal_id="goal-s1",
        space_id="space-t1",
        objective="Simple task",
        constraints=[],
        required_capabilities=["general.compute"],
        single_agent_eligible=True,
        command_id="cmd-s1",
    )
    graph = TaskGraph(
        space_id="space-t1",
        plan_version=1,
        nodes=[TaskNode(id="task-1", capability="general.compute")],
    )

    table = tb.build_team(graph, spec)
    assert len(table.assignments) == 1
    asgn = table.get_assignment("task-1")
    assert asgn is not None
    assert asgn.assignee_id == "agent-single-space-t1"
    assert asgn.role == "general_agent"
    assert asgn.plan_version == 1

    # Verify task.assigned pulse was emitted
    asgn_pulses = [p for p in bus.published if p.type == "task.assigned"]
    assert len(asgn_pulses) == 1
    assert asgn_pulses[0].payload["task_id"] == "task-1"
    assert asgn_pulses[0].payload["assignee_id"] == "agent-single-space-t1"
    assert asgn_pulses[0].payload["plan_version"] == 1


def test_team_builder_multi_role_and_resource_requirements() -> None:
    bus = SpyPulseBus()
    tb = TeamBuilder(bus=bus)

    spec = GoalSpec(
        goal_id="goal-m1",
        space_id="space-t2",
        objective="GPU model training and file extraction",
        constraints=[],
        required_capabilities=["compute.gpu", "fs.read_write"],
        single_agent_eligible=False,
        command_id="cmd-m1",
    )
    graph = TaskGraph(
        space_id="space-t2",
        plan_version=1,
        nodes=[
            TaskNode(id="gpu-task", capability="compute.gpu", params={"gpu_instance": "cuda-0"}),
            TaskNode(id="fs-task", capability="fs.read_write"),
        ],
    )

    table = tb.build_team(graph, spec)
    assert len(table.assignments) == 2

    gpu_asgn = table.get_assignment("gpu-task")
    assert gpu_asgn is not None
    assert gpu_asgn.role == "gpu_specialist"
    assert len(gpu_asgn.resource_requirements) == 1
    res_req = gpu_asgn.resource_requirements[0]
    assert res_req.resource_type == "gpu"
    assert res_req.instance_id == "cuda-0"

    fs_asgn = table.get_assignment("fs-task")
    assert fs_asgn is not None
    assert fs_asgn.role == "file_operator"
    assert len(fs_asgn.resource_requirements) == 0

    # Verify 2 task.assigned pulses emitted
    asgn_pulses = [p for p in bus.published if p.type == "task.assigned"]
    assert len(asgn_pulses) == 2
