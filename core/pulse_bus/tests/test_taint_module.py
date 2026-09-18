"""Unit tests for TaintResolver — taint module contract.

spec §10 (Prompt-Injection & Taint Model), TAINT-003 — Phase 1
"""

from __future__ import annotations

from datetime import datetime, timezone

from ryu.pulse_bus.pulse import Pulse, Severity
from ryu.pulse_bus.store import InMemoryPulseStore
from ryu.pulse_bus.taint import TaintResolver


def make_pulse(
    id: str,
    correlation_id: str = "c1",
    parent: str | None = None,
    taint: bool = False,
    ptype: str = "space.created",
) -> Pulse:
    return Pulse(
        id=id,
        space_id="s1",
        type=ptype,
        severity=Severity.INFO,
        source="test",
        timestamp=datetime.now(timezone.utc),
        payload={"space_id": "1", "owner_id": "1"},
        taint=taint,
        correlation_id=correlation_id,
        parent_pulse_id=parent,
    )


def test_root_no_taint() -> None:
    store = InMemoryPulseStore()
    resolver = TaintResolver(store)
    assert not resolver.resolve_taint(make_pulse("p1"))


def test_child_inherits_taint() -> None:
    store = InMemoryPulseStore()
    store.append(make_pulse("p1", taint=True))
    resolver = TaintResolver(store)
    child = make_pulse("p2", parent="p1")
    assert resolver.resolve_taint(child)


def test_child_of_clean_parent() -> None:
    store = InMemoryPulseStore()
    store.append(make_pulse("p1", taint=False))
    resolver = TaintResolver(store)
    child = make_pulse("p2", parent="p1")
    assert not resolver.resolve_taint(child)


def test_child_after_clearance() -> None:
    store = InMemoryPulseStore()
    store.append(make_pulse("p1", taint=True))
    # Clearance event for correlation c1
    store.append(make_pulse("clear", correlation_id="c1", ptype="security.taint.cleared"))
    resolver = TaintResolver(store)
    child = make_pulse("p2", correlation_id="c1", parent="p1")
    assert not resolver.resolve_taint(child)


def test_clearance_different_correlation() -> None:
    store = InMemoryPulseStore()
    store.append(make_pulse("p1", correlation_id="c1", taint=True))
    # Clearance event for c2 only — must NOT affect c1
    store.append(make_pulse("clear", correlation_id="c2", ptype="security.taint.cleared"))
    resolver = TaintResolver(store)
    child = make_pulse("p2", correlation_id="c1", parent="p1")
    assert resolver.resolve_taint(child)
