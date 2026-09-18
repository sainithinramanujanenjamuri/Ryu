"""Integration test fixtures for Phase 1 Durable Pulse Bus.

Requires RYU_INTEGRATION_TESTS=1 and running PostgreSQL + Redis (Docker Compose).
spec §6 (Pulse Bus), Phase 1
"""

from __future__ import annotations

import os
from collections.abc import Generator

import pytest
from ryu.pulse_bus.config import DurableBusConfig, PostgresConfig, RedisConfig
from ryu.pulse_bus.durable_bus import DurablePulseBus
from ryu.pulse_bus.store import PostgresPulseStore
from ryu.pulse_bus.transport import RedisStreamTransport

if not os.environ.get("RYU_INTEGRATION_TESTS") == "1":
    pytest.skip("Set RYU_INTEGRATION_TESTS=1 to run integration tests", allow_module_level=True)


@pytest.fixture
def pg_store() -> Generator[PostgresPulseStore, None, None]:
    config = PostgresConfig(
        host=os.environ.get("RYU_PG_HOST", "localhost"),
        port=int(os.environ.get("RYU_PG_PORT", "5432")),
        db=os.environ.get("RYU_PG_DB", "ryu_dev"),
        user=os.environ.get("RYU_PG_USER", "ryu"),
        password=os.environ.get("RYU_PG_PASSWORD", "ryu_dev_password"),
    )
    store = PostgresPulseStore(config)
    yield store
    # Cleanup: truncate table between tests
    with store._get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("TRUNCATE TABLE pulses RESTART IDENTITY CASCADE;")


@pytest.fixture
def redis_transport() -> Generator[RedisStreamTransport, None, None]:
    config = RedisConfig(
        host=os.environ.get("RYU_REDIS_HOST", "localhost"),
        port=int(os.environ.get("RYU_REDIS_PORT", "6379")),
    )
    transport = RedisStreamTransport(config)
    yield transport
    # Cleanup: flush all Redis data between tests
    transport.redis.flushall()


@pytest.fixture
def durable_bus(
    pg_store: PostgresPulseStore,
    redis_transport: RedisStreamTransport,
) -> DurablePulseBus:
    config = DurableBusConfig(
        pg=pg_store.config,
        redis=redis_transport.config,
        integration_tests=True,
    )
    return DurablePulseBus(pg_store, redis_transport, config=config)
