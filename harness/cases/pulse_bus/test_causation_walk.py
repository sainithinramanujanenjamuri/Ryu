"""Harness case: Causation walk reconstructs parent chain.

Architecture acceptance criterion (docs/Architecture §6):
  "from any Pulse, walk parent_pulse_id back to root to reconstruct the
   exact causal chain; cross-check against correlation_id to confirm it
   belongs to the same Command."

Verifies PULSE-004 and PULSE-005:
  - A 3-pulse chain (root → mid → leaf) connected via parent_pulse_id
    can be fully reconstructed by walk_causation.
  - All three pulses share the same correlation_id.
  - walk_causation returns chain in root-to-leaf order.

spec §6 (Pulse Bus), PULSE-004, PULSE-005
ROADMAP Phase 0 §87 — harness/cases/pulse_bus/test_causation_walk.py
"""

import pytest
from ryu.pulse_bus.bus import PulseBus
from ryu.pulse_bus.pulse import Pulse


@pytest.fixture()
def bus() -> PulseBus:
    return PulseBus()


def _space_created(pulse_id: str, corr: str, parent: str | None = None) -> Pulse:
    return Pulse(
        id=pulse_id,
        type="space.created",
        payload={"space_id": "space-walk", "owner_id": "user-001"},
        space_id="space-walk",
        source="harness",
        correlation_id=corr,
        parent_pulse_id=parent,
    )


def _task_started(pulse_id: str, corr: str, parent: str | None = None) -> Pulse:
    return Pulse(
        id=pulse_id,
        type="task.started",
        payload={"task_id": "task-001", "plan_version": 1},
        space_id="space-walk",
        source="harness",
        correlation_id=corr,
        parent_pulse_id=parent,
    )


def _task_completed(pulse_id: str, corr: str, parent: str | None = None) -> Pulse:
    return Pulse(
        id=pulse_id,
        type="task.completed",
        payload={"task_id": "task-001", "result_ref": "ref-001", "plan_version": 1},
        space_id="space-walk",
        source="harness",
        correlation_id=corr,
        parent_pulse_id=parent,
    )


def test_three_pulse_chain_walks_to_root(bus: PulseBus) -> None:
    """
    A 3-pulse chain must walk back to root via parent_pulse_id.

    Chain:  root (space.created)
              └── mid (task.started)
                    └── leaf (task.completed)

    walk_causation(leaf.id) must return [root, mid, leaf].
    """
    corr = "corr-causation-001"

    bus.publish(_space_created("p-root", corr))
    bus.publish(_task_started("p-mid", corr, parent="p-root"))
    bus.publish(_task_completed("p-leaf", corr, parent="p-mid"))

    chain = bus.walk_causation("p-leaf")

    assert len(chain) == 3, f"Expected 3 pulses in chain, got {len(chain)}"

    # Root-first order
    assert chain[0].id == "p-root"
    assert chain[1].id == "p-mid"
    assert chain[2].id == "p-leaf"


def test_correlation_id_consistent_across_chain(bus: PulseBus) -> None:
    """All pulses in the chain must share the same correlation_id."""
    corr = "corr-causation-002"

    bus.publish(_space_created("c-root", corr))
    bus.publish(_task_started("c-mid", corr, parent="c-root"))
    bus.publish(_task_completed("c-leaf", corr, parent="c-mid"))

    chain = bus.walk_causation("c-leaf")

    for pulse in chain:
        assert pulse.correlation_id == corr, (
            f"Pulse {pulse.id!r} has correlation_id={pulse.correlation_id!r}, "
            f"expected {corr!r}"
        )


def test_root_pulse_has_no_parent(bus: PulseBus) -> None:
    """The first pulse in the reconstructed chain must have parent_pulse_id=None."""
    corr = "corr-causation-003"
    bus.publish(_space_created("r-root", corr))
    bus.publish(_task_started("r-mid", corr, parent="r-root"))
    bus.publish(_task_completed("r-leaf", corr, parent="r-mid"))

    chain = bus.walk_causation("r-leaf")
    assert chain[0].parent_pulse_id is None, "Root pulse must have no parent"


def test_single_pulse_walk_returns_itself(bus: PulseBus) -> None:
    """Walking causation from a root-only pulse must return [root]."""
    corr = "corr-causation-004"
    bus.publish(_space_created("solo-root", corr))
    chain = bus.walk_causation("solo-root")
    assert len(chain) == 1
    assert chain[0].id == "solo-root"


def test_walk_unknown_id_raises(bus: PulseBus) -> None:
    """Walking causation for an unknown id must raise KeyError."""
    with pytest.raises(KeyError):
        bus.walk_causation("does-not-exist")

