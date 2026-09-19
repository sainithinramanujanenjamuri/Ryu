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
    pytest.skip("spec §4, KERNEL-005 — Phase 5: Agent execution supervision not implemented.")


def test_kernel_checkpoint_restore() -> None:
    pytest.skip("spec §4, KERNEL-006 — Phase 4: Checkpoint restoration not implemented.")
