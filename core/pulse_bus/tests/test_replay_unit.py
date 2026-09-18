"""Unit tests for PulseReplayer — replay module contract.

spec §6 (Pulse Bus), PULSE-009 — Phase 1
"""

from __future__ import annotations

from datetime import datetime, timezone

from ryu.pulse_bus.pulse import Pulse, Severity
from ryu.pulse_bus.replay import PulseReplayer
from ryu.pulse_bus.store import InMemoryPulseStore


def make_pulse(
    id: str,
    correlation_id: str = "c1",
    parent: str | None = None,
    taint: bool = False,
) -> Pulse:
    return Pulse(
        id=id,
        space_id="s1",
        type="space.created",
        severity=Severity.INFO,
        source="test",
        timestamp=datetime.now(timezone.utc),
        payload={"space_id": "1", "owner_id": "1"},
        taint=taint,
        correlation_id=correlation_id,
        parent_pulse_id=parent,
    )


def test_replay_from() -> None:
    store = InMemoryPulseStore()
    store.append(make_pulse("p1"))
    store.append(make_pulse("p2"))
    replayer = PulseReplayer(store)
    res = list(replayer.replay_from(0))
    assert len(res) == 2
    assert res[0].id == "p1"
    assert res[1].id == "p2"


def test_replay_by_correlation() -> None:
    store = InMemoryPulseStore()
    store.append(make_pulse("p1", correlation_id="c1"))
    store.append(make_pulse("p2", correlation_id="c2"))
    replayer = PulseReplayer(store)
    res = replayer.replay_by_correlation("c1")
    assert len(res) == 1
    assert res[0].id == "p1"


def test_replay_causal_chain() -> None:
    store = InMemoryPulseStore()
    store.append(make_pulse("p1"))
    store.append(make_pulse("p2", parent="p1"))
    store.append(make_pulse("p3", parent="p2"))
    replayer = PulseReplayer(store)
    res = replayer.replay_causal_chain("p3")
    assert [p.id for p in res] == ["p1", "p2", "p3"]


def test_taint_is_reproduced_as_stored() -> None:
    store = InMemoryPulseStore()
    store.append(make_pulse("p1", taint=True))
    replayer = PulseReplayer(store)
    res = list(replayer.replay_from(0))
    assert res[0].taint is True
