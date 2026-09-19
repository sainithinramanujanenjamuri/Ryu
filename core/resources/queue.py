"""Resource contention queue with FIFO, Priority, and Anti-Starvation Aging.

spec §9 (Resource Manager), ROADMAP Phase 3, ADR-0005, CONTRACT_MATRIX RESOURCE-006 — Phase 3
"""

from __future__ import annotations

import itertools
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from core.resources.identity import ResourceIdentity


class QueueDiscipline(str, Enum):
    """Queue ordering policies."""

    FIFO = "fifo"
    PRIORITY_FIFO = "priority_fifo"


_seq_counter = itertools.count(1)


@dataclass
class QueuedRequest:
    """A waiting request queued for a contested resource."""

    request_id: str
    resource_id: ResourceIdentity
    space_id: str
    requester_id: str
    units: int = 1
    priority: int = 0
    duration_seconds: float = 60.0
    idempotency_key: str | None = None
    requested_at: datetime = field(default_factory=datetime.utcnow)
    sequence: int = field(default_factory=lambda: next(_seq_counter))
    bypass_count: int = 0
    cancelled: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


class ResourceQueue:
    """Thread-safe queue managing pending resource acquisition requests."""

    def __init__(
        self,
        resource_id: ResourceIdentity,
        discipline: QueueDiscipline = QueueDiscipline.FIFO,
    ) -> None:
        self.resource_id = resource_id
        self.discipline = discipline
        self._lock = threading.RLock()
        self._queue: list[QueuedRequest] = []

    def enqueue(
        self,
        space_id: str,
        requester_id: str,
        units: int = 1,
        priority: int = 0,
        duration_seconds: float = 60.0,
        idempotency_key: str | None = None,
        request_id: str | None = None,
        requested_at: datetime | None = None,
    ) -> tuple[QueuedRequest, int]:
        """Enqueue a request and return (QueuedRequest, queue_position: 1-based index)."""
        with self._lock:
            # Check if this idempotency key is already queued
            if idempotency_key:
                for idx, existing in enumerate(self._queue):
                    if (
                        not existing.cancelled
                        and existing.space_id == space_id
                        and existing.requester_id == requester_id
                        and existing.idempotency_key == idempotency_key
                    ):
                        return existing, idx + 1

            rid = request_id or f"req-{uuid.uuid4()}"
            req = QueuedRequest(
                request_id=rid,
                resource_id=self.resource_id,
                space_id=space_id,
                requester_id=requester_id,
                units=units,
                priority=priority,
                duration_seconds=duration_seconds,
                idempotency_key=idempotency_key,
                requested_at=requested_at or datetime.utcnow(),
            )
            self._queue.append(req)
            position = self._get_position_locked(req.request_id)
            return req, position

    def _get_position_locked(self, request_id: str) -> int:
        """Compute 1-based position among active (non-cancelled) queued requests."""
        pos = 0
        for req in self._queue:
            if not req.cancelled:
                pos += 1
                if req.request_id == request_id:
                    return pos
        return 0

    def get_position(self, request_id: str) -> int:
        """Get 1-based queue position for request_id (0 if not found/cancelled)."""
        with self._lock:
            return self._get_position_locked(request_id)

    def cancel(self, request_id: str, requester_id: str, space_id: str) -> bool:
        """Cancel a queued request.

        Enforces:
        - Must be in the queue
        - Requester identity and space identity must match
        """
        with self._lock:
            for req in self._queue:
                if req.request_id == request_id and not req.cancelled:
                    if req.space_id != space_id:
                        raise PermissionError("Cross-space cancellation rejected")
                    if req.requester_id != requester_id:
                        raise PermissionError("Unauthorized request cancellation by non-holder")
                    req.cancelled = True
                    self._queue.remove(req)
                    return True
            return False

    def pop_next(
        self,
        available_capacity: int,
        max_bypasses: int = 5,
    ) -> QueuedRequest | None:
        """Select, remove, and return the next eligible request that fits in available_capacity.

        Follows the configured queue discipline:
        - FIFO: Strictly first by sequence/arrival
        - PRIORITY_FIFO: Highest priority first, with anti-starvation elevation if
          bypass_count >= max_bypasses.
        """
        with self._lock:
            # Clean up any cancelled requests
            self._queue = [r for r in self._queue if not r.cancelled]
            if not self._queue:
                return None

            candidates = [r for r in self._queue if r.units <= available_capacity]
            if not candidates:
                return None

            chosen: QueuedRequest

            if self.discipline == QueueDiscipline.FIFO:
                # First arrival that fits
                candidates.sort(key=lambda r: r.sequence)
                chosen = candidates[0]
            else:
                # Check for starving requests first (bypass_count >= max_bypasses)
                starving = [r for r in candidates if r.bypass_count >= max_bypasses]
                if starving:
                    # Choose oldest starving candidate
                    starving.sort(key=lambda r: r.sequence)
                    chosen = starving[0]
                else:
                    # Highest priority first, tie-break by arrival sequence
                    candidates.sort(key=lambda r: (-r.priority, r.sequence))
                    chosen = candidates[0]

            # Update bypass counts for candidates that were bypassed by chosen
            for r in self._queue:
                bypassed_by_priority = r.priority < chosen.priority
                bypassed_by_fifo = (
                    self.discipline == QueueDiscipline.FIFO and r.sequence > chosen.sequence
                )
                if r != chosen and (bypassed_by_priority or bypassed_by_fifo):
                    r.bypass_count += 1

            self._queue.remove(chosen)
            return chosen

    def list_requests(self) -> list[QueuedRequest]:
        with self._lock:
            return [r for r in self._queue if not r.cancelled]

    def __len__(self) -> int:
        with self._lock:
            return sum(1 for r in self._queue if not r.cancelled)
