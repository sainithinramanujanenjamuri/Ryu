"""Harness case: High-concurrency lease race.

Tests zero double-grants and accurate queue positions.
spec §9 (Resource Manager), ROADMAP Phase 3, CONTRACT_MATRIX RESOURCE-001, 002, 005, 006
"""

import os
from concurrent.futures import ThreadPoolExecutor

from ryu.pulse_bus.bus import PulseBus

from core.resources.identity import Resource, ResourceIdentity
from core.resources.manager import ResourceAcquisitionResult, ResourceManager


def test_lease_race_zero_double_grants_and_accurate_queue() -> None:
    """Stress test: N workers race for a single GPU instance.

    Verifies:
    1. Exactly 1 immediate lease grant.
    2. Every losing request is enqueued with accurate queue_position.
    3. Zero double-grants.
    4. Total allocated capacity strictly equals 1.
    """
    bus = PulseBus()
    mgr = ResourceManager(bus=bus)

    gpu_id = ResourceIdentity(resource_type="gpu", provider_id="node-1", instance_id="cuda-0")
    gpu_res = Resource(identity=gpu_id, space_id="space-race-harness", total_capacity=1)
    mgr.register_resource(gpu_res)

    # In full mode test 1,000 racers, in default smoke mode test 100 racers
    racer_count = 1000 if os.environ.get("RYU_STRESS_FULL") == "1" else 100

    results: list[ResourceAcquisitionResult] = []

    def race_worker(worker_idx: int) -> ResourceAcquisitionResult:
        return mgr.acquire(
            space_id="space-race-harness",
            requester_id=f"worker-{worker_idx}",
            identity=gpu_id,
            duration_seconds=120.0,
        )

    with ThreadPoolExecutor(max_workers=20) as executor:
        futures = [executor.submit(race_worker, i) for i in range(racer_count)]
        for f in futures:
            results.append(f.result())

    granted = [r for r in results if r.granted]
    queued = [r for r in results if not r.granted]

    assert len(granted) == 1, f"Expected exactly 1 grant, got {len(granted)}"
    assert len(queued) == racer_count - 1, (
        f"Expected {racer_count - 1} queued requests, got {len(queued)}"
    )
    assert gpu_res.allocated_capacity == 1, "Allocated capacity must strictly be 1"

    # Verify positions are dense 1..racer_count-1 with no duplicates or gaps
    positions = sorted([r.queue_position for r in queued if r.queue_position is not None])
    expected_positions = list(range(1, racer_count))
    assert positions == expected_positions, "Queue positions must be contiguous 1..N-1"
