"""Harness case: Redis publication — PULSE-008, Phase 1.

Verifies that a Pulse published via DurablePulseBus is published to the Redis Stream.

spec §6 (Pulse Bus), Phase 1
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest
from ryu.pulse_bus.durable_bus import DurablePulseBus
from ryu.pulse_bus.pulse import Pulse, Severity
from ryu.pulse_bus.transport import RedisStreamTransport

if not os.environ.get("RYU_INTEGRATION_TESTS") == "1":
    pytest.skip("Set RYU_INTEGRATION_TESTS=1 to run integration tests", allow_module_level=True)


def test_redis_publication(
    durable_bus: DurablePulseBus, redis_transport: RedisStreamTransport
) -> None:
    """Published Pulse appears in the Redis Stream."""
    p = Pulse(
        id="rp1",
        space_id="s_redis",
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
    stream = "ryu:pulses:s_redis"
    if durable_bus.config:
        stream = f"{durable_bus.config.redis.stream_prefix}:s_redis"
    res = redis_transport.redis.xrange(stream)
    assert len(res) == 1
    assert res[0][1]["id"] == "rp1"
