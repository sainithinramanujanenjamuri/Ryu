"""Future harness cases: Space boundary contracts scheduled for Phase 3+.

spec §4 (Space Kernel), CONTRACT_MATRIX SPACE-002, SPACE-003, SPACE-004 — Phase 3+
"""

from __future__ import annotations

import pytest


def test_space_resource_isolation() -> None:
    """SPACE-002: Resource grants are scoped to the owning Space."""
    from ryu.pulse_bus.bus import PulseBus

    from core.resources.identity import Resource, ResourceIdentity
    from core.resources.manager import ResourceManager

    bus = PulseBus()
    mgr = ResourceManager(bus=bus)
    res_id = ResourceIdentity("gpu", "local", "cuda-0")
    mgr.register_resource(Resource(identity=res_id, space_id="space-A", total_capacity=1))

    # Space B attempting to acquire resource in Space A is rejected
    with pytest.raises(PermissionError, match="Cross-space resource access rejected"):
        mgr.acquire("space-B", "agent-b", res_id)


def test_space_agent_isolation() -> None:
    """SPACE-003: Agent state/subscriptions cannot cross Space boundary
    without defined authorization.
    """
    from datetime import datetime, timezone

    from ryu.pulse_bus.bus import PulseBus
    from ryu.pulse_bus.pulse import Pulse, Severity

    from core.orchestrator.goal_analyzer import Command
    from core.orchestrator.orchestrator import SpaceOrchestrator
    from core.resources.manager import ResourceManager
    from core.space.kernel import SpaceKernel

    bus = PulseBus()
    kernel_a = SpaceKernel(space_id="space-iso-A", owner_id="user-a", bus=bus)
    rm = ResourceManager(bus=bus)
    orch_a = SpaceOrchestrator(space_id="space-iso-A", kernel=kernel_a, resource_mgr=rm, bus=bus)

    # Command directed at space B submitted to space A orchestrator is rejected
    cmd_b = Command(
        command_id="cmd-b-traversal", space_id="space-iso-B", objective="Traversal attempt"
    )
    with pytest.raises(PermissionError, match="Cross-space goal submission rejected"):
        orch_a.submit_goal(cmd_b)

    # Monitor A ignores events from Space B
    orch_a.monitor.handle_pulse(
        Pulse(
            id="foreign-task",
            space_id="space-iso-B",
            type="task.assigned",
            severity=Severity.INFO,
            source="foreign",
            correlation_id="c",
            payload={"task_id": "foreign-task", "assignee_id": "agent-b", "plan_version": 1},
            timestamp=datetime.now(timezone.utc),
        )
    )
    assert orch_a.monitor.get_task_state("foreign-task") == "unknown"


def test_space_artifact_isolation() -> None:
    pytest.skip("spec §4, SPACE-004 — Phase 10: Space Memory not implemented.")
