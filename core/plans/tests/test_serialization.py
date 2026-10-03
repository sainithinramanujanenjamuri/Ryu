"""Unit tests for TaskGraph and TaskNode deterministic serialization.

Tests:
- Deterministic canonical dict and JSON representations
- Fails closed on missing fields, invalid states, corrupted JSON
- Fails closed on graph cycles and missing dependencies
- Preservation of node parameters, dependencies, attempt count, and metadata

spec §16, PLAN-DURABLE-005, PLAN-DURABLE-008 — Phase 15.1
"""

from __future__ import annotations

import json

import pytest

from core.plans.serialization import (
    deserialize_task_graph_json,
    serialize_task_graph_json,
    task_graph_from_dict,
    task_node_from_dict,
    task_node_to_dict,
)
from core.plans.task_graph import (
    GraphCycleError,
    MissingDependencyError,
    TaskGraph,
    TaskNode,
)


def test_task_node_serialization_roundtrip():
    node = TaskNode(
        id="task-1",
        capability="python.eval_sandboxed",
        params={"code": "x = 42", "timeout": 30},
        optional=True,
        state="running",
        dependencies=["dep-b", "dep-a"],
        attempt=2,
        result_ref="ref-123",
        error=None,
    )
    d = task_node_to_dict(node)
    assert d["id"] == "task-1"
    assert d["optional"] is True
    assert d["state"] == "running"
    # Dependencies are sorted deterministically
    assert d["dependencies"] == ["dep-a", "dep-b"]

    reconstructed = task_node_from_dict(d)
    assert reconstructed.id == node.id
    assert reconstructed.capability == node.capability
    assert reconstructed.params == node.params
    assert reconstructed.optional == node.optional
    assert reconstructed.state == node.state
    assert reconstructed.attempt == 2
    assert reconstructed.result_ref == "ref-123"


def test_task_node_from_dict_validation_failures():
    # Non-dict
    with pytest.raises(ValueError, match="expected dict"):
        task_node_from_dict("not-a-dict")  # type: ignore[arg-type]

    # Missing ID
    with pytest.raises(ValueError, match="missing or invalid 'id'"):
        task_node_from_dict({"capability": "cap"})

    # Missing capability
    with pytest.raises(ValueError, match="missing or invalid 'capability'"):
        task_node_from_dict({"id": "t1"})

    # Invalid state
    with pytest.raises(ValueError, match="Invalid task state"):
        task_node_from_dict({"id": "t1", "capability": "cap", "state": "invalid_state"})


def test_task_graph_serialization_deterministic_order():
    n1 = TaskNode(id="b_task", capability="cap_b", dependencies=["a_task"])
    n2 = TaskNode(id="a_task", capability="cap_a")
    graph1 = TaskGraph(space_id="space-1", plan_version=3, nodes=[n1, n2])
    graph2 = TaskGraph(space_id="space-1", plan_version=3, nodes=[n2, n1])

    json1 = serialize_task_graph_json(graph1)
    json2 = serialize_task_graph_json(graph2)

    # Identical JSON string regardless of original node list insertion order
    assert json1 == json2

    parsed = json.loads(json1)
    assert parsed["space_id"] == "space-1"
    assert parsed["plan_version"] == 3
    # Nodes sorted by ID
    assert [n["id"] for n in parsed["nodes"]] == ["a_task", "b_task"]


def test_task_graph_deserialization_roundtrip():
    n1 = TaskNode(id="t1", capability="cap1", state="completed")
    n2 = TaskNode(id="t2", capability="cap2", dependencies=["t1"], state="ready")
    original = TaskGraph(space_id="space-x", plan_version=5, nodes=[n1, n2])

    serialized = serialize_task_graph_json(original)
    reconstructed = deserialize_task_graph_json(serialized)

    assert reconstructed.space_id == "space-x"
    assert reconstructed.plan_version == 5
    assert len(reconstructed.nodes) == 2
    assert reconstructed.get_node("t1").state == "completed"
    assert reconstructed.get_node("t2").dependencies == ["t1"]


def test_task_graph_deserialization_fails_closed_on_corrupt_json():
    with pytest.raises(ValueError, match="empty or non-string"):
        deserialize_task_graph_json("")

    with pytest.raises(ValueError, match="Failed to parse TaskGraph JSON"):
        deserialize_task_graph_json("{{invalid json")

    with pytest.raises(ValueError, match="missing or empty 'space_id'"):
        deserialize_task_graph_json('{"plan_version": 1, "nodes": []}')

    with pytest.raises(ValueError, match="invalid 'plan_version'"):
        deserialize_task_graph_json('{"space_id": "s1", "plan_version": 0, "nodes": []}')

    with pytest.raises(ValueError, match="'nodes' must be a list"):
        deserialize_task_graph_json('{"space_id": "s1", "plan_version": 1, "nodes": "not-a-list"}')


def test_task_graph_deserialization_fails_closed_on_cycles():
    # Node t1 depends on t2, t2 depends on t1
    payload = {
        "space_id": "space-cycle",
        "plan_version": 1,
        "nodes": [
            {"id": "t1", "capability": "cap", "dependencies": ["t2"]},
            {"id": "t2", "capability": "cap", "dependencies": ["t1"]},
        ],
    }
    with pytest.raises(GraphCycleError):
        task_graph_from_dict(payload)


def test_task_graph_deserialization_fails_closed_on_missing_dependency():
    payload = {
        "space_id": "space-missing-dep",
        "plan_version": 1,
        "nodes": [
            {"id": "t1", "capability": "cap", "dependencies": ["unknown-task"]},
        ],
    }
    with pytest.raises(MissingDependencyError):
        task_graph_from_dict(payload)
