"""Regression test suite for GOV-004-F1 (Defect Resolution & Pulse Contract Integrity).

Verifies:
1. ConcurrentDAGScheduler cleanly dispatches tasks without emitting uncontracted pulse types.
2. Real execution pipeline emits contracted `task.started` (and not `task.dispatched`).
3. PulseValidator rejects any pulse with unregistered type `task.dispatched` (PULSE-001, PULSE-012).
4. All pulses published during concurrent scheduling and task execution strictly belong to known types.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from pathlib import Path

import pytest
from ryu.pulse_bus.bus import PulseBus
from ryu.pulse_bus.pulse import Pulse, Severity
from ryu.pulse_bus.reject import PulseRejectedError
from ryu.pulse_bus.validator import PulseValidator

from core.orchestrator.dispatch_model import (
    DeterministicDispatcher,
    TaskExecutionRequest,
    TaskExecutionResult,
)
from core.orchestrator.execution_state import InMemoryExecutionAttemptStore
from core.orchestrator.scheduler import (
    ConcurrentDAGScheduler,
    SchedulerConfig,
)
from core.plans.delta import PlanDelta
from core.plans.task_graph import TaskState
from core.resources.identity import Resource, ResourceIdentity
from core.resources.manager import ResourceManager
from core.resources.store import InMemoryResourceStore
from core.space.kernel import SpaceKernel


class StrictValidatingPulseBus(PulseBus):
    """PulseBus that enforces strict PulseValidator contract checking before publishing."""

    def __init__(self) -> None:
        super().__init__()
        self.validator = PulseValidator()
        self.published: list[Pulse] = []
        self._lock = threading.Lock()

    def publish(self, pulse: Pulse) -> Pulse:
        self.validator.validate(pulse.type, pulse.payload, pulse.source)
        with self._lock:
            self.published.append(pulse)
        return super().publish(pulse)

    def find_by_type(self, pulse_type: str) -> list[Pulse]:
        with self._lock:
            return [p for p in self.published if p.type == pulse_type]


class DummyWorkerInvoker:
    def __init__(self) -> None:
        self.invoked: list[TaskExecutionRequest] = []

    def invoke(self, request: TaskExecutionRequest) -> TaskExecutionResult:
        self.invoked.append(request)
        return TaskExecutionResult(
            request_id=request.request_id,
            status="ok",
            task_id=request.task_id,
            space_id=request.space_id,
            plan_version=request.plan_version,
            output_data={"stdout": "success"},
            artifacts=[],
            duration_seconds=0.01,
            details={"exit_code": 0},
            error=None,
            taint=False,
        )


def _setup_test_space(space_id: str, bus: PulseBus) -> tuple[SpaceKernel, ResourceManager]:
    kernel = SpaceKernel(
        space_id=space_id,
        owner_id="test-owner",
        bus=bus,
        budget=100.0,
        budget_policy="hard_stop",
    )
    res_mgr = ResourceManager(bus=bus, store=InMemoryResourceStore())
    res_id = ResourceIdentity("compute", "host-1", "worker-1")
    res_mgr.register_resource(Resource(identity=res_id, space_id=space_id, total_capacity=100))
    return kernel, res_mgr


def test_pulse_validator_rejects_uncontracted_task_dispatched() -> None:
    """PULSE-001 / PULSE-012: PulseValidator must reject `task.dispatched` as unregistered."""
    validator = PulseValidator()
    assert "task.dispatched" not in validator.known_types()
    assert "task.started" in validator.known_types()

    invalid_pulse = Pulse(
        id="test-pulse-sched-1",
        space_id="test-space",
        type="task.dispatched",
        severity=Severity.INFO,
        source="scheduler",
        timestamp=datetime.now(timezone.utc),
        payload={"task_id": "t1", "plan_version": 1},
        taint=False,
        correlation_id="corr-test",
    )

    with pytest.raises(PulseRejectedError) as exc_info:
        validator.validate(invalid_pulse.type, invalid_pulse.payload, invalid_pulse.source)
    assert "task.dispatched" in str(exc_info.value)


def test_scheduler_dispatches_cleanly_without_uncontracted_pulses(tmp_path: Path) -> None:
    """GOV-004-F1: ConcurrentDAGScheduler must dispatch tasks without publishing unregistered pulses."""
    bus = StrictValidatingPulseBus()
    kernel, res_mgr = _setup_test_space("space-regress-1", bus)
    invoker = DummyWorkerInvoker()
    attempt_store = InMemoryExecutionAttemptStore()
    dispatcher = DeterministicDispatcher()

    # Create task in kernel
    cur_ver = kernel.get_plan_version()
    delta = PlanDelta(
        space_id=kernel.space_id,
        base_version=cur_ver,
        resulting_version=cur_ver + 1,
        ops=[
            {
                "op": "add",
                "target_node_id": "task-alpha",
                "capability": "python.eval_sandboxed",
                "state": "ready",
                "dependencies": [],
                "params": {},
                "optional": False,
            }
        ],
    )
    ok, new_ver, _ = kernel.commit_plan_delta(delta)
    assert ok

    scheduler = ConcurrentDAGScheduler(
        config=SchedulerConfig(max_concurrent_workers=2, per_space_concurrency=2),
        bus=bus,
        dispatcher=dispatcher,
        attempt_store=attempt_store,
    )
    scheduler.register_space(
        space_id="space-regress-1",
        kernel=kernel,
        resource_mgr=res_mgr,
        invoker=invoker,
        resource_identity=ResourceIdentity("compute", "host-1", "worker-1"),
        base_dir=tmp_path,
    )

    # Execute scheduler step
    dispatched = scheduler.step()
    assert dispatched >= 1

    # Drain worker execution
    scheduler.drain(timeout=5.0)

    # Verify no uncontracted pulses were emitted
    assert len(bus.find_by_type("task.dispatched")) == 0

    # Verify all published pulses are known registered types
    known_types = bus.validator.known_types()
    for p in bus.published:
        assert p.type in known_types, f"Unregistered pulse type published: {p.type}"

    # Verify task.started was published by the execution pipeline
    started_pulses = bus.find_by_type("task.started")
    assert len(started_pulses) >= 1
    assert started_pulses[0].payload["task_id"] == "task-alpha"


def test_task_execution_lifecycle_pulses_integrity(tmp_path: Path) -> None:
    """Verify execution lifecycle pulses: plan.created/delta -> task.started -> task.completed."""
    bus = StrictValidatingPulseBus()
    kernel, res_mgr = _setup_test_space("space-regress-2", bus)
    invoker = DummyWorkerInvoker()
    attempt_store = InMemoryExecutionAttemptStore()
    dispatcher = DeterministicDispatcher()

    cur_ver = kernel.get_plan_version()
    delta = PlanDelta(
        space_id=kernel.space_id,
        base_version=cur_ver,
        resulting_version=cur_ver + 1,
        ops=[
            {
                "op": "add",
                "target_node_id": "task-beta",
                "capability": "python.eval_sandboxed",
                "state": "ready",
                "dependencies": [],
                "params": {},
                "optional": False,
            }
        ],
    )
    ok, new_ver, _ = kernel.commit_plan_delta(delta)
    assert ok

    scheduler = ConcurrentDAGScheduler(
        config=SchedulerConfig(max_concurrent_workers=2, per_space_concurrency=2),
        bus=bus,
        dispatcher=dispatcher,
        attempt_store=attempt_store,
    )
    scheduler.register_space(
        space_id="space-regress-2",
        kernel=kernel,
        resource_mgr=res_mgr,
        invoker=invoker,
        resource_identity=ResourceIdentity("compute", "host-1", "worker-1"),
        base_dir=tmp_path,
    )

    scheduler.step()
    scheduler.drain(timeout=5.0)

    # Verify terminal status in graph
    node = kernel.get_task_graph().get_node("task-beta")
    assert node is not None
    assert node.state == TaskState.COMPLETED.value

    # Verify published pulse types sequence
    types = [p.type for p in bus.published]
    assert "task.started" in types
    assert "task.completed" in types
    assert "task.dispatched" not in types
