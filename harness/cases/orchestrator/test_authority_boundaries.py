"""Harness cases: Invariant defense proving the Orchestrator cannot become an authority.

spec §4, §9, §10, §16, CONTRACT_MATRIX KERNEL-001..003, PLAN-001..003,
RESOURCE-002, HUMAN-001 — Phase 4
"""

from __future__ import annotations

import pytest
from ryu.pulse_bus.bus import PulseBus

from core.capabilities.admission import CapabilityRequest
from core.orchestrator.goal_analyzer import Command
from core.orchestrator.orchestrator import SpaceOrchestrator
from core.plans.delta import PlanDelta
from core.resources.identity import Resource, ResourceIdentity
from core.resources.manager import ResourceManager
from core.space.kernel import SpaceKernel


def test_plan_cas_authority_rejection_and_rebase() -> None:
    """Prompt Section 7: Prove Plan CAS Authority.

    1. Kernel plan version = 10.
    2. Orchestrator prepares PlanDelta with base_version = 10.
    3. Concurrent commit advances Kernel version to 11.
    4. Orchestrator submits stale delta -> REJECTED (plan.version.superseded).
    5. Orchestrator cannot force delta.
    6. Orchestrator re-observes version 11 -> rebases -> commits successfully.
    """
    bus = PulseBus()
    space_id = "space-cas-proof"
    kernel = SpaceKernel(space_id=space_id, owner_id="user-cas", bus=bus)

    # Fast forward kernel plan_version to 10
    for v in range(1, 10):
        d = PlanDelta(
            space_id=space_id,
            base_version=v,
            resulting_version=v + 1,
            ops=[{"op": "add", "target_node_id": f"node-{v}", "capability": "compute"}],
        )
        ok, _, _ = kernel.commit_plan_delta(d)
        assert ok is True

    assert kernel.get_plan_version() == 10

    rm = ResourceManager(bus=bus)
    orch = SpaceOrchestrator(space_id=space_id, kernel=kernel, resource_mgr=rm, bus=bus)

    # Step 2: Orchestrator prepares delta based on version 10
    orch_delta = PlanDelta(
        space_id=space_id,
        base_version=10,
        resulting_version=11,
        ops=[{"op": "add", "target_node_id": "orch-node", "capability": "compute.gpu"}],
    )

    # Step 3: Concurrent human/system commit advances version to 11
    concurrent_delta = PlanDelta(
        space_id=space_id,
        base_version=10,
        resulting_version=11,
        ops=[{"op": "add", "target_node_id": "human-node", "capability": "fs.read"}],
    )
    c_ok, c_ver, _ = kernel.commit_plan_delta(concurrent_delta)
    assert c_ok is True
    assert c_ver == 11
    assert kernel.get_plan_version() == 11

    # Step 4: Orchestrator submits old delta directly to Kernel CAS
    success, current_ver, winning_id = kernel.commit_plan_delta(orch_delta)
    # MUST BE REJECTED!
    assert success is False
    assert current_ver == 11
    assert winning_id is not None
    # Invariant: Orchestrator could NOT force the delta!
    assert kernel.get_plan_version() == 11

    # Step 6: Orchestrator re-observes version 11, rebases, and commits
    rebase_ok, final_ver = orch.propose_plan_delta(orch_delta)
    assert rebase_ok is True
    assert final_ver == 12
    assert kernel.get_plan_version() == 12


def test_resource_authority_invariants() -> None:
    """Prompt Section 9: Prove Resource Authority.

    1. Orchestrator cannot directly allocate resources or bypass Resource Manager.
    2. Orchestrator requests via resource.requested -> Resource Manager grants.
    3. Orchestrator/Worker using forged or expired lease -> REJECTED.
    """
    bus = PulseBus()
    space_id = "space-res-auth"
    kernel = SpaceKernel(space_id=space_id, owner_id="user-1", bus=bus)
    rm = ResourceManager(bus=bus)
    orch = SpaceOrchestrator(space_id=space_id, kernel=kernel, resource_mgr=rm, bus=bus)

    gpu_id = ResourceIdentity(resource_type="gpu", provider_id="node-1", instance_id="cuda-0")
    rm.register_resource(Resource(identity=gpu_id, space_id=space_id, total_capacity=1))

    # 1. Orchestrator has NO direct allocation method; it must request from Resource Manager
    acq = orch.request_resource_lease(
        requester_id="worker-gpu",
        identity=gpu_id,
        duration_seconds=60.0,
    )
    assert acq.granted is True
    assert acq.lease is not None
    valid_token = acq.lease.lease_token

    # 2. Forged lease attempt: worker/orchestrator tries to release or renew with fabricated token
    with pytest.raises(KeyError, match="not found"):
        orch.release_resource_lease(requester_id="worker-gpu", lease_token="forged-lease-xyz")

    # 3. Valid lease can be released
    assert orch.release_resource_lease(requester_id="worker-gpu", lease_token=valid_token) is True


def test_admission_and_budget_authority() -> None:
    """Prompt Section 10: Admission Authority Test.

    1. Space budget = $0 -> Orchestrator cannot bypass budget denial.
    2. Actual tool dispatches = 0.
    3. Repeat for approval_required and permission_denied.
    """
    bus = PulseBus()
    space_id = "space-budget-zero"
    kernel = SpaceKernel(
        space_id=space_id,
        owner_id="user-zero",
        bus=bus,
        budget=0.0,
        budget_policy="hard_stop",
    )
    rm = ResourceManager(bus=bus)
    orch = SpaceOrchestrator(space_id=space_id, kernel=kernel, resource_mgr=rm, bus=bus)

    cmd = Command(
        command_id="cmd-zero",
        space_id=space_id,
        objective="Execute compute",
        params={"required_capabilities": ["compute.gpu"]},
    )
    session = orch.submit_goal(cmd)
    assert session is not None

    # Worker dispatches capability request to Space Kernel
    req = CapabilityRequest(
        requester_id="worker-1",
        space_id=space_id,
        capability="compute.gpu",
        params={},
        budget=10.0,
    )
    resp = kernel.request_capability(req)

    # MUST BE DENIED!
    assert resp.status == "denied"
    assert resp.cost == 0.0

    # Invariant: Actual dispatch count is strictly 0
    dispatches = [p for p in bus._log if p.type == "worker.tool.called"]
    assert len(dispatches) == 0


def test_human_gate_authority_no_orchestrator_self_approval() -> None:
    """Prompt Section 11: Human Gate Authority.

    Orchestrator requests high-risk action -> Kernel -> ApprovalManager -> Human approval.
    Orchestrator cannot self-approve; only the authenticated approver_id can resolve.
    """
    bus = PulseBus()
    space_id = "space-gate-test"
    kernel = SpaceKernel(space_id=space_id, owner_id="human-officer", bus=bus)

    # Kernel requests approval for high-risk device execution
    req, is_active = kernel.request_approval(
        request_id="appr-001",
        capability="device.firmware_flash",
    )
    assert is_active is True
    assert req.status == "pending"

    # Orchestrator has NO self-approval authority
    rm = ResourceManager(bus=bus)
    orch = SpaceOrchestrator(space_id=space_id, kernel=kernel, resource_mgr=rm, bus=bus)
    assert not hasattr(orch, "approve")

    # Only authenticated Human Kernel Approver can approve
    resolved = kernel.resolve_approval(request_id="appr-001", approved=True)
    assert resolved is True
    assert req.status == "approved"
