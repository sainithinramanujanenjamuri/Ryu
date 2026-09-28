"""Tests for Phase 12.2: Kernel CAS & Plan State Machine Integration.

Authoritative Specification: docs/PHASE_12_EXECUTION_ENGINE_SPEC.md
Architectural Decision: adr/0041-autonomous-task-dispatcher-dag-traversal-and-plan-convergence-engine.md
ADR: adr/0003-plan-cas-livelock-bound.md
SCCA Laws: Law 1 (Space Boundary), Law 2 (Requested Capabilities), Law 6 (Contained Escalation)
Core Boundary: AGENTS.md §7 (Deterministic Core Independence)
"""

from __future__ import annotations

import ast
import concurrent.futures
import os
from typing import Any

import pytest
from ryu.pulse_bus.pulse import Pulse

from core.orchestrator.dispatch_model import (
    DeterministicDispatcher,
    DispatchAction,
)
from core.plans.delta import PlanDelta
from core.plans.task_graph import (
    IllegalStateTransitionError,
    TaskNode,
)
from core.space.kernel import SpaceKernel


class SpyPulseBus:
    """In-memory pulse bus spy capturing all published pulses."""

    def __init__(self) -> None:
        self.published: list[Pulse] = []

    def publish(self, pulse: Pulse) -> Pulse:
        self.published.append(pulse)
        return pulse


def _node(target: Any, task_id: str, version: int | None = None) -> TaskNode:
    """Helper to retrieve and type-narrow a TaskNode from a SpaceKernel or TaskGraph."""
    if isinstance(target, SpaceKernel):
        graph = target.get_task_graph(version=version)
    else:
        graph = target
    node = graph.get_node(task_id)
    assert node is not None
    return node


# 1. Valid task transition through PlanDelta
def test_valid_task_transition_through_plan_delta() -> None:
    bus = SpyPulseBus()
    kernel = SpaceKernel(space_id="space-1", owner_id="owner-1", bus=bus)

    # Initialize plan with a pending task
    node = TaskNode(id="task-1", capability="compute.cpu", state="pending")
    kernel.plan_store.init_space_plan("space-1", [node])

    assert kernel.get_plan_version() == 1
    assert _node(kernel, "task-1").state == "pending"

    # Propose transition pending -> ready via PlanDelta
    delta = PlanDelta(
        space_id="space-1",
        base_version=1,
        resulting_version=2,
        ops=[
            {
                "op": "transition",
                "target_node_id": "task-1",
                "to_state": "ready",
                "from_state": "pending",
                "reason": "dependencies_cleared",
            }
        ],
    )
    success, new_version, winning_id = kernel.commit_plan_delta(delta)

    assert success is True
    assert new_version == 2
    assert winning_id is None
    assert kernel.get_plan_version() == 2
    assert _node(kernel, "task-1").state == "ready"


# 2. Successful CAS mutation (sequential monotonic advancement)
def test_successful_cas_mutation() -> None:
    kernel = SpaceKernel(space_id="space-seq", owner_id="owner-1")
    node = TaskNode(id="task-seq", capability="exec", state="pending")
    kernel.plan_store.init_space_plan("space-seq", [node])

    # Sequence of valid transitions:
    # pending -> ready (v1 -> v2)
    # ready -> admission_pending (v2 -> v3)
    # admission_pending -> admitted (v3 -> v4)
    # admitted -> lease_pending (v4 -> v5)
    # lease_pending -> leased (v5 -> v6)
    transitions = [
        ("ready", 1),
        ("admission_pending", 2),
        ("admitted", 3),
        ("lease_pending", 4),
        ("leased", 5),
    ]

    for to_state, base_ver in transitions:
        delta = PlanDelta(
            space_id="space-seq",
            base_version=base_ver,
            resulting_version=base_ver + 1,
            ops=[
                {
                    "op": "transition",
                    "target_node_id": "task-seq",
                    "to_state": to_state,
                }
            ],
        )
        success, ver, _ = kernel.commit_plan_delta(delta)
        assert success is True
        assert ver == base_ver + 1
        assert kernel.get_plan_version() == base_ver + 1
        assert _node(kernel, "task-seq").state == to_state


# 3. Stale CAS rejection
def test_stale_cas_rejection() -> None:
    bus = SpyPulseBus()
    kernel = SpaceKernel(space_id="space-stale", owner_id="owner-1", bus=bus)
    node = TaskNode(id="task-stale", capability="exec", state="pending")
    kernel.plan_store.init_space_plan("space-stale", [node])

    # Winner commits base 1 -> 2
    winner_delta = PlanDelta(
        space_id="space-stale",
        base_version=1,
        resulting_version=2,
        ops=[
            {
                "op": "transition",
                "target_node_id": "task-stale",
                "to_state": "ready",
            }
        ],
    )
    success1, ver1, _ = kernel.commit_plan_delta(winner_delta)
    assert success1 is True
    assert ver1 == 2

    # Stale writer proposes against base 1
    stale_delta = PlanDelta(
        space_id="space-stale",
        base_version=1,
        resulting_version=2,
        ops=[
            {
                "op": "transition",
                "target_node_id": "task-stale",
                "to_state": "blocked",
            }
        ],
    )
    success2, ver2, winning_id = kernel.commit_plan_delta(stale_delta)

    assert success2 is False
    assert ver2 == 2
    assert winning_id == winner_delta.delta_id
    assert kernel.get_plan_version() == 2
    # Verify state was not mutated by stale delta
    assert _node(kernel, "task-stale").state == "ready"

    # Verify plan.version.superseded pulse
    superseded_pulses = [p for p in bus.published if p.type == "plan.version.superseded"]
    assert len(superseded_pulses) == 1
    assert superseded_pulses[0].payload["superseded_version"] == 1
    assert superseded_pulses[0].payload["current_version"] == 2
    assert superseded_pulses[0].payload["winning_delta_id"] == winner_delta.delta_id


# 4. Concurrent CAS race
def test_concurrent_cas_race() -> None:
    kernel = SpaceKernel(space_id="space-race", owner_id="owner-1")
    node = TaskNode(id="task-race", capability="exec", state="pending")
    kernel.plan_store.init_space_plan("space-race", [node])

    concurrency = 20
    deltas = [
        PlanDelta(
            space_id="space-race",
            base_version=1,
            resulting_version=2,
            ops=[
                {
                    "op": "transition",
                    "target_node_id": "task-race",
                    "to_state": "ready",
                }
            ],
            delta_id=f"delta-race-{i}",
        )
        for i in range(concurrency)
    ]

    results: list[tuple[bool, int, str | None]] = []

    def attempt_commit(d: PlanDelta) -> tuple[bool, int, str | None]:
        return kernel.commit_plan_delta(d)

    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = [executor.submit(attempt_commit, d) for d in deltas]
        for f in concurrent.futures.as_completed(futures):
            results.append(f.result())

    success_count = sum(1 for r in results if r[0] is True)
    failure_count = sum(1 for r in results if r[0] is False)

    assert success_count == 1
    assert failure_count == concurrency - 1
    assert kernel.get_plan_version() == 2
    assert _node(kernel, "task-race").state == "ready"


# 5. Plan version increment & validation
def test_plan_version_increment_validation() -> None:
    # Non-monotonic resulting_version must be rejected at construction
    with pytest.raises(ValueError, match="Invalid resulting_version"):
        PlanDelta(
            space_id="space-1",
            base_version=1,
            resulting_version=3,
            ops=[],
        )

    # Base version == resulting version must be rejected
    with pytest.raises(ValueError, match="Invalid resulting_version"):
        PlanDelta(
            space_id="space-1",
            base_version=2,
            resulting_version=2,
            ops=[],
        )


# 6. Previous version immutability
def test_previous_version_immutability() -> None:
    kernel = SpaceKernel(space_id="space-immut", owner_id="owner-1")
    node = TaskNode(id="task-immut", capability="exec", state="pending")
    kernel.plan_store.init_space_plan("space-immut", [node])

    # Reference to version 1 TaskGraph
    graph_v1 = kernel.get_task_graph()
    assert graph_v1.plan_version == 1
    assert _node(graph_v1, "task-immut").state == "pending"

    # Commit transition pending -> ready -> plan_version 2
    delta = PlanDelta(
        space_id="space-immut",
        base_version=1,
        resulting_version=2,
        ops=[
            {
                "op": "transition",
                "target_node_id": "task-immut",
                "to_state": "ready",
            }
        ],
    )
    success, ver, _ = kernel.commit_plan_delta(delta)
    assert success is True
    assert ver == 2

    # Current authoritative graph is at v2
    graph_v2 = kernel.get_task_graph()
    assert graph_v2.plan_version == 2
    assert _node(graph_v2, "task-immut").state == "ready"

    # Historical v1 snapshot is completely immutable and retains pending state
    hist_v1 = kernel.get_historical_task_graph(1)
    assert hist_v1 is not None
    assert hist_v1.plan_version == 1
    assert _node(hist_v1, "task-immut").state == "pending"

    # Earlier referenced object also preserved
    assert graph_v1.plan_version == 1
    assert _node(graph_v1, "task-immut").state == "pending"

    # get_task_graph with explicit version
    assert _node(kernel, "task-immut", version=1).state == "pending"
    assert _node(kernel, "task-immut", version=2).state == "ready"


# 7. Space isolation (SCCA Law 1, SPACE-001)
def test_space_isolation_rejected() -> None:
    bus = SpyPulseBus()
    kernel_a = SpaceKernel(space_id="space-A", owner_id="owner-1", bus=bus)
    kernel_b = SpaceKernel(space_id="space-B", owner_id="owner-2", bus=bus)

    node_a = TaskNode(id="task-a", capability="exec", state="pending")
    kernel_a.plan_store.init_space_plan("space-A", [node_a])

    # Attempt to commit space-A delta through kernel_b
    delta_a = PlanDelta(
        space_id="space-A",
        base_version=1,
        resulting_version=2,
        ops=[
            {
                "op": "transition",
                "target_node_id": "task-a",
                "to_state": "ready",
            }
        ],
    )

    with pytest.raises(PermissionError, match="Space isolation violation"):
        kernel_b.commit_plan_delta(delta_a)

    # Verify no state changes on either space
    assert kernel_a.get_plan_version() == 1
    assert _node(kernel_a, "task-a").state == "pending"
    assert kernel_b.get_plan_version() == 1

    # Verify no plan.delta pulse was emitted for space-B
    plan_deltas_b = [
        p for p in bus.published if p.type == "plan.delta" and p.space_id == "space-B"
    ]
    assert len(plan_deltas_b) == 0


# 8. Malformed Space identity rejected
def test_malformed_space_identity() -> None:
    kernel = SpaceKernel(space_id="space-valid", owner_id="owner-1")

    # Mismatched or empty space_id
    delta_empty = PlanDelta(
        space_id="",
        base_version=1,
        resulting_version=2,
        ops=[{"op": "transition", "target_node_id": "t", "to_state": "ready"}],
    )

    with pytest.raises(PermissionError, match="Space isolation violation"):
        kernel.commit_plan_delta(delta_empty)

    delta_wrong = PlanDelta(
        space_id="space-other",
        base_version=1,
        resulting_version=2,
        ops=[{"op": "transition", "target_node_id": "t", "to_state": "ready"}],
    )

    with pytest.raises(PermissionError, match="Space isolation violation"):
        kernel.commit_plan_delta(delta_wrong)


# 9. Duplicate mutation request (Idempotency)
def test_duplicate_mutation_request() -> None:
    kernel = SpaceKernel(space_id="space-idemp", owner_id="owner-1")
    node = TaskNode(id="task-dup", capability="exec", state="pending")
    kernel.plan_store.init_space_plan("space-idemp", [node])

    # First request: pending -> ready
    delta = PlanDelta(
        space_id="space-idemp",
        base_version=1,
        resulting_version=2,
        ops=[
            {
                "op": "transition",
                "target_node_id": "task-dup",
                "to_state": "ready",
                "from_state": "pending",
            }
        ],
    )
    success1, ver1, _ = kernel.commit_plan_delta(delta)
    assert success1 is True
    assert ver1 == 2

    # Exact duplicate request with base_version=1: rejected by CAS
    success2, ver2, _ = kernel.commit_plan_delta(delta)
    assert success2 is False
    assert ver2 == 2

    # Re-submission with updated base_version=2 but requesting pending -> ready:
    # from_state is 'pending' but current is 'ready', so rejected
    delta_stale_from = PlanDelta(
        space_id="space-idemp",
        base_version=2,
        resulting_version=3,
        ops=[
            {
                "op": "transition",
                "target_node_id": "task-dup",
                "to_state": "ready",
                "from_state": "pending",
            }
        ],
    )
    with pytest.raises(IllegalStateTransitionError, match="expected current state 'pending'"):
        kernel.commit_plan_delta(delta_stale_from)

    # Re-submission requesting ready -> ready: illegal state transition
    delta_ready_ready = PlanDelta(
        space_id="space-idemp",
        base_version=2,
        resulting_version=3,
        ops=[
            {
                "op": "transition",
                "target_node_id": "task-dup",
                "to_state": "ready",
            }
        ],
    )
    with pytest.raises(IllegalStateTransitionError, match="Illegal state transition"):
        kernel.commit_plan_delta(delta_ready_ready)


# 10. Invalid transitions rejected
def test_invalid_transitions_rejected() -> None:
    kernel = SpaceKernel(space_id="space-invalid", owner_id="owner-1")
    node = TaskNode(id="task-inv", capability="exec", state="pending")
    kernel.plan_store.init_space_plan("space-invalid", [node])

    illegal_targets = [
        "completed",
        "running",
        "dispatched",
        "leased",
        "admitted",
        "evaluating",
    ]

    for target in illegal_targets:
        delta = PlanDelta(
            space_id="space-invalid",
            base_version=1,
            resulting_version=2,
            ops=[
                {
                    "op": "transition",
                    "target_node_id": "task-inv",
                    "to_state": target,
                }
            ],
        )
        with pytest.raises(IllegalStateTransitionError):
            kernel.commit_plan_delta(delta)

    # Verify plan version and task state remain completely untouched
    assert kernel.get_plan_version() == 1
    assert _node(kernel, "task-inv").state == "pending"


# 11. Valid dependency transition
def test_valid_dependency_transition() -> None:
    kernel = SpaceKernel(space_id="space-dep", owner_id="owner-1")
    dispatcher = DeterministicDispatcher()

    # t1 (no dependencies), t2 (depends on t1)
    t1 = TaskNode(id="t1", capability="compute.cpu", state="pending")
    t2 = TaskNode(id="t2", capability="fs.write", state="pending", dependencies=["t1"])
    kernel.plan_store.init_space_plan("space-dep", [t1, t2])

    graph = kernel.get_task_graph()
    decisions = dispatcher.evaluate_plan(graph, "space-dep")

    # t1 is ready for dispatch; t2 awaits dependencies
    dec_t1 = next(d for d in decisions if d.task_id == "t1")
    dec_t2 = next(d for d in decisions if d.task_id == "t2")
    assert dec_t1.action == DispatchAction.DISPATCH
    assert dec_t2.action == DispatchAction.AWAIT_DEPENDENCIES

    # Transition t1 through lifecycle to completed
    kernel.propose_task_transition("t1", "ready", expected_plan_version=1)
    kernel.propose_task_transition("t1", "admission_pending", expected_plan_version=2)
    kernel.propose_task_transition("t1", "admitted", expected_plan_version=3)
    kernel.propose_task_transition("t1", "lease_pending", expected_plan_version=4)
    kernel.propose_task_transition("t1", "leased", expected_plan_version=5)
    kernel.propose_task_transition("t1", "dispatched", expected_plan_version=6)
    kernel.propose_task_transition("t1", "running", expected_plan_version=7)
    kernel.propose_task_transition("t1", "completed", expected_plan_version=8)

    # Now inspect updated plan
    updated_graph = kernel.get_task_graph()
    assert _node(updated_graph, "t1").state == "completed"

    # Re-evaluate with dispatcher: t2 is now ready for dispatch!
    new_decisions = dispatcher.evaluate_plan(updated_graph, "space-dep")
    dec_t2_now = next(d for d in new_decisions if d.task_id == "t2")
    assert dec_t2_now.action == DispatchAction.DISPATCH

    # t2 can now transition to ready
    success, ver, _ = kernel.propose_task_transition(
        "t2", "ready", expected_plan_version=kernel.get_plan_version()
    )
    assert success is True
    assert _node(kernel, "t2").state == "ready"


# 12. Plan delta persistence (Checkpoint & Recovery)
def test_plan_delta_persistence_and_checkpoints() -> None:
    kernel = SpaceKernel(space_id="space-chk", owner_id="owner-1")
    t1 = TaskNode(id="t1", capability="exec", state="pending")
    kernel.plan_store.init_space_plan("space-chk", [t1])

    kernel.propose_task_transition("t1", "ready", expected_plan_version=1)
    kernel.propose_task_transition("t1", "admission_pending", expected_plan_version=2)

    # Checkpoint authoritative kernel state
    checkpoint = kernel.create_checkpoint()
    assert checkpoint["plan_version"] == 3
    assert len(checkpoint["nodes"]) == 1
    assert checkpoint["nodes"][0]["state"] == "admission_pending"

    # Simulate restart with fresh kernel and restore
    fresh_kernel = SpaceKernel(space_id="space-chk", owner_id="owner-1")
    fresh_kernel.restore_checkpoint(checkpoint)

    assert fresh_kernel.get_plan_version() == 3
    assert _node(fresh_kernel, "t1").state == "admission_pending"


# 13. Correct plan pulses (plan.created, plan.delta, plan.version.superseded)
def test_correct_plan_pulses() -> None:
    bus = SpyPulseBus()
    kernel = SpaceKernel(space_id="space-pulses", owner_id="owner-1", bus=bus)

    # space.created emitted on initialization
    assert any(p.type == "space.created" for p in bus.published)

    node = TaskNode(id="t1", capability="exec", state="pending")
    kernel.plan_store.init_space_plan("space-pulses", [node])

    # Successful commit emits plan.delta
    delta1 = PlanDelta(
        space_id="space-pulses",
        base_version=1,
        resulting_version=2,
        ops=[{"op": "transition", "target_node_id": "t1", "to_state": "ready"}],
    )
    kernel.commit_plan_delta(delta1)

    plan_deltas = [p for p in bus.published if p.type == "plan.delta"]
    assert len(plan_deltas) == 1
    assert plan_deltas[0].payload["base_version"] == 1
    assert plan_deltas[0].payload["resulting_version"] == 2
    assert plan_deltas[0].payload["ops"][0]["op"] == "transition"

    # Stale commit emits plan.version.superseded
    stale_delta = PlanDelta(
        space_id="space-pulses",
        base_version=1,
        resulting_version=2,
        ops=[{"op": "transition", "target_node_id": "t1", "to_state": "blocked"}],
    )
    kernel.commit_plan_delta(stale_delta)

    superseded = [p for p in bus.published if p.type == "plan.version.superseded"]
    assert len(superseded) == 1
    assert superseded[0].payload["superseded_version"] == 1
    assert superseded[0].payload["current_version"] == 2


# 14. No premature worker execution
def test_no_premature_worker_execution() -> None:
    kernel = SpaceKernel(space_id="space-noworker", owner_id="owner-1")
    dispatcher = DeterministicDispatcher()

    t1 = TaskNode(id="t1", capability="exec", state="pending")
    kernel.plan_store.init_space_plan("space-noworker", [t1])

    # Propose transition through dispatcher
    success, new_ver, _ = dispatcher.propose_transition(
        kernel=kernel,
        task_id="t1",
        to_state="ready",
        expected_plan_version=1,
    )

    assert success is True
    assert new_ver == 2
    assert _node(kernel, "t1").state == "ready"

    # Verify that dispatcher has no references to workers, processes, or runtimes
    assert not hasattr(dispatcher, "worker")
    assert not hasattr(dispatcher, "invoke_worker")
    assert not hasattr(kernel, "worker")


# 15. Dispatcher authority boundary & bounded rebase
def test_dispatcher_authority_boundary_and_bounded_rebase() -> None:
    kernel = SpaceKernel(space_id="space-bound", owner_id="owner-1")
    dispatcher = DeterministicDispatcher()

    t1 = TaskNode(id="t1", capability="exec", state="pending")
    kernel.plan_store.init_space_plan("space-bound", [t1])

    # Dispatcher proposes transition to SpaceKernel
    success, ver, _ = dispatcher.propose_transition(
        kernel=kernel,
        task_id="t1",
        to_state="ready",
        expected_plan_version=1,
    )
    assert success is True
    assert ver == 2

    # Simulate another writer bumping the plan to v3
    delta_bump = PlanDelta(
        space_id="space-bound",
        base_version=2,
        resulting_version=3,
        ops=[{"op": "add", "target_node_id": "t2", "capability": "search"}],
    )
    kernel.commit_plan_delta(delta_bump)
    assert kernel.get_plan_version() == 3

    # Dispatcher uses rebase_and_propose_transition to handle superseded version
    rebase_success, final_ver = dispatcher.rebase_and_propose_transition(
        kernel=kernel,
        task_id="t1",
        to_state="admission_pending",
        max_rebases=3,
    )
    assert rebase_success is True
    assert final_ver == 4
    assert _node(kernel, "t1").state == "admission_pending"


# 16. Core dependency guard AST verification
def test_core_dependency_guard_ast() -> None:
    forbidden = (
        "agents",
        "workers",
        "skills",
        "workflows",
        "llm",
        "channels",
        "memory",
    )

    files_to_check = [
        "core/plans/delta.py",
        "core/plans/plan_store.py",
        "core/plans/task_graph.py",
        "core/space/kernel.py",
        "core/orchestrator/dispatch_model.py",
    ]

    for rel_path in files_to_check:
        full_path = os.path.join(os.path.dirname(__file__), "..", "..", "..", rel_path)
        with open(full_path, "r", encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=rel_path)

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split(".")[0]
                    assert root not in forbidden, f"{rel_path} imports forbidden root '{root}'"
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    root = node.module.split(".")[0]
                    assert root not in forbidden, f"{rel_path} imports from forbidden root '{root}'"
