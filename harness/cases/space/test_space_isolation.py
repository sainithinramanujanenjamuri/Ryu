"""Harness cases: Space isolation and boundary contracts (SPACE-001, SPACE-006).

spec §4 (Space Kernel), CONTRACT_MATRIX SPACE-001, SPACE-006 — Phase 2
"""

from __future__ import annotations

import pytest

from core.capabilities.admission import CapabilityRequest
from core.plans.delta import PlanDelta
from core.space.kernel import SpaceKernel


def test_space_isolation_cross_space_access_denied() -> None:
    """SPACE-001: Kernel strictly rejects cross-space operations."""
    kernel_a = SpaceKernel(space_id="space-alpha", owner_id="user-1")

    # Attempt cross-space capability request
    req = CapabilityRequest(
        requester_id="agent-1",
        space_id="space-beta",  # mismatch
        capability="browser.navigate",
    )
    with pytest.raises(PermissionError) as exc:
        kernel_a.request_capability(req)
    assert "SPACE-001" in str(exc.value)

    # Attempt cross-space PlanDelta commit
    delta = PlanDelta(
        space_id="space-beta",  # mismatch
        base_version=1,
        resulting_version=2,
        ops=[],
    )
    with pytest.raises(PermissionError) as exc:
        kernel_a.commit_plan_delta(delta)
    assert "SPACE-001" in str(exc.value)


def test_space_identity_enforcement() -> None:
    """SPACE-006: Every execution has an unambiguous Space identity."""
    kernel = SpaceKernel(space_id="space-ident-42", owner_id="owner-99")
    assert kernel.space_id == "space-ident-42"
    assert kernel.owner_id == "owner-99"

    # Operation with matching space_id succeeds
    req = CapabilityRequest(
        requester_id="agent-1",
        space_id="space-ident-42",
        capability="browse",
    )
    # verify_space_identity passes cleanly
    kernel.verify_space_identity(req.space_id)

