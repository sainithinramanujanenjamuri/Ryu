"""Harness case: Durable replay after simulated restart — PULSE-009, REC-001.

Verifies that a causation chain published via DurablePulseBus survives a process restart
by creating a new store/bus instance backed by the same PostgreSQL database.

Architecture acceptance criterion (docs/Architecture §6, ROADMAP Phase 1 exit gate):
  kill process mid-chain, restart, replay → chain intact, taint state correct.

spec §6 (Pulse Bus), PULSE-009, REC-001 — Phase 1
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest
from ryu.pulse_bus.durable_bus import DurablePulseBus
from ryu.pulse_bus.pulse import Pulse, Severity
from ryu.pulse_bus.store import PostgresPulseStore
from ryu.pulse_bus.transport import RedisStreamTransport

if not os.environ.get("RYU_INTEGRATION_TESTS") == "1":
    pytest.skip("Set RYU_INTEGRATION_TESTS=1 to run integration tests", allow_module_level=True)


def test_durable_replay(durable_bus: DurablePulseBus) -> None:
    """Chain survives process restart: new bus instance reads from same PostgreSQL."""
    assert isinstance(durable_bus.store, PostgresPulseStore)
    assert isinstance(durable_bus.transport, RedisStreamTransport)

    def make_p(id: str, parent: str | None) -> Pulse:
        return Pulse(
            id=id,
            space_id="s1",
            type="space.created",
            severity=Severity.INFO,
            source="test",
            timestamp=datetime.now(timezone.utc),
            payload={"space_id": "1", "owner_id": "1"},
            taint=False,
            correlation_id="c1",
            parent_pulse_id=parent,
        )

    durable_bus.publish(make_p("r1", None))
    durable_bus.publish(make_p("m1", "r1"))
    durable_bus.publish(make_p("l1", "m1"))

    # Simulate process restart: create new store/bus instances, same DB config
    new_store = PostgresPulseStore(durable_bus.store.config)
    new_bus = DurablePulseBus(new_store, RedisStreamTransport(durable_bus.transport.config))
    chain = new_bus.walk_causation("l1")
    assert [p.id for p in chain] == ["r1", "m1", "l1"]
