"""Monitor: Subscribes to Pulses and tracks Space execution timeline.

spec §4 (Monitor), §16 (Pulse Contracts), ORCH-005 — Phase 4
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any

from ryu.pulse_bus.pulse import Pulse


@dataclass
class TimelineState:
    """Observable execution state for a Space reconstructed entirely from Pulses."""

    space_id: str
    authoritative_plan_version: int = 1
    task_states: dict[str, str] = field(default_factory=dict)
    task_attempts: dict[str, int] = field(default_factory=dict)
    task_results: dict[str, str] = field(default_factory=dict)
    held_leases: dict[str, str] = field(default_factory=dict)  # resource_id -> lease_token
    queued_resources: dict[str, int] = field(default_factory=dict)  # resource_id -> queue_position
    budget_exceeded: bool = False
    window_id: str | None = None
    failures: list[dict[str, Any]] = field(default_factory=list)
    events: list[Pulse] = field(default_factory=list)


class Monitor:
    """Pulse-driven observer tracking execution progress, resource states, and drift.

    Invariants:
    - Pure observer: executes zero tools, mutates zero plans, issues zero leases.
    - Single source of truth for current runtime state derived from Pulses.
    - Uses only the official Pulse Bus; zero private side-channels.
    """

    def __init__(self, space_id: str) -> None:
        self.space_id = space_id
        self.state = TimelineState(space_id=space_id)
        self._lock = threading.RLock()

    def handle_pulse(self, pulse: Pulse) -> None:
        """Process an incoming Pulse and update timeline state."""
        # Enforce Space isolation: ignore or flag cross-space pulses
        if pulse.space_id != self.space_id:
            return

        with self._lock:
            self.state.events.append(pulse)
            ptype = pulse.type
            payload = pulse.payload

            if ptype == "plan.created":
                self.state.authoritative_plan_version = payload.get("plan_version", 1)

            elif ptype == "plan.delta":
                self.state.authoritative_plan_version = payload.get(
                    "resulting_version", self.state.authoritative_plan_version + 1
                )

            elif ptype == "task.assigned":
                tid = payload.get("task_id", "")
                if tid:
                    self.state.task_states[tid] = "assigned"

            elif ptype == "task.started":
                tid = payload.get("task_id", "")
                if tid:
                    self.state.task_states[tid] = "started"

            elif ptype == "task.completed":
                tid = payload.get("task_id", "")
                if tid:
                    self.state.task_states[tid] = "completed"
                    self.state.task_results[tid] = payload.get("result_ref", "")

            elif ptype == "task.failed":
                tid = payload.get("task_id", "")
                if tid:
                    self.state.task_states[tid] = "failed"
                    failure_info = {
                        "task_id": tid,
                        "error_class": payload.get("error_class", "unknown"),
                        "message": payload.get("message", ""),
                        "plan_version": payload.get("plan_version", 1),
                        "timestamp": pulse.timestamp,
                    }
                    self.state.failures.append(failure_info)

            elif ptype == "task.retried":
                tid = payload.get("task_id", "")
                if tid:
                    self.state.task_states[tid] = "retried"
                    self.state.task_attempts[tid] = payload.get(
                        "attempt", self.state.task_attempts.get(tid, 0) + 1
                    )

            elif ptype == "resource.granted":
                res_id = payload.get("resource_id", "")
                lease_tok = payload.get("lease_token", "")
                if res_id and lease_tok:
                    self.state.held_leases[res_id] = lease_tok
                    self.state.queued_resources.pop(res_id, None)

            elif ptype == "resource.released":
                res_id = payload.get("resource_id", "")
                if res_id:
                    self.state.held_leases.pop(res_id, None)

            elif ptype == "resource.conflict":
                res_id = payload.get("resource_id", "")
                qpos = payload.get("queue_position", 1)
                if res_id:
                    self.state.queued_resources[res_id] = qpos

            elif ptype == "resource.denied":
                res_id = payload.get("resource_id", "")
                if res_id:
                    self.state.held_leases.pop(res_id, None)
                    self.state.queued_resources.pop(res_id, None)

            elif ptype == "space.budget.exceeded":
                self.state.budget_exceeded = True
                self.state.window_id = payload.get("window_id")

    def get_task_state(self, task_id: str) -> str:
        with self._lock:
            return self.state.task_states.get(task_id, "unknown")

    def get_authoritative_plan_version(self) -> int:
        with self._lock:
            return self.state.authoritative_plan_version

    def is_task_complete(self, task_id: str) -> bool:
        with self._lock:
            return self.state.task_states.get(task_id) == "completed"

    def has_failures(self) -> bool:
        with self._lock:
            return len(self.state.failures) > 0

    def get_latest_failure(self) -> dict[str, Any] | None:
        with self._lock:
            return self.state.failures[-1] if self.state.failures else None
