"""Unit tests for Planner.

spec §4 (Planner), §16 (TaskGraph & PlanDelta), ORCH-003 — Phase 4
"""

from __future__ import annotations

from core.orchestrator.goal_analyzer import GoalSpec
from core.orchestrator.planner import Planner


def test_planner_single_agent_proposal() -> None:
    planner = Planner()
    spec = GoalSpec(
        goal_id="goal-1",
        space_id="space-p1",
        objective="Single task calculation",
        constraints=[],
        required_capabilities=["general.compute"],
        single_agent_eligible=True,
        command_id="cmd-1",
    )

    proposed = planner.plan_goal(spec)

    assert proposed.space_id == "space-p1"
    assert proposed.proposed_version == 1
    assert len(proposed.task_graph.nodes) == 1
    node = proposed.task_graph.nodes[0]
    assert node.id == "task-single-goal-1"
    assert node.capability == "general.compute"
    assert node.state == "pending"
    assert node.optional is False


def test_planner_multi_node_graph_with_optional_nodes() -> None:
    planner = Planner()
    spec = GoalSpec(
        goal_id="goal-2",
        space_id="space-p2",
        objective="Multi stage train and report metrics",
        constraints=[],
        required_capabilities=["compute.gpu", "metrics.reporting"],
        single_agent_eligible=False,
        command_id="cmd-2",
    )

    proposed = planner.plan_goal(spec)

    assert proposed.space_id == "space-p2"
    assert len(proposed.task_graph.nodes) == 2
    gpu_node = proposed.task_graph.nodes[0]
    metrics_node = proposed.task_graph.nodes[1]

    assert gpu_node.capability == "compute.gpu"
    assert gpu_node.optional is False

    assert metrics_node.capability == "metrics.reporting"
    assert metrics_node.optional is True  # Tagged optional for degraded mode pruning


def test_planner_explicit_steps_in_metadata() -> None:
    planner = Planner()
    spec = GoalSpec(
        goal_id="goal-3",
        space_id="space-p3",
        objective="Custom pipeline",
        constraints=[],
        required_capabilities=[],
        single_agent_eligible=False,
        command_id="cmd-3",
        metadata={
            "steps": [
                {"id": "fetch", "capability": "net.http", "optional": False},
                {"id": "process", "capability": "code.execute", "optional": False},
                {"id": "cleanup", "capability": "fs.read_write", "optional": True},
            ]
        },
    )

    proposed = planner.plan_goal(spec)
    assert len(proposed.task_graph.nodes) == 3
    assert proposed.task_graph.nodes[0].id == "fetch"
    assert proposed.task_graph.nodes[1].id == "process"
    assert proposed.task_graph.nodes[2].id == "cleanup"
    assert proposed.task_graph.nodes[2].optional is True
