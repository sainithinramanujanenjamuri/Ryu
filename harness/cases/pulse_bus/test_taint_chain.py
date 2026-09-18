"""Harness case: Taint chain propagation and forward-only clearance.

Architecture acceptance criterion (docs/Architecture §10):
  "any Pulse whose payload originates from external content is published with
   taint: true. Every downstream Pulse that lists it as parent_pulse_id inherits
   taint: true. A security.taint.cleared Pulse with a given correlation_id, published
   only after human review and re-approval, causes all downstream Pulses born after it
   under that correlation_id to publish with taint: false.
   Pulses born before the clear retain taint: true."

Verifies TAINT-002 and TAINT-003 (Phase 0 / Phase 1 boundary):
  - Tainted root → 2 downstream pulses inherit taint=True.
  - security.taint.cleared for the correlation_id → next pulse gets taint=False.
  - Earlier pulses remain unchanged with taint=True.

spec §10, PULSE-006, TAINT-002, TAINT-003
ROADMAP Phase 0 §88 — harness/cases/pulse_bus/test_taint_chain.py
"""

import pytest
from ryu.pulse_bus.bus import PulseBus
from ryu.pulse_bus.pulse import Pulse


@pytest.fixture()
def bus() -> PulseBus:
    return PulseBus()


def test_tainted_root_downstream_inherit_then_clear(bus: PulseBus) -> None:
    """
    Full taint chain harness:
      1. Publish tainted root.
      2. Publish 2 children — both must inherit taint=True.
      3. Publish security.taint.cleared for the correlation.
      4. Publish 1 child after clear — must get taint=False.
      5. Verify earlier pulses still have taint=True (forward-only clearance).
    """
    corr = "corr-taint-harness"

    # Step 1: tainted root (e.g. content from external source)
    root = bus.publish(Pulse(
        id="th-root",
        type="space.created",
        payload={"space_id": "space-taint", "owner_id": "user-001"},
        space_id="space-taint",
        source="channel.external",
        correlation_id=corr,
        taint=True,
    ))
    assert root.taint is True, "Root must be tainted"

    # Step 2a: first downstream child
    child1 = bus.publish(Pulse(
        id="th-child1",
        type="task.started",
        payload={"task_id": "task-001", "plan_version": 1},
        space_id="space-taint",
        source="worker",
        correlation_id=corr,
        parent_pulse_id="th-root",
    ))
    assert child1.taint is True, "First child of tainted root must inherit taint=True"

    # Step 2b: second downstream child
    child2 = bus.publish(Pulse(
        id="th-child2",
        type="task.started",
        payload={"task_id": "task-002", "plan_version": 1},
        space_id="space-taint",
        source="worker",
        correlation_id=corr,
        parent_pulse_id="th-root",
    ))
    assert child2.taint is True, "Second child of tainted root must inherit taint=True"

    # Step 3: human reviews and clears taint for this correlation
    clear = bus.publish(Pulse(
        id="th-clear",
        type="security.taint.cleared",
        payload={
            "correlation_id": corr,
            "approver_id": "human-001",
            "scope": "chain",
            "timestamp": "2026-09-18T12:00:10Z",
        },
        space_id="space-taint",
        source="security",
        correlation_id=corr,
    ))
    assert clear.type == "security.taint.cleared"

    # Step 4: child born after clear must be clean
    child_after = bus.publish(Pulse(
        id="th-after",
        type="task.started",
        payload={"task_id": "task-003", "plan_version": 1},
        space_id="space-taint",
        source="worker",
        correlation_id=corr,
        parent_pulse_id="th-root",
    ))
    assert child_after.taint is False, (
        "Child born after security.taint.cleared must get taint=False (forward-only clearance)"
    )

    # Step 5: verify pre-clear pulses still have taint=True in the log
    log_by_id = {p.id: p for p in bus.log()}
    assert log_by_id["th-child1"].taint is True, "Pre-clear child1 must remain taint=True"
    assert log_by_id["th-child2"].taint is True, "Pre-clear child2 must remain taint=True"


def test_taint_clear_does_not_affect_different_correlation(bus: PulseBus) -> None:
    """Taint clearance for one correlation must not affect a different correlation."""
    corr_a = "corr-taint-A"
    corr_b = "corr-taint-B"

    # Publish tainted root for A
    bus.publish(Pulse(
        id="a-root",
        type="space.created",
        payload={"space_id": "space-A", "owner_id": "user-001"},
        space_id="space-A",
        source="harness",
        correlation_id=corr_a,
        taint=True,
    ))

    # Publish tainted root for B
    bus.publish(Pulse(
        id="b-root",
        type="space.created",
        payload={"space_id": "space-B", "owner_id": "user-002"},
        space_id="space-B",
        source="harness",
        correlation_id=corr_b,
        taint=True,
    ))

    # Clear taint only for A
    bus.publish(Pulse(
        id="a-clear",
        type="security.taint.cleared",
        payload={
            "correlation_id": corr_a,
            "approver_id": "human-001",
            "scope": "chain",
            "timestamp": "2026-09-18T12:00:10Z",
        },
        space_id="space-A",
        source="security",
        correlation_id=corr_a,
    ))

    # Child of B must still be tainted
    child_b = bus.publish(Pulse(
        id="b-child",
        type="task.started",
        payload={"task_id": "task-B", "plan_version": 1},
        space_id="space-B",
        source="harness",
        correlation_id=corr_b,
        parent_pulse_id="b-root",
    ))
    assert child_b.taint is True, "Clearance for corr-A must not affect corr-B"

    # Child of A must now be clean
    child_a = bus.publish(Pulse(
        id="a-child",
        type="task.started",
        payload={"task_id": "task-A", "plan_version": 1},
        space_id="space-A",
        source="harness",
        correlation_id=corr_a,
        parent_pulse_id="a-root",
    ))
    assert child_a.taint is False, "Child after clearance for corr-A must be clean"

