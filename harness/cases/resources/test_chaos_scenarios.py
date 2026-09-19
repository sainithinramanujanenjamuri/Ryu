"""Chaos Harness v1: Systematic fault-injection scenarios.

Tests 10 mandatory failure/chaos scenarios under contention, races, and injection:
1. database unavailable
2. transaction failure
3. resource contention
4. lease expiration race
5. concurrent acquisition
6. concurrent release
7. manager restart
8. stale lease
9. duplicate request
10. cancellation race
spec §9, §10, Phase 3 Chaos Harness v1
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pytest
from ryu.pulse_bus.bus import PulseBus
from ryu.pulse_bus.pulse import Pulse

from core.resources.clock import FakeClock
from core.resources.identity import Resource, ResourceIdentity
from core.resources.lease import Lease, LeaseState
from core.resources.manager import ResourceAcquisitionResult, ResourceManager
from core.resources.store import InMemoryResourceStore


class FaultyStore(InMemoryResourceStore):
    """Store that injects database connection and transaction faults."""

    def __init__(self, db_unavailable: bool = False, fail_transactions: bool = False) -> None:
        super().__init__()
        self.db_unavailable = db_unavailable
        self.fail_transactions = fail_transactions

    def save_lease(self, lease: Lease) -> None:
        if self.db_unavailable:
            raise ConnectionError("Database cluster unreachable (injected fault)")
        if self.fail_transactions:
            raise RuntimeError(
                "PostgreSQL transaction aborted: serialization_failure (injected fault)"
            )
        super().save_lease(lease)


class SpyPulseBus(PulseBus):
    def __init__(self) -> None:
        super().__init__()
        self.published: list[Pulse] = []

    def publish(self, pulse: Pulse) -> Pulse:
        self.published.append(pulse)
        return super().publish(pulse)


# Scenario 1: Database unavailable
def test_chaos_01_database_unavailable() -> None:
    bus = SpyPulseBus()
    store = FaultyStore(db_unavailable=True)
    mgr = ResourceManager(bus=bus, store=store)
    res_id = ResourceIdentity("gpu", "local", "cuda-0")
    mgr._resources[res_id.to_handle()] = Resource(identity=res_id, space_id="s1")

    with pytest.raises(ConnectionError, match="Database cluster unreachable"):
        mgr.acquire("s1", "a1", res_id)


# Scenario 2: Transaction failure
def test_chaos_02_transaction_failure() -> None:
    bus = SpyPulseBus()
    store = FaultyStore(fail_transactions=True)
    mgr = ResourceManager(bus=bus, store=store)
    res_id = ResourceIdentity("gpu", "local", "cuda-0")
    mgr._resources[res_id.to_handle()] = Resource(identity=res_id, space_id="s1")

    with pytest.raises(RuntimeError, match="PostgreSQL transaction aborted"):
        mgr.acquire("s1", "a1", res_id)


# Scenario 3: Resource contention
def test_chaos_03_resource_contention() -> None:
    bus = SpyPulseBus()
    mgr = ResourceManager(bus=bus)
    res_id = ResourceIdentity("gpu", "local", "cuda-0")
    mgr.register_resource(Resource(identity=res_id, space_id="s1", total_capacity=1))

    # A acquires
    r_a = mgr.acquire("s1", "agent-a", res_id)
    assert r_a.granted

    # B and C contend
    r_b = mgr.acquire("s1", "agent-b", res_id)
    r_c = mgr.acquire("s1", "agent-c", res_id)

    assert not r_b.granted and r_b.queue_position == 1
    assert not r_c.granted and r_c.queue_position == 2

    # Verify conflict pulses published with positions
    conflicts = [p for p in bus.published if p.type == "resource.conflict"]
    assert len(conflicts) == 2
    assert conflicts[0].payload["queue_position"] == 1
    assert conflicts[1].payload["queue_position"] == 2


# Scenario 4: Lease expiration race
def test_chaos_04_lease_expiration_race() -> None:
    clock = FakeClock(datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc))
    bus = SpyPulseBus()
    mgr = ResourceManager(bus=bus, clock=clock)
    res_id = ResourceIdentity("gpu", "local", "cuda-0")
    mgr.register_resource(Resource(identity=res_id, space_id="s1", total_capacity=1))

    # Lease acquired for 10s
    res = mgr.acquire("s1", "agent-1", res_id, duration_seconds=10.0)
    token = res.lease.lease_token  # type: ignore[union-attr]

    # Advance clock past expiry
    clock.advance(15.0)

    # Expiration sweep marks it expired
    expired = mgr.check_expirations()
    assert len(expired) == 1
    assert expired[0].state == LeaseState.EXPIRED

    # Holder attempts renewal after expiration -> rejected!
    with pytest.raises(ValueError, match="Cannot renew lease"):
        mgr.renew("s1", "agent-1", token)


# Scenario 5: Concurrent acquisition race
def test_chaos_05_concurrent_acquisition() -> None:
    bus = SpyPulseBus()
    mgr = ResourceManager(bus=bus)
    res_id = ResourceIdentity("gpu", "local", "cuda-0")
    res = Resource(identity=res_id, space_id="s1", total_capacity=1)
    mgr.register_resource(res)

    results: list[ResourceAcquisitionResult] = []

    def racer(i: int) -> ResourceAcquisitionResult:
        return mgr.acquire("s1", f"agent-{i}", res_id)

    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(racer, i) for i in range(50)]
        for f in futures:
            results.append(f.result())

    granted = [r for r in results if r.granted]
    assert len(granted) == 1, "Exactly one grant under race"
    assert res.allocated_capacity == 1


# Scenario 6: Concurrent release race
def test_chaos_06_concurrent_release() -> None:
    bus = SpyPulseBus()
    mgr = ResourceManager(bus=bus)
    res_id = ResourceIdentity("gpu", "local", "cuda-0")
    res = Resource(identity=res_id, space_id="s1", total_capacity=1)
    mgr.register_resource(res)

    r = mgr.acquire("s1", "agent-1", res_id)
    token = r.lease.lease_token  # type: ignore[union-attr]

    # Multiple threads race to release the same lease
    def releaser() -> bool:
        return mgr.release("s1", "agent-1", token)

    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(releaser) for _ in range(10)]
        results = [f.result() for f in futures]

    # All returns True (idempotent release), capacity cleanly at 0
    assert all(results)
    assert res.allocated_capacity == 0


# Scenario 7: Manager restart & recovery
def test_chaos_07_manager_restart() -> None:
    clock = FakeClock(datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc))
    store = InMemoryResourceStore()

    # Manager 1 acquires lease
    mgr1 = ResourceManager(bus=SpyPulseBus(), clock=clock, store=store)
    res_id = ResourceIdentity("gpu", "cluster", "node-gpu")
    mgr1.register_resource(Resource(identity=res_id, space_id="s1", total_capacity=1))
    r = mgr1.acquire("s1", "agent-1", res_id, duration_seconds=60.0)
    token = r.lease.lease_token  # type: ignore[union-attr]

    # Restart: Manager 2 loads from store
    mgr2 = ResourceManager(bus=SpyPulseBus(), clock=clock, store=store)
    mgr2.recover_from_store("s1")

    recovered_lease = mgr2._lease_manager.get_lease(token)
    assert recovered_lease is not None
    assert recovered_lease.state == LeaseState.ACTIVE
    assert mgr2.get_resource(res_id).allocated_capacity == 1  # type: ignore[union-attr]


# Scenario 8: Stale lease access
def test_chaos_08_stale_lease() -> None:
    clock = FakeClock(datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc))
    mgr = ResourceManager(bus=SpyPulseBus(), clock=clock)
    res_id = ResourceIdentity("gpu", "local", "cuda-0")
    mgr.register_resource(Resource(identity=res_id, space_id="s1", total_capacity=1))

    r = mgr.acquire("s1", "agent-1", res_id, duration_seconds=10.0)
    token = r.lease.lease_token  # type: ignore[union-attr]

    # Advance clock past expiry
    clock.advance(20.0)
    mgr.check_expirations()

    # Attempt release of already expired lease
    with pytest.raises(PermissionError):
        # Stale attempt from wrong space
        mgr.release("s2", "agent-1", token)


# Scenario 9: Duplicate request idempotency
def test_chaos_09_duplicate_request() -> None:
    mgr = ResourceManager(bus=SpyPulseBus())
    res_id = ResourceIdentity("gpu", "local", "cuda-0")
    res = Resource(identity=res_id, space_id="s1", total_capacity=1)
    mgr.register_resource(res)

    r1 = mgr.acquire("s1", "agent-1", res_id, idempotency_key="idemp-dup")
    r2 = mgr.acquire("s1", "agent-1", res_id, idempotency_key="idemp-dup")

    assert r1.granted and r2.granted
    assert r2.cached is True
    assert r1.lease.lease_token == r2.lease.lease_token  # type: ignore[union-attr]
    assert res.allocated_capacity == 1


# Scenario 10: Cancellation race
def test_chaos_10_cancellation_race() -> None:
    mgr = ResourceManager(bus=SpyPulseBus())
    res_id = ResourceIdentity("gpu", "local", "cuda-0")
    mgr.register_resource(Resource(identity=res_id, space_id="s1", total_capacity=1))

    # Hold the resource
    mgr.acquire("s1", "holder", res_id)

    # Queue a request
    queue = mgr._queues[res_id.to_handle()]
    req, _ = queue.enqueue("s1", "agent-cancelling")

    # Cancel the request
    cancelled = mgr.cancel_request("s1", "agent-cancelling", res_id, req.request_id)
    assert cancelled

    # Repeated cancel returns False
    cancelled_again = mgr.cancel_request("s1", "agent-cancelling", res_id, req.request_id)
    assert not cancelled_again
