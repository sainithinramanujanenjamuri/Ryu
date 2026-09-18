"""Unit tests for AdmissionController.

Verifies:
- Pre-dispatch budget checks
- hard_stop mode: $0 budget = 0 dispatches
- exactly one space.budget.exceeded escalation per window_id
- approval_required mode with soft threshold
- degraded mode preference
- zero exceptions raised on denial

spec §4 (Admission Control), KERNEL-001/002/003 — Phase 2
"""

from __future__ import annotations

from typing import Any

from ryu.pulse_bus.pulse import Pulse

from core.capabilities.admission import (
    AdmissionController,
    CapabilityRequest,
)


class SpyPulseBus:
    """Records published pulses without external dependencies."""
    def __init__(self) -> None:
        self.published: list[Pulse] = []

    def publish(self, pulse: Pulse) -> Pulse:
        self.published.append(pulse)
        return pulse


class MockWorkerDispatcher:
    """Spy dispatcher to prove zero provider/tool calls happen on budget denial."""
    def __init__(self, admission: AdmissionController) -> None:
        self.admission = admission
        self.dispatch_count = 0

    def dispatch(self, request: CapabilityRequest) -> dict[str, Any]:
        resp = self.admission.check_admission(request)
        if resp.status != "ok":
            return {"status": resp.status, "error": resp.error}
        # If admitted, execute tool call
        self.dispatch_count += 1
        return {"status": "executed", "result": "success"}


def test_hard_stop_at_zero_budget() -> None:
    bus = SpyPulseBus()
    admission = AdmissionController(bus=bus)
    dispatcher = MockWorkerDispatcher(admission)

    # Set budget = $0
    admission.set_budget("space-zero", budget=0.0, policy_mode="hard_stop")

    req = CapabilityRequest(
        requester_id="agent-1",
        space_id="space-zero",
        capability="browser.navigate",
        params={"url": "https://example.com"},
    )

    # 1. Check admission returns denied without exception
    resp = dispatcher.dispatch(req)
    assert resp["status"] == "denied"
    assert resp["error"] == "budget_exhausted"

    # 2. INVARIANT 1: Zero dispatches occurred
    assert dispatcher.dispatch_count == 0

    # 3. Exactly one escalation pulse emitted
    assert len(bus.published) == 1
    assert bus.published[0].type == "space.budget.exceeded"
    assert bus.published[0].severity == "critical"
    assert bus.published[0].payload["policy_mode"] == "hard_stop"
    assert bus.published[0].payload["remaining_budget"] == 0.0

    # 4. Subsequent requests in same window do not emit duplicate escalations
    for _ in range(5):
        r = dispatcher.dispatch(req)
        assert r["status"] == "denied"

    assert dispatcher.dispatch_count == 0
    # Still only 1 pulse
    assert len(bus.published) == 1


def test_replenishment_mints_new_window_and_admits() -> None:
    bus = SpyPulseBus()
    admission = AdmissionController(bus=bus)
    dispatcher = MockWorkerDispatcher(admission)

    admission.set_budget("space-replenish", budget=0.0, policy_mode="hard_stop")
    req = CapabilityRequest(
        requester_id="agent-1",
        space_id="space-replenish",
        capability="shell.execute",
    )

    # Denied initially
    dispatcher.dispatch(req)
    assert dispatcher.dispatch_count == 0
    assert len(bus.published) == 1

    # Replenish budget
    new_win = admission.replenish_budget("space-replenish", amount=50.0)
    assert new_win is not None

    # Now admitted and dispatched
    r = dispatcher.dispatch(req)
    assert r["status"] == "executed"
    assert dispatcher.dispatch_count == 1


def test_approval_required_mode() -> None:
    bus = SpyPulseBus()
    admission = AdmissionController(bus=bus)

    # Initial $100 budget, soft threshold 80%
    admission.set_budget(
        "space-approval",
        budget=100.0,
        policy_mode="approval_required",
        soft_threshold=0.80,
    )

    req = CapabilityRequest(
        requester_id="agent-1",
        space_id="space-approval",
        capability="llm.generate",
    )

    # 1. Admit initially (spent = 0)
    resp1 = admission.check_admission(req)
    assert resp1.status == "ok"

    # 2. Spend 85 (remaining 15, spent 85% >= 80%)
    admission.record_spend("space-approval", 85.0)

    # 3. New request is denied pending approval
    resp2 = admission.check_admission(req)
    assert resp2.status == "denied"
    assert resp2.error == "approval_required"

    # 4. Emitted warning escalation pulse
    assert len(bus.published) == 1
    assert bus.published[0].type == "space.budget.exceeded"
    assert bus.published[0].severity == "warning"
    assert bus.published[0].payload["policy_mode"] == "approval_required"


def test_degraded_mode() -> None:
    admission = AdmissionController()
    admission.set_budget("space-deg", budget=50.0, policy_mode="degraded")

    req = CapabilityRequest(
        requester_id="agent-1",
        space_id="space-deg",
        capability="llm.generate",
    )
    resp = admission.check_admission(req)
    assert resp.status == "ok"
    assert resp.result == {"degraded": True}

