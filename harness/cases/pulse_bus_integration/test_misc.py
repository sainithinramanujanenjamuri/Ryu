"""Harness cases: Idempotency, space isolation, taint preservation, and backpressure.

spec §6 (Pulse Bus), §10 (Taint), §16 (Contracts), SPACE-005, RESOURCE-008 — Phase 1
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest
from ryu.pulse_bus.durable_bus import DurablePulseBus
from ryu.pulse_bus.pulse import Pulse, Severity

if not os.environ.get("RYU_INTEGRATION_TESTS") == "1":
    pytest.skip("Set RYU_INTEGRATION_TESTS=1 to run integration tests", allow_module_level=True)


def test_duplicate_pulse_id(durable_bus: DurablePulseBus) -> None:
    """Duplicate pulse submission must be idempotent."""
    p = Pulse(
        id="dup1",
        space_id="s1",
        type="space.created",
        severity=Severity.INFO,
        source="test",
        timestamp=datetime.now(timezone.utc),
        payload={"space_id": "1", "owner_id": "1"},
        taint=False,
        correlation_id="c1",
        parent_pulse_id=None,
    )
    durable_bus.publish(p)
    durable_bus.publish(p)  # should be idempotent
    res = durable_bus.store.read_by_correlation("c1")
    assert len(res) == 1


def test_space_scoped_retrieval(durable_bus: DurablePulseBus) -> None:
    """Space boundary must isolate pulses by space_id (SPACE-005)."""
    p_a = Pulse(
        id="sA1",
        space_id="space_A",
        type="space.created",
        severity=Severity.INFO,
        source="test",
        timestamp=datetime.now(timezone.utc),
        payload={"space_id": "1", "owner_id": "1"},
        taint=False,
        correlation_id="c1",
        parent_pulse_id=None,
    )
    p_b = Pulse(
        id="sB1",
        space_id="space_B",
        type="space.created",
        severity=Severity.INFO,
        source="test",
        timestamp=datetime.now(timezone.utc),
        payload={"space_id": "1", "owner_id": "1"},
        taint=False,
        correlation_id="c2",
        parent_pulse_id=None,
    )
    durable_bus.publish(p_a)
    durable_bus.publish(p_b)
    res_a = durable_bus.store.read_by_space("space_A")
    assert len(res_a) == 1
    assert res_a[0].id == "sA1"


def test_taint_preservation(durable_bus: DurablePulseBus) -> None:
    """Taint state persists across storage and forward clearance works (TAINT-003)."""
    durable_bus.publish(
        Pulse(
            id="tp1",
            space_id="s1",
            type="space.created",
            severity=Severity.INFO,
            source="test",
            timestamp=datetime.now(timezone.utc),
            payload={"space_id": "1", "owner_id": "1"},
            taint=True,
            correlation_id="c_taint",
            parent_pulse_id=None,
        )
    )
    # child inherits taint
    child1 = durable_bus.publish(
        Pulse(
            id="tp2",
            space_id="s1",
            type="space.created",
            severity=Severity.INFO,
            source="test",
            timestamp=datetime.now(timezone.utc),
            payload={"space_id": "1", "owner_id": "1"},
            taint=False,
            correlation_id="c_taint",
            parent_pulse_id="tp1",
        )
    )
    assert child1.taint is True

    # clearance event
    durable_bus.publish(
        Pulse(
            id="tp_clear",
            space_id="s1",
            type="security.taint.cleared",
            severity=Severity.INFO,
            source="test",
            timestamp=datetime.now(timezone.utc),
            payload={
                "correlation_id": "c_taint",
                "approver_id": "a1",
                "scope": "all",
                "timestamp": "2026-09-18T00:00:00Z",
            },
            taint=False,
            correlation_id="c_taint",
            parent_pulse_id=None,
        )
    )
    child2 = durable_bus.publish(
        Pulse(
            id="tp3",
            space_id="s1",
            type="space.created",
            severity=Severity.INFO,
            source="test",
            timestamp=datetime.now(timezone.utc),
            payload={"space_id": "1", "owner_id": "1"},
            taint=False,
            correlation_id="c_taint",
            parent_pulse_id="tp2",
        )
    )
    assert child2.taint is False
    chain = durable_bus.walk_causation("tp3")
    assert chain[0].taint is True
    assert chain[1].taint is True
    assert chain[2].taint is False


def test_backpressure(durable_bus: DurablePulseBus) -> None:
    """rate.limited pulses are admitted without error (RESOURCE-008)."""
    p = Pulse(
        id="bp1",
        space_id="s1",
        type="rate.limited",
        severity=Severity.INFO,
        source="test",
        timestamp=datetime.now(timezone.utc),
        payload={"requester_id": "1", "budget_type": "tokens", "retry_after": 10},
        taint=False,
        correlation_id="c1",
        parent_pulse_id=None,
    )
    res = durable_bus.publish(p)
    assert res.id == "bp1"
