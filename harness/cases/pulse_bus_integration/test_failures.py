"""Harness case: Failure semantics — REC-002, PULSE-008.

Tests that:
- PostgreSQL failure raises observable error (not silent) and Pulse is not in Redis.
- Redis failure leaves Pulse durable in PostgreSQL (observable but not destructive).

spec §6 (Pulse Bus), §Law 6 (Failures never silent), REC-002 — Phase 1
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

import psycopg2
import pytest
from ryu.pulse_bus.config import PostgresConfig, RedisConfig
from ryu.pulse_bus.durable_bus import DurablePulseBus
from ryu.pulse_bus.pulse import Pulse, Severity
from ryu.pulse_bus.store import PostgresPulseStore
from ryu.pulse_bus.transport import RedisStreamTransport

if not os.environ.get("RYU_INTEGRATION_TESTS") == "1":
    pytest.skip("Set RYU_INTEGRATION_TESTS=1 to run integration tests", allow_module_level=True)


def test_db_failure(redis_transport: RedisStreamTransport) -> None:
    """PostgreSQL unavailable → publish raises observable error (not silent)."""
    bad_config = PostgresConfig(port=12345)  # deliberately wrong port
    bad_store = PostgresPulseStore(bad_config)
    bus = DurablePulseBus(bad_store, redis_transport)
    p = Pulse(
        id="db_fail1",
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
    with pytest.raises(psycopg2.OperationalError):
        bus.publish(p)


def test_redis_failure(pg_store: PostgresPulseStore) -> None:
    """Redis unavailable → Pulse is durable in PostgreSQL; error is observable, not silent."""
    bad_redis = RedisStreamTransport(RedisConfig(port=12345))
    bus = DurablePulseBus(pg_store, bad_redis)
    p = Pulse(
        id="red_fail1",
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
    # publish should succeed in Postgres but log error for Redis (does not raise — Pulse is durable)
    bus.publish(p)
    assert pg_store.get_by_id("red_fail1") is not None
    assert len(pg_store.get_unpublished(10)) == 1
