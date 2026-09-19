"""Unit tests for SpaceOrchestrator thin coordinator.

spec §4 (Space Orchestrator), ROADMAP Phase 4, ORCH-001 — Phase 4
"""

from __future__ import annotations

import pytest
from ryu.pulse_bus.bus import PulseBus
from ryu.pulse_bus.pulse import Pulse

from core.orchestrator.goal_analyzer import Command
from core.orchestrator.orchestrator import SpaceOrchestrator
from core.resources.identity import Resource, ResourceIdentity
from core.resources.manager import ResourceManager
from core.space.kernel import SpaceKernel


class SpyPulseBus(PulseBus):
    def __init__(self) -> None:
        super().__init__()
        self.published: list[Pulse] = []

    def publish(self, pulse: Pulse) -> Pulse:
        self.published.append(pulse)
        return super().publish(pulse)


def test_orchestrator_full_sequencing() -> None:
    bus = SpyPulseBus()
    space_id = "space-orch-1"
    kernel = SpaceKernel(space_id=space_id, owner_id="user-1", bus=bus)
    rm = ResourceManager(bus=bus)
    orch = SpaceOrchestrator(space_id=space_id, kernel=kernel, resource_mgr=rm, bus=bus)

    cmd = Command(
        command_id="cmd-orch-1",
        space_id=space_id,
        objective="Extract log data and analyze performance",
        params={"required_capabilities": ["fs.read_write", "code.execute"]},
    )

    session = orch.submit_goal(cmd)

    assert session.command_id == "cmd-orch-1"
    assert session.space_id == space_id
    assert session.goal_spec.objective == "Extract log data and analyze performance"
    assert len(session.task_graph.nodes) == 2
    assert len(session.assignments.assignments) == 2

    # Verify pulse sequence: space.created -> goal.defined -> plan.created -> 2x task.assigned
    types = [p.type for p in bus.published]
    assert "space.created" in types
    assert "goal.defined" in types
    assert "plan.created" in types
    assert types.count("task.assigned") == 2


def test_orchestrator_idempotency_on_duplicate_command() -> None:
    # ADR-0007: duplicate command_id returns existing session without duplicate planning
    bus = SpyPulseBus()
    space_id = "space-orch-idemp"
    kernel = SpaceKernel(space_id=space_id, owner_id="user-1", bus=bus)
    rm = ResourceManager(bus=bus)
    orch = SpaceOrchestrator(space_id=space_id, kernel=kernel, resource_mgr=rm, bus=bus)

    cmd = Command(
        command_id="cmd-repeat-1",
        space_id=space_id,
        objective="Run periodic benchmark",
    )

    # First submission
    session1 = orch.submit_goal(cmd)
    pulse_count_after_first = len(bus.published)

    # Second identical submission
    session2 = orch.submit_goal(cmd)
    pulse_count_after_second = len(bus.published)

    # Identical session returned; zero duplicate pulses emitted
    assert session1 is session2
    assert pulse_count_after_second == pulse_count_after_first


def test_orchestrator_cross_space_isolation() -> None:
    bus = SpyPulseBus()
    kernel = SpaceKernel(space_id="space-A", owner_id="user-1", bus=bus)
    rm = ResourceManager(bus=bus)
    orch = SpaceOrchestrator(space_id="space-A", kernel=kernel, resource_mgr=rm, bus=bus)

    # Attempt to submit command for space-B into space-A orchestrator
    cmd_foreign = Command(
        command_id="cmd-foreign",
        space_id="space-B",
        objective="Infiltrate space B",
    )

    with pytest.raises(PermissionError, match="Cross-space goal submission rejected"):
        orch.submit_goal(cmd_foreign)


def test_orchestrator_resource_lease_coordination() -> None:
    bus = SpyPulseBus()
    space_id = "space-orch-res"
    kernel = SpaceKernel(space_id=space_id, owner_id="user-1", bus=bus)
    rm = ResourceManager(bus=bus)
    orch = SpaceOrchestrator(space_id=space_id, kernel=kernel, resource_mgr=rm, bus=bus)

    # Register GPU in space
    gpu_id = ResourceIdentity(resource_type="gpu", provider_id="node-1", instance_id="cuda-0")
    rm.register_resource(Resource(identity=gpu_id, space_id=space_id, total_capacity=1))

    # Orchestrator coordinates lease request through Resource Manager
    acq = orch.request_resource_lease(
        requester_id="worker-1",
        identity=gpu_id,
        duration_seconds=60.0,
    )
    assert acq.granted is True
    assert acq.lease is not None
    token = acq.lease.lease_token

    # Release lease
    released = orch.release_resource_lease(requester_id="worker-1", lease_token=token)
    assert released is True
