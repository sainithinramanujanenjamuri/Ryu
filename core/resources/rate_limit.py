"""Per-Space token and tool-call rate limiting with backpressure.

Over-limit requests emit `rate.limited` at `severity: info` with retry_after.
Never silently drops requests.
spec §9 (Resource Manager), §12 (Backpressure), CONTRACT_MATRIX RESOURCE-007..008 — Phase 3
"""

from __future__ import annotations

import math
import threading
from dataclasses import dataclass
from typing import Any

from ryu.pulse_bus.bus import PulseBus
from ryu.pulse_bus.pulse import Pulse, Severity

from core.resources.clock import Clock, SystemClock


@dataclass
class RateLimitConfig:
    """Configured rate limits per second for a Space."""

    max_tokens_per_sec: float = 10000.0
    max_tool_calls_per_sec: float = 100.0


@dataclass
class RateLimitResult:
    """Outcome of a rate limit check."""

    allowed: bool
    retry_after: int = 0
    budget_type: str = "tokens"
    current_tokens_per_sec: float = 0.0
    current_tool_calls_per_sec: float = 0.0


class TokenBucket:
    """Thread-safe token bucket rate limiter."""

    def __init__(self, capacity: float, refill_rate_per_sec: float, clock: Clock) -> None:
        self.capacity = capacity
        self.refill_rate = refill_rate_per_sec
        self.tokens = capacity
        self.clock = clock
        self.last_update = clock.now().timestamp()
        self._lock = threading.Lock()

    def consume(self, amount: float) -> tuple[bool, int]:
        """Try to consume amount tokens.

        Returns (allowed: bool, retry_after: int in seconds).
        """
        with self._lock:
            now = self.clock.now().timestamp()
            elapsed = max(0.0, now - self.last_update)
            self.last_update = now

            # Refill tokens
            self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_rate)

            if self.tokens >= amount:
                self.tokens -= amount
                return True, 0
            else:
                needed = amount - self.tokens
                retry_after_sec = (
                    math.ceil(needed / self.refill_rate) if self.refill_rate > 0 else 60
                )
                return False, max(1, retry_after_sec)


class SpaceRateLimiter:
    """Enforces per-Space rate limits (tokens/sec and tool_calls/sec)."""

    def __init__(
        self,
        bus: PulseBus | Any,
        clock: Clock | None = None,
        default_config: RateLimitConfig | None = None,
    ) -> None:
        self.bus = bus
        self.clock = clock or SystemClock()
        self.default_config = default_config or RateLimitConfig()
        self._configs: dict[str, RateLimitConfig] = {}
        self._token_buckets: dict[str, TokenBucket] = {}
        self._tool_buckets: dict[str, TokenBucket] = {}
        self._lock = threading.Lock()

    def set_config(self, space_id: str, config: RateLimitConfig) -> None:
        with self._lock:
            self._configs[space_id] = config
            self._token_buckets[space_id] = TokenBucket(
                capacity=config.max_tokens_per_sec,
                refill_rate_per_sec=config.max_tokens_per_sec,
                clock=self.clock,
            )
            self._tool_buckets[space_id] = TokenBucket(
                capacity=config.max_tool_calls_per_sec,
                refill_rate_per_sec=config.max_tool_calls_per_sec,
                clock=self.clock,
            )

    def _get_or_create_buckets(self, space_id: str) -> tuple[TokenBucket, TokenBucket]:
        with self._lock:
            if space_id not in self._token_buckets:
                cfg = self._configs.get(space_id, self.default_config)
                self._token_buckets[space_id] = TokenBucket(
                    capacity=cfg.max_tokens_per_sec,
                    refill_rate_per_sec=cfg.max_tokens_per_sec,
                    clock=self.clock,
                )
                self._tool_buckets[space_id] = TokenBucket(
                    capacity=cfg.max_tool_calls_per_sec,
                    refill_rate_per_sec=cfg.max_tool_calls_per_sec,
                    clock=self.clock,
                )
            return self._token_buckets[space_id], self._tool_buckets[space_id]

    def check_and_consume(
        self,
        space_id: str,
        requester_id: str,
        budget_type: str,  # "tokens" or "tool_calls"
        amount: int = 1,
    ) -> RateLimitResult:
        """Check rate limit for space and consume tokens if allowed.

        On exceed, publishes rate.limited (severity: info) and returns allowed=False with
        retry_after.
        """
        if budget_type not in ("tokens", "tool_calls"):
            raise ValueError(
                f"Unknown budget_type: {budget_type}. Expected 'tokens' or 'tool_calls'."
            )

        token_b, tool_b = self._get_or_create_buckets(space_id)
        bucket = token_b if budget_type == "tokens" else tool_b

        allowed, retry_after = bucket.consume(float(amount))
        if not allowed:
            # Publish rate.limited pulse per contract registry schema
            # Schema requires: requester_id, budget_type, retry_after
            # Severity must be info (Contract RESOURCE-008)
            pulse = Pulse(
                type="rate.limited",
                severity=Severity.INFO,
                space_id=space_id,
                source=f"rate_limiter:{space_id}",
                correlation_id=f"corr-rate-{space_id}",
                payload={
                    "requester_id": requester_id,
                    "budget_type": budget_type,
                    "retry_after": int(retry_after),
                },
                timestamp=self.clock.now(),
            )
            self.bus.publish(pulse)

            return RateLimitResult(
                allowed=False,
                retry_after=retry_after,
                budget_type=budget_type,
            )

        return RateLimitResult(
            allowed=True,
            retry_after=0,
            budget_type=budget_type,
        )
