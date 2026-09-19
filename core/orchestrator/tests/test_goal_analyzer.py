"""Unit tests for GoalAnalyzer.

spec §4 (Goal Analyzer), §16 (goal.defined), ORCH-002 — Phase 4
"""

from __future__ import annotations

import pytest
from ryu.pulse_bus.bus import PulseBus
from ryu.pulse_bus.pulse import Pulse

from core.orchestrator.goal_analyzer import Command, GoalAnalyzer


class SpyPulseBus(PulseBus):
    def __init__(self) -> None:
        super().__init__()
        self.published: list[Pulse] = []

    def publish(self, pulse: Pulse) -> Pulse:
        self.published.append(pulse)
        return super().publish(pulse)


def test_goal_analyzer_pure_transformation() -> None:
    bus = SpyPulseBus()
    analyzer = GoalAnalyzer(bus=bus)

    cmd = Command(
        command_id="cmd-101",
        space_id="space-analysis",
        objective="Analyze log files and report errors",
        constraints=["read_only"],
        params={"required_capabilities": ["fs.read_write", "code.execute"]},
    )

    spec = analyzer.analyze_goal(cmd)

    assert spec.goal_id == "goal-cmd-101"
    assert spec.space_id == "space-analysis"
    assert spec.objective == "Analyze log files and report errors"
    assert spec.constraints == ["read_only"]
    assert spec.required_capabilities == ["fs.read_write", "code.execute"]
    assert spec.single_agent_eligible is False

    # Verify goal.defined pulse was emitted
    goal_pulses = [p for p in bus.published if p.type == "goal.defined"]
    assert len(goal_pulses) == 1
    gp = goal_pulses[0]
    assert gp.payload["goal_id"] == "goal-cmd-101"
    assert gp.payload["single_agent_eligible"] is False
    assert gp.payload["goal_spec"]["objective"] == "Analyze log files and report errors"


def test_goal_analyzer_single_agent_eligibility() -> None:
    bus = SpyPulseBus()
    analyzer = GoalAnalyzer(bus=bus)

    # Simple command with single capability heuristic
    cmd = Command(
        command_id="cmd-simple",
        space_id="space-simple",
        objective="Calculate sha256 checksum",
    )
    spec = analyzer.analyze_goal(cmd)
    assert spec.single_agent_eligible is True

    # Explicit override in params
    cmd_explicit = Command(
        command_id="cmd-override",
        space_id="space-simple",
        objective="Multi task pipeline",
        params={"required_capabilities": ["cap1", "cap2"], "single_agent": True},
    )
    spec_override = analyzer.analyze_goal(cmd_explicit)
    assert spec_override.single_agent_eligible is True


def test_goal_analyzer_validation_errors() -> None:
    analyzer = GoalAnalyzer()

    # Empty space_id
    with pytest.raises(ValueError, match="valid non-empty space_id"):
        analyzer.analyze_goal(Command(command_id="c1", space_id="", objective="test"))

    # Empty command_id
    with pytest.raises(ValueError, match="valid non-empty command_id"):
        analyzer.analyze_goal(Command(command_id="", space_id="s1", objective="test"))

    # Empty objective
    with pytest.raises(ValueError, match="objective cannot be empty"):
        analyzer.analyze_goal(Command(command_id="c1", space_id="s1", objective=""))
