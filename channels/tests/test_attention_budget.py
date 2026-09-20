"""Unit tests for AttentionBudget, priority queuing, and saturation reporting.

spec §4, §10, ROADMAP Phase 8, ADR-0025 — Phase 8
"""

import pytest
from core.space.attention import AttentionBudget


def test_attention_budget_limit_and_queuing():
    budget = AttentionBudget(default_limit=2)
    space_id = "space-test"

    # Submit 2 -> both active
    assert budget.submit_approval(space_id, "app-1") is True
    assert budget.submit_approval(space_id, "app-2") is True
    assert budget.is_saturated(space_id) is True
    assert budget.get_active_count(space_id) == 2
    assert budget.get_queued_count(space_id) == 0

    # Submit 3rd -> queued
    assert budget.submit_approval(space_id, "app-3") is False
    assert budget.get_queued_count(space_id) == 1

    report = budget.get_state_report(space_id)
    assert report["is_saturated"] is True
    assert report["active_count"] == 2
    assert report["queued_count"] == 1


def test_priority_queuing_order():
    budget = AttentionBudget(default_limit=1)
    space_id = "space-prio"

    # Fill active slot
    budget.submit_approval(space_id, "active-1")

    # Queue Class 3 (Low), then Class 1 (Urgent/Held), then Class 2 (Standard)
    budget.submit_approval(space_id, "low-1", priority_class=3)
    budget.submit_approval(space_id, "urgent-1", priority_class=1)
    budget.submit_approval(space_id, "standard-1", priority_class=2)

    assert budget.get_queued_count(space_id) == 3

    # Complete active-1 -> next activated must be urgent-1 (Class 1)
    next_up = budget.complete_approval(space_id, "active-1")
    assert next_up == "urgent-1"

    # Complete urgent-1 -> next activated must be standard-1 (Class 2)
    next_up = budget.complete_approval(space_id, "urgent-1")
    assert next_up == "standard-1"

    # Complete standard-1 -> next activated must be low-1 (Class 3)
    next_up = budget.complete_approval(space_id, "standard-1")
    assert next_up == "low-1"


def test_dynamic_limit_expansion():
    budget = AttentionBudget(default_limit=1)
    space_id = "space-expand"

    budget.submit_approval(space_id, "app-1")
    budget.submit_approval(space_id, "app-2")
    budget.submit_approval(space_id, "app-3")

    assert budget.get_active_count(space_id) == 1
    assert budget.get_queued_count(space_id) == 2

    # Expand limit to 3 -> both queued items activated
    activated = budget.set_limit(space_id, 3)
    assert activated == ["app-2", "app-3"]
    assert budget.get_active_count(space_id) == 3
    assert budget.get_queued_count(space_id) == 0

