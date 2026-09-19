"""Phase 3 Resource Manager acceptance harness cases.

spec §9 (Resource Manager), CONTRACT_MATRIX RESOURCE-001 through RESOURCE-008 — Phase 3
"""

from datetime import datetime, timezone

from ryu.pulse_bus.bus import PulseBus
from ryu.pulse_bus.pulse import Pulse

from core.resources.clock import FakeClock
from core.resources.identity import Resource, ResourceIdentity
from core.resources.lease import LeaseState
from core.resources.manager import ResourceManager
from core.resources.rate_limit import RateLimitConfig, SpaceRateLimiter


class SpyPulseBus(PulseBus):
    def __init__(self) -> None:
        super().__init__()
        self.published: list[Pulse] = []

    def publish(self, pulse: Pulse) -> Pulse:
        self.published.append(pulse)
        return super().publish(pulse)


def test_resource_lease_issued() -> None:
    """RESOURCE-001, RESOURCE-002: Resource identity and Lease issuance."""
    bus = SpyPulseBus()
    mgr = ResourceManager(bus=bus)
    res_id = ResourceIdentity("gpu", "cluster-1", "cuda-0")
    res = Resource(identity=res_id, space_id="space-1", total_capacity=1)
    mgr.register_resource(res)

    acq = mgr.acquire("space-1", "agent-1", res_id, duration_seconds=60.0)
    assert acq.granted is True
    assert acq.lease is not None
    assert acq.lease.resource_id == res_id
    assert acq.lease.state == LeaseState.ACTIVE
    assert acq.lease.requester_id == "agent-1"


def test_resource_no_double_grant() -> None:
    """RESOURCE-005: Concurrent acquisition cannot double-grant one exclusive resource."""
    bus = SpyPulseBus()
    mgr = ResourceManager(bus=bus)
    res_id = ResourceIdentity("gpu", "cluster-1", "cuda-0")
    res = Resource(identity=res_id, space_id="space-1", total_capacity=1)
    mgr.register_resource(res)

    acq1 = mgr.acquire("space-1", "agent-1", res_id)
    acq2 = mgr.acquire("space-1", "agent-2", res_id)

    assert acq1.granted is True
    assert acq2.granted is False
    assert res.allocated_capacity == 1, "Exclusive resource cannot be double-allocated"


def test_resource_conflict_pulse_emitted() -> None:
    """RESOURCE-006: Losing requests receive accurate resource.conflict with queue_position."""
    bus = SpyPulseBus()
    mgr = ResourceManager(bus=bus)
    res_id = ResourceIdentity("gpu", "cluster-1", "cuda-0")
    mgr.register_resource(Resource(identity=res_id, space_id="space-1", total_capacity=1))

    # First request wins
    mgr.acquire("space-1", "agent-1", res_id)

    # Second request is contested and queued
    acq2 = mgr.acquire("space-1", "agent-2", res_id)
    assert acq2.granted is False
    assert acq2.queue_position == 1

    conflicts = [p for p in bus.published if p.type == "resource.conflict"]
    assert len(conflicts) == 1
    assert conflicts[0].payload["resource_id"] == res_id.to_handle()
    assert conflicts[0].payload["queue_position"] == 1
    assert conflicts[0].severity == "warning"


def test_rate_limited_severity_info() -> None:
    """RESOURCE-008: rate.limited pulses must maintain severity: info."""
    bus = SpyPulseBus()
    clock = FakeClock(datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc))
    limiter = SpaceRateLimiter(
        bus=bus,
        clock=clock,
        default_config=RateLimitConfig(max_tokens_per_sec=10.0, max_tool_calls_per_sec=1.0),
    )

    # First call allowed
    res1 = limiter.check_and_consume("space-1", "agent-1", "tool_calls", amount=1)
    assert res1.allowed

    # Second call exceeded
    res2 = limiter.check_and_consume("space-1", "agent-1", "tool_calls", amount=1)
    assert not res2.allowed

    rate_pulses = [p for p in bus.published if p.type == "rate.limited"]
    assert len(rate_pulses) == 1
    assert rate_pulses[0].severity == "info"
    assert rate_pulses[0].payload["requester_id"] == "agent-1"
    assert rate_pulses[0].payload["budget_type"] == "tool_calls"
