"""Escalation window manager for Space budget enforcement.

Tracks escalation window identity and prevents duplicate escalation Pulses
within the same window (KERNEL-003, docs/Architecture §4).

spec §4 (Admission Control), §16 (space.budget.exceeded) — Phase 2
"""

from __future__ import annotations

import threading
import uuid


class EscalationWindowManager:
    """
    Manages active window_id per Space and enforces single-escalation semantics.

    Thread-safe to prevent race conditions during concurrent capability requests.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._windows: dict[str, str] = {}
        self._escalated: set[tuple[str, str]] = set()

    def get_or_create_window(self, space_id: str) -> str:
        """Get the currently active window_id for a Space, minting one if absent."""
        with self._lock:
            if space_id not in self._windows:
                self._windows[space_id] = f"win-{space_id}-{uuid.uuid4().hex[:8]}"
            return self._windows[space_id]

    def should_escalate_budget(self, space_id: str, window_id: str) -> bool:
        """
        Atomically test-and-set the escalation flag for a (space_id, window_id).

        Returns True on the first call for this window, False on subsequent calls.
        """
        with self._lock:
            key = (space_id, window_id)
            if key in self._escalated:
                return False
            self._escalated.add(key)
            return True

    def acknowledge(self, space_id: str, approver_id: str) -> str:
        """Human acknowledgment mints a new window_id and resets escalation state."""
        with self._lock:
            new_window = f"win-{space_id}-{uuid.uuid4().hex[:8]}"
            self._windows[space_id] = new_window
            return new_window

    def replenish(self, space_id: str, amount: float) -> str:
        """Budget replenishment mints a new window_id and resets escalation state."""
        with self._lock:
            new_window = f"win-{space_id}-{uuid.uuid4().hex[:8]}"
            self._windows[space_id] = new_window
            return new_window

