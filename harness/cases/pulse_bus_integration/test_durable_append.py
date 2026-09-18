"""Harness case: Durable append — PULSE-008.

Verifies that a Pulse published via DurablePulseBus is persisted in PostgreSQL
and retrievable by pulse_id after publication.

spec §6 (Pulse Bus), PULSE-008 — Phase 1
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest
from ryu.pulse_bus.durable_bus import DurablePulseBus
from ryu.pulse_bus.pulse import Pulse, Severity

if not os.environ.get("RYU_INTEGRATION_TESTS") == "1":
    pytest.skip("Set RYU_INTEGRATION_TESTS=1 to run integration tests", allow_module_level=True)


def test_durable_append(durable_bus: DurablePulseBus) -> None:
    """Published Pulse must be retrievable by ID from PostgreSQL."""
    p = Pulse(
        id="durable_p1",
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
    retrieved = durable_bus.store.get_by_id("durable_p1")
    assert retrieved is not None
    assert retrieved.id == "durable_p1"
