"""Unit tests for Plan Versioning, TaskGraph, PlanStore CAS, and In-Flight resolution.

spec §16 (TaskGraph & PlanDelta), PLAN-001/002/003/004, ADR-0003 — Phase 2
"""

from __future__ import annotations

import concurrent.futures

import pytest
from ryu.pulse_bus.pulse import Pulse

from core.plans.delta import DeltaOp, PlanDelta
from core.plans.inflight_resolve import resolve_inflight_node
from core.plans.plan_store import PlanStore
from core.plans.task_graph import TaskGraph, TaskNode


class SpyPulseBus:
    def __init__(self) -> None:
        self.published: list[Pulse] = []

    def publish(self, pulse: Pulse) -> Pulse:
        self.published.append(pulse)
        return pulse


def test_task_graph_and_node_initialization() -> None:
    node1 = TaskNode(id="n1", capability="browse", state="pending", optional=True)
    graph = TaskGraph(space_id="space-1", plan_version=1, nodes=[node1])

    assert graph.plan_version == 1
    assert graph.get_node("n1") == node1
    assert graph.get_node("non-existent") is None

    with pytest.raises(ValueError):
        TaskNode(id="n2", capability="eval", state="invalid_state")


def test_plan_delta_validation() -> None:
    delta = PlanDelta(
        space_id="space-1",
        base_version=1,
        resulting_version=2,
        ops=[{"op": "add", "target_node_id": "n2", "capability": "search"}],
    )
    assert delta.base_version == 1
    assert delta.resulting_version == 2

    # Non-monotonic resulting_version must raise ValueError
    with pytest.raises(ValueError):
        PlanDelta(space_id="space-1", base_version=1, resulting_version=3, ops=[])

    # Unknown operation must raise ValueError
    with pytest.raises(ValueError):
        PlanDelta(
            space_id="space-1",
            base_version=1,
            resulting_version=2,
            ops=[{"op": "forbidden_op", "target_node_id": "n"}],
        )

    # DeltaOp validation
    with pytest.raises(ValueError):
        DeltaOp(op="destroy", target_node_id="n1")


def test_cas_single_writer_success() -> None:
    bus = SpyPulseBus()
    store = PlanStore(bus=bus)
    store.init_space_plan("space-1")

    assert store.get_plan_version("space-1") == 1

    delta = PlanDelta(
        space_id="space-1",
        base_version=1,
        resulting_version=2,
        ops=[{"op": "add", "target_node_id": "task-A", "capability": "python.execute"}],
    )

    success, ver, winning_id = store.commit_delta(delta)
    assert success is True
    assert ver == 2
    assert winning_id is None
    assert store.get_plan_version("space-1") == 2

    graph = store.get_task_graph("space-1")
    assert graph.get_node("task-A") is not None

    # Verified plan.delta pulse published
    assert len(bus.published) == 1
    assert bus.published[0].type == "plan.delta"
    assert bus.published[0].payload["base_version"] == 1
    assert bus.published[0].payload["resulting_version"] == 2


def test_cas_single_writer_failure_on_stale_base() -> None:
    bus = SpyPulseBus()
    store = PlanStore(bus=bus)
    store.init_space_plan("space-1")

    # Winner commits base 1 -> 2
    delta1 = PlanDelta(space_id="space-1", base_version=1, resulting_version=2, ops=[])
    success1, ver1, _ = store.commit_delta(delta1)
    assert success1 is True
    assert ver1 == 2

    # Stale writer attempts base 1 -> 2
    delta2 = PlanDelta(space_id="space-1", base_version=1, resulting_version=2, ops=[])
    success2, ver2, winning_id = store.commit_delta(delta2)
    assert success2 is False
    assert ver2 == 2
    assert winning_id == delta1.delta_id

    # Verified plan.version.superseded published
    assert len(bus.published) == 2
    assert bus.published[1].type == "plan.version.superseded"
    assert bus.published[1].payload["superseded_version"] == 1
    assert bus.published[1].payload["current_version"] == 2
    assert bus.published[1].payload["winning_delta_id"] == delta1.delta_id


def test_concurrent_cas_race_single_winner() -> None:
    """Mandatory concurrent race test: exactly one winner out of N racing writers."""
    bus = SpyPulseBus()
    store = PlanStore(bus=bus)
    store.init_space_plan("space-race")

    concurrency = 20
    deltas = [
        PlanDelta(
            space_id="space-race",
            base_version=1,
            resulting_version=2,
            ops=[{"op": "add", "target_node_id": f"node-{i}", "capability": "exec"}],
        )
        for i in range(concurrency)
    ]

    results: list[tuple[bool, int, str | None]] = []

    def commit(d: PlanDelta) -> tuple[bool, int, str | None]:
        return store.commit_delta(d)

    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = [executor.submit(commit, d) for d in deltas]
        for f in concurrent.futures.as_completed(futures):
            results.append(f.result())

    # Exactly 1 success, 19 failures
    success_count = sum(1 for r in results if r[0] is True)
    failure_count = sum(1 for r in results if r[0] is False)
    assert success_count == 1
    assert failure_count == concurrency - 1

    # Authoritative plan_version is exactly 2
    assert store.get_plan_version("space-race") == 2


def test_livelock_bounded_rebase_escalation() -> None:
    """ADR-0003: 3 failed rebase attempts emit task.failed with terminal.plan_livelock."""
    bus = SpyPulseBus()
    store = PlanStore(bus=bus, max_rebases=3)
    store.init_space_plan("space-livelock")

    # Increment authoritative version to 5
    for i in range(1, 5):
        store.commit_delta(
            PlanDelta(space_id="space-livelock", base_version=i, resulting_version=i + 1, ops=[])
        )
    assert store.get_plan_version("space-livelock") == 5

    # Proposal "prop-1" repeatedly attempts stale commits
    stale_delta = PlanDelta(
        space_id="space-livelock", base_version=1, resulting_version=2, ops=[]
    )

    # Attempt 1
    store.commit_delta(stale_delta, proposal_id="prop-1")
    # Attempt 2
    store.commit_delta(stale_delta, proposal_id="prop-1")
    # Attempt 3 (hits max_rebases)
    store.commit_delta(stale_delta, proposal_id="prop-1")

    # Verify task.failed was published
    failed_pulses = [p for p in bus.published if p.type == "task.failed"]
    assert len(failed_pulses) == 1
    assert failed_pulses[0].severity == "error"
    assert failed_pulses[0].payload["task_id"] == "prop-1"
    assert failed_pulses[0].payload["error_class"] == "terminal.plan_livelock"
    assert failed_pulses[0].payload["plan_version"] == 5


def test_inflight_resolution_rules() -> None:
    n_unaffected = TaskNode(id="n_stay", capability="c")
    n_modified = TaskNode(id="n_mod", capability="c")
    n_removed = TaskNode(id="n_del", capability="c")

    delta = PlanDelta(
        space_id="space-1",
        base_version=1,
        resulting_version=2,
        ops=[
            {"op": "reassign", "target_node_id": "n_mod", "params": {"worker": "w2"}},
            {"op": "remove", "target_node_id": "n_del"},
        ],
    )

    assert resolve_inflight_node(n_unaffected, delta) == "finish"
    assert resolve_inflight_node(n_modified, delta) == "checkpoint"
    assert resolve_inflight_node(n_removed, delta) == "cancel"

