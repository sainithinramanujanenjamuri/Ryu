"""Single-writer Compare-And-Swap (CAS) Plan Store.

The Space Kernel serializes Plan Delta application as an atomic single-writer commit.
Racing deltas produce a single deterministic winner; stale commits are rejected with
plan.version.superseded and bounded rebases (docs/Architecture §16, ADR-0003).

spec §16 (TaskGraph & PlanDelta), PLAN-001/002/003, ADR-0003 — Phase 2
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any, Protocol

from ryu.pulse_bus.pulse import Pulse, Severity

from core.plans.delta import DeltaOp, PlanDelta
from core.plans.task_graph import (
    IllegalStateTransitionError,
    TaskGraph,
    TaskNode,
    TaskNotFoundError,
)


class PulsePublisher(Protocol):
    def publish(self, pulse: Pulse) -> Pulse: ...


class PlanStore:
    """
    Atomic Plan versioning and CAS store.

    Enforces single-writer CAS on plan_version and bounds automatic rebasing to 3 attempts.
    Maintains immutable historical plan versions (docs/Architecture §16, ADR-0003, ADR-0041).
    """

    def __init__(self, bus: PulsePublisher | None = None, max_rebases: int = 3) -> None:
        self.bus = bus
        self.max_rebases = max_rebases
        self._lock = threading.Lock()
        self._graphs: dict[str, TaskGraph] = {}
        self._history: dict[str, dict[int, TaskGraph]] = {}
        self._last_winning_delta: dict[str, str] = {}
        self._rebase_attempts: dict[tuple[str, str], int] = {}

    def _clone_graph(self, graph: TaskGraph) -> TaskGraph:
        """Create an independent, deep-cloned copy of a TaskGraph and its TaskNodes."""
        cloned_nodes = [
            TaskNode(
                id=n.id,
                capability=n.capability,
                params=dict(n.params),
                optional=n.optional,
                state=n.state,
                dependencies=list(n.dependencies),
                attempt=n.attempt,
                result_ref=n.result_ref,
                error=n.error,
            )
            for n in graph.nodes
        ]
        return TaskGraph(
            space_id=graph.space_id,
            plan_version=graph.plan_version,
            nodes=cloned_nodes,
        )

    def init_space_plan(self, space_id: str, nodes: list[TaskNode] | None = None) -> TaskGraph:
        """Initialize a new Space TaskGraph at plan_version 1."""
        with self._lock:
            graph = TaskGraph(
                space_id=space_id,
                plan_version=1,
                nodes=nodes or [],
            )
            self._graphs[space_id] = graph
            if space_id not in self._history:
                self._history[space_id] = {}
            self._history[space_id][1] = self._clone_graph(graph)
            self._last_winning_delta[space_id] = "init"
            return graph

    def get_plan_version(self, space_id: str) -> int:
        """Return the current authoritative plan_version for a Space."""
        with self._lock:
            if space_id not in self._graphs:
                self.init_space_plan(space_id)
            return self._graphs[space_id].plan_version

    def get_task_graph(self, space_id: str, version: int | None = None) -> TaskGraph:
        """Return the TaskGraph for a Space (authoritative current or immutable historical)."""
        with self._lock:
            if space_id not in self._graphs:
                self.init_space_plan(space_id)
            if version is not None:
                space_hist = self._history.get(space_id, {})
                if version in space_hist:
                    return space_hist[version]
                if version == self._graphs[space_id].plan_version:
                    return self._graphs[space_id]
                raise KeyError(f"Plan version {version} not found for space '{space_id}'")
            return self._graphs[space_id]

    def get_historical_graph(self, space_id: str, version: int) -> TaskGraph | None:
        """Return an immutable historical TaskGraph snapshot, or None if not found."""
        with self._lock:
            return self._history.get(space_id, {}).get(version)

    def commit_delta(
        self,
        delta: PlanDelta,
        proposal_id: str | None = None,
    ) -> tuple[bool, int, str | None]:
        """
        Attempt to commit a PlanDelta via Compare-And-Swap.

        Returns:
            (success: bool, current_version: int, winning_delta_id: str | None)
        """
        space_id = delta.space_id

        with self._lock:
            if space_id not in self._graphs:
                self.init_space_plan(space_id)
            graph = self._graphs[space_id]
            current_ver = graph.plan_version

            # CAS SUCCESS: delta.base_version matches current_ver
            if delta.base_version == current_ver:
                # Ensure current version is snapshotted in history before creating new version
                if space_id not in self._history:
                    self._history[space_id] = {}
                self._history[space_id][current_ver] = self._clone_graph(graph)

                # Clone for atomic mutation without corrupting base_version
                new_graph = self._clone_graph(graph)

                # Apply ops to new_graph
                for op in delta.ops:
                    op_type: str = op.op if isinstance(op, DeltaOp) else str(op.get("op", ""))
                    target_id: str = op.target_node_id if isinstance(op, DeltaOp) else str(op.get("target_node_id", ""))
                    op_payload: dict[str, Any] = op.payload if isinstance(op, DeltaOp) else op

                    if op_type == "add":
                        existing = new_graph.get_node(target_id)
                        if not existing:
                            new_node = TaskNode(
                                id=target_id,
                                capability=op_payload.get("capability", "default"),
                                params=dict(op_payload.get("params", {})),
                                optional=op_payload.get("optional", False),
                                dependencies=list(op_payload.get("dependencies", [])),
                            )
                            new_graph.nodes.append(new_node)
                    elif op_type == "remove":
                        new_graph.nodes = [n for n in new_graph.nodes if n.id != target_id]
                    elif op_type in ("reassign", "rollback"):
                        target_node = new_graph.get_node(target_id)
                        if target_node:
                            target_node.params.update(op_payload.get("params", {}))
                    elif op_type == "transition":
                        target_node = new_graph.get_node(target_id)
                        if target_node is None:
                            raise TaskNotFoundError(
                                f"Task '{target_id}' not found in plan for space '{space_id}'"
                            )
                        from_state = op_payload.get("from_state") or op_payload.get("params", {}).get("from_state")
                        if from_state and target_node.state.lower() != from_state.lower():
                            raise IllegalStateTransitionError(
                                f"Task '{target_id}' expected current state '{from_state}', "
                                f"but is '{target_node.state}'"
                            )
                        to_state = op_payload.get("to_state") or op_payload.get("params", {}).get("to_state")
                        if not to_state:
                            raise ValueError(f"Transition op requires 'to_state', got: {op}")
                        target_node.transition_to(to_state)
                        if "error" in op_payload:
                            target_node.error = op_payload["error"]
                        if "result_ref" in op_payload:
                            target_node.result_ref = op_payload["result_ref"]
                        if "attempt" in op_payload:
                            target_node.attempt = int(op_payload["attempt"])

                # Advance authoritative version
                new_graph.plan_version = delta.resulting_version
                self._graphs[space_id] = new_graph
                self._history[space_id][delta.resulting_version] = self._clone_graph(new_graph)
                self._last_winning_delta[space_id] = delta.delta_id

                # Reset rebase attempts on success
                if proposal_id:
                    self._rebase_attempts.pop((space_id, proposal_id), None)

                # Publish plan.delta
                if self.bus is not None:
                    serializable_ops = [
                        {"op": o.op, "target_node_id": o.target_node_id, **o.payload}
                        if isinstance(o, DeltaOp)
                        else o
                        for o in delta.ops
                    ]
                    self.bus.publish(
                        Pulse(
                            id=f"plan-delta-{space_id}-{delta.resulting_version}",
                            space_id=space_id,
                            type="plan.delta",
                            severity=Severity.INFO,
                            source="plan_store",
                            timestamp=datetime.now(timezone.utc),
                            payload={
                                "base_version": delta.base_version,
                                "resulting_version": delta.resulting_version,
                                "ops": serializable_ops,
                            },
                            taint=False,
                            correlation_id=f"corr-plan-{space_id}",
                            parent_pulse_id=None,
                        )
                    )

                return True, new_graph.plan_version, None

            # CAS FAILURE: Stale base_version
            winning_id = self._last_winning_delta.get(space_id, "unknown")

            # Publish plan.version.superseded
            if self.bus is not None:
                self.bus.publish(
                    Pulse(
                        id=f"plan-superseded-{space_id}-{delta.base_version}-{delta.delta_id}",
                        space_id=space_id,
                        type="plan.version.superseded",
                        severity=Severity.WARNING,
                        source="plan_store",
                        timestamp=datetime.now(timezone.utc),
                        payload={
                            "superseded_version": delta.base_version,
                            "current_version": current_ver,
                            "winning_delta_id": winning_id,
                        },
                        taint=False,
                        correlation_id=f"corr-plan-{space_id}",
                        parent_pulse_id=None,
                    )
                )

            # Check for livelock escalation if proposal_id provided (ADR-0003)
            if proposal_id:
                key = (space_id, proposal_id)
                attempts = self._rebase_attempts.get(key, 0) + 1
                self._rebase_attempts[key] = attempts

                if attempts >= self.max_rebases and self.bus is not None:
                    self.bus.publish(
                        Pulse(
                            id=f"livelock-failed-{space_id}-{proposal_id}",
                            space_id=space_id,
                            type="task.failed",
                            severity=Severity.ERROR,
                            source="plan_store",
                            timestamp=datetime.now(timezone.utc),
                            payload={
                                "task_id": proposal_id,
                                "error_class": "terminal.plan_livelock",
                                "message": (
                                    f"Plan CAS livelock: exceeded maximum automatic rebase "
                                    f"attempts ({self.max_rebases})"
                                ),
                                "plan_version": current_ver,
                            },
                            taint=False,
                            correlation_id=f"corr-plan-{space_id}",
                            parent_pulse_id=None,
                        )
                    )

            return False, current_ver, winning_id
