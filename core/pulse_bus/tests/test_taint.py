"""Unit tests: Taint inheritance and forward-only clearance.

Verifies taint propagation rules per docs/Architecture §10 and TAINT-002:
  - A tainted parent causes child pulses to inherit taint=True.
  - security.taint.cleared for a correlation_id causes subsequent children
    to get taint=False (forward-only).
  - Earlier (pre-clear) pulses must remain unchanged with taint=True.

spec §10, TAINT-002, TAINT-003 — Phase 0
"""

import datetime

import pytest
from ryu.pulse_bus.bus import PulseBus
from ryu.pulse_bus.pulse import Pulse


def _ts(offset_seconds: int = 0) -> datetime.datetime:
    return datetime.datetime(2026, 9, 18, 12, 0, offset_seconds, tzinfo=datetime.timezone.utc)


@pytest.fixture()
def bus() -> PulseBus:
    return PulseBus()


def _publish_space_created(
    bus: PulseBus,
    pulse_id: str,
    corr: str,
    taint: bool = False,
    parent: str | None = None,
) -> Pulse:
    p = Pulse(
        id=pulse_id,
        type="space.created",
        payload={"space_id": "space-001", "owner_id": "user-001"},
        space_id="space-001",
        source="test",
        correlation_id=corr,
        taint=taint,
        parent_pulse_id=parent,
        timestamp=_ts(),
    )
    return bus.publish(p)


def _publish_task_started(
    bus: PulseBus, pulse_id: str, corr: str, parent: str | None = None
) -> Pulse:
    p = Pulse(
        id=pulse_id,
        type="task.started",
        payload={"task_id": "task-001", "plan_version": 1},
        space_id="space-001",
        source="test",
        correlation_id=corr,
        parent_pulse_id=parent,
        timestamp=_ts(),
    )
    return bus.publish(p)


def test_root_tainted_pulse_propagates_to_child(bus: PulseBus) -> None:
    """A tainted root pulse must cause its child to inherit taint=True."""
    root = _publish_space_created(bus, "root-1", "corr-taint", taint=True)
    assert root.taint is True, "Root must have taint=True"

    child = _publish_task_started(bus, "child-1", "corr-taint", parent="root-1")
    assert child.taint is True, "Child of tainted parent must inherit taint=True"


def test_clean_root_does_not_taint_child(bus: PulseBus) -> None:
    """A clean root pulse must NOT propagate taint to its child."""
    root = _publish_space_created(bus, "root-clean", "corr-clean", taint=False)
    assert root.taint is False

    child = _publish_task_started(bus, "child-clean", "corr-clean", parent="root-clean")
    assert child.taint is False, "Child of clean parent must remain clean"


def test_taint_cleared_forward_only(bus: PulseBus) -> None:
    """
    After security.taint.cleared for a correlation_id, subsequent children
    must get taint=False. Earlier children (pre-clear) must remain taint=True.
    """
    corr = "corr-clear-test"

    # 1. Publish tainted root
    root = _publish_space_created(bus, "root-tc", corr, taint=True)
    assert root.taint is True

    # 2. Publish child BEFORE clear — must inherit taint
    child_before = _publish_task_started(bus, "child-before", corr, parent="root-tc")
    assert child_before.taint is True, "Pre-clear child must be tainted"

    # 3. Publish security.taint.cleared for this correlation
    clear_pulse = Pulse(
        id="clear-1",
        type="security.taint.cleared",
        payload={
            "correlation_id": corr,
            "approver_id": "human-001",
            "scope": "chain",
            "timestamp": "2026-09-18T12:00:10Z",
        },
        space_id="space-001",
        source="test",
        correlation_id=corr,
        timestamp=_ts(10),
    )
    admitted_clear = bus.publish(clear_pulse)
    assert admitted_clear.id == "clear-1"

    # 4. Publish child AFTER clear — must get taint=False
    child_after = _publish_task_started(bus, "child-after", corr, parent="root-tc")
    assert child_after.taint is False, "Post-clear child must get taint=False"

    # 5. Earlier pulse must remain unchanged
    pre_clear_from_log = next(p for p in bus.log() if p.id == "child-before")
    assert pre_clear_from_log.taint is True, "Pre-clear pulse must remain taint=True"


def test_taint_chain_of_three(bus: PulseBus) -> None:
    """A three-step chain from tainted root must have all three pulses tainted."""
    corr = "corr-chain3"
    root = _publish_space_created(bus, "r1", corr, taint=True)
    mid = _publish_task_started(bus, "m1", corr, parent="r1")
    leaf = _publish_task_started(bus, "l1", corr, parent="m1")

    assert root.taint is True
    assert mid.taint is True
    assert leaf.taint is True

