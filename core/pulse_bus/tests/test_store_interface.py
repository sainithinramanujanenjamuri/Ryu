"""Unit tests for InMemoryPulseStore — PulseStore interface contract.

spec §6 (Pulse Bus), PULSE-008 — Phase 1
"""

from __future__ import annotations

from datetime import datetime, timezone

from ryu.pulse_bus.pulse import Pulse, Severity
from ryu.pulse_bus.store import InMemoryPulseStore


def make_pulse(
    id: str,
    correlation_id: str = "c1",
    parent: str | None = None,
    space_id: str = "s1",
) -> Pulse:
    return Pulse(
        id=id,
        space_id=space_id,
        type="space.created",
        severity=Severity.INFO,
        source="test",
        timestamp=datetime.now(timezone.utc),
        payload={"space_id": "1", "owner_id": "1"},
        taint=False,
        correlation_id=correlation_id,
        parent_pulse_id=parent,
    )


def test_append_and_retrieve() -> None:
    store = InMemoryPulseStore()
    p1 = make_pulse("p1")
    pos = store.append(p1)
    assert pos == 1
    assert store.get_by_id("p1") == p1
    assert store.exists("p1")


def test_duplicate_append_idempotent() -> None:
    store = InMemoryPulseStore()
    p1 = make_pulse("p1")
    pos1 = store.append(p1)
    pos2 = store.append(p1)
    assert pos1 == pos2
    assert len(store.read(0)) == 1


def test_ordering() -> None:
    store = InMemoryPulseStore()
    store.append(make_pulse("p1"))
    store.append(make_pulse("p2"))
    res = store.read(0)
    assert len(res) == 2
    assert res[0].id == "p1"
    assert res[1].id == "p2"


def test_read_by_correlation() -> None:
    store = InMemoryPulseStore()
    store.append(make_pulse("p1", correlation_id="c1"))
    store.append(make_pulse("p2", correlation_id="c2"))
    store.append(make_pulse("p3", correlation_id="c1"))
    res = store.read_by_correlation("c1")
    assert len(res) == 2
    assert res[0].id == "p1"
    assert res[1].id == "p3"


def test_read_by_space() -> None:
    store = InMemoryPulseStore()
    store.append(make_pulse("p1", space_id="s1"))
    store.append(make_pulse("p2", space_id="s2"))
    res = store.read_by_space("s1")
    assert len(res) == 1
    assert res[0].id == "p1"


def test_read_by_parent() -> None:
    store = InMemoryPulseStore()
    store.append(make_pulse("p1"))
    store.append(make_pulse("p2", parent="p1"))
    res = store.read_by_parent("p1")
    assert len(res) == 1
    assert res[0].id == "p2"


def test_mark_published() -> None:
    store = InMemoryPulseStore()
    store.append(make_pulse("p1"))
    store.append(make_pulse("p2"))
    store.mark_published("p1")
    unpub = store.get_unpublished(10)
    assert len(unpub) == 1
    assert unpub[0].id == "p2"
