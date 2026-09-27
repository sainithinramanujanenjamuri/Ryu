"""Unit & contract tests for Phase 12.1 Dispatcher Contracts & TaskGraph Model.

spec §4 (Space Orchestrator), §16 (TaskGraph & PlanDelta), DISPATCH-001..005, ADR-0041
Phase 12: Autonomous Plan Execution & Task Dispatch Engine
"""

from __future__ import annotations

import ast
import os

import pytest

from core.orchestrator.dispatch_model import (
    CrossSpaceViolationError,
    DeterministicDispatcher,
    DispatchAction,
    DispatchAttempt,
    VerifiedExecutionEvidence,
    compute_dispatch_idempotency_key,
)
from core.plans.task_graph import (
    GraphCycleError,
    IllegalStateTransitionError,
    MissingDependencyError,
    TaskGraph,
    TaskNode,
    TaskState,
)


# 1. Valid TaskGraph
def test_valid_task_graph() -> None:
    t1 = TaskNode(id="t1", capability="compute.cpu")
    t2 = TaskNode(id="t2", capability="fs.write", dependencies=["t1"])
    graph = TaskGraph(space_id="sp-1", plan_version=1, nodes=[t1, t2])

    graph.validate_dependencies()
    assert not graph.has_cycles()
    assert len(graph.nodes) == 2
    assert graph.get_node("t1") == t1
    assert graph.get_node("t2") == t2


# 2. Empty TaskGraph
def test_empty_task_graph() -> None:
    graph = TaskGraph(space_id="sp-1", plan_version=1, nodes=[])
    graph.validate_dependencies()
    assert not graph.has_cycles()
    assert graph.get_ready_tasks() == []
    assert graph.topological_sort() == []
    assert not graph.is_completed()
    assert not graph.has_failures()


# 3. Single-Node Graph
def test_single_node_graph() -> None:
    t1 = TaskNode(id="t1", capability="general.compute")
    graph = TaskGraph(space_id="sp-1", plan_version=1, nodes=[t1])

    ready = graph.get_ready_tasks()
    assert len(ready) == 1
    assert ready[0].id == "t1"


# 4. Linear DAG (A -> B -> C)
def test_linear_dag() -> None:
    t1 = TaskNode(id="t1", capability="c1")
    t2 = TaskNode(id="t2", capability="c2", dependencies=["t1"])
    t3 = TaskNode(id="t3", capability="c3", dependencies=["t2"])
    graph = TaskGraph(space_id="sp-1", plan_version=1, nodes=[t1, t2, t3])

    # Initially, only t1 is ready
    assert [n.id for n in graph.get_ready_tasks()] == ["t1"]

    # When t1 completes, t2 becomes ready
    t1.transition_to(TaskState.READY)
    t1.transition_to(TaskState.ADMISSION_PENDING)
    t1.transition_to(TaskState.ADMITTED)
    t1.transition_to(TaskState.LEASE_PENDING)
    t1.transition_to(TaskState.LEASED)
    t1.transition_to(TaskState.DISPATCHED)
    t1.transition_to(TaskState.RUNNING)
    t1.transition_to(TaskState.COMPLETED)
    assert [n.id for n in graph.get_ready_tasks()] == ["t2"]

    # When t2 completes, t3 becomes ready
    t2.transition_to(TaskState.READY)
    t2.transition_to(TaskState.ADMISSION_PENDING)
    t2.transition_to(TaskState.ADMITTED)
    t2.transition_to(TaskState.LEASE_PENDING)
    t2.transition_to(TaskState.LEASED)
    t2.transition_to(TaskState.DISPATCHED)
    t2.transition_to(TaskState.RUNNING)
    t2.transition_to(TaskState.COMPLETED)
    assert [n.id for n in graph.get_ready_tasks()] == ["t3"]


# 5. Branching DAG (A -> B, A -> C)
def test_branching_dag() -> None:
    root = TaskNode(id="root", capability="fetch")
    b1 = TaskNode(id="b1", capability="proc1", dependencies=["root"])
    b2 = TaskNode(id="b2", capability="proc2", dependencies=["root"])
    graph = TaskGraph(space_id="sp-1", plan_version=1, nodes=[root, b1, b2])

    assert [n.id for n in graph.get_ready_tasks()] == ["root"]

    root.state = "completed"
    ready = [n.id for n in graph.get_ready_tasks()]
    assert ready == ["b1", "b2"]


# 6. Merging DAG (A -> C, B -> C)
def test_merging_dag() -> None:
    a = TaskNode(id="a", capability="proc_a")
    b = TaskNode(id="b", capability="proc_b")
    c = TaskNode(id="c", capability="merge", dependencies=["a", "b"])
    graph = TaskGraph(space_id="sp-1", plan_version=1, nodes=[a, b, c])

    assert [n.id for n in graph.get_ready_tasks()] == ["a", "b"]

    # Only 'a' finishes; 'c' is not ready yet
    a.state = "completed"
    assert [n.id for n in graph.get_ready_tasks()] == ["b"]

    # 'b' finishes; now 'c' is ready
    b.state = "completed"
    assert [n.id for n in graph.get_ready_tasks()] == ["c"]


# 7. Parallel-Ready Nodes
def test_parallel_ready_nodes() -> None:
    p1 = TaskNode(id="p1", capability="worker.scan")
    p2 = TaskNode(id="p2", capability="worker.ping")
    p3 = TaskNode(id="p3", capability="worker.diag")
    graph = TaskGraph(space_id="sp-1", plan_version=1, nodes=[p1, p2, p3])

    ready = [n.id for n in graph.get_ready_tasks()]
    assert ready == ["p1", "p2", "p3"]


# 8. Missing Dependency
def test_missing_dependency() -> None:
    t1 = TaskNode(id="t1", capability="proc", dependencies=["non_existent_node"])
    graph = TaskGraph(space_id="sp-1", plan_version=1, nodes=[t1])

    with pytest.raises(MissingDependencyError) as exc:
        graph.validate_dependencies()
    assert "non_existent_node" in str(exc.value)


# 9. Duplicate Dependency Deduplication
def test_duplicate_dependency_deduplication() -> None:
    t1 = TaskNode(id="t1", capability="root")
    t2 = TaskNode(id="t2", capability="child", dependencies=["t1", "t1", "t1"])
    assert t2.dependencies == ["t1"]

    graph = TaskGraph(space_id="sp-1", plan_version=1, nodes=[t1, t2])
    graph.validate_dependencies()
    assert not graph.has_cycles()


# 10. Self-Dependency Rejection
def test_self_dependency_rejection() -> None:
    t1 = TaskNode(id="t1", capability="loop", dependencies=["t1"])
    graph = TaskGraph(space_id="sp-1", plan_version=1, nodes=[t1])

    with pytest.raises(GraphCycleError) as exc:
        graph.validate_dependencies()
    assert "cannot depend on itself" in str(exc.value)


# 11. Cycle Detection (A -> B -> C -> A)
def test_cycle_detection() -> None:
    a = TaskNode(id="a", capability="step", dependencies=["c"])
    b = TaskNode(id="b", capability="step", dependencies=["a"])
    c = TaskNode(id="c", capability="step", dependencies=["b"])
    graph = TaskGraph(space_id="sp-1", plan_version=1, nodes=[a, b, c])

    assert graph.has_cycles()
    with pytest.raises(GraphCycleError) as exc:
        graph.validate_dependencies()
    assert "Cycle detected" in str(exc.value)


# 12. Deterministic Topological Traversal
def test_deterministic_traversal() -> None:
    # Multiple valid topological sorts exist, but node ID tie-breaking ensures 100% determinism
    z = TaskNode(id="z", capability="c")
    a = TaskNode(id="a", capability="c")
    m = TaskNode(id="m", capability="c", dependencies=["z", "a"])
    graph = TaskGraph(space_id="sp-1", plan_version=1, nodes=[m, z, a])

    order = [n.id for n in graph.topological_sort()]
    assert order == ["a", "z", "m"]


# 13. Valid State Transitions
def test_valid_state_transitions() -> None:
    node = TaskNode(id="t1", capability="test.cap")
    assert node.state == "pending"

    node.transition_to(TaskState.READY)
    assert node.state == "ready"

    node.transition_to(TaskState.ADMISSION_PENDING)
    assert node.state == "admission_pending"

    node.transition_to(TaskState.ADMITTED)
    assert node.state == "admitted"

    node.transition_to(TaskState.LEASE_PENDING)
    assert node.state == "lease_pending"

    node.transition_to(TaskState.LEASED)
    assert node.state == "leased"

    node.transition_to(TaskState.DISPATCHED)
    assert node.state == "dispatched"

    node.transition_to(TaskState.RUNNING)
    assert node.state == "running"

    node.transition_to(TaskState.OBSERVING)
    assert node.state == "observing"

    node.transition_to(TaskState.EVALUATING)
    assert node.state == "evaluating"

    node.transition_to(TaskState.COMPLETED)
    assert node.state == "completed"


# 14. Invalid State Transitions
def test_invalid_state_transitions() -> None:
    node = TaskNode(id="t1", capability="test.cap", state="pending")

    # Direct pending -> running is illegal
    with pytest.raises(IllegalStateTransitionError):
        node.transition_to(TaskState.RUNNING)

    # Direct pending -> completed is illegal
    with pytest.raises(IllegalStateTransitionError):
        node.transition_to(TaskState.COMPLETED)

    # Unknown state raises ValueError
    with pytest.raises(ValueError):
        node.transition_to("invalid_unknown_state")

    # Completed is terminal
    node.state = "completed"
    with pytest.raises(IllegalStateTransitionError):
        node.transition_to(TaskState.READY)


# 15. Duplicate Dispatch Identity
def test_duplicate_dispatch_identity() -> None:
    key1 = compute_dispatch_idempotency_key(
        space_id="space-alpha", plan_version=1, task_id="task-x", attempt=1
    )
    key2 = compute_dispatch_idempotency_key(
        space_id="space-alpha", plan_version=1, task_id="task-x", attempt=1
    )
    assert key1 == key2

    # Different attempt produces distinct key
    key_attempt2 = compute_dispatch_idempotency_key(
        space_id="space-alpha", plan_version=1, task_id="task-x", attempt=2
    )
    assert key1 != key_attempt2

    # Dispatcher deduplicates tracked attempts
    dispatcher = DeterministicDispatcher()
    attempt = DispatchAttempt(
        idempotency_key=key1,
        space_id="space-alpha",
        plan_version=1,
        task_id="task-x",
        attempt=1,
    )
    assert dispatcher.record_attempt(attempt) is True
    # Duplicate attempt is rejected
    assert dispatcher.record_attempt(attempt) is False


# 16. Stale Plan Version Distinction
def test_stale_plan_version() -> None:
    key_v1 = compute_dispatch_idempotency_key(
        space_id="sp-1", plan_version=1, task_id="t1", attempt=1
    )
    key_v2 = compute_dispatch_idempotency_key(
        space_id="sp-1", plan_version=2, task_id="t1", attempt=1
    )
    assert key_v1 != key_v2


# 17. Cross-Space Task Rejection (SCCA Law 1)
def test_cross_space_task_rejection() -> None:
    graph = TaskGraph(space_id="space-alpha", plan_version=1, nodes=[TaskNode(id="t1", capability="c")])
    dispatcher = DeterministicDispatcher()

    # Evaluating space-alpha graph in space-beta context must be rejected
    with pytest.raises(CrossSpaceViolationError) as exc:
        dispatcher.evaluate_plan(graph, space_id="space-beta")
    assert "space-alpha" in str(exc.value)
    assert "space-beta" in str(exc.value)


# 18. Core Boundary Rule Enforcement (AGENTS.md §7)
def test_core_boundary_rule_enforcement() -> None:
    target_files = [
        os.path.join("core", "plans", "task_graph.py"),
        os.path.join("core", "orchestrator", "dispatch_model.py"),
    ]

    forbidden_roots = {"agents", "workers", "skills", "workflows", "llm", "channels", "memory"}

    for rel_path in target_files:
        full_path = os.path.abspath(rel_path)
        with open(full_path, "r", encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=full_path)

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root_mod = alias.name.split(".")[0]
                    assert root_mod not in forbidden_roots, (
                        f"Forbidden import '{alias.name}' found in '{rel_path}'"
                    )
            elif isinstance(node, ast.ImportFrom) and node.module:
                root_mod = node.module.split(".")[0]
                assert root_mod not in forbidden_roots, (
                    f"Forbidden from-import '{node.module}' found in '{rel_path}'"
                )


# 19. Evidence Model Verification (DISPATCH-004)
def test_verified_execution_evidence_model() -> None:
    # 1. Artifact evidence
    ev_art = VerifiedExecutionEvidence(
        task_id="t1",
        evidence_type="artifact",
        verified=True,
        sha256="abc12345",
        duration_seconds=1.2,
    )
    assert ev_art.verified is True
    assert ev_art.sha256 == "abc12345"

    # 2. Structured output evidence (without file artifact)
    ev_struct = VerifiedExecutionEvidence(
        task_id="t2",
        evidence_type="structured_output",
        verified=True,
        exit_code=0,
        duration_seconds=0.45,
        output_payload={"status": "HEALTHY", "nodes_checked": 4},
    )
    assert ev_struct.verified is True
    assert ev_struct.exit_code == 0
    assert ev_struct.output_payload["nodes_checked"] == 4


# 20. DeterministicDispatcher End-to-End Decision Generation
def test_deterministic_dispatcher_decisions() -> None:
    t1 = TaskNode(id="t1", capability="fetch")
    t2 = TaskNode(id="t2", capability="process", dependencies=["t1"])
    graph = TaskGraph(space_id="sp-1", plan_version=1, nodes=[t1, t2])
    dispatcher = DeterministicDispatcher()

    # Step 1: t1 is ready, t2 awaits dependencies
    decisions = dispatcher.evaluate_plan(graph, space_id="sp-1")
    assert len(decisions) == 2
    d1 = next(d for d in decisions if d.task_id == "t1")
    d2 = next(d for d in decisions if d.task_id == "t2")
    assert d1.action == DispatchAction.DISPATCH
    assert d1.idempotency_key is not None
    assert d2.action == DispatchAction.AWAIT_DEPENDENCIES

    # Step 2: Mark t1 completed
    t1.state = "completed"
    ready_decisions = dispatcher.get_ready_decisions(graph, space_id="sp-1")
    assert len(ready_decisions) == 1
    assert ready_decisions[0].task_id == "t2"
    assert ready_decisions[0].action == DispatchAction.DISPATCH
