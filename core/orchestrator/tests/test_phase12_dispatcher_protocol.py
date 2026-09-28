"""Tests for Phase 12.4: Deterministic Dispatcher Protocol & Execution Pipeline.

Authoritative Specification: docs/PHASE_12_EXECUTION_ENGINE_SPEC.md (§7, §8)
Architectural Decision: adr/0041-autonomous-task-dispatcher-dag-traversal-and-plan-convergence-engine.md
SCCA Laws:
    - Law 1: Everything Happens Inside a Space
    - Law 2: Capabilities Are Requested, Never Owned
    - Law 6: Failures Are Contained, Escalated, and Never Silent
Core Boundary: AGENTS.md §7 (Deterministic Core Independence)

NOTE: This test suite runs strictly within core/ and does NOT import from workers/,
satisfying the Core Independence Rule and runtime ImportBlocker.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from typing import Any

from ryu.pulse_bus.pulse import Pulse

from core.orchestrator.dispatch_model import (
    DeterministicDispatcher,
    TaskExecutionRequest,
    TaskExecutionResult,
)
from core.plans.delta import PlanDelta
from core.plans.task_graph import (
    TaskState,
)
from core.resources.clock import FakeClock
from core.resources.identity import Resource, ResourceIdentity
from core.resources.manager import ResourceManager
from core.space.kernel import SpaceKernel


class SpyPulseBus:
    """In-memory pulse bus capturing all published pulses for audit verification."""

    def __init__(self) -> None:
        self.published: list[Pulse] = []
        self._lock = threading.Lock()

    def publish(self, pulse: Pulse) -> Pulse:
        with self._lock:
            self.published.append(pulse)
        return pulse

    def find_by_type(self, pulse_type: str) -> list[Pulse]:
        with self._lock:
            return [p for p in self.published if p.type == pulse_type]


class SpyWorkerInvoker:
    """Mock worker invoker implementing WorkerInvokerProtocol without importing workers/."""

    def __init__(
        self,
        canned_status: str = "ok",
        canned_output: dict[str, Any] | None = None,
        canned_artifacts: list[str] | None = None,
        canned_taint: bool = False,
        raise_exc: Exception | None = None,
        sleep_seconds: float = 0.0,
    ) -> None:
        self.invocations: list[TaskExecutionRequest] = []
        self.canned_status = canned_status
        self.canned_output = canned_output or {"stdout": "test output"}
        self.canned_artifacts = canned_artifacts or []
        self.canned_taint = canned_taint
        self.raise_exc = raise_exc
        self.sleep_seconds = sleep_seconds
        self._lock = threading.Lock()

    def invoke(self, request: TaskExecutionRequest) -> TaskExecutionResult:
        with self._lock:
            self.invocations.append(request)

        if self.sleep_seconds > 0:
            time.sleep(self.sleep_seconds)

        if self.raise_exc is not None:
            raise self.raise_exc

        return TaskExecutionResult(
            request_id=request.request_id,
            status=self.canned_status,
            task_id=request.task_id,
            space_id=request.space_id,
            plan_version=request.plan_version,
            output_data=self.canned_output,
            artifacts=self.canned_artifacts,
            taint=self.canned_taint or request.is_tainted,
            duration_seconds=0.05,
            error=None if self.canned_status == "ok" else f"Invoker status: {self.canned_status}",
            error_class=None if self.canned_status == "ok" else "transient.timeout" if self.canned_status == "timeout" else "terminal.invalid_params",
            logs=["step 1 done", "step 2 done"],
        )


def _setup_env(
    space_id: str = "space-alpha",
    owner_id: str = "owner-alpha",
    budget: float = 100.0,
    policy: str = "hard_stop",
    clock: FakeClock | None = None,
) -> tuple[SpyPulseBus, SpaceKernel, ResourceManager, DeterministicDispatcher]:
    bus = SpyPulseBus()
    kernel = SpaceKernel(
        space_id=space_id,
        owner_id=owner_id,
        bus=bus,
        budget=budget,
        budget_policy=policy,
    )
    res_mgr = ResourceManager(bus=bus, clock=clock)
    dispatcher = DeterministicDispatcher()
    return bus, kernel, res_mgr, dispatcher


def _add_task_to_kernel(
    kernel: SpaceKernel,
    task_id: str,
    capability: str = "compute.cpu",
    state: str = "ready",
    dependencies: list[str] | None = None,
    params: dict[str, Any] | None = None,
) -> int:
    cur_ver = kernel.get_plan_version()
    delta = PlanDelta(
        space_id=kernel.space_id,
        base_version=cur_ver,
        resulting_version=cur_ver + 1,
        ops=[
            {
                "op": "add",
                "target_node_id": task_id,
                "capability": capability,
                "state": state,
                "dependencies": dependencies or [],
                "params": params or {},
            }
        ],
    )
    ok, new_ver, err = kernel.commit_plan_delta(delta)
    assert ok, f"Failed to initialize task: {err}"
    return new_ver


def _lease_task(
    dispatcher: DeterministicDispatcher,
    kernel: SpaceKernel,
    res_mgr: ResourceManager,
    task_id: str,
    units: int = 1,
) -> tuple[int, str]:
    res_ident = ResourceIdentity("compute", "host-1", "core-0")
    res_mgr.register_resource(Resource(identity=res_ident, space_id=kernel.space_id, total_capacity=10))

    pipe_res = dispatcher.coordinate_admission_and_lease(
        kernel=kernel,
        resource_mgr=res_mgr,
        task_id=task_id,
        resource_identity=res_ident,
        units=units,
        duration_seconds=60.0,
    )
    assert pipe_res.leased
    assert pipe_res.lease_token is not None
    return pipe_res.plan_version, pipe_res.lease_token


# =========================================================================
# PROTOCOL & LIFECYCLE TESTS
# =========================================================================


def test_successful_task_dispatch_to_observing() -> None:
    """Test standard execution flow: LEASED -> DISPATCHED -> RUNNING -> OBSERVING."""
    bus, kernel, res_mgr, dispatcher = _setup_env()
    task_id = "task-01"
    _add_task_to_kernel(kernel, task_id, capability="compute.cpu", state="ready")
    ver, lease_token = _lease_task(dispatcher, kernel, res_mgr, task_id)

    invoker = SpyWorkerInvoker(
        canned_status="ok",
        canned_output={"stdout": "success execution"},
        canned_artifacts=["artifact-1"],
    )

    result = dispatcher.dispatch_task(
        kernel=kernel,
        resource_mgr=res_mgr,
        task_id=task_id,
        invoker=invoker,
        expected_plan_version=ver,
    )

    assert result.status == "ok"
    assert result.terminal_state == TaskState.OBSERVING.value
    assert result.output_data == {"stdout": "success execution"}
    assert result.artifacts == ["artifact-1"]
    assert len(invoker.invocations) == 1
    assert invoker.invocations[0].task_id == task_id
    assert invoker.invocations[0].lease_token == lease_token

    # Verify task in kernel is in OBSERVING state
    node = kernel.get_task_graph().get_node(task_id)
    assert node is not None
    assert node.state == TaskState.OBSERVING.value

    # Verify task.started pulse was published
    started_pulses = bus.find_by_type("task.started")
    assert len(started_pulses) == 1
    assert started_pulses[0].payload["task_id"] == task_id

    # Verify lease was automatically released
    lease = res_mgr.get_lease(lease_token)
    assert lease is None or lease.state.value == "released"


def test_missing_lease_rejection() -> None:
    """Test dispatch rejected if task has no active lease."""
    bus, kernel, res_mgr, dispatcher = _setup_env()
    task_id = "task-no-lease"
    _add_task_to_kernel(kernel, task_id, capability="compute.cpu", state="ready")

    invoker = SpyWorkerInvoker()
    result = dispatcher.dispatch_task(
        kernel=kernel,
        resource_mgr=res_mgr,
        task_id=task_id,
        invoker=invoker,
    )

    assert result.status == "rejected"
    assert "leased" in (result.reason or "").lower() or "missing" in (result.error or "").lower()
    assert len(invoker.invocations) == 0

    # Also test when task is in LEASED state but lease was released from res_mgr
    ver, lease_token = _lease_task(dispatcher, kernel, res_mgr, task_id)
    res_mgr.release(kernel.space_id, task_id, lease_token)

    result2 = dispatcher.dispatch_task(
        kernel=kernel,
        resource_mgr=res_mgr,
        task_id=task_id,
        invoker=invoker,
        expected_plan_version=ver,
    )
    assert result2.status == "rejected"
    assert len(invoker.invocations) == 0


def test_expired_lease_rejection() -> None:
    """Test dispatch rejected if lease has expired."""
    clock = FakeClock(initial_time=datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc))
    bus, kernel, res_mgr, dispatcher = _setup_env(clock=clock)
    task_id = "task-expired"
    _add_task_to_kernel(kernel, task_id, capability="compute.cpu", state="ready")
    ver, lease_token = _lease_task(dispatcher, kernel, res_mgr, task_id)

    # Advance clock past lease duration (60.0s)
    clock.advance(100.0)

    invoker = SpyWorkerInvoker()
    result = dispatcher.dispatch_task(
        kernel=kernel,
        resource_mgr=res_mgr,
        task_id=task_id,
        invoker=invoker,
        expected_plan_version=ver,
    )

    assert result.status == "rejected"
    assert "expired" in (result.error or "").lower() or "expired" in (result.reason or "").lower()
    assert len(invoker.invocations) == 0


def test_wrong_space_lease_rejection() -> None:
    """Test dispatch rejected if lease belongs to a different space (Law 1)."""
    bus, kernel, res_mgr, dispatcher = _setup_env(space_id="space-alpha")
    task_id = "task-cross-space"
    _add_task_to_kernel(kernel, task_id, capability="compute.cpu", state="ready")

    # Acquire lease under different space
    res_ident = ResourceIdentity("compute", "host-1", "core-0")
    res_mgr.register_resource(Resource(identity=res_ident, space_id="space-beta", total_capacity=10))
    acq = res_mgr.acquire(
        space_id="space-beta",
        requester_id=task_id,
        identity=res_ident,
        units=1,
        duration_seconds=60.0,
    )
    assert acq.lease is not None

    # Transition task through pipeline to LEASED with wrong-space lease token
    cur_ver = kernel.get_plan_version()
    kernel.propose_task_transition(
        task_id=task_id,
        to_state=TaskState.ADMISSION_PENDING.value,
        expected_plan_version=cur_ver,
    )
    cur_ver = kernel.get_plan_version()
    kernel.propose_task_transition(
        task_id=task_id,
        to_state=TaskState.ADMITTED.value,
        expected_plan_version=cur_ver,
    )
    cur_ver = kernel.get_plan_version()
    kernel.propose_task_transition(
        task_id=task_id,
        to_state=TaskState.LEASE_PENDING.value,
        expected_plan_version=cur_ver,
    )
    cur_ver = kernel.get_plan_version()
    kernel.propose_task_transition(
        task_id=task_id,
        to_state=TaskState.LEASED.value,
        expected_plan_version=cur_ver,
        result_ref=acq.lease.lease_token,
    )

    invoker = SpyWorkerInvoker()
    result = dispatcher.dispatch_task(
        kernel=kernel,
        resource_mgr=res_mgr,
        task_id=task_id,
        invoker=invoker,
    )

    assert result.status == "rejected"
    assert "cross_space" in (result.error or "").lower() or "cross-space" in (result.reason or "").lower()
    assert len(invoker.invocations) == 0


def test_wrong_task_lease_rejection() -> None:
    """Test dispatch rejected if lease was issued to a different requester."""
    bus, kernel, res_mgr, dispatcher = _setup_env()
    task_id = "task-target"
    _add_task_to_kernel(kernel, task_id, capability="compute.cpu", state="ready")

    # Acquire lease for task-other
    res_ident = ResourceIdentity("compute", "host-1", "core-0")
    res_mgr.register_resource(Resource(identity=res_ident, space_id=kernel.space_id, total_capacity=10))
    acq = res_mgr.acquire(
        space_id=kernel.space_id,
        requester_id="task-other",
        identity=res_ident,
        units=1,
        duration_seconds=60.0,
    )
    assert acq.lease is not None

    cur_ver = kernel.get_plan_version()
    kernel.propose_task_transition(
        task_id=task_id,
        to_state=TaskState.ADMISSION_PENDING.value,
        expected_plan_version=cur_ver,
    )
    cur_ver = kernel.get_plan_version()
    kernel.propose_task_transition(
        task_id=task_id,
        to_state=TaskState.ADMITTED.value,
        expected_plan_version=cur_ver,
    )
    cur_ver = kernel.get_plan_version()
    kernel.propose_task_transition(
        task_id=task_id,
        to_state=TaskState.LEASE_PENDING.value,
        expected_plan_version=cur_ver,
    )
    cur_ver = kernel.get_plan_version()
    kernel.propose_task_transition(
        task_id=task_id,
        to_state=TaskState.LEASED.value,
        expected_plan_version=cur_ver,
        result_ref=acq.lease.lease_token,
    )

    invoker = SpyWorkerInvoker()
    result = dispatcher.dispatch_task(
        kernel=kernel,
        resource_mgr=res_mgr,
        task_id=task_id,
        invoker=invoker,
    )

    assert result.status == "rejected"
    assert "wrong_task_lease" in (result.error or "").lower() or "wrong task" in (result.reason or "").lower()
    assert len(invoker.invocations) == 0


def test_cas_plan_version_mismatch_rejection() -> None:
    """Test dispatch rejected if expected plan version is stale."""
    bus, kernel, res_mgr, dispatcher = _setup_env()
    task_id = "task-cas-mismatch"
    _add_task_to_kernel(kernel, task_id, capability="compute.cpu", state="ready")
    ver, lease_token = _lease_task(dispatcher, kernel, res_mgr, task_id)

    invoker = SpyWorkerInvoker()
    # Pass stale version (ver - 1)
    result = dispatcher.dispatch_task(
        kernel=kernel,
        resource_mgr=res_mgr,
        task_id=task_id,
        invoker=invoker,
        expected_plan_version=ver - 1,
    )

    assert result.status == "rejected"
    assert result.error is not None and "plan_version_mismatch" in result.error
    assert len(invoker.invocations) == 0


def test_idempotent_task_dispatch() -> None:
    """Test that a repeated dispatch invocation returns cached result without re-executing."""
    bus, kernel, res_mgr, dispatcher = _setup_env()
    task_id = "task-idempotent"
    _add_task_to_kernel(kernel, task_id, capability="compute.cpu", state="ready")
    ver, lease_token = _lease_task(dispatcher, kernel, res_mgr, task_id)

    invoker = SpyWorkerInvoker(
        canned_status="ok",
        canned_output={"val": 42},
    )

    # First dispatch
    res1 = dispatcher.dispatch_task(
        kernel=kernel,
        resource_mgr=res_mgr,
        task_id=task_id,
        invoker=invoker,
        expected_plan_version=ver,
    )
    assert res1.status == "ok"
    assert len(invoker.invocations) == 1

    # Second dispatch with same parameters
    res2 = dispatcher.dispatch_task(
        kernel=kernel,
        resource_mgr=res_mgr,
        task_id=task_id,
        invoker=invoker,
        expected_plan_version=ver,
    )
    assert res2.status == "ok"
    # Invoker must NOT have been called a second time
    assert len(invoker.invocations) == 1


def test_invoker_exception_escalates_to_failed_and_releases_lease() -> None:
    """Test that unexpected worker exception transitions task to FAILED, publishes pulse, and releases lease."""
    bus, kernel, res_mgr, dispatcher = _setup_env()
    task_id = "task-crash"
    _add_task_to_kernel(kernel, task_id, capability="compute.cpu", state="ready")
    ver, lease_token = _lease_task(dispatcher, kernel, res_mgr, task_id)

    invoker = SpyWorkerInvoker(raise_exc=RuntimeError("Subprocess segfault"))

    result = dispatcher.dispatch_task(
        kernel=kernel,
        resource_mgr=res_mgr,
        task_id=task_id,
        invoker=invoker,
        expected_plan_version=ver,
    )

    assert result.status == "failed"
    assert result.terminal_state == TaskState.FAILED.value
    assert result.error is not None and "Subprocess segfault" in result.error

    # Kernel task in FAILED state
    node = kernel.get_task_graph().get_node(task_id)
    assert node is not None
    assert node.state == TaskState.FAILED.value

    # task.failed pulse published (Law 6)
    failed_pulses = bus.find_by_type("task.failed")
    assert len(failed_pulses) == 1
    assert "Subprocess segfault" in failed_pulses[0].payload["message"]

    # Lease released
    lease = res_mgr.get_lease(lease_token)
    assert lease is None or lease.state.value == "released"


def test_invoker_timeout_transitions_to_timed_out_and_releases_lease() -> None:
    """Test timeout transitions task to TIMED_OUT, publishes pulse, and releases lease."""
    bus, kernel, res_mgr, dispatcher = _setup_env()
    task_id = "task-timeout"
    _add_task_to_kernel(kernel, task_id, capability="compute.cpu", state="ready")
    ver, lease_token = _lease_task(dispatcher, kernel, res_mgr, task_id)

    invoker = SpyWorkerInvoker(canned_status="timeout")

    result = dispatcher.dispatch_task(
        kernel=kernel,
        resource_mgr=res_mgr,
        task_id=task_id,
        invoker=invoker,
        expected_plan_version=ver,
    )

    assert result.status == "timeout"
    assert result.terminal_state == TaskState.TIMED_OUT.value

    node = kernel.get_task_graph().get_node(task_id)
    assert node is not None
    assert node.state == TaskState.TIMED_OUT.value

    failed_pulses = bus.find_by_type("task.failed")
    assert len(failed_pulses) == 1
    assert failed_pulses[0].payload["error_class"] == "transient.timeout"

    lease = res_mgr.get_lease(lease_token)
    assert lease is None or lease.state.value == "released"


def test_invoker_cancelled_transitions_to_cancelled() -> None:
    """Test cancellation transitions task to CANCELLED and releases lease."""
    bus, kernel, res_mgr, dispatcher = _setup_env()
    task_id = "task-cancelled"
    _add_task_to_kernel(kernel, task_id, capability="compute.cpu", state="ready")
    ver, lease_token = _lease_task(dispatcher, kernel, res_mgr, task_id)

    invoker = SpyWorkerInvoker(canned_status="cancelled")

    result = dispatcher.dispatch_task(
        kernel=kernel,
        resource_mgr=res_mgr,
        task_id=task_id,
        invoker=invoker,
        expected_plan_version=ver,
    )

    assert result.status == "cancelled"
    assert result.terminal_state == TaskState.CANCELLED.value

    node = kernel.get_task_graph().get_node(task_id)
    assert node is not None
    assert node.state == TaskState.CANCELLED.value

    lease = res_mgr.get_lease(lease_token)
    assert lease is None or lease.state.value == "released"


def test_taint_propagation_forward_only() -> None:
    """Test that input taint or output taint propagates forward-only."""
    bus, kernel, res_mgr, dispatcher = _setup_env()
    task_id = "task-tainted"
    _add_task_to_kernel(kernel, task_id, capability="compute.cpu", state="ready")
    ver, lease_token = _lease_task(dispatcher, kernel, res_mgr, task_id)

    # 1. Input is tainted
    invoker = SpyWorkerInvoker(canned_status="ok", canned_taint=False)
    result = dispatcher.dispatch_task(
        kernel=kernel,
        resource_mgr=res_mgr,
        task_id=task_id,
        invoker=invoker,
        expected_plan_version=ver,
        is_tainted=True,
    )
    assert result.taint is True
    assert invoker.invocations[0].is_tainted is True


def test_end_to_end_execute_task_pipeline_success() -> None:
    """Test execute_task_pipeline from READY -> LEASED -> RUNNING -> OBSERVING."""
    bus, kernel, res_mgr, dispatcher = _setup_env()
    task_id = "task-pipe-ok"
    _add_task_to_kernel(kernel, task_id, capability="compute.cpu", state="ready")

    res_ident = ResourceIdentity("compute", "host-1", "core-0")
    res_mgr.register_resource(Resource(identity=res_ident, space_id=kernel.space_id, total_capacity=10))

    invoker = SpyWorkerInvoker(
        canned_status="ok",
        canned_output={"result": "pipeline executed"},
    )

    pipeline_res = dispatcher.execute_task_pipeline(
        kernel=kernel,
        resource_mgr=res_mgr,
        task_id=task_id,
        resource_identity=res_ident,
        invoker=invoker,
    )

    assert pipeline_res.status == "ok"
    assert pipeline_res.terminal_state == TaskState.OBSERVING.value
    assert pipeline_res.output_data == {"result": "pipeline executed"}
    assert len(invoker.invocations) == 1

    node = kernel.get_task_graph().get_node(task_id)
    assert node is not None
    assert node.state == TaskState.OBSERVING.value


def test_end_to_end_execute_task_pipeline_admission_rejection() -> None:
    """Test that pipeline aborts if admission is denied, with zero worker invocations."""
    bus, kernel, res_mgr, dispatcher = _setup_env(budget=0.0, policy="hard_stop")
    task_id = "task-pipe-no-admit"
    _add_task_to_kernel(
        kernel,
        task_id,
        capability="compute.cpu",
        state="ready",
    )

    res_ident = ResourceIdentity("compute", "host-1", "core-0")
    res_mgr.register_resource(Resource(identity=res_ident, space_id=kernel.space_id, total_capacity=10))

    invoker = SpyWorkerInvoker()

    pipeline_res = dispatcher.execute_task_pipeline(
        kernel=kernel,
        resource_mgr=res_mgr,
        task_id=task_id,
        resource_identity=res_ident,
        invoker=invoker,
    )

    assert pipeline_res.status == "rejected"
    assert pipeline_res.terminal_state == TaskState.BLOCKED.value
    assert len(invoker.invocations) == 0
