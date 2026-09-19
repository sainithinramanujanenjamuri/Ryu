"""Unit tests for ResourceQueue: FIFO, Priority, and Anti-Starvation Aging.

spec §9 (Resource Manager), ADR-0005, CONTRACT_MATRIX RESOURCE-006 — Phase 3
"""

import pytest

from core.resources.identity import ResourceIdentity
from core.resources.queue import QueueDiscipline, ResourceQueue


def test_queue_fifo_ordering() -> None:
    res_id = ResourceIdentity("gpu", "local", "cuda-0")
    queue = ResourceQueue(resource_id=res_id, discipline=QueueDiscipline.FIFO)

    req1, pos1 = queue.enqueue(space_id="space-1", requester_id="agent-1", units=1)
    req2, pos2 = queue.enqueue(space_id="space-1", requester_id="agent-2", units=1)
    req3, pos3 = queue.enqueue(space_id="space-1", requester_id="agent-3", units=1)

    assert pos1 == 1
    assert pos2 == 2
    assert pos3 == 3
    assert len(queue) == 3

    # Pop next with 1 unit capacity
    first = queue.pop_next(available_capacity=1)
    assert first is not None
    assert first.request_id == req1.request_id
    assert len(queue) == 2

    # Agent-2 is now position 1
    assert queue.get_position(req2.request_id) == 1
    assert queue.get_position(req3.request_id) == 2


def test_queue_priority_ordering() -> None:
    res_id = ResourceIdentity("gpu", "local", "cuda-0")
    queue = ResourceQueue(resource_id=res_id, discipline=QueueDiscipline.PRIORITY_FIFO)

    # Enqueue low priority (0), medium priority (5), high priority (10)
    req_low, _ = queue.enqueue(space_id="space-1", requester_id="low", priority=0)
    req_med, _ = queue.enqueue(space_id="space-1", requester_id="med", priority=5)
    req_high, _ = queue.enqueue(space_id="space-1", requester_id="high", priority=10)

    # Pop next: should pick high priority first
    popped1 = queue.pop_next(available_capacity=1)
    assert popped1 is not None
    assert popped1.request_id == req_high.request_id

    # Then medium
    popped2 = queue.pop_next(available_capacity=1)
    assert popped2 is not None
    assert popped2.request_id == req_med.request_id

    # Then low
    popped3 = queue.pop_next(available_capacity=1)
    assert popped3 is not None
    assert popped3.request_id == req_low.request_id


def test_queue_anti_starvation_aging() -> None:
    res_id = ResourceIdentity("gpu", "local", "cuda-0")
    # Set max_bypasses to 3 for fast test
    queue = ResourceQueue(resource_id=res_id, discipline=QueueDiscipline.PRIORITY_FIFO)

    # Enqueue low priority request first
    req_low, _ = queue.enqueue(space_id="space-1", requester_id="low-priority-worker", priority=0)

    # High-priority requests flood in: 3 bypasses
    for i in range(3):
        h, _ = queue.enqueue(space_id="space-1", requester_id=f"high-{i}", priority=10)
        popped = queue.pop_next(available_capacity=1, max_bypasses=3)
        assert popped is not None
        assert popped.request_id == h.request_id

    # At this point, req_low has been bypassed 3 times (bypass_count == 3)
    assert req_low.bypass_count == 3

    # Another high-priority arrives
    h_new, _ = queue.enqueue(space_id="space-1", requester_id="high-starver", priority=10)

    # Because req_low reached max_bypasses=3, it MUST be popped first to prevent starvation!
    popped_next = queue.pop_next(available_capacity=1, max_bypasses=3)
    assert popped_next is not None
    assert popped_next.request_id == req_low.request_id
    assert popped_next.requester_id == "low-priority-worker"


def test_queue_cancellation() -> None:
    res_id = ResourceIdentity("gpu", "local", "cuda-0")
    queue = ResourceQueue(resource_id=res_id)

    req1, _ = queue.enqueue(space_id="space-1", requester_id="agent-1")
    req2, _ = queue.enqueue(space_id="space-1", requester_id="agent-2")

    # Wrong holder cancellation fails
    with pytest.raises(PermissionError, match="Unauthorized request cancellation"):
        queue.cancel(req1.request_id, "wrong-agent", "space-1")

    # Cross-space cancellation fails
    with pytest.raises(PermissionError, match="Cross-space cancellation rejected"):
        queue.cancel(req1.request_id, "agent-1", "space-2")

    # Authorized cancellation succeeds
    cancelled = queue.cancel(req1.request_id, "agent-1", "space-1")
    assert cancelled
    assert len(queue) == 1

    # Position of req2 is now 1
    assert queue.get_position(req2.request_id) == 1

    # Popping skips req1 entirely and returns req2
    popped = queue.pop_next(available_capacity=1)
    assert popped is not None
    assert popped.request_id == req2.request_id


def test_queue_idempotency_key_lookup() -> None:
    res_id = ResourceIdentity("gpu", "local", "cuda-0")
    queue = ResourceQueue(resource_id=res_id)

    req1, pos1 = queue.enqueue(
        space_id="space-1",
        requester_id="agent-1",
        idempotency_key="idemp-123",
    )
    assert pos1 == 1

    # Re-enqueue with same key returns existing request without duplicating
    req2, pos2 = queue.enqueue(
        space_id="space-1",
        requester_id="agent-1",
        idempotency_key="idemp-123",
    )
    assert req2.request_id == req1.request_id
    assert pos2 == 1
    assert len(queue) == 1

