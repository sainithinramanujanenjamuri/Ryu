"""Harness cases: Space isolation attack resistance.

spec §4 (Space Isolation), Law 1, CONTRACT_MATRIX SPACE-001..003 — Phase 4
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest
from ryu.pulse_bus.bus import PulseBus

from core.capabilities.admission import CapabilityRequest
from core.orchestrator.goal_analyzer import Command
from core.orchestrator.orchestrator import SpaceOrchestrator
from core.plans.delta import PlanDelta
from core.resources.identity import Resource, ResourceIdentity
from core.resources.manager import ResourceManager
from core.space.kernel import SpaceKernel


def test_cross_space_isolation_attacks() -> None:
    """Prompt Section 16: Space Isolation Attack.

    Orchestrator A in Space A attempts to access/mutate:
    1. Plan B
    2. Resource B
    3. Lease B
    4. Approval B
    5. Budget B
    Expected: Every single cross-Space operation is rejected with PermissionError.
    """
    bus = PulseBus()

    kernel_a = SpaceKernel(space_id="space-A", owner_id="user-A", bus=bus, budget=100.0)
    kernel_b = SpaceKernel(space_id="space-B", owner_id="user-B", bus=bus, budget=100.0)
    assert kernel_b.space_id == "space-B"

    rm = ResourceManager(bus=bus)
    # Register GPU strictly in Space B
    gpu_b = ResourceIdentity(resource_type="gpu", provider_id="node-b", instance_id="cuda-b0")
    rm.register_resource(Resource(identity=gpu_b, space_id="space-B", total_capacity=1))

    orch_a = SpaceOrchestrator(space_id="space-A", kernel=kernel_a, resource_mgr=rm, bus=bus)

    # Attack 1: Orchestrator A attempts to submit goal for Space B
    cmd_attack = Command(command_id="cmd-hack", space_id="space-B", objective="Hack space B")
    with pytest.raises(PermissionError, match="Cross-space goal submission rejected"):
        orch_a.submit_goal(cmd_attack)

    # Attack 2: Orchestrator A attempts to commit PlanDelta targeting Space B
    delta_attack = PlanDelta(
        space_id="space-B",
        base_version=1,
        resulting_version=2,
        ops=[{"op": "add", "target_node_id": "hack-node", "capability": "compute"}],
    )
    with pytest.raises(PermissionError, match="Space isolation violation"):
        kernel_a.commit_plan_delta(delta_attack)

    # Attack 3: Orchestrator A attempts to acquire Space B's GPU
    with pytest.raises(PermissionError, match="Cross-space resource access rejected"):
        rm.acquire(space_id="space-A", requester_id="worker-a", identity=gpu_b)

    # Attack 4: Worker A attempts to spend Space B's budget
    req_attack = CapabilityRequest(
        requester_id="worker-a",
        space_id="space-B",
        capability="compute.gpu",
        budget=10.0,
    )
    with pytest.raises(PermissionError, match="Space isolation violation"):
        kernel_a.request_capability(req_attack)

    # Attack 5: Concurrent cross-space attacks
    def attack_worker(i: int) -> bool:
        try:
            kernel_a.verify_space_identity(f"space-B-{i}")
            return True
        except PermissionError:
            return False

    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(attack_worker, i) for i in range(10)]
        results = [f.result() for f in futures]

    assert all(r is False for r in results), "Zero cross-space attacks may succeed"
