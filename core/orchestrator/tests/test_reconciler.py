"""Unit tests for PlanReconciler.

spec §4, §10, ROADMAP Phase 4, ADR-0008, OPEN-008 — Phase 4
"""

from __future__ import annotations

from ryu.pulse_bus.bus import PulseBus
from ryu.pulse_bus.pulse import Pulse

from core.orchestrator.adapter import Adapter
from core.orchestrator.monitor import Monitor
from core.orchestrator.reconciler import PlanReconciler
from core.plans.delta import PlanDelta
from core.space.kernel import SpaceKernel


class SpyPulseBus(PulseBus):
    def __init__(self) -> None:
        super().__init__()
        self.published: list[Pulse] = []

    def publish(self, pulse: Pulse) -> Pulse:
        self.published.append(pulse)
        return super().publish(pulse)


def test_reconciler_transient_failure_bounded_retries() -> None:
    bus = SpyPulseBus()
    space_id = "space-rec-1"
    kernel = SpaceKernel(space_id=space_id, owner_id="user-1", bus=bus)
    monitor = Monitor(space_id=space_id)
    adapter = Adapter(space_id=space_id, bus=bus)
    reconciler = PlanReconciler(
        space_id=space_id,
        monitor=monitor,
        adapter=adapter,
        kernel=kernel,
        bus=bus,
    )

    # Attempt 1 -> Local retry
    r1 = reconciler.reconcile_task_failure("task-1", "transient.timeout", "timed out")
    assert r1.reconciled is True
    assert r1.action_taken == "retried"
    assert r1.details["attempt"] == 1

    # Attempt 2 -> Local retry
    r2 = reconciler.reconcile_task_failure("task-1", "transient.timeout", "timed out")
    assert r2.action_taken == "retried"
    assert r2.details["attempt"] == 2

    # Attempt 3 -> Local retry
    r3 = reconciler.reconcile_task_failure("task-1", "transient.timeout", "timed out")
    assert r3.action_taken == "retried"
    assert r3.details["attempt"] == 3

    # Attempt 4 (retries exhausted) -> Reassign via PlanDelta
    r4 = reconciler.reconcile_task_failure("task-1", "transient.timeout", "timed out")
    assert r4.reconciled is True
    assert r4.action_taken == "reassigned"
    assert r4.plan_version == 2


def test_reconciler_terminal_failure_escalation() -> None:
    bus = SpyPulseBus()
    space_id = "space-rec-2"
    kernel = SpaceKernel(space_id=space_id, owner_id="user-1", bus=bus)
    monitor = Monitor(space_id=space_id)
    adapter = Adapter(space_id=space_id, bus=bus)
    reconciler = PlanReconciler(
        space_id=space_id,
        monitor=monitor,
        adapter=adapter,
        kernel=kernel,
        bus=bus,
    )

    # terminal.permission_denied must escalate immediately (zero retries)
    r = reconciler.reconcile_task_failure(
        "task-sec", "terminal.permission_denied", "Unauthorized capability"
    )
    assert r.reconciled is False
    assert r.action_taken == "escalated"
    assert r.details["escalation_target"] == "human_gate"


def test_reconciler_automatic_rebase_on_stale_delta() -> None:
    bus = SpyPulseBus()
    space_id = "space-rec-3"
    kernel = SpaceKernel(space_id=space_id, owner_id="user-1", bus=bus)
    monitor = Monitor(space_id=space_id)
    adapter = Adapter(space_id=space_id, bus=bus)
    reconciler = PlanReconciler(
        space_id=space_id,
        monitor=monitor,
        adapter=adapter,
        kernel=kernel,
        bus=bus,
    )

    # Version in Kernel starts at 1
    assert kernel.get_plan_version() == 1

    # Simulate concurrent plan commit in Kernel advancing version to 2
    delta_concurrent = PlanDelta(
        space_id=space_id,
        base_version=1,
        resulting_version=2,
        ops=[{"op": "add", "target_node_id": "concurrent-node", "capability": "compute"}],
    )
    ok, ver, _ = kernel.commit_plan_delta(delta_concurrent)
    assert ok is True
    assert ver == 2

    # Now reconciler attempts to submit stale delta based on version 1
    stale_delta = PlanDelta(
        space_id=space_id,
        base_version=1,
        resulting_version=2,
        ops=[{"op": "add", "target_node_id": "reconciler-node", "capability": "compute"}],
    )

    success, final_ver = reconciler.commit_delta_with_rebase(stale_delta)
    # The reconciler detected the stale version and automatically rebased onto version 2!
    assert success is True
    assert final_ver == 3
    assert kernel.get_plan_version() == 3
