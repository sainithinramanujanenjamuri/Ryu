"""Unit tests for EscalationWindowManager.

Verifies:
- window creation
- acknowledgment
- replenishment
- duplicate suppression
- concurrent request safety

spec §4 (Admission Control), KERNEL-003 — Phase 2
"""

from __future__ import annotations

import concurrent.futures

from core.capabilities.windows import EscalationWindowManager


def test_window_creation() -> None:
    wm = EscalationWindowManager()
    win1 = wm.get_or_create_window("space-1")
    assert win1.startswith("win-space-1-")
    # Subsequent call returns same window
    assert wm.get_or_create_window("space-1") == win1


def test_acknowledgement_mints_new_window() -> None:
    wm = EscalationWindowManager()
    win1 = wm.get_or_create_window("space-1")
    win2 = wm.acknowledge("space-1", "approver-1")
    assert win2.startswith("win-space-1-")
    assert win2 != win1
    assert wm.get_or_create_window("space-1") == win2


def test_replenishment_mints_new_window() -> None:
    wm = EscalationWindowManager()
    win1 = wm.get_or_create_window("space-1")
    win2 = wm.replenish("space-1", 100.0)
    assert win2.startswith("win-space-1-")
    assert win2 != win1


def test_duplicate_suppression_within_window() -> None:
    wm = EscalationWindowManager()
    win1 = wm.get_or_create_window("space-1")
    assert wm.should_escalate_budget("space-1", win1) is True
    assert wm.should_escalate_budget("space-1", win1) is False
    assert wm.should_escalate_budget("space-1", win1) is False

    # After acknowledgment, new window can escalate once again
    win2 = wm.acknowledge("space-1", "approver-1")
    assert wm.should_escalate_budget("space-1", win2) is True
    assert wm.should_escalate_budget("space-1", win2) is False


def test_concurrent_duplicate_suppression() -> None:
    wm = EscalationWindowManager()
    win1 = wm.get_or_create_window("space-conc")

    results: list[bool] = []

    def try_escalate() -> bool:
        return wm.should_escalate_budget("space-conc", win1)

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(try_escalate) for _ in range(50)]
        for f in concurrent.futures.as_completed(futures):
            results.append(f.result())

    # Exactly one thread must have succeeded
    assert results.count(True) == 1
    assert results.count(False) == 49

