"""Unit tests for ResourceManager: contention, leases, capacity, concurrency, recovery.

spec §9 (Resource Manager), §16 (Lease), CONTRACT_MATRIX RESOURCE-001..006 — Phase 3
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pytest
from ryu.pulse_bus.bus import PulseBus
from ryu.pulse_bus.pulse import Pulse

from core.resources.clock import FakeClock
from core.resources.identity import Resource, ResourceIdentity
from core.resources.lease import LeaseState
from core.resources.manager import ResourceAcquisitionResult, ResourceManager
from core.resources.store import InMemoryResourceStore


class SpyPulseBus(PulseBus):
    def __init__(self) -> None:
        super().__init__()
        self.published: list[Pulse] = []

    def publish(self, pulse: Pulse) -> Pulse:
        self.published.append(pulse)
        return super().publish(pulse)


def test_resource_manager_registration_and_discovery() -> None:
    bus = SpyPulseBus()
    mgr = ResourceManager(bus=bus)
    ident = ResourceIdentity("gpu", "node-1", "cuda-0")
    res = Resource(identity=ident, space_id="space-alpha", total_capacity=1)

    mgr.register_resource(res)

    found = mgr.get_resource(ident)
    assert found is not None
    assert found.handle == "gpu/node-1/cuda-0"
    assert found.space_id == "space-alpha"

    # List resources
    all_res = mgr.list_resources()
    assert len(all_res) == 1
    assert mgr.list_resources("space-alpha") == [res]
    assert mgr.list_resources("other-space") == []


def test_space_isolation_enforcement() -> None:
    bus = SpyPulseBus()
    mgr = ResourceManager(bus=bus)
    ident = ResourceIdentity("gpu", "node-1", "cuda-0")
    res = Resource(identity=ident, space_id="space-alpha", total_capacity=1)
    mgr.register_resource(res)

    # Cross-space acquisition rejected (SPACE-001)
    with pytest.raises(PermissionError, match="Cross-space resource access rejected"):
        mgr.acquire(
            space_id="space-beta",
            requester_id="agent-rogue",
            identity=ident,
        )


def test_contention_resolution_and_queueing() -> None:
    bus = SpyPulseBus()
    clock = FakeClock(datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc))
    mgr = ResourceManager(bus=bus, clock=clock)

    ident = ResourceIdentity("gpu", "node-1", "cuda-0")
    res = Resource(identity=ident, space_id="space-alpha", total_capacity=1)
    mgr.register_resource(res)

    # 1. Agent-1 requests free resource -> immediate grant
    res1 = mgr.acquire("space-alpha", "agent-1", ident, duration_seconds=60.0)
    assert res1.granted
    assert res1.lease is not None
    assert res1.lease.requester_id == "agent-1"
    token1 = res1.lease.lease_token

    # Verify resource.granted pulse published
    granted_pulses = [p for p in bus.published if p.type == "resource.granted"]
    assert len(granted_pulses) == 1
    assert granted_pulses[0].payload["lease_token"] == token1

    # 2. Agent-2 requests leased resource -> queued, conflict pulse
    res2 = mgr.acquire("space-alpha", "agent-2", ident, duration_seconds=60.0)
    assert not res2.granted
    assert res2.queue_position == 1

    conflict_pulses = [p for p in bus.published if p.type == "resource.conflict"]
    assert len(conflict_pulses) == 1
    assert conflict_pulses[0].payload["queue_position"] == 1
    assert conflict_pulses[0].severity == "warning"

    # 3. Agent-3 requests leased resource -> queued at pos 2
    res3 = mgr.acquire("space-alpha", "agent-3", ident, duration_seconds=60.0)
    assert not res3.granted
    assert res3.queue_position == 2

    # 4. Agent-1 releases -> Agent-2 automatically dequeued and granted!
    mgr.release("space-alpha", "agent-1", token1)

    released_pulses = [p for p in bus.published if p.type == "resource.released"]
    assert len(released_pulses) == 1

    # Now Agent-2 was granted
    granted_pulses = [p for p in bus.published if p.type == "resource.granted"]
    assert len(granted_pulses) == 2
    token2 = granted_pulses[1].payload["lease_token"]

    # Verify Agent-2 holds the lease
    lease2 = mgr._lease_manager.get_lease(token2)
    assert lease2 is not None
    assert lease2.requester_id == "agent-2"
    assert lease2.state == LeaseState.ACTIVE


def test_concurrent_acquisition_race() -> None:
    """20 threads race to acquire a capacity=1 exclusive resource.

    Must produce exactly 1 immediate winner, 19 queued, zero double grants.
    """
    bus = SpyPulseBus()
    mgr = ResourceManager(bus=bus)
    ident = ResourceIdentity("gpu", "cluster", "h100-0")
    res = Resource(identity=ident, space_id="space-race", total_capacity=1)
    mgr.register_resource(res)

    results: list[ResourceAcquisitionResult] = []

    def racer(worker_idx: int) -> ResourceAcquisitionResult:
        return mgr.acquire(
            space_id="space-race",
            requester_id=f"worker-{worker_idx}",
            identity=ident,
            duration_seconds=60.0,
        )

    with ThreadPoolExecutor(max_workers=20) as executor:
        futures = [executor.submit(racer, i) for i in range(20)]
        for f in futures:
            results.append(f.result())

    immediate_grants = [r for r in results if r.granted]
    queued_requests = [r for r in results if not r.granted]

    assert len(immediate_grants) == 1, (
        "Exactly one racer must immediately acquire exclusive resource"
    )
    assert len(queued_requests) == 19, "Remaining 19 racers must be queued"
    assert res.allocated_capacity == 1, "Allocated capacity must strictly be 1"

    # Verify positions are 1 to 19
    positions = sorted([r.queue_position for r in queued_requests if r.queue_position is not None])
    assert positions == list(range(1, 20))


def test_capacity_resource_accounting() -> None:
    bus = SpyPulseBus()
    mgr = ResourceManager(bus=bus)
    ident = ResourceIdentity("worker_slots", "cluster", "node-1")
    # Capacity of 4 worker slots
    res = Resource(identity=ident, space_id="space-cap", total_capacity=4)
    mgr.register_resource(res)

    # Acquire 2 slots
    r1 = mgr.acquire("space-cap", "agent-1", ident, units=2)
    assert r1.granted
    assert res.allocated_capacity == 2

    # Acquire 2 more slots
    r2 = mgr.acquire("space-cap", "agent-2", ident, units=2)
    assert r2.granted
    assert res.allocated_capacity == 4

    # Acquire 1 slot (full: 4/4) -> queued
    r3 = mgr.acquire("space-cap", "agent-3", ident, units=1)
    assert not r3.granted
    assert r3.queue_position == 1

    # Release 2 slots from agent-1 -> agent-3 dequeued and granted 1 slot
    mgr.release("space-cap", "agent-1", r1.lease.lease_token)  # type: ignore[union-attr]
    assert res.allocated_capacity == 3  # 2 (agent-2) + 1 (agent-3)


def test_idempotency_caching() -> None:
    bus = SpyPulseBus()
    mgr = ResourceManager(bus=bus)
    ident = ResourceIdentity("gpu", "local", "cuda-0")
    res = Resource(identity=ident, space_id="space-idemp", total_capacity=1)
    mgr.register_resource(res)

    # First attempt with idempotency key
    r1 = mgr.acquire(
        space_id="space-idemp",
        requester_id="agent-1",
        identity=ident,
        idempotency_key="key-abc",
    )
    assert r1.granted
    assert not r1.cached
    assert res.allocated_capacity == 1

    # Retry with identical key -> returns cached lease, does NOT allocate twice
    r2 = mgr.acquire(
        space_id="space-idemp",
        requester_id="agent-1",
        identity=ident,
        idempotency_key="key-abc",
    )
    assert r2.granted
    assert r2.cached
    assert r2.lease.lease_token == r1.lease.lease_token  # type: ignore[union-attr]
    assert res.allocated_capacity == 1


def test_lease_expiration_sweep_and_queue_drain() -> None:
    bus = SpyPulseBus()
    clock = FakeClock(datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc))
    mgr = ResourceManager(bus=bus, clock=clock)
    ident = ResourceIdentity("gpu", "local", "cuda-0")
    res = Resource(identity=ident, space_id="space-exp", total_capacity=1)
    mgr.register_resource(res)

    # Agent-1 acquires with 30s lease
    r1 = mgr.acquire("space-exp", "agent-1", ident, duration_seconds=30.0)
    assert r1.granted

    # Agent-2 queues
    r2 = mgr.acquire("space-exp", "agent-2", ident, duration_seconds=60.0)
    assert not r2.granted
    assert r2.queue_position == 1

    # Advance clock by 35s (past agent-1's lease expiry)
    clock.advance(35.0)

    # Trigger expiration check
    expired = mgr.check_expirations()
    assert len(expired) == 1
    assert expired[0].lease_token == r1.lease.lease_token  # type: ignore[union-attr]
    assert expired[0].state == LeaseState.EXPIRED

    # Agent-2 should have been granted automatically upon agent-1's expiration!
    granted_pulses = [p for p in bus.published if p.type == "resource.granted"]
    assert len(granted_pulses) == 2
    assert res.allocated_capacity == 1


def test_crash_recovery_from_store() -> None:
    clock = FakeClock(datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc))
    store = InMemoryResourceStore()

    # Session 1
    bus1 = SpyPulseBus()
    mgr1 = ResourceManager(bus=bus1, clock=clock, store=store)
    ident = ResourceIdentity("gpu", "host", "cuda-0")
    res = Resource(identity=ident, space_id="space-rec", total_capacity=1)
    mgr1.register_resource(res)

    r1 = mgr1.acquire(
        "space-rec", "agent-1", ident, duration_seconds=100.0, idempotency_key="idemp-rec"
    )
    assert r1.granted
    token = r1.lease.lease_token  # type: ignore[union-attr]

    # Advance clock 20s (lease still active)
    clock.advance(20.0)

    # Session 2: Crash & restart
    bus2 = SpyPulseBus()
    mgr2 = ResourceManager(bus=bus2, clock=clock, store=store)
    mgr2.recover_from_store("space-rec")

    # Verify resource restored
    recovered_res = mgr2.get_resource(ident)
    assert recovered_res is not None
    assert recovered_res.allocated_capacity == 1

    # Verify lease is active in recovered manager
    recovered_lease = mgr2._lease_manager.get_lease(token)
    assert recovered_lease is not None
    assert recovered_lease.state == LeaseState.ACTIVE

    # Verify idempotency key lookup survives restart
    r_retry = mgr2.acquire("space-rec", "agent-1", ident, idempotency_key="idemp-rec")
    assert r_retry.granted
    assert r_retry.cached
    assert r_retry.lease.lease_token == token  # type: ignore[union-attr]
