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
    pytest.skip("spec §4, SPACE-003 — Phase 4: Space Kernel and Agent runtime not implemented.")


def test_space_artifact_isolation() -> None:
    pytest.skip("spec §4, SPACE-004 — Phase 10: Space Memory not implemented.")
