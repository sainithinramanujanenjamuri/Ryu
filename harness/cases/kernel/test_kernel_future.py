"""Future harness cases: Space Kernel capabilities scheduled for Phase 3+.

spec §4 (Space Kernel), CONTRACT_MATRIX KERNEL-004 through KERNEL-007,
PLAN-005 through PLAN-006 — Phase 3+
"""

from __future__ import annotations

import pytest


def test_kernel_concurrency_lease_enforcement() -> None:
    """KERNEL-004 (Phase 3): Lease enforcement under concurrent access."""
    from ryu.pulse_bus.bus import PulseBus

    from core.resources.identity import Resource, ResourceIdentity
    from core.resources.manager import ResourceManager

    bus = PulseBus()
    mgr = ResourceManager(bus=bus)
    res_id = ResourceIdentity("gpu", "local", "cuda-0")
    mgr.register_resource(Resource(identity=res_id, space_id="space-kernel", total_capacity=1))

    # First lease granted
    res1 = mgr.acquire("space-kernel", "agent-1", res_id)
    assert res1.granted is True

    # Second concurrent lease denied/queued
    res2 = mgr.acquire("space-kernel", "agent-2", res_id)
    assert res2.granted is False
    assert res2.queue_position == 1


def test_kernel_agent_execution_supervision() -> None:
    """KERNEL-005: Agent capability execution is supervised by Space Kernel Admission."""
    from ryu.pulse_bus.bus import PulseBus

    from agents.base import BaseAgent
    from core.capabilities.admission import CapabilityRequest
    from core.space.kernel import SpaceKernel
    from llm.provider import MockLLMProvider

    bus = PulseBus()
    kernel = SpaceKernel(space_id="space-supervise", owner_id="user-1", bus=bus)
    kernel.admission.set_budget("space-supervise", budget=50.0, policy_mode="hard_stop")

    agent = BaseAgent(
        agent_id="agent-supervised-1",
        space_id="space-supervise",
        provider=MockLLMProvider(),
        allowed_capabilities={"fs.read", "compute.read", "metrics.read"},
    )

    # 1. Valid proposal by agent
    proposal = agent.step("task-1", "Inspect system health")
    assert proposal.is_valid is True

    # 2. Kernel supervises execution: Admitted within budget
    admit_req = CapabilityRequest(
        capability="compute.read",
        requester_id=agent.agent_id,
        space_id=agent.space_id,
    )
    resp = kernel.request_capability(admit_req)
    assert resp.status == "ok"

    # 3. Kernel blocks unbudgeted or cross-space execution
    cross_space_req = CapabilityRequest(
        capability="compute.read",
        requester_id=agent.agent_id,
        space_id="foreign-space",
    )
    with pytest.raises(PermissionError):
        kernel.request_capability(cross_space_req)


def test_kernel_checkpoint_restore() -> None:
    """KERNEL-006: Checkpoint creation and restoration."""
    from ryu.pulse_bus.bus import PulseBus

    from core.plans.task_graph import TaskNode
    from core.space.kernel import SpaceKernel

    bus = PulseBus()
    kernel = SpaceKernel(space_id="space-chk", owner_id="user-1", bus=bus)
    graph = kernel.get_task_graph()
    graph.nodes.append(TaskNode(id="task-1", capability="compute.cpu", params={"units": 2}))

    # Create checkpoint
    checkpoint = kernel.create_checkpoint(checkpoint_id="chk-001")
    assert checkpoint["checkpoint_id"] == "chk-001"
    assert checkpoint["plan_version"] == 1
    assert len(checkpoint["nodes"]) == 1

    # Simulate restore in new kernel instance
    restored_kernel = SpaceKernel(space_id="space-chk", owner_id="user-1", bus=bus)
    restored_kernel.restore_checkpoint(checkpoint)

    assert restored_kernel.get_plan_version() == 1
    restored_nodes = restored_kernel.get_task_graph().nodes
    assert len(restored_nodes) == 1
    assert restored_nodes[0].id == "task-1"
    assert restored_nodes[0].capability == "compute.cpu"
