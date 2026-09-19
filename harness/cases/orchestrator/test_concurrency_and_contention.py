"""Harness cases: Concurrency and contention reconciliation.

spec §4, §9, §16, CONTRACT_MATRIX PLAN-002, RESOURCE-005..006 — Phase 4
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from ryu.pulse_bus.bus import PulseBus

from core.orchestrator.orchestrator import SpaceOrchestrator
from core.plans.delta import PlanDelta
from core.resources.identity import Resource, ResourceIdentity
from core.resources.manager import ResourceManager
from core.space.kernel import SpaceKernel


def test_resource_contention_reconciliation() -> None:
    """Prompt Section 15: Resource Contention Reconciliation.

    Task A and Task B race for exclusive GPU instance.
    Task A wins lease; Task B is queued and receives resource.conflict with queue_position = 1.
    Orchestrator Monitor tracks the contention state accurately.
    """
    bus = PulseBus()
    space_id = "space-contend"
    kernel = SpaceKernel(space_id=space_id, owner_id="user-1", bus=bus)
    rm = ResourceManager(bus=bus)
    orch = SpaceOrchestrator(space_id=space_id, kernel=kernel, resource_mgr=rm, bus=bus)

    gpu_id = ResourceIdentity(resource_type="gpu", provider_id="node-1", instance_id="cuda-0")
    rm.register_resource(Resource(identity=gpu_id, space_id=space_id, total_capacity=1))

    # Task A acquires GPU
    res_a = orch.request_resource_lease(requester_id="worker-A", identity=gpu_id)
    assert res_a.granted is True
    assert res_a.lease is not None

    # Task B requests same GPU
    res_b = orch.request_resource_lease(requester_id="worker-B", identity=gpu_id)
    assert res_b.granted is False
    assert res_b.queue_position == 1

    # Invariant: Monitor state accurately reflects held lease and queue position
    assert orch.monitor.state.held_leases["gpu/node-1/cuda-0"] == res_a.lease.lease_token
    assert orch.monitor.state.queued_resources["gpu/node-1/cuda-0"] == 1

    # Task A releases GPU -> Task B is automatically granted lease from queue
    orch.release_resource_lease(requester_id="worker-A", lease_token=res_a.lease.lease_token)
    assert orch.monitor.state.held_leases["gpu/node-1/cuda-0"] != res_a.lease.lease_token
    assert orch.monitor.state.queued_resources.get("gpu/node-1/cuda-0", 0) == 0


def test_concurrent_orchestrators_race_for_plan_cas() -> None:
    """Prompt Section 19: Concurrent Orchestrators.

    Two Orchestrator instances (A and B) run against the same Space Kernel.
    Both observe Plan version = N.
    Both attempt to commit a PlanDelta simultaneously.
    Expected: Exactly one succeeds; the loser receives plan.version.superseded.
    Neither may bypass CAS.
    """
    bus = PulseBus()
    space_id = "space-concurrent-orch"
    kernel = SpaceKernel(space_id=space_id, owner_id="user-1", bus=bus)
    rm = ResourceManager(bus=bus)

    orch_a = SpaceOrchestrator(space_id=space_id, kernel=kernel, resource_mgr=rm, bus=bus)
    orch_b = SpaceOrchestrator(space_id=space_id, kernel=kernel, resource_mgr=rm, bus=bus)
    assert orch_a.space_id == orch_b.space_id == space_id

    # Current version is 1
    assert kernel.get_plan_version() == 1

    delta_a = PlanDelta(
        space_id=space_id,
        base_version=1,
        resulting_version=2,
        ops=[{"op": "add", "target_node_id": "task-orch-A", "capability": "compute"}],
    )
    delta_b = PlanDelta(
        space_id=space_id,
        base_version=1,
        resulting_version=2,
        ops=[{"op": "add", "target_node_id": "task-orch-B", "capability": "compute"}],
    )

    results: list[tuple[bool, int, str | None]] = []

    def commit_delta(delta: PlanDelta) -> tuple[bool, int, str | None]:
        return kernel.commit_plan_delta(delta)

    with ThreadPoolExecutor(max_workers=2) as executor:
        f_a = executor.submit(commit_delta, delta_a)
        f_b = executor.submit(commit_delta, delta_b)
        results = [f_a.result(), f_b.result()]

    successes = [r for r in results if r[0] is True]
    failures = [r for r in results if r[0] is False]

    # Exactly one CAS winner!
    assert len(successes) == 1, f"Expected 1 winner, got {len(successes)}"
    assert len(failures) == 1, f"Expected 1 superseded loser, got {len(failures)}"
    assert kernel.get_plan_version() == 2
