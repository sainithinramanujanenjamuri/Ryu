"""Deterministic serialization and deserialization for TaskGraph and TaskNode.

Enforces:
- Deterministic canonical dict/JSON representation (nodes sorted by ID, keys sorted)
- Strict validation on deserialization (fails closed on malformed or corrupted data)
- Full preservation of task execution states, dependencies, params, attempt, and metadata
- Preservation of SecretRef references without resolving or leaking secrets (ADR-0045)

spec §16 (TaskGraph), REC-003, PLAN-DURABLE-005, PLAN-DURABLE-008 — Phase 15.1
"""

from __future__ import annotations

import json
from typing import Any

from core.plans.task_graph import (
    ALLOWED_TASK_STATES,
    TaskGraph,
    TaskNode,
)


def task_node_to_dict(node: TaskNode) -> dict[str, Any]:
    """Serialize a TaskNode into a canonical dictionary representation."""
    return {
        "id": node.id,
        "capability": node.capability,
        "params": dict(node.params),
        "optional": bool(node.optional),
        "state": node.state,
        "dependencies": sorted(list(node.dependencies)),
        "attempt": int(node.attempt),
        "result_ref": node.result_ref,
        "error": node.error,
    }


def task_node_from_dict(data: dict[str, Any]) -> TaskNode:
    """Deserialize and validate a TaskNode from a dictionary.

    Fails closed on missing required attributes or invalid state.
    """
    if not isinstance(data, dict):
        raise ValueError(f"Malformed TaskNode data: expected dict, got {type(data).__name__}")

    if "id" not in data or not isinstance(data["id"], str) or not data["id"].strip():
        raise ValueError(f"Malformed TaskNode data: missing or invalid 'id' in {data}")

    if "capability" not in data or not isinstance(data["capability"], str):
        raise ValueError(f"Malformed TaskNode data: missing or invalid 'capability' in {data}")

    raw_state = str(data.get("state", "pending")).lower().strip()
    if raw_state not in ALLOWED_TASK_STATES:
        raise ValueError(
            f"Invalid task state '{raw_state}' for task '{data['id']}'; must be in {ALLOWED_TASK_STATES}"
        )

    raw_deps = data.get("dependencies", [])
    if not isinstance(raw_deps, (list, tuple)):
        raise ValueError(f"Malformed dependencies for task '{data['id']}': expected list")
    dependencies = [str(d) for d in raw_deps]

    raw_params = data.get("params", {})
    if not isinstance(raw_params, dict):
        raise ValueError(f"Malformed params for task '{data['id']}': expected dict")

    return TaskNode(
        id=str(data["id"]).strip(),
        capability=str(data["capability"]).strip(),
        params=dict(raw_params),
        optional=bool(data.get("optional", False)),
        state=raw_state,
        dependencies=dependencies,
        attempt=int(data.get("attempt", 1)),
        result_ref=data.get("result_ref"),
        error=data.get("error"),
    )


def task_graph_to_dict(graph: TaskGraph) -> dict[str, Any]:
    """Serialize a TaskGraph into a canonical, deterministic dictionary.

    Nodes are sorted by ID for stable serialization and hashing.
    """
    sorted_nodes = sorted(graph.nodes, key=lambda n: n.id)
    return {
        "space_id": graph.space_id,
        "plan_version": int(graph.plan_version),
        "nodes": [task_node_to_dict(n) for n in sorted_nodes],
    }


def task_graph_from_dict(data: dict[str, Any]) -> TaskGraph:
    """Deserialize and validate a TaskGraph from a dictionary representation.

    Fails closed:
    - Missing space_id or plan_version
    - Malformed node entries
    - Circular or missing dependencies
    """
    if not isinstance(data, dict):
        raise ValueError(f"Malformed TaskGraph data: expected dict, got {type(data).__name__}")

    space_id = data.get("space_id")
    if not isinstance(space_id, str) or not space_id.strip():
        raise ValueError("Malformed TaskGraph data: missing or empty 'space_id'")

    plan_version = data.get("plan_version")
    if plan_version is None or not isinstance(plan_version, int) or plan_version < 1:
        raise ValueError(
            f"Malformed TaskGraph data: invalid 'plan_version' {plan_version}; must be integer >= 1"
        )

    raw_nodes = data.get("nodes", [])
    if not isinstance(raw_nodes, list):
        raise ValueError(f"Malformed TaskGraph data: 'nodes' must be a list, got {type(raw_nodes).__name__}")

    nodes = [task_node_from_dict(nd) for nd in raw_nodes]

    graph = TaskGraph(
        space_id=space_id.strip(),
        plan_version=plan_version,
        nodes=nodes,
    )

    # Validate structural integrity (missing deps, self-loops, DAG cycles)
    # Fails closed by raising MissingDependencyError or GraphCycleError
    graph.validate_dependencies()

    return graph


def serialize_task_graph_json(graph: TaskGraph) -> str:
    """Serialize a TaskGraph to a deterministic canonical JSON string."""
    canonical_dict = task_graph_to_dict(graph)
    return json.dumps(canonical_dict, sort_keys=True, separators=(",", ":"))


def deserialize_task_graph_json(json_str: str) -> TaskGraph:
    """Deserialize and validate a TaskGraph from a JSON string.

    Fails closed on malformed JSON or corrupted graph schema.
    """
    if not isinstance(json_str, str) or not json_str.strip():
        raise ValueError("Cannot deserialize TaskGraph from empty or non-string input")

    try:
        data = json.loads(json_str)
    except Exception as exc:
        raise ValueError(f"Failed to parse TaskGraph JSON: {exc}") from exc

    return task_graph_from_dict(data)
