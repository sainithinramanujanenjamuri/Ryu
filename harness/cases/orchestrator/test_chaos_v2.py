"""Chaos Harness v2: 15 fault-injection scenarios for Space Orchestrator resilience.

spec §4, §9, §10, §16, ROADMAP Phase 4 — Chaos Harness v2
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest
from ryu.pulse_bus.bus import PulseBus
from ryu.pulse_bus.pulse import Pulse, Severity

from core.capabilities.admission import CapabilityRequest
from core.orchestrator.goal_analyzer import Command
from core.orchestrator.orchestrator import SpaceOrchestrator
from core.plans.delta import PlanDelta
from core.resources.clock import FakeClock
from core.resources.identity import Resource, ResourceIdentity
from core.resources.manager import ResourceManager
from core.resources.store import InMemoryResourceStore
from core.space.kernel import SpaceKernel


def test_scenario_01_orchestrator_crash() -> None:
    """Scenario 1: Orchestrator crashes mid-execution; Kernel and RM state survive."""
    bus = PulseBus()
    kernel = SpaceKernel("space-c1", "user-1", bus=bus)
    rm = ResourceManager(bus=bus)
    orch = SpaceOrchestrator("space-c1", kernel, rm, bus)

    orch.submit_goal(Command("c1", "space-c1", "Objective 1"))
    assert kernel.get_plan_version() == 1

    # Crash
    orch.close()
    del orch

    # Authoritative state intact
    assert kernel.get_plan_version() == 1
    assert kernel.space_id == "space-c1"


def test_scenario_02_kernel_restart() -> None:
    """Scenario 2: Kernel checkpoints state, restarts, and restores plan version."""
    bus = PulseBus()
    kernel1 = SpaceKernel("space-c2", "user-1", bus=bus)
    chk = kernel1.create_checkpoint("chk-002")

    # Restart in new Kernel instance
    kernel2 = SpaceKernel("space-c2", "user-1", bus=bus)
    kernel2.restore_checkpoint(chk)
    assert kernel2.get_plan_version() == 1


def test_scenario_03_resource_manager_restart() -> None:
    """Scenario 3: Resource Manager crashes and recovers active leases from store."""
    clock = FakeClock(datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc))
    store = InMemoryResourceStore()
    bus = PulseBus()

    rm1 = ResourceManager(bus=bus, clock=clock, store=store)
    gpu_id = ResourceIdentity("gpu", "node-1", "cuda-0")
    rm1.register_resource(Resource(gpu_id, "space-c3", total_capacity=1))
    acq = rm1.acquire("space-c3", "agent-1", gpu_id, duration_seconds=100.0)
    assert acq.granted is True

    # RM crash and restart
    rm2 = ResourceManager(bus=bus, clock=clock, store=store)
    rm2.recover_from_store("space-c3")
    res = rm2.get_resource("gpu/node-1/cuda-0")
    assert res is not None
    assert res.allocated_capacity == 1


def test_scenario_04_concurrent_plan_proposals() -> None:
    """Scenario 4: Two concurrent plan proposals; exactly one wins CAS."""
    bus = PulseBus()
    kernel = SpaceKernel("space-c4", "user-1", bus=bus)
    delta1 = PlanDelta("space-c4", 1, 2, [{"op": "add", "target_node_id": "n1", "capability": "c"}])
    delta2 = PlanDelta("space-c4", 1, 2, [{"op": "add", "target_node_id": "n2", "capability": "c"}])

    ok1, v1, _ = kernel.commit_plan_delta(delta1)
    ok2, v2, _ = kernel.commit_plan_delta(delta2)

    assert ok1 is True
    assert ok2 is False
    assert kernel.get_plan_version() == 2


def test_scenario_05_stale_plan_delta() -> None:
    """Scenario 5: Stale PlanDelta is rejected with plan.version.superseded."""
    bus = PulseBus()
    kernel = SpaceKernel("space-c5", "user-1", bus=bus)
    # Advance version to 3
    kernel.commit_plan_delta(
        PlanDelta("space-c5", 1, 2, [{"op": "add", "target_node_id": "n1", "capability": "c"}])
    )
    kernel.commit_plan_delta(
        PlanDelta("space-c5", 2, 3, [{"op": "add", "target_node_id": "n2", "capability": "c"}])
    )

    # Stale delta with base_version=1
    stale = PlanDelta(
        "space-c5", 1, 2, [{"op": "add", "target_node_id": "stale", "capability": "c"}]
    )
    ok, ver, _ = kernel.commit_plan_delta(stale)
    assert ok is False
    assert ver == 3


def test_scenario_06_resource_contention_during_replanning() -> None:
    """Scenario 6: Resource contention occurs while replanning; losing task queued."""
    bus = PulseBus()
    kernel = SpaceKernel("space-c6", "user-1", bus=bus)
    rm = ResourceManager(bus=bus)
    orch = SpaceOrchestrator("space-c6", kernel, rm, bus)

    gpu_id = ResourceIdentity("gpu", "node-1", "cuda-0")
    rm.register_resource(Resource(gpu_id, "space-c6", total_capacity=1))

    # Worker 1 holds GPU
    r1 = orch.request_resource_lease("worker-1", gpu_id)
    assert r1.granted is True

    # Replan triggers Worker 2 to also request GPU
    r2 = orch.request_resource_lease("worker-2", gpu_id)
    assert r2.granted is False
    assert r2.queue_position == 1


def test_scenario_07_lease_expiration_during_orchestration() -> None:
    """Scenario 7: Lease expires during workflow; capacity reclaimed."""
    clock = FakeClock(datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc))
    bus = PulseBus()
    kernel = SpaceKernel("space-c7", "user-1", bus=bus)
    rm = ResourceManager(bus=bus, clock=clock)
    orch = SpaceOrchestrator("space-c7", kernel, rm, bus)

    gpu_id = ResourceIdentity("gpu", "node-1", "cuda-0")
    rm.register_resource(Resource(gpu_id, "space-c7", total_capacity=1))

    r1 = orch.request_resource_lease("worker-1", gpu_id, duration_seconds=10.0)
    assert r1.granted is True

    # Clock jumps past expiration
    clock.advance(15.0)
    rm.check_expirations()

    # Reclaimed: another worker can now acquire
    r2 = orch.request_resource_lease("worker-2", gpu_id, duration_seconds=10.0)
    assert r2.granted is True


def test_scenario_08_duplicate_goal_submission() -> None:
    """Scenario 8: Duplicate goal submission is deduplicated without re-planning."""
    bus = PulseBus()
    kernel = SpaceKernel("space-c8", "user-1", bus=bus)
    rm = ResourceManager(bus=bus)
    orch = SpaceOrchestrator("space-c8", kernel, rm, bus)

    cmd = Command("dup-cmd", "space-c8", "Objective")
    s1 = orch.submit_goal(cmd)
    s2 = orch.submit_goal(cmd)
    assert s1 is s2


def test_scenario_09_pulse_delivery_interruption() -> None:
    """Scenario 9: Unhandled or malformed event does not crash monitor."""
    bus = PulseBus()
    monitor = SpaceOrchestrator(
        "space-c9",
        SpaceKernel("space-c9", "user-1", bus=bus),
        ResourceManager(bus=bus),
        bus,
    ).monitor

    # Deliver pulse with unexpected extra fields
    monitor.handle_pulse(
        Pulse(
            id="p-extra",
            space_id="space-c9",
            type="task.started",
            severity=Severity.INFO,
            source="test",
            correlation_id="c",
            payload={"task_id": "t1", "unexpected_field": 123},
        )
    )
    assert monitor.get_task_state("t1") == "started"


def test_scenario_10_database_transaction_failure() -> None:
    """Scenario 10: Injected store transaction failure is observable and does not corrupt."""
    class FailingStore(InMemoryResourceStore):
        def save_lease(self, lease: Any) -> None:
            raise RuntimeError("Database connection aborted")

    bus = PulseBus()
    rm = ResourceManager(bus=bus, store=FailingStore())
    gpu_id = ResourceIdentity("gpu", "node-1", "cuda-0")
    rm.register_resource(Resource(gpu_id, "space-c10", total_capacity=1))

    with pytest.raises(RuntimeError, match="Database connection aborted"):
        rm.acquire("space-c10", "worker-1", gpu_id)


def test_scenario_11_budget_exhaustion_during_execution() -> None:
    """Scenario 11: Mid-execution budget exhaustion halts further dispatches."""
    bus = PulseBus()
    kernel = SpaceKernel("space-c11", "user-1", bus=bus, budget=10.0, budget_policy="hard_stop")

    # Call 1 consumes 10.0
    r1 = kernel.request_capability(
        CapabilityRequest("w1", "space-c11", "tool.compute", budget=10.0)
    )
    assert r1.status == "ok"
    kernel.admission.record_spend("space-c11", 10.0)

    # Call 2 denied (budget $0)
    r2 = kernel.request_capability(
        CapabilityRequest("w2", "space-c11", "tool.compute", budget=5.0)
    )
    assert r2.status == "denied"


def test_scenario_12_approval_timeout_during_orchestration() -> None:
    """Scenario 12: High-risk approval gate times out with default_deny."""
    bus = PulseBus()
    kernel = SpaceKernel("space-c12", "user-1", bus=bus)

    req, _ = kernel.request_approval("appr-to", "device.firmware", timeout_seconds=5.0)
    status = kernel.approval_mgr.check_timeout("appr-to", current_time=req.created_at + 10.0)
    assert status == "denied"
    assert req.status == "denied"


def test_scenario_13_cross_space_attack() -> None:
    """Scenario 13: Orchestrator A cannot access Space B resources or plans."""
    bus = PulseBus()
    kernel_b = SpaceKernel("space-B", "user-b", bus=bus)
    kernel_a = SpaceKernel("space-A", "user-a", bus=bus)
    rm_a = ResourceManager(bus=bus)
    orch_a = SpaceOrchestrator("space-A", kernel_a, rm_a, bus)

    with pytest.raises(PermissionError):
        kernel_b.verify_space_identity(orch_a.space_id)


def test_scenario_14_orchestrator_restart_after_partial_planning() -> None:
    """Scenario 14: Orchestrator restarts after initial plan; rebinds to existing session."""
    bus = PulseBus()
    kernel = SpaceKernel("space-c14", "user-1", bus=bus)
    rm = ResourceManager(bus=bus)

    orch1 = SpaceOrchestrator("space-c14", kernel, rm, bus)
    orch1.submit_goal(Command("c14", "space-c14", "Obj"))
    orch1.close()

    orch2 = SpaceOrchestrator("space-c14", kernel, rm, bus)
    # Authoritative plan remains version 1
    assert orch2.kernel.get_plan_version() == 1


def test_scenario_15_concurrent_orchestrators() -> None:
    """Scenario 15: Two orchestrators race to adapt plan; Kernel CAS resolves cleanly."""
    bus = PulseBus()
    kernel = SpaceKernel("space-c15", "user-1", bus=bus)
    rm = ResourceManager(bus=bus)
    orch1 = SpaceOrchestrator("space-c15", kernel, rm, bus)
    orch2 = SpaceOrchestrator("space-c15", kernel, rm, bus)

    d1 = PlanDelta("space-c15", 1, 2, [{"op": "add", "target_node_id": "n1", "capability": "c"}])
    d2 = PlanDelta("space-c15", 1, 2, [{"op": "add", "target_node_id": "n2", "capability": "c"}])

    ok1, v1 = orch1.propose_plan_delta(d1)
    # orch2 submits delta with old base_version=1 -> automatic rebase bumps to 3
    ok2, v2 = orch2.propose_plan_delta(d2)

    assert ok1 is True
    assert ok2 is True
    assert v1 == 2
    assert v2 == 3
    assert kernel.get_plan_version() == 3
