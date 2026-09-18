"""Harness cases: Redis acknowledgement and consumer recovery.

Verifies:
- Consumed + acknowledged messages are removed from pending list.
- Unacknowledged (stale) messages can be reclaimed by another consumer.

spec §6 (Pulse Bus), Phase 1
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone

import pytest
from ryu.pulse_bus.durable_bus import DurablePulseBus
from ryu.pulse_bus.pulse import Pulse, Severity
from ryu.pulse_bus.transport import RedisStreamTransport

if not os.environ.get("RYU_INTEGRATION_TESTS") == "1":
    pytest.skip("Set RYU_INTEGRATION_TESTS=1 to run integration tests", allow_module_level=True)


def test_redis_acknowledgement(
    durable_bus: DurablePulseBus, redis_transport: RedisStreamTransport
) -> None:
    """Consume + acknowledge → no pending messages in stream group."""
    stream = "ryu:pulses:s_ack"
    group = "test-group"
    redis_transport.ensure_group(stream, group)

    p = Pulse(
        id="ack1",
        space_id="s_ack",
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

    msgs = redis_transport.consume(stream, group, "c1", 10)
    assert len(msgs) == 1
    entry_id = msgs[0][0]

    redis_transport.acknowledge(stream, group, entry_id)
    pending = redis_transport.get_pending(stream, group)
    assert pending["pending"] == 0


def test_consumer_recovery(
    durable_bus: DurablePulseBus, redis_transport: RedisStreamTransport
) -> None:
    """Unacknowledged messages can be reclaimed by another consumer."""
    stream = "ryu:pulses:s_rec"
    group = "test-group"
    redis_transport.ensure_group(stream, group)

    p = Pulse(
        id="rec1",
        space_id="s_rec",
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

    msgs = redis_transport.consume(stream, group, "c1", 10)
    assert len(msgs) == 1
    # Do NOT acknowledge — simulate consumer crash
    time.sleep(0.01)

    stale = redis_transport.reclaim_stale(stream, group, "c2", 5)  # 5ms idle threshold
    assert len(stale) == 1
    assert stale[0][1].id == "rec1"
