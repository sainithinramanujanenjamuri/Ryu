"""Attention Budget: throttles concurrent human approval notifications.

Enforces a locked default of max N=3 concurrent active approvals per Space.
Excess approval requests are queued, preventing human notification floods (docs/Architecture §4).

spec §4 (Space Kernel), ROADMAP Phase 2 — Phase 2
"""

from __future__ import annotations

import collections
import threading
from typing import Callable

DEFAULT_ATTENTION_LIMIT = 3


class AttentionBudget:
    """
    Guarantees active_open_approvals <= N per Space under concurrency.

    When the active limit is reached, subsequent requests are placed into a FIFO queue
    rather than flooding human channels.
    """

    def __init__(self, default_limit: int = DEFAULT_ATTENTION_LIMIT) -> None:
        self.default_limit = default_limit
        self._lock = threading.Lock()
        self._limits: dict[str, int] = {}
        self._active: dict[str, set[str]] = collections.defaultdict(set)
        self._queues: dict[str, collections.deque[str]] = collections.defaultdict(collections.deque)
        self._resolved: dict[str, set[str]] = collections.defaultdict(set)

    def set_limit(self, space_id: str, limit: int) -> None:
        """Configure maximum concurrent active approvals for a Space."""
        if limit <= 0:
            raise ValueError(f"Attention budget limit must be positive, got {limit}")
        with self._lock:
            self._limits[space_id] = limit

    def get_limit(self, space_id: str) -> int:
        """Return the attention budget limit for a Space."""
        with self._lock:
            return self._limits.get(space_id, self.default_limit)

    def submit_approval(self, space_id: str, request_id: str) -> bool:
        """
        Submit an approval request into the attention budget.

        Returns:
            True if immediately admitted to active set,
            False if queued due to attention limit exhaustion.
        """
        with self._lock:
            limit = self._limits.get(space_id, self.default_limit)
            active_set = self._active[space_id]

            if len(active_set) < limit:
                active_set.add(request_id)
                return True
            else:
                self._queues[space_id].append(request_id)
                return False

    def complete_approval(
        self,
        space_id: str,
        request_id: str,
        on_activated: Callable[[str], None] | None = None,
    ) -> str | None:
        """
        Record completion of an active approval request.

        If requests are queued, dequeues the next request and marks it active.

        Returns:
            request_id of newly activated request (if any), otherwise None.
        """
        with self._lock:
            active_set = self._active[space_id]
            if request_id in active_set:
                active_set.remove(request_id)
                self._resolved[space_id].add(request_id)

            queue = self._queues[space_id]
            limit = self._limits.get(space_id, self.default_limit)

            if queue and len(active_set) < limit:
                next_req = queue.popleft()
                active_set.add(next_req)
                if on_activated is not None:
                    on_activated(next_req)
                return next_req

            return None

    def get_active_count(self, space_id: str) -> int:
        """Return number of currently active approvals for a Space."""
        with self._lock:
            return len(self._active[space_id])

    def get_queued_count(self, space_id: str) -> int:
        """Return number of currently queued approvals for a Space."""
        with self._lock:
            return len(self._queues[space_id])

    def get_resolved_count(self, space_id: str) -> int:
        """Return number of resolved approvals for a Space."""
        with self._lock:
            return len(self._resolved[space_id])

