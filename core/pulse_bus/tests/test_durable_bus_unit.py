"""Unit tests for DurablePulseBus — durable bus unit contract.

Uses InMemoryPulseStore + NoopTransport (no external services required).
spec §6 (Pulse Bus), PULSE-008 — Phase 1
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest
from ryu.pulse_bus.durable_bus import DurablePulseBus
from ryu.pulse_bus.pulse import Pulse, Severity
from ryu.pulse_bus.reject import PulseRejectedError
from ryu.pulse_bus.store import InMemoryPulseStore
from ryu.pulse_bus.transport import NoopTransport


def make_pulse(
    id: str,
    ptype: str = "space.created",
    payload: dict[str, Any] | None = None,
) -> Pulse:
    if payload is None:
        payload = {"space_id": "1", "owner_id": "1"}
    return Pulse(
        id=id,
        space_id="s1",
        type=ptype,
        severity=Severity.INFO,
        source="test",
        timestamp=datetime.now(timezone.utc),
        payload=payload,
        taint=False,
        correlation_id="c1",
        parent_pulse_id=None,
    )


def test_publish_validates() -> None:
    """Invalid type must be rejected before any store write."""
    bus = DurablePulseBus(InMemoryPulseStore(), NoopTransport())
    with pytest.raises(PulseRejectedError):
        bus.publish(make_pulse("p1", ptype="unknown.type"))
    assert bus.store.get_by_id("p1") is None


def test_publish_validates_payload() -> None:
    """Invalid payload must be rejected before any store write."""
    bus = DurablePulseBus(InMemoryPulseStore(), NoopTransport())
    with pytest.raises(PulseRejectedError):
        bus.publish(make_pulse("p1", payload={}))  # missing required fields
    assert bus.store.get_by_id("p1") is None


def test_publish_appends() -> None:
    """Successful publish appends to store and marks as published."""
    bus = DurablePulseBus(InMemoryPulseStore(), NoopTransport())
    bus.publish(make_pulse("p1"))
    assert bus.store.get_by_id("p1") is not None
    # NoopTransport succeeds -> mark_published called -> no unpublished entries
    assert len(bus.store.get_unpublished(10)) == 0


def test_redis_failure_preserves_durability() -> None:
    """Redis transport failure must not lose the Pulse from the store."""
    bus = DurablePulseBus(InMemoryPulseStore(), NoopTransport(raise_on_publish=True))
    bus.publish(make_pulse("p1"))
    assert bus.store.get_by_id("p1") is not None
    # Redis failed -> still unpublished in store (durable but not yet in Redis)
    assert len(bus.store.get_unpublished(10)) == 1


def test_walk_causation() -> None:
    """walk_causation reads causal chain from the store."""
    bus = DurablePulseBus(InMemoryPulseStore(), NoopTransport())
    bus.publish(make_pulse("p1"))
    bus.publish(Pulse(
        id="p2",
        space_id="s1",
        type="space.created",
        severity=Severity.INFO,
        source="test",
        timestamp=datetime.now(timezone.utc),
        payload={"space_id": "1", "owner_id": "1"},
        taint=False,
        correlation_id="c1",
        parent_pulse_id="p1",
    ))
    chain = bus.walk_causation("p2")
    assert [p.id for p in chain] == ["p1", "p2"]


def test_subscribe_receives_published() -> None:
    """Subscriber callbacks fire on successful publish."""
    bus = DurablePulseBus(InMemoryPulseStore(), NoopTransport())
    received: list[Pulse] = []

    def cb(p: Pulse) -> None:
        received.append(p)

    bus.subscribe(cb, "space.created")
    bus.publish(make_pulse("p1"))
    assert len(received) == 1
    assert received[0].id == "p1"
