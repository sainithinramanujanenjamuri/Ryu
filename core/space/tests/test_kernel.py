"""Unit tests for SpaceKernel coordination, isolation, and taint canary.

spec §4 (Space Kernel), SPACE-001/006, TAINT-005 — Phase 2
"""

from __future__ import annotations

import pytest
from ryu.pulse_bus.pulse import Pulse

from core.capabilities.admission import CapabilityRequest
from core.plans.delta import PlanDelta
from core.space.kernel import SpaceKernel


class SpyPulseBus:
    def __init__(self) -> None:
        self.published: list[Pulse] = []

    def publish(self, pulse: Pulse) -> Pulse:
        self.published.append(pulse)
        return pulse


def test_space_kernel_initialization_and_pulse() -> None:
    bus = SpyPulseBus()
    kernel = SpaceKernel(space_id="space-main", owner_id="user-1", bus=bus, budget=100.0)

    assert kernel.space_id == "space-main"
    assert kernel.owner_id == "user-1"
    assert kernel.get_plan_version() == 1

    # Published space.created
    assert len(bus.published) == 1
    assert bus.published[0].type == "space.created"
    assert bus.published[0].payload["space_id"] == "space-main"
    assert bus.published[0].payload["owner_id"] == "user-1"


def test_space_isolation_boundary_enforcement() -> None:
    """SPACE-001 / SPACE-006: Kernel strictly denies cross-space operations."""
    kernel = SpaceKernel(space_id="space-A", owner_id="user-A")

    # 1. Cross-space capability request
    req = CapabilityRequest(
        requester_id="agent-alien",
        space_id="space-B",  # different space
        capability="browse",
    )
    with pytest.raises(PermissionError) as exc:
        kernel.request_capability(req)
    assert "SPACE-001" in str(exc.value)

    # 2. Cross-space plan delta
    delta = PlanDelta(
        space_id="space-B",
        base_version=1,
        resulting_version=2,
        ops=[],
    )
    with pytest.raises(PermissionError) as exc:
        kernel.commit_plan_delta(delta)
    assert "SPACE-001" in str(exc.value)


def test_taint_injection_canary_denies_grant() -> None:
    """TAINT-005: Tainted execution context attempting security grant is blocked and denied."""
    bus = SpyPulseBus()
    kernel = SpaceKernel(space_id="space-taint", owner_id="user-1", bus=bus, budget=100.0)

    # Capability request attempting security grant from tainted source
    req = CapabilityRequest(
        requester_id="adversary-payload",
        space_id="space-taint",
        capability="security.grant.approve_admin",
    )

    resp = kernel.request_capability(req, is_tainted=True)
    assert resp.status == "denied"
    assert resp.error == "tainted_security_grant_blocked"

    # Verified security.grant.denied pulse published with taint=True
    denied_pulses = [p for p in bus.published if p.type == "security.grant.denied"]
    assert len(denied_pulses) == 1
    assert denied_pulses[0].taint is True
    assert denied_pulses[0].payload["capability"] == "security.grant.approve_admin"
    assert "TAINT-005" in denied_pulses[0].payload["reason"]


def test_kernel_approval_and_attention_coordination() -> None:
    kernel = SpaceKernel(
        space_id="space-coord",
        owner_id="admin-1",
        budget=100.0,
        attention_limit=2,
    )

    req1, active1 = kernel.request_approval("app-1", "shell.run")
    assert active1 is True

    req2, active2 = kernel.request_approval("app-2", "db.write")
    assert active2 is True

    # 3rd request exceeds attention limit 2 -> queued
    req3, active3 = kernel.request_approval("app-3", "browser.run")
    assert active3 is False

    # Resolve app-1 -> app-3 automatically dequeued
    kernel.resolve_approval("app-1", approved=True)
    assert kernel.attention.get_active_count("space-coord") == 2
    assert kernel.attention.get_queued_count("space-coord") == 0

