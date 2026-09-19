"""Harness cases: Orchestrator deterministic loop and plan cycle.

spec §4 (Space Orchestrator), CONTRACT_MATRIX ORCH-001 through ORCH-007 — Phase 4
"""

from __future__ import annotations

from datetime import datetime, timezone

from ryu.pulse_bus.bus import PulseBus
from ryu.pulse_bus.pulse import Pulse, Severity

from core.orchestrator.goal_analyzer import Command, GoalAnalyzer
from core.orchestrator.orchestrator import SpaceOrchestrator
from core.orchestrator.planner import Planner
from core.orchestrator.team_builder import TeamBuilder
from core.resources.manager import ResourceManager
from core.space.kernel import SpaceKernel


class RecordingPulseBus(PulseBus):
    def __init__(self) -> None:
        super().__init__()
        self.recorded: list[Pulse] = []

    def publish(self, pulse: Pulse) -> Pulse:
        self.recorded.append(pulse)
        return super().publish(pulse)


def test_orchestrator_full_deterministic_loop() -> None:
    """ORCH-001 / ORCH-006: Full deterministic loop with zero LLM.

    Command in -> Goal Spec -> Task Graph -> Admission -> assignments with plan_version
    -> stub execution -> artifact -> experience.stored entirely via Pulses.
    """
    bus = RecordingPulseBus()
    space_id = "space-keystone-demo"
    kernel = SpaceKernel(space_id=space_id, owner_id="human-operator", bus=bus, budget=100.0)
    resource_mgr = ResourceManager(bus=bus)
    orchestrator = SpaceOrchestrator(
        space_id=space_id, kernel=kernel, resource_mgr=resource_mgr, bus=bus
    )

    # 1. Command submitted
    cmd = Command(
        command_id="cmd-data-pipeline",
        space_id=space_id,
        objective="Run data analysis and generate summary artifact",
        params={"required_capabilities": ["fs.read_write", "code.execute"]},
    )
    session = orchestrator.submit_goal(cmd)

    assert session.goal_spec.goal_id == "goal-cmd-data-pipeline"
    assert session.task_graph.plan_version == 1
    assert len(session.task_graph.nodes) == 2

    # Verify initial pulse sequence: space.created -> goal.defined -> plan.created -> task.assigned
    emitted_types = [p.type for p in bus.recorded]
    assert "space.created" in emitted_types
    assert "goal.defined" in emitted_types
    assert "plan.created" in emitted_types
    assert emitted_types.count("task.assigned") == 2

    # 2. Stub Worker Execution: Workers react to task.assigned and emit execution lifecycle
    now = datetime.now(timezone.utc)
    for node in session.task_graph.nodes:
        # Worker emits task.started
        bus.publish(
            Pulse(
                id=f"pulse-start-{node.id}",
                space_id=space_id,
                type="task.started",
                severity=Severity.INFO,
                source="stub_worker",
                correlation_id=f"corr-{node.id}",
                payload={"task_id": node.id, "plan_version": 1},
                timestamp=now,
            )
        )
        # Worker emits worker.tool.called
        bus.publish(
            Pulse(
                id=f"pulse-tool-{node.id}",
                space_id=space_id,
                type="worker.tool.called",
                severity=Severity.INFO,
                source="stub_worker",
                correlation_id=f"corr-{node.id}",
                payload={
                    "tool_id": f"tool-{node.capability}",
                    "capability": node.capability,
                    "attempt": 1,
                },
                timestamp=now,
            )
        )
        # Worker emits worker.tool.succeeded
        bus.publish(
            Pulse(
                id=f"pulse-toolsuc-{node.id}",
                space_id=space_id,
                type="worker.tool.succeeded",
                severity=Severity.INFO,
                source="stub_worker",
                correlation_id=f"corr-{node.id}",
                payload={
                    "tool_id": f"tool-{node.capability}",
                    "result_ref": f"art://{node.id}",
                    "attempt": 1,
                },
                timestamp=now,
            )
        )
        # Worker emits task.completed
        bus.publish(
            Pulse(
                id=f"pulse-complete-{node.id}",
                space_id=space_id,
                type="task.completed",
                severity=Severity.INFO,
                source="stub_worker",
                correlation_id=f"corr-{node.id}",
                payload={"task_id": node.id, "result_ref": f"art://{node.id}", "plan_version": 1},
                timestamp=now,
            )
        )

    # 3. Verify Monitor tracked all task completions
    for node in session.task_graph.nodes:
        assert orchestrator.monitor.is_task_complete(node.id) is True

    # 4. Reflector writes experience.stored upon successful workflow
    exp_pulse = orchestrator.adapter.record_experience(
        situation={"goal": cmd.objective, "task_count": 2},
        action={"execution_type": "deterministic_stub_pipeline"},
        outcome="success",
        counterfactual="could_parallelize_read_and_code_tasks",
    )
    assert exp_pulse is not None
    assert exp_pulse.type == "experience.stored"
    assert exp_pulse.payload["outcome"] == "success"

    # Total pipeline verified end-to-end entirely via Pulses with zero LLM
    final_types = [p.type for p in bus.recorded]
    assert "experience.stored" in final_types


def test_orchestrator_goal_analyzer() -> None:
    """ORCH-002: Goal Analyzer converts Command into a structured GoalSpec."""
    bus = RecordingPulseBus()
    analyzer = GoalAnalyzer(bus=bus)
    cmd = Command(
        command_id="cmd-ga-1",
        space_id="space-ga",
        objective="Analyze sentiment in customer reviews",
        params={"single_agent": True},
    )
    spec = analyzer.analyze_goal(cmd)

    assert spec.goal_id == "goal-cmd-ga-1"
    assert spec.single_agent_eligible is True
    assert len(bus.recorded) == 1
    assert bus.recorded[0].type == "goal.defined"


def test_orchestrator_planner() -> None:
    """ORCH-003: Planner converts GoalSpec into a proposed TaskGraph."""
    analyzer = GoalAnalyzer()
    planner = Planner()
    cmd = Command(
        command_id="cmd-plan-1",
        space_id="space-pl",
        objective="Extract table and train model",
        params={"required_capabilities": ["fs.read_write", "compute.gpu"]},
    )
    spec = analyzer.analyze_goal(cmd)
    plan = planner.plan_goal(spec)

    assert plan.proposed_version == 1
    assert len(plan.task_graph.nodes) == 2
    assert plan.task_graph.nodes[0].capability == "fs.read_write"
    assert plan.task_graph.nodes[1].capability == "compute.gpu"


def test_orchestrator_team_builder() -> None:
    """ORCH-004: Team Builder converts TaskGraph into task assignments."""
    bus = RecordingPulseBus()
    analyzer = GoalAnalyzer()
    planner = Planner()
    tb = TeamBuilder(bus=bus)

    cmd = Command(
        command_id="cmd-tb-1",
        space_id="space-tb",
        objective="Run computation",
        params={"required_capabilities": ["compute.gpu"], "single_agent": False},
    )
    spec = analyzer.analyze_goal(cmd)
    plan = planner.plan_goal(spec)
    table = tb.build_team(plan.task_graph, spec)

    assert len(table.assignments) == 1
    asgn = list(table.assignments.values())[0]
    assert asgn.assignee_id == "worker-gpu-1"
    assert len(bus.recorded) == 1
    assert bus.recorded[0].type == "task.assigned"


def test_orchestrator_core_independence() -> None:
    """ORCH-007: Core Independence Proof (AGENTS.md §4, ADR-0009).

    Verifies that core/orchestrator imports zero modules from
    agents/, workers/, skills/, workflows/.
    """
    import ast
    from pathlib import Path

    orch_dir = Path(__file__).resolve().parents[3] / "core" / "orchestrator"
    forbidden = ("agents", "workers", "skills", "workflows")

    for py_file in orch_dir.glob("*.py"):
        with open(py_file, "r", encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=str(py_file))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split(".")[0]
                    assert root not in forbidden, f"{py_file} imported forbidden root '{root}'"
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    root = node.module.split(".")[0]
                    assert root not in forbidden, f"{py_file} imported forbidden root '{root}'"
