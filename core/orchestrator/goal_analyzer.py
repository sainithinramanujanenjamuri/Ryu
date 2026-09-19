"""Goal Analyzer: Transforms raw human commands into structured Goal Specs.

spec §4 (Goal Analyzer), §16 (goal.defined), ORCH-002 — Phase 4
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol

from ryu.pulse_bus.pulse import Pulse, Severity


class PulsePublisher(Protocol):
    def publish(self, pulse: Pulse) -> Pulse: ...


@dataclass(frozen=True)
class Command:
    """Raw human or channel input submitted to a Space."""

    command_id: str
    space_id: str
    objective: str
    params: dict[str, Any] = field(default_factory=dict)
    constraints: list[str] = field(default_factory=list)
    correlation_id: str | None = None


@dataclass(frozen=True)
class GoalSpec:
    """Structured, validated specification of a human goal."""

    goal_id: str
    space_id: str
    objective: str
    constraints: list[str]
    required_capabilities: list[str]
    single_agent_eligible: bool
    command_id: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class GoalAnalyzer:
    """Analyzes human commands into structured GoalSpecs without executing tools.

    Invariants:
    - Pure translation: executes zero tools, allocates zero resources, creates zero leases.
    - Emits typed `goal.defined` Pulse via the bus.
    - Stamped with `single_agent_eligible` boolean flag per §4, §18.
    """

    def __init__(self, bus: PulsePublisher | None = None) -> None:
        self.bus = bus

    def analyze_goal(self, command: Command) -> GoalSpec:
        """Analyze a command into a GoalSpec and publish `goal.defined`."""
        if not command.space_id:
            raise ValueError("Command must specify a valid non-empty space_id")
        if not command.command_id:
            raise ValueError("Command must specify a valid non-empty command_id")
        if not command.objective:
            raise ValueError("Command objective cannot be empty")

        goal_id = f"goal-{command.command_id}"

        # Extract capabilities from explicit params or objective keywords
        explicit_caps = command.params.get("required_capabilities", [])
        if explicit_caps:
            required_caps = list(explicit_caps)
        else:
            required_caps = self._detect_capabilities(command.objective)

        # Single agent eligibility heuristic (§18):
        # Eligible if explicitly requested or if single capability and simple scope
        explicit_single = command.params.get("single_agent", None)
        if explicit_single is not None:
            single_agent = bool(explicit_single)
        else:
            single_agent = len(required_caps) <= 1

        goal_spec = GoalSpec(
            goal_id=goal_id,
            space_id=command.space_id,
            objective=command.objective,
            constraints=list(command.constraints),
            required_capabilities=required_caps,
            single_agent_eligible=single_agent,
            command_id=command.command_id,
            metadata=dict(command.params.get("metadata", {})),
        )

        if self.bus is not None:
            correlation = command.correlation_id or f"corr-{command.command_id}"
            pulse = Pulse(
                id=f"pulse-goal-defined-{goal_id}",
                space_id=command.space_id,
                type="goal.defined",
                severity=Severity.INFO,
                source="goal_analyzer",
                correlation_id=correlation,
                payload={
                    "goal_id": goal_id,
                    "goal_spec": goal_spec.to_dict(),
                    "single_agent_eligible": single_agent,
                },
                timestamp=datetime.now(timezone.utc),
            )
            self.bus.publish(pulse)

        return goal_spec

    def _detect_capabilities(self, objective: str) -> list[str]:
        """Deterministic capability detector based on objective keywords."""
        caps: list[str] = []
        obj_lower = objective.lower()
        if "gpu" in obj_lower or "cuda" in obj_lower or "train" in obj_lower:
            caps.append("compute.gpu")
        if "file" in obj_lower or "read" in obj_lower or "write" in obj_lower:
            caps.append("fs.read_write")
        if "web" in obj_lower or "fetch" in obj_lower or "http" in obj_lower:
            caps.append("net.http")
        if "code" in obj_lower or "compile" in obj_lower or "build" in obj_lower:
            caps.append("code.execute")

        if not caps:
            caps.append("general.compute")
        return caps
