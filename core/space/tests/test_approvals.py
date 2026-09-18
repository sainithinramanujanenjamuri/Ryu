"""Unit tests for ApprovalManager, AttentionBudget, and timeout classes.

spec §4 (Space Kernel), §10 (Approval tiers), ROADMAP Phase 2 — Phase 2
"""

from __future__ import annotations

import concurrent.futures

from ryu.pulse_bus.pulse import Pulse

from core.space.approver import ApprovalManager
from core.space.attention import AttentionBudget


class SpyPulseBus:
    def __init__(self) -> None:
        self.published: list[Pulse] = []

    def publish(self, pulse: Pulse) -> Pulse:
        self.published.append(pulse)
        return pulse


def test_single_approver_resolution() -> None:
    mgr = ApprovalManager(default_approver_id="root_admin")
    assert mgr.get_approver_id("space-A") == "root_admin"

    mgr.set_space_approver("space-A", "team_lead_alice")
    assert mgr.get_approver_id("space-A") == "team_lead_alice"


def test_timeout_class_default_deny() -> None:
    bus = SpyPulseBus()
    mgr = ApprovalManager(bus=bus)

    # 10 second timeout
    req = mgr.request_approval(
        request_id="req-1",
        space_id="space-1",
        capability="node.shell",
        timeout_class="default_deny",
        timeout_seconds=10.0,
    )

    # At t + 5s: still pending
    assert mgr.check_timeout("req-1", current_time=req.created_at + 5.0) == "pending"
    assert req.status == "pending"

    # At t + 15s: timed out -> denied
    assert mgr.check_timeout("req-1", current_time=req.created_at + 15.0) == "denied"
    assert req.status == "denied"

    # Published security.grant.denied pulse
    assert len(bus.published) == 1
    assert bus.published[0].type == "security.grant.denied"
    assert bus.published[0].payload["request_id"] == "req-1"


def test_timeout_class_default_hold() -> None:
    bus = SpyPulseBus()
    mgr = ApprovalManager(bus=bus)

    req = mgr.request_approval(
        request_id="req-2",
        space_id="space-1",
        capability="budget.continue",
        timeout_class="default_hold",
        timeout_seconds=10.0,
    )

    # At t + 15s: timed out -> held (not denied, not approved)
    assert mgr.check_timeout("req-2", current_time=req.created_at + 15.0) == "held"
    assert req.status == "held"
    # No pulse published for hold
    assert len(bus.published) == 0


def test_attention_budget_queueing() -> None:
    budget = AttentionBudget(default_limit=3)

    # Submit 3 approvals -> all admitted as active
    assert budget.submit_approval("s1", "app-1") is True
    assert budget.submit_approval("s1", "app-2") is True
    assert budget.submit_approval("s1", "app-3") is True
    assert budget.get_active_count("s1") == 3
    assert budget.get_queued_count("s1") == 0

    # 4th approval -> queued (not dropped)
    assert budget.submit_approval("s1", "app-4") is False
    assert budget.get_active_count("s1") == 3
    assert budget.get_queued_count("s1") == 1

    # 5th approval -> queued
    assert budget.submit_approval("s1", "app-5") is False
    assert budget.get_queued_count("s1") == 2

    # Complete app-1 -> app-4 is dequeued and becomes active
    activated = budget.complete_approval("s1", "app-1")
    assert activated == "app-4"
    assert budget.get_active_count("s1") == 3
    assert budget.get_queued_count("s1") == 1


def test_concurrent_attention_budget_limit() -> None:
    """Invariant: active_open_approvals <= 3 under concurrent submissions."""
    budget = AttentionBudget(default_limit=3)

    results: list[bool] = []

    def submit(req_id: str) -> bool:
        return budget.submit_approval("s-conc", req_id)

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(submit, f"req-{i}") for i in range(20)]
        for f in concurrent.futures.as_completed(futures):
            results.append(f.result())

    # Exactly 3 active, 17 queued
    assert results.count(True) == 3
    assert results.count(False) == 17
    assert budget.get_active_count("s-conc") == 3
    assert budget.get_queued_count("s-conc") == 17

