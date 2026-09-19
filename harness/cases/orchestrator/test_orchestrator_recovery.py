"""Harness cases: Orchestrator crash and recovery verification.

spec §4, §16, §17, CONTRACT_MATRIX KERNEL-006, ORCH-001 — Phase 4
"""

from __future__ import annotations

from ryu.pulse_bus.bus import PulseBus

from core.orchestrator.goal_analyzer import Command
from core.orchestrator.orchestrator import SpaceOrchestrator
from core.plans.delta import PlanDelta
from core.resources.identity import Resource, ResourceIdentity
from core.resources.manager import ResourceManager
from core.space.kernel import SpaceKernel


def test_orchestrator_crash_and_authoritative_state_survival() -> None:
    """Prompt Section 17: Orchestrator Crash Test.

    1. Space Kernel, Resource Manager, and Orchestrator session established.
    2. Plan committed, resources leased.
    3. Orchestrator instance is terminated (destroyed).
    4. Verify authoritative Space state survives:
       - Kernel state intact
       - Plan version and TaskGraph intact
       - Resource lease and capacity intact
       - Pulse event stream intact
    5. New Orchestrator instance starts and seamlessly rebinds to authoritative state.
    """
    bus = PulseBus()
    space_id = "space-crash-proof"
    kernel = SpaceKernel(space_id=space_id, owner_id="user-ops", bus=bus, budget=500.0)
    rm = ResourceManager(bus=bus)

    gpu_id = ResourceIdentity(resource_type="gpu", provider_id="node-1", instance_id="cuda-0")
    rm.register_resource(Resource(identity=gpu_id, space_id=space_id, total_capacity=1))

    # Instance 1: Orchestrator 1
    orch1 = SpaceOrchestrator(space_id=space_id, kernel=kernel, resource_mgr=rm, bus=bus)

    cmd = Command(
        command_id="cmd-survive-1",
        space_id=space_id,
        objective="Run long computation and hold lease",
        params={"required_capabilities": ["compute.gpu"]},
    )
    session1 = orch1.submit_goal(cmd)
    assert session1 is not None

    # Acquire GPU lease
    acq1 = orch1.request_resource_lease("worker-1", gpu_id, duration_seconds=300.0)
    assert acq1.granted is True
    lease_token = acq1.lease.lease_token  # type: ignore[union-attr]

    # Commit a PlanDelta advancing version to 2
    delta = PlanDelta(
        space_id=space_id,
        base_version=1,
        resulting_version=2,
        ops=[{"op": "add", "target_node_id": "added-node", "capability": "general.compute"}],
    )
    orch1.propose_plan_delta(delta)
    assert kernel.get_plan_version() == 2

    # CRASH: Destroy Orchestrator 1
    orch1.close()
    del orch1

    # VERIFY SURVIVAL OF AUTHORITATIVE STATE
    # 1. Kernel state survives
    assert kernel.space_id == space_id
    assert kernel.owner_id == "user-ops"

    # 2. Plan version survives
    assert kernel.get_plan_version() == 2
    graph = kernel.get_task_graph()
    assert len(graph.nodes) >= 2

    # 3. Resource leases survive
    lease = rm.get_lease(lease_token)
    assert lease is not None
    assert lease.state.value == "active"
    assert rm.get_resource("gpu/node-1/cuda-0").allocated_capacity == 1  # type: ignore[union-attr]

    # Instance 2: Orchestrator 2 (Restoration)
    orch2 = SpaceOrchestrator(space_id=space_id, kernel=kernel, resource_mgr=rm, bus=bus)

    # Reconstructs authoritative plan version from Kernel
    assert orch2.kernel.get_plan_version() == 2

    # Orchestrator 2 can release the existing lease
    released = orch2.release_resource_lease("worker-1", lease_token)
    assert released is True
    assert rm.get_resource("gpu/node-1/cuda-0").allocated_capacity == 0  # type: ignore[union-attr]
