"""Harness cases: Space Kernel admission control, budget enforcement, and plan versioning.

spec §4 (Admission Control), §16 (TaskGraph & PlanDelta),
CONTRACT_MATRIX KERNEL-001, KERNEL-002, KERNEL-003, PLAN-001, PLAN-003 — Phase 2
"""

from __future__ import annotations

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


def test_kernel_pre_dispatch_admission() -> None:
    """KERNEL-001: Every CapabilityRequest must pass pre-dispatch admission check."""
    bus = SpyPulseBus()
    kernel = SpaceKernel(space_id="space-k1", owner_id="user-1", bus=bus, budget=10.0)

    req = CapabilityRequest(
        requester_id="agent-1",
        space_id="space-k1",
        capability="browser.navigate",
        params={"url": "https://example.com"},
    )
    resp = kernel.request_capability(req)
    assert resp.status == "ok"


def test_kernel_hard_stop_at_zero_budget() -> None:
    """KERNEL-002: Hard stop at $0 budget causes zero dispatches and returns denied."""
    bus = SpyPulseBus()
    kernel = SpaceKernel(space_id="space-k2", owner_id="user-1", bus=bus, budget=0.0)

    req = CapabilityRequest(
        requester_id="agent-1",
        space_id="space-k2",
        capability="shell.execute",
        params={"cmd": "ls"},
    )
    resp = kernel.request_capability(req)
    assert resp.status == "denied"
    assert resp.error == "budget_exhausted"


def test_kernel_single_escalation_per_window() -> None:
    """KERNEL-003: Exactly one space.budget.exceeded escalation Pulse per window_id."""
    bus = SpyPulseBus()
    kernel = SpaceKernel(space_id="space-k3", owner_id="user-1", bus=bus, budget=0.0)

    req = CapabilityRequest(
        requester_id="agent-1",
        space_id="space-k3",
        capability="api.call",
    )

    # Issue 10 denied requests
    for _ in range(10):
        resp = kernel.request_capability(req)
        assert resp.status == "denied"

    # Must produce exactly one space.budget.exceeded pulse
    escalations = [p for p in bus.published if p.type == "space.budget.exceeded"]
    assert len(escalations) == 1
    assert escalations[0].severity == "critical"
    assert escalations[0].payload["policy_mode"] == "hard_stop"


def test_plan_cas_versioning() -> None:
    """PLAN-001: Plan CAS atomically increments plan_version on match."""
    bus = SpyPulseBus()
    kernel = SpaceKernel(space_id="space-p1", owner_id="user-1", bus=bus, budget=100.0)

    assert kernel.get_plan_version() == 1

    delta = PlanDelta(
        space_id="space-p1",
        base_version=1,
        resulting_version=2,
        ops=[{"op": "add", "target_node_id": "task-1", "capability": "python.eval"}],
    )
    success, new_ver, winning_id = kernel.commit_plan_delta(delta)
    assert success is True
    assert new_ver == 2
    assert winning_id is None
    assert kernel.get_plan_version() == 2


def test_plan_superseded_notification() -> None:
    """PLAN-003: Stale base_version rejects commit and publishes plan.version.superseded."""
    bus = SpyPulseBus()
    kernel = SpaceKernel(space_id="space-p2", owner_id="user-1", bus=bus, budget=100.0)

    # First commit advances 1 -> 2
    delta1 = PlanDelta(space_id="space-p2", base_version=1, resulting_version=2, ops=[])
    kernel.commit_plan_delta(delta1)
    assert kernel.get_plan_version() == 2

    # Second commit attempts stale base 1 -> 2
    delta2 = PlanDelta(space_id="space-p2", base_version=1, resulting_version=2, ops=[])
    success, current_ver, winning_id = kernel.commit_plan_delta(delta2)

    assert success is False
    assert current_ver == 2
    assert winning_id == delta1.delta_id

    superseded_pulses = [p for p in bus.published if p.type == "plan.version.superseded"]
    assert len(superseded_pulses) == 1
    assert superseded_pulses[0].payload["superseded_version"] == 1
    assert superseded_pulses[0].payload["current_version"] == 2
    assert superseded_pulses[0].payload["winning_delta_id"] == delta1.delta_id

