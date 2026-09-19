"""Unit tests for SpaceRateLimiter: per-Space tokens/sec and tool_calls/sec.

spec §9, §12, CONTRACT_MATRIX RESOURCE-007, RESOURCE-008 — Phase 3
"""

from datetime import datetime, timezone

from ryu.pulse_bus.bus import PulseBus
from ryu.pulse_bus.pulse import Pulse

from core.resources.clock import FakeClock
from core.resources.rate_limit import RateLimitConfig, SpaceRateLimiter


class SpyPulseBus(PulseBus):
    def __init__(self) -> None:
        super().__init__()
        self.published: list[Pulse] = []

    def publish(self, pulse: Pulse) -> Pulse:
        self.published.append(pulse)
        return super().publish(pulse)


def test_rate_limiter_allows_under_budget() -> None:
    bus = SpyPulseBus()
    clock = FakeClock(datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc))
    limiter = SpaceRateLimiter(
        bus=bus,
        clock=clock,
        default_config=RateLimitConfig(max_tokens_per_sec=100.0, max_tool_calls_per_sec=10.0),
    )

    # 50 tokens should be allowed
    res = limiter.check_and_consume("space-1", "agent-1", budget_type="tokens", amount=50)
    assert res.allowed
    assert res.retry_after == 0
    assert len(bus.published) == 0


def test_rate_limiter_exceed_publishes_info_pulse() -> None:
    bus = SpyPulseBus()
    clock = FakeClock(datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc))
    limiter = SpaceRateLimiter(
        bus=bus,
        clock=clock,
        default_config=RateLimitConfig(max_tokens_per_sec=100.0, max_tool_calls_per_sec=5.0),
    )

    # Consume 4 tool calls
    res1 = limiter.check_and_consume("space-1", "agent-1", budget_type="tool_calls", amount=4)
    assert res1.allowed

    # Try 3 more (total 7 > 5) -> over limit
    res2 = limiter.check_and_consume("space-1", "agent-1", budget_type="tool_calls", amount=3)
    assert not res2.allowed
    assert res2.retry_after >= 1

    # Invariant RESOURCE-008: severity MUST be info!
    assert len(bus.published) == 1
    p = bus.published[0]
    assert p.type == "rate.limited"
    assert p.severity == "info"
    assert p.space_id == "space-1"
    assert p.payload["requester_id"] == "agent-1"
    assert p.payload["budget_type"] == "tool_calls"
    assert p.payload["retry_after"] >= 1

    # Advance clock past retry_after: should allow again
    clock.advance(float(res2.retry_after) + 1.0)
    res3 = limiter.check_and_consume("space-1", "agent-1", budget_type="tool_calls", amount=2)
    assert res3.allowed
