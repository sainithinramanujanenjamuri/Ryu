"""Unit tests for Monitor.

spec §4 (Monitor), §16 (Pulse Contracts), ORCH-005 — Phase 4
"""

from __future__ import annotations

from datetime import datetime, timezone

from ryu.pulse_bus.pulse import Pulse, Severity

from core.orchestrator.monitor import Monitor


def test_monitor_state_tracking() -> None:
    monitor = Monitor(space_id="space-mon-1")

    now = datetime.now(timezone.utc)

    # 1. Initial plan created
    monitor.handle_pulse(
        Pulse(
            id="p-1",
            space_id="space-mon-1",
            type="plan.created",
            severity=Severity.INFO,
            source="orchestrator",
            correlation_id="corr-1",
            payload={"plan_version": 1, "task_count": 2},
            timestamp=now,
        )
    )
    assert monitor.get_authoritative_plan_version() == 1

    # 2. Task assigned and started
    monitor.handle_pulse(
        Pulse(
            id="p-2",
            space_id="space-mon-1",
            type="task.assigned",
            severity=Severity.INFO,
            source="team_builder",
            correlation_id="corr-1",
            payload={"task_id": "task-1", "assignee_id": "agent-1", "plan_version": 1},
            timestamp=now,
        )
    )
    assert monitor.get_task_state("task-1") == "assigned"

    monitor.handle_pulse(
        Pulse(
            id="p-3",
            space_id="space-mon-1",
            type="task.started",
            severity=Severity.INFO,
            source="worker",
            correlation_id="corr-1",
            payload={"task_id": "task-1", "plan_version": 1},
            timestamp=now,
        )
    )
    assert monitor.get_task_state("task-1") == "started"

    # 3. Resource conflict followed by grant
    monitor.handle_pulse(
        Pulse(
            id="p-4",
            space_id="space-mon-1",
            type="resource.conflict",
            severity=Severity.WARNING,
            source="resource_manager",
            correlation_id="corr-1",
            payload={"resource_id": "gpu/node-1/cuda-0", "queue_position": 2},
            timestamp=now,
        )
    )
    assert monitor.state.queued_resources["gpu/node-1/cuda-0"] == 2

    monitor.handle_pulse(
        Pulse(
            id="p-5",
            space_id="space-mon-1",
            type="resource.granted",
            severity=Severity.INFO,
            source="resource_manager",
            correlation_id="corr-1",
            payload={"resource_id": "gpu/node-1/cuda-0", "lease_token": "lease-abc"},
            timestamp=now,
        )
    )
    assert monitor.state.held_leases["gpu/node-1/cuda-0"] == "lease-abc"
    assert "gpu/node-1/cuda-0" not in monitor.state.queued_resources

    # 4. Task completed
    monitor.handle_pulse(
        Pulse(
            id="p-6",
            space_id="space-mon-1",
            type="task.completed",
            severity=Severity.INFO,
            source="worker",
            correlation_id="corr-1",
            payload={"task_id": "task-1", "result_ref": "artifact://art-1", "plan_version": 1},
            timestamp=now,
        )
    )
    assert monitor.is_task_complete("task-1") is True
    assert monitor.state.task_results["task-1"] == "artifact://art-1"


def test_monitor_cross_space_isolation() -> None:
    monitor = Monitor(space_id="space-local")
    now = datetime.now(timezone.utc)

    # Pulse from another Space should be ignored
    monitor.handle_pulse(
        Pulse(
            id="p-foreign",
            space_id="space-foreign",
            type="plan.created",
            severity=Severity.INFO,
            source="orchestrator",
            correlation_id="corr-f",
            payload={"plan_version": 99, "task_count": 5},
            timestamp=now,
        )
    )
    assert monitor.get_authoritative_plan_version() == 1
    assert len(monitor.state.events) == 0
