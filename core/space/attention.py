"""Attention Budget: throttles concurrent human approval notifications.

Enforces configurable max N concurrent active approvals per Space (default N=3).
Excess approval requests are queued, preventing human notification floods (docs/Architecture §4).
Subordinated to Space Orchestrator as a policy constraint (ADR-0025).

spec §4 (Space Kernel), §10 (Attention budget), ROADMAP Phase 8 — Phase 8
"""

from __future__ import annotations

import collections
import threading
from typing import Any, Callable

DEFAULT_ATTENTION_LIMIT = 3


class AttentionBudget:
    """
    Guarantees active_open_approvals <= N per Space under concurrency.

    When the active limit is reached, subsequent requests are placed into a prioritized FIFO queue
    rather than flooding human channels. Reports saturation state to Space Orchestrator.
    """

    def __init__(self, default_limit: int = DEFAULT_ATTENTION_LIMIT) -> None:
        self.default_limit = default_limit
        self._lock = threading.Lock()
        self._limits: dict[str, int] = {}
        self._active: dict[str, set[str]] = collections.defaultdict(set)
        # Queues per space partitioned by priority class (1=Urgent/Held, 2=Standard, 3=Low)
        self._queues: dict[str, dict[int, collections.deque[str]]] = collections.defaultdict(
            lambda: {1: collections.deque(), 2: collections.deque(), 3: collections.deque()}
        )
        self._resolved: dict[str, set[str]] = collections.defaultdict(set)

    def set_limit(
        self,
        space_id: str,
        limit: int,
        on_activated: Callable[[str], None] | None = None,
    ) -> list[str]:
        """Configure maximum concurrent active approvals for a Space and activate queued items if expanded."""
        if limit <= 0:
            raise ValueError(f"Attention budget limit must be positive, got {limit}")
        activated: list[str] = []
        with self._lock:
            self._limits[space_id] = limit
            active_set = self._active[space_id]
            while len(active_set) < limit:
                next_req = self._pop_next_queued(space_id)
                if next_req is None:
                    break
                active_set.add(next_req)
                activated.append(next_req)
                if on_activated is not None:
                    on_activated(next_req)
        return activated

    def get_limit(self, space_id: str) -> int:
        """Return the attention budget limit for a Space."""
        with self._lock:
            return self._limits.get(space_id, self.default_limit)

    def submit_approval(self, space_id: str, request_id: str, priority_class: int = 2) -> bool:
        """
        Submit an approval request into the attention budget.

        Returns:
            True if immediately admitted to active set,
            False if queued due to attention limit exhaustion.
        """
        if priority_class not in (1, 2, 3):
            priority_class = 2

        with self._lock:
            limit = self._limits.get(space_id, self.default_limit)
            active_set = self._active[space_id]

            # Coalesce / deduplicate if already active
            if request_id in active_set:
                return True

            # Coalesce if already in queue
            space_q = self._queues[space_id]
            for q in space_q.values():
                if request_id in q:
                    return False

            if len(active_set) < limit:
                active_set.add(request_id)
                return True
            else:
                space_q[priority_class].append(request_id)
                return False

    def complete_approval(
        self,
        space_id: str,
        request_id: str,
        on_activated: Callable[[str], None] | None = None,
    ) -> str | None:
        """
        Record completion of an active approval request.
        If requests are queued, dequeues the highest priority request and marks it active.

        Returns:
            request_id of newly activated request (if any), otherwise None.
        """
        with self._lock:
            active_set = self._active[space_id]
            if request_id in active_set:
                active_set.remove(request_id)
                self._resolved[space_id].add(request_id)

            limit = self._limits.get(space_id, self.default_limit)
            if len(active_set) < limit:
                next_req = self._pop_next_queued(space_id)
                if next_req is not None:
                    active_set.add(next_req)
                    if on_activated is not None:
                        on_activated(next_req)
                    return next_req

            return None

    def _pop_next_queued(self, space_id: str) -> str | None:
        """Pop the next request from the highest non-empty priority queue (must hold lock)."""
        space_q = self._queues[space_id]
        for p in (1, 2, 3):
            if space_q[p]:
                return space_q[p].popleft()
        return None

    def is_saturated(self, space_id: str) -> bool:
        """Return True if active approvals currently equal or exceed the limit."""
        with self._lock:
            limit = self._limits.get(space_id, self.default_limit)
            return len(self._active[space_id]) >= limit

    def get_state_report(self, space_id: str) -> dict[str, Any]:
        """Return a structured report of attention budget saturation for the Orchestrator."""
        with self._lock:
            limit = self._limits.get(space_id, self.default_limit)
            active_cnt = len(self._active[space_id])
            queued_cnt = sum(len(q) for q in self._queues[space_id].values())
            return {
                "space_id": space_id,
                "limit": limit,
                "active_count": active_cnt,
                "queued_count": queued_cnt,
                "is_saturated": active_cnt >= limit,
            }

    def get_active_count(self, space_id: str) -> int:
        """Return number of currently active approvals for a Space."""
        with self._lock:
            return len(self._active[space_id])

    def get_queued_count(self, space_id: str) -> int:
        """Return number of currently queued approvals for a Space."""
        with self._lock:
            return sum(len(q) for q in self._queues[space_id].values())

    def get_resolved_count(self, space_id: str) -> int:
        """Return number of resolved approvals for a Space."""
        with self._lock:
            return len(self._resolved[space_id])
