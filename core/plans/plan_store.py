"""Single-writer Compare-And-Swap (CAS) Plan Store.

The Space Kernel serializes Plan Delta application as an atomic single-writer commit.
Racing deltas produce a single deterministic winner; stale commits are rejected with
plan.version.superseded and bounded rebases (docs/Architecture §16, ADR-0003).

spec §16 (TaskGraph & PlanDelta), PLAN-001/002/003, ADR-0003 — Phase 2
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Protocol

from ryu.pulse_bus.pulse import Pulse, Severity

from core.plans.delta import PlanDelta
from core.plans.task_graph import TaskGraph, TaskNode


class PulsePublisher(Protocol):
    def publish(self, pulse: Pulse) -> Pulse: ...


class PlanStore:
    """
    Atomic Plan versioning and CAS store.

    Enforces single-writer CAS on plan_version and bounds automatic rebasing to 3 attempts.
    """

    def __init__(self, bus: PulsePublisher | None = None, max_rebases: int = 3) -> None:
        self.bus = bus
        self.max_rebases = max_rebases
        self._lock = threading.Lock()
        self._graphs: dict[str, TaskGraph] = {}
        self._last_winning_delta: dict[str, str] = {}
        self._rebase_attempts: dict[tuple[str, str], int] = {}

    def init_space_plan(self, space_id: str, nodes: list[TaskNode] | None = None) -> TaskGraph:
        """Initialize a new Space TaskGraph at plan_version 1."""
        with self._lock:
            graph = TaskGraph(
                space_id=space_id,
                plan_version=1,
                nodes=nodes or [],
            )
            self._graphs[space_id] = graph
            self._last_winning_delta[space_id] = "init"
            return graph

    def get_plan_version(self, space_id: str) -> int:
        """Return the current authoritative plan_version for a Space."""
        with self._lock:
            if space_id not in self._graphs:
                self.init_space_plan(space_id)
            return self._graphs[space_id].plan_version

    def get_task_graph(self, space_id: str) -> TaskGraph:
        """Return the current TaskGraph for a Space."""
        with self._lock:
            if space_id not in self._graphs:
                self.init_space_plan(space_id)
            return self._graphs[space_id]

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
                # Apply ops to graph
                for op in delta.ops:
                    op_type = op.get("op")
                    target_id = op.get("target_node_id", "")
                    if op_type == "add":
                        existing = graph.get_node(target_id)
                        if not existing:
                            new_node = TaskNode(
                                id=target_id,
                                capability=op.get("capability", "default"),
                                params=op.get("params", {}),
                                optional=op.get("optional", False),
                            )
                            graph.nodes.append(new_node)
                    elif op_type == "remove":
                        graph.nodes = [n for n in graph.nodes if n.id != target_id]
                    elif op_type in ("reassign", "rollback"):
                        target_node = graph.get_node(target_id)
                        if target_node:
                            target_node.params.update(op.get("params", {}))

                # Advance authoritative version
                graph.plan_version = delta.resulting_version
                self._last_winning_delta[space_id] = delta.delta_id

                # Reset rebase attempts on success
                if proposal_id:
                    self._rebase_attempts.pop((space_id, proposal_id), None)

                # Publish plan.delta
                if self.bus is not None:
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
                                "ops": delta.ops,
                            },
                            taint=False,
                            correlation_id=f"corr-plan-{space_id}",
                            parent_pulse_id=None,
                        )
                    )

                return True, graph.plan_version, None

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
