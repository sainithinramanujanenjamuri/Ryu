"""Phase 12.4 Worker Invocation & Sandbox Integration Test Suite.

Authoritative Specification: docs/PHASE_12_EXECUTION_ENGINE_SPEC.md (§7, §8)
Authoritative Architectural Decision: adr/0041-autonomous-task-dispatcher-dag-traversal-and-plan-convergence-engine.md
SCCA Laws:
    - Law 1: Everything Happens Inside a Space
    - Law 2: Capabilities Are Requested, Never Owned
    - Law 6: Failures Are Contained, Escalated, and Never Silent
Core Boundary: AGENTS.md §7 (Deterministic Core Independence)

Verifies genuine subprocess execution, worker sandbox integration,
and the full 20-point Security & Chaos Battery.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from ryu.pulse_bus.bus import PulseBus
from ryu.pulse_bus.pulse import Pulse

from core.orchestrator.dispatch_model import (
    DeterministicDispatcher,
    TaskExecutionRequest,
)
from core.plans.delta import PlanDelta
from core.plans.task_graph import TaskState
from core.resources.clock import FakeClock
from core.resources.identity import Resource, ResourceIdentity
from core.resources.manager import ResourceManager
from core.resources.store import InMemoryResourceStore
from core.space.kernel import SpaceKernel
from node.audit import DeviceAuditLog
from node.contract import (
    DeviceInfo,
    DeviceType,
    NodeInfo,
    NodeState,
)
from node.grants import DeviceGrantManager
from node.registry import NodeRegistry
from node.runtime import NodeRuntime
from workers.contract import (
    ExecutionRequest,
    FilesystemPolicy,
    SandboxPolicy,
    WorkerIdentity,
)
from workers.file.worker import FileWorker
from workers.invoker import RuntimeWorkerInvoker
from workers.node.worker import NodeWorker
from workers.python.worker import PythonWorker


class SpyPulseBus(PulseBus):
    """In-memory bus recording all pulses for audit verification."""

    def __init__(self) -> None:
        super().__init__()
        self.published: list[Pulse] = []
        self._lock = threading.Lock()

    def publish(self, pulse: Pulse) -> Pulse:
        with self._lock:
            self.published.append(pulse)
        return pulse

    def find_by_type(self, pulse_type: str) -> list[Pulse]:
        with self._lock:
            return [p for p in self.published if p.type == pulse_type]


def _setup_pipeline_env(
    space_id: str = "space-alpha",
    owner_id: str = "owner-alpha",
    budget: float = 100.0,
    policy: str = "hard_stop",
    clock: FakeClock | None = None,
    node_runtime: NodeRuntime | None = None,
) -> tuple[SpyPulseBus, SpaceKernel, ResourceManager, DeterministicDispatcher, RuntimeWorkerInvoker]:
    bus = SpyPulseBus()
    kernel = SpaceKernel(
        space_id=space_id,
        owner_id=owner_id,
        bus=bus,
        budget=budget,
        budget_policy=policy,
    )
    res_mgr = ResourceManager(bus=bus, clock=clock, store=InMemoryResourceStore())
    invoker = RuntimeWorkerInvoker(
        bus=bus,
        resource_manager=res_mgr,
        node_runtime=node_runtime,
    )
    dispatcher = DeterministicDispatcher()
    return bus, kernel, res_mgr, dispatcher, invoker


def _add_task(
    kernel: SpaceKernel,
    task_id: str,
    capability: str = "python.eval_sandboxed",
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
    assert ok, f"Failed to commit task: {err}"
    return new_ver


def _lease_task(
    dispatcher: DeterministicDispatcher,
    kernel: SpaceKernel,
    res_mgr: ResourceManager,
    task_id: str,
    units: int = 1,
    duration_seconds: float = 60.0,
) -> tuple[int, str]:
    res_ident = ResourceIdentity("compute", "host-1", "core-0")
    res_mgr.register_resource(Resource(identity=res_ident, space_id=kernel.space_id, total_capacity=10))

    pipe_res = dispatcher.coordinate_admission_and_lease(
        kernel=kernel,
        resource_mgr=res_mgr,
        task_id=task_id,
        resource_identity=res_ident,
        units=units,
        duration_seconds=duration_seconds,
    )
    assert pipe_res.leased, f"Leasing failed: {pipe_res.reason}"
    assert pipe_res.lease_token is not None
    return pipe_res.plan_version, pipe_res.lease_token


# =========================================================================
# GENUINE EXECUTION PIPELINE TESTS
# =========================================================================


def test_phase12_genuine_python_execution_pipeline() -> None:
    """Genuine execution test: Task -> Plan CAS -> Admission -> Lease -> Dispatcher ->

    RuntimeWorkerInvoker -> PythonWorker -> SandboxManager subprocess -> ExecutionResult.
    Target output: 'RYU_PHASE_12_4_EXECUTION_VERIFIED' in stdout.
    """
    bus, kernel, res_mgr, dispatcher, invoker = _setup_pipeline_env()
    task_id = "task-py-01"

    code_snippet = "print('RYU_PHASE_12_4_EXECUTION_VERIFIED')\n"
    _add_task(
        kernel,
        task_id,
        capability="python.eval_sandboxed",
        state="ready",
        params={"code": code_snippet},
    )

    ver, lease_token = _lease_task(dispatcher, kernel, res_mgr, task_id)

    # Dispatch to real sandboxed Python worker
    result = dispatcher.dispatch_task(
        kernel=kernel,
        resource_mgr=res_mgr,
        task_id=task_id,
        invoker=invoker,
        expected_plan_version=ver,
    )

    assert result.status == "ok"
    assert result.terminal_state == TaskState.OBSERVING.value
    assert "RYU_PHASE_12_4_EXECUTION_VERIFIED" in result.output_data["stdout"]
    assert result.duration_seconds > 0.0

    # Task in kernel must be in OBSERVING state
    node = kernel.get_task_graph().get_node(task_id)
    assert node is not None
    assert node.state == TaskState.OBSERVING.value
    assert result.execution_result is not None
    assert node.result_ref == result.execution_result.request_id

    # task.started pulse published
    started_pulses = bus.find_by_type("task.started")
    assert len(started_pulses) == 1
    assert started_pulses[0].payload["task_id"] == task_id

    # Lease automatically released
    lease = res_mgr.get_lease(lease_token)
    assert lease is None or lease.state.value == "released"


def test_phase12_genuine_shell_execution_pipeline() -> None:
    """Genuine execution test with ShellWorker executing allowed command inside sandbox."""
    bus, kernel, res_mgr, dispatcher, invoker = _setup_pipeline_env()
    task_id = "task-sh-01"

    _add_task(
        kernel,
        task_id,
        capability="terminal.exec",
        state="ready",
        params={"command": ["python", "-c", "print('SHELL_PHASE_12_4_VERIFIED')"]},
    )

    ver, lease_token = _lease_task(dispatcher, kernel, res_mgr, task_id)

    result = dispatcher.dispatch_task(
        kernel=kernel,
        resource_mgr=res_mgr,
        task_id=task_id,
        invoker=invoker,
        expected_plan_version=ver,
    )

    assert result.status == "ok"
    assert result.terminal_state == TaskState.OBSERVING.value
    assert "SHELL_PHASE_12_4_VERIFIED" in result.output_data["stdout"]

    node = kernel.get_task_graph().get_node(task_id)
    assert node is not None
    assert node.state == TaskState.OBSERVING.value

    lease = res_mgr.get_lease(lease_token)
    assert lease is None or lease.state.value == "released"


def test_phase12_genuine_file_execution_pipeline(tmp_path: Path) -> None:
    """Genuine execution test with FileWorker performing filesystem write inside sandbox."""
    bus, kernel, res_mgr, dispatcher, invoker = _setup_pipeline_env()
    task_id = "task-file-01"
    target_file = tmp_path / "phase12_test_file.txt"

    policy = SandboxPolicy(
        fs_policy=FilesystemPolicy(
            read_paths=[str(tmp_path)],
            write_paths=[str(tmp_path)],
        )
    )

    _add_task(
        kernel,
        task_id,
        capability="file.write",
        state="ready",
        params={
            "operation": "write",
            "path": str(target_file),
            "content": "RYU_FILE_WORKER_VERIFIED",
            "sandbox_policy": policy,
        },
    )

    ver, lease_token = _lease_task(dispatcher, kernel, res_mgr, task_id)

    result = dispatcher.dispatch_task(
        kernel=kernel,
        resource_mgr=res_mgr,
        task_id=task_id,
        invoker=invoker,
        expected_plan_version=ver,
    )

    assert result.status == "ok"
    assert result.terminal_state == TaskState.OBSERVING.value
    assert target_file.exists()
    assert target_file.read_text(encoding="utf-8") == "RYU_FILE_WORKER_VERIFIED"

    lease = res_mgr.get_lease(lease_token)
    assert lease is None or lease.state.value == "released"


def test_phase12_genuine_node_execution_pipeline(tmp_path: Path) -> None:
    """Genuine execution test with NodeWorker executing device-bound capability via NodeRuntime."""
    bus = SpyPulseBus()
    kernel = SpaceKernel(
        space_id="space-node",
        owner_id="owner-node",
        bus=bus,
        budget=100.0,
    )
    res_mgr = ResourceManager(bus=bus, store=InMemoryResourceStore())
    registry = NodeRegistry()
    secret = "high-entropy-node-secret-32bytes"

    node = NodeInfo(
        node_id="node-test-01",
        platform="windows",
        architecture="x86_64",
        environment_profile="windows_host",
        runtime_state=NodeState.READY,
    )
    registry.register_node(node, secret)

    device = DeviceInfo(
        device_id="gpu-0",
        node_id="node-test-01",
        device_type=DeviceType.GPU,
        total_capacity=1,
    )
    registry.register_device(device)
    registry.sync_resources_to_manager(res_mgr, space_id=kernel.space_id)

    gm = DeviceGrantManager(registry=registry, resource_manager=res_mgr, bus=bus)
    audit_log = DeviceAuditLog(log_path=tmp_path / "audit.log.jsonl")

    runtime = NodeRuntime(
        node_id="node-test-01",
        shared_secret=secret,
        registry=registry,
        audit_log=audit_log,
        bus=bus,
    )

    invoker = RuntimeWorkerInvoker(
        bus=bus,
        resource_manager=res_mgr,
        node_runtime=runtime,
    )
    node_worker = NodeWorker(
        node_runtime=runtime,
        identity=WorkerIdentity(
            worker_id="task-node-01",
            capability="node.compute",
            space_id=kernel.space_id,
        ),
        bus=bus,
        resource_manager=res_mgr,
    )
    invoker.register_worker("node.compute", node_worker)
    dispatcher = DeterministicDispatcher()

    # Acquire lease for gpu
    res_ident = ResourceIdentity("gpu", "node-test-01", "gpu-0")
    acq = res_mgr.acquire(
        space_id=kernel.space_id,
        requester_id="task-node-01",
        identity=res_ident,
        units=1,
        duration_seconds=60.0,
    )
    assert acq.lease is not None

    # Create grant
    grant = gm.create_grant(
        space_id=kernel.space_id,
        worker_id="task-node-01",
        node_id="node-test-01",
        device_id="gpu-0",
        capability="node.compute",
        lease_token=acq.lease.lease_token,
    )

    # Register task and transition to LEASED
    task_id = "task-node-01"
    _add_task(
        kernel,
        task_id,
        capability="node.compute",
        state="ready",
        params={
            "device_id": "gpu-0",
            "grant": grant.to_dict(),
            "operation": "matrix_multiply",
        },
    )

    # Manually transition task to LEASED with the valid lease token
    cur_ver = kernel.get_plan_version()
    kernel.propose_task_transition(task_id, TaskState.ADMISSION_PENDING.value, cur_ver)
    cur_ver = kernel.get_plan_version()
    kernel.propose_task_transition(task_id, TaskState.ADMITTED.value, cur_ver)
    cur_ver = kernel.get_plan_version()
    kernel.propose_task_transition(task_id, TaskState.LEASE_PENDING.value, cur_ver)
    cur_ver = kernel.get_plan_version()
    kernel.propose_task_transition(
        task_id,
        TaskState.LEASED.value,
        cur_ver,
        result_ref=acq.lease.lease_token,
    )
    leased_ver = kernel.get_plan_version()

    result = dispatcher.dispatch_task(
        kernel=kernel,
        resource_mgr=res_mgr,
        task_id=task_id,
        invoker=invoker,
        expected_plan_version=leased_ver,
    )

    assert result.status == "ok"
    assert result.terminal_state == TaskState.OBSERVING.value


# =========================================================================
# THE 20 SECURITY & CHAOS BATTERY TESTS
# =========================================================================


def test_sec_01_missing_lease() -> None:
    """Security 1: Task without lease rejected before dispatch."""
    _, kernel, res_mgr, dispatcher, invoker = _setup_pipeline_env()
    task_id = "sec-task-01"
    _add_task(kernel, task_id, capability="python.eval_sandboxed", state="ready")

    res = dispatcher.dispatch_task(kernel, res_mgr, task_id, invoker)
    assert res.status == "rejected"
    assert "leased" in (res.reason or "").lower() or "missing" in (res.error or "").lower()


def test_sec_02_expired_lease() -> None:
    """Security 2: Expired lease rejected before execution launch."""
    clock = FakeClock(initial_time=datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc))
    _, kernel, res_mgr, dispatcher, invoker = _setup_pipeline_env(clock=clock)
    task_id = "sec-task-02"
    _add_task(kernel, task_id, capability="python.eval_sandboxed", state="ready")
    ver, _ = _lease_task(dispatcher, kernel, res_mgr, task_id)

    clock.advance(120.0)

    res = dispatcher.dispatch_task(kernel, res_mgr, task_id, invoker, expected_plan_version=ver)
    assert res.status == "rejected"
    assert "expired" in (res.error or "").lower() or "invalid" in (res.reason or "").lower()


def test_sec_03_forged_lease() -> None:
    """Security 3: Forged / non-existent lease token rejected."""
    _, kernel, res_mgr, dispatcher, invoker = _setup_pipeline_env()
    task_id = "sec-task-03"
    _add_task(kernel, task_id, capability="python.eval_sandboxed", state="ready")

    # Transition to leased with forged token
    cur_ver = kernel.get_plan_version()
    kernel.propose_task_transition(task_id, TaskState.ADMISSION_PENDING.value, cur_ver)
    cur_ver = kernel.get_plan_version()
    kernel.propose_task_transition(task_id, TaskState.ADMITTED.value, cur_ver)
    cur_ver = kernel.get_plan_version()
    kernel.propose_task_transition(task_id, TaskState.LEASE_PENDING.value, cur_ver)
    cur_ver = kernel.get_plan_version()
    kernel.propose_task_transition(
        task_id,
        TaskState.LEASED.value,
        cur_ver,
        result_ref="forged-token-99999",
    )

    res = dispatcher.dispatch_task(kernel, res_mgr, task_id, invoker)
    assert res.status == "rejected"
    assert "lease_not_found" in (res.error or "").lower()


def test_sec_04_wrong_space_lease() -> None:
    """Security 4: Cross-space lease access rejected (Law 1)."""
    _, kernel, res_mgr, dispatcher, invoker = _setup_pipeline_env(space_id="space-local")
    task_id = "sec-task-04"
    _add_task(kernel, task_id, capability="python.eval_sandboxed", state="ready")

    # Acquire lease under foreign space
    res_ident = ResourceIdentity("compute", "host-1", "core-0")
    res_mgr.register_resource(Resource(identity=res_ident, space_id="space-foreign", total_capacity=10))
    acq = res_mgr.acquire(
        space_id="space-foreign",
        requester_id=task_id,
        identity=res_ident,
        units=1,
    )
    assert acq.lease is not None

    cur_ver = kernel.get_plan_version()
    kernel.propose_task_transition(task_id, TaskState.ADMISSION_PENDING.value, cur_ver)
    cur_ver = kernel.get_plan_version()
    kernel.propose_task_transition(task_id, TaskState.ADMITTED.value, cur_ver)
    cur_ver = kernel.get_plan_version()
    kernel.propose_task_transition(task_id, TaskState.LEASE_PENDING.value, cur_ver)
    cur_ver = kernel.get_plan_version()
    kernel.propose_task_transition(
        task_id,
        TaskState.LEASED.value,
        cur_ver,
        result_ref=acq.lease.lease_token,
    )

    res = dispatcher.dispatch_task(kernel, res_mgr, task_id, invoker)
    assert res.status == "rejected"
    assert "cross_space" in (res.error or "").lower() or "cross-space" in (res.reason or "").lower()


def test_sec_05_wrong_task_lease() -> None:
    """Security 5: Lease issued to another requester rejected."""
    _, kernel, res_mgr, dispatcher, invoker = _setup_pipeline_env()
    task_id = "sec-task-05"
    _add_task(kernel, task_id, capability="python.eval_sandboxed", state="ready")

    res_ident = ResourceIdentity("compute", "host-1", "core-0")
    res_mgr.register_resource(Resource(identity=res_ident, space_id=kernel.space_id, total_capacity=10))
    acq = res_mgr.acquire(
        space_id=kernel.space_id,
        requester_id="different-task",
        identity=res_ident,
        units=1,
    )
    assert acq.lease is not None

    cur_ver = kernel.get_plan_version()
    kernel.propose_task_transition(task_id, TaskState.ADMISSION_PENDING.value, cur_ver)
    cur_ver = kernel.get_plan_version()
    kernel.propose_task_transition(task_id, TaskState.ADMITTED.value, cur_ver)
    cur_ver = kernel.get_plan_version()
    kernel.propose_task_transition(task_id, TaskState.LEASE_PENDING.value, cur_ver)
    cur_ver = kernel.get_plan_version()
    kernel.propose_task_transition(
        task_id,
        TaskState.LEASED.value,
        cur_ver,
        result_ref=acq.lease.lease_token,
    )

    res = dispatcher.dispatch_task(kernel, res_mgr, task_id, invoker)
    assert res.status == "rejected"
    assert "wrong_task_lease" in (res.error or "").lower()


def test_sec_06_stale_plan_version() -> None:
    """Security 6: Stale CAS version rejects dispatch atomically."""
    _, kernel, res_mgr, dispatcher, invoker = _setup_pipeline_env()
    task_id = "sec-task-06"
    _add_task(kernel, task_id, capability="python.eval_sandboxed", state="ready")
    ver, _ = _lease_task(dispatcher, kernel, res_mgr, task_id)

    res = dispatcher.dispatch_task(kernel, res_mgr, task_id, invoker, expected_plan_version=ver - 1)
    assert res.status == "rejected"
    assert res.error is not None and "plan_version_mismatch" in res.error


def test_sec_07_missing_admission() -> None:
    """Security 7: Task without admission cannot be dispatched directly from READY."""
    _, kernel, res_mgr, dispatcher, invoker = _setup_pipeline_env()
    task_id = "sec-task-07"
    _add_task(kernel, task_id, capability="python.eval_sandboxed", state="ready")

    res = dispatcher.dispatch_task(kernel, res_mgr, task_id, invoker)
    assert res.status == "rejected"
    assert "leased" in res.reason.lower()


def test_sec_08_forged_admission() -> None:
    """Security 8: Bypassing admission by jumping directly to LEASED without kernel CAS fails."""
    _, kernel, res_mgr, dispatcher, invoker = _setup_pipeline_env()
    task_id = "sec-task-08"
    _add_task(kernel, task_id, capability="python.eval_sandboxed", state="ready")

    # Direct propose_task_transition from READY -> LEASED violates legal state transitions
    cur_ver = kernel.get_plan_version()
    with pytest.raises(Exception):
        kernel.propose_task_transition(task_id, TaskState.LEASED.value, cur_ver)


def test_sec_09_wrong_capability() -> None:
    """Security 9: Worker invoked for unsupported capability returns permission_denied."""
    invoker = RuntimeWorkerInvoker()
    req = TaskExecutionRequest(
        request_id="req-wrong-cap",
        task_id="task-1",
        space_id="space-1",
        plan_id="plan-1",
        plan_version=1,
        attempt=1,
        capability="unsupported.fake.capability",
    )
    res = invoker.invoke(req)
    assert res.status == "denied"
    assert res.error_class is not None and "permission_denied" in res.error_class


def test_sec_10_cross_space_execution() -> None:
    """Security 10: Worker configured for space A rejects execution request from space B."""
    worker = PythonWorker(
        identity=WorkerIdentity(worker_id="py-1", capability="python.eval_sandboxed", space_id="space-A")
    )
    req = ExecutionRequest(
        request_id="req-cross",
        correlation_id="corr-cross",
        space_id="space-B",  # Mismatched space
        worker_id="py-1",
        capability="python.eval_sandboxed",
        arguments={"code": "print(1)"},
    )
    res = worker.execute(req)
    assert res.status == "denied"
    assert res.error is not None and "Cross-space" in res.error.message


def test_sec_11_worker_identity_mismatch() -> None:
    """Security 11: Worker capability mismatch rejected."""
    worker = PythonWorker(
        identity=WorkerIdentity(worker_id="py-1", capability="python.eval_sandboxed", space_id="space-A")
    )
    req = ExecutionRequest(
        request_id="req-cap-mismatch",
        correlation_id="corr-mismatch",
        space_id="space-A",
        worker_id="py-1",
        capability="terminal.exec",  # Mismatch capability
        arguments={"command": ["ls"]},
    )
    res = worker.execute(req)
    assert res.status == "denied"
    assert res.error is not None and "capability mismatch" in res.error.message.lower()


def test_sec_12_node_identity_mismatch(tmp_path: Path) -> None:
    """Security 12: Node worker executing grant with node mismatch rejected."""
    bus = PulseBus()
    rm = ResourceManager(bus=bus, store=InMemoryResourceStore())
    registry = NodeRegistry()
    secret = "high-entropy-node-secret-32bytes"

    node = NodeInfo(
        node_id="node-test-01",
        platform="windows",
        architecture="x86_64",
        environment_profile="windows_host",
        runtime_state=NodeState.READY,
    )
    registry.register_node(node, secret)
    device = DeviceInfo(device_id="gpu-0", node_id="node-test-01", device_type=DeviceType.GPU, total_capacity=1)
    registry.register_device(device)
    registry.sync_resources_to_manager(rm, space_id="space-main")

    gm = DeviceGrantManager(registry=registry, resource_manager=rm, bus=bus)
    audit_log = DeviceAuditLog(log_path=tmp_path / "audit.log.jsonl")
    runtime = NodeRuntime("node-test-01", secret, registry, audit_log, bus=bus)

    worker = NodeWorker(node_runtime=runtime, identity=WorkerIdentity("node-w-1", "gpu.cuda", "space-main"))

    res_ident = ResourceIdentity("gpu", "node-test-01", "gpu-0")
    acq = rm.acquire(space_id="space-main", requester_id="node-w-1", identity=res_ident, units=1)
    assert acq.lease is not None

    grant = gm.create_grant(
        space_id="space-main",
        worker_id="node-w-1",
        node_id="node-test-01",
        device_id="gpu-0",
        capability="gpu.cuda",
        lease_token=acq.lease.lease_token,
    )

    grant_dict = grant.to_dict()
    grant_dict["node_id"] = "different-node-id"  # Tampered node_id

    req = ExecutionRequest(
        request_id="req-node-mismatch",
        correlation_id="corr-node",
        space_id="space-main",
        worker_id="node-w-1",
        capability="gpu.cuda",
        lease_id=acq.lease.lease_token,
        arguments={"device_id": "gpu-0", "grant": grant_dict, "operation": "test"},
    )
    res = worker.execute(req)
    assert not res.is_success
    assert res.error is not None and ("cross-node" in res.error.message.lower() or "node_id mismatch" in res.error.message.lower())


def test_sec_13_sandbox_bypass_attempt(tmp_path: Path) -> None:
    """Security 13: Filesystem sandbox blocks directory traversal attempt."""
    worker = FileWorker(
        identity=WorkerIdentity("file-1", "file.*", "space-1")
    )
    safe_dir = tmp_path / "safe"
    safe_dir.mkdir()

    policy = SandboxPolicy(
        fs_policy=FilesystemPolicy(
            read_paths=[str(safe_dir)],
            write_paths=[str(safe_dir)],
        )
    )

    # Attempt to write outside safe_dir
    req = ExecutionRequest(
        request_id="req-traversal",
        correlation_id="corr-trav",
        space_id="space-1",
        worker_id="file-1",
        capability="file.write",
        arguments={
            "operation": "write",
            "path": str(tmp_path.parent / "forbidden.txt"),
            "content": "illegal content",
        },
        sandbox_policy=policy,
    )
    res = worker.execute(req)
    assert res.status in ("violation", "failed", "denied")


def test_sec_14_secret_leakage_redaction() -> None:
    """Security 14: Sensitive credentials and bearer tokens redacted from stdout and logs."""
    worker = PythonWorker()
    secret_token = "ghp_SecretToken1234567890abcdef"
    req = ExecutionRequest(
        request_id="req-secret",
        correlation_id="corr-sec",
        space_id="default-space",
        worker_id=worker.worker_id,
        capability="python.eval_sandboxed",
        arguments={"code": f"print('bearer {secret_token}')"},
    )
    res = worker.execute(req)
    assert res.is_success
    assert secret_token not in str(res.output_data)
    assert "[REDACTED]" in str(res.output_data)


def test_sec_15_taint_forward_only() -> None:
    """Security 15: Tainted input cannot be cleared by worker execution (Forward-only)."""
    _, kernel, res_mgr, dispatcher, invoker = _setup_pipeline_env()
    task_id = "sec-task-15"
    _add_task(
        kernel,
        task_id,
        capability="python.eval_sandboxed",
        state="ready",
        params={"code": "print('clean_code')"},
    )
    ver, _ = _lease_task(dispatcher, kernel, res_mgr, task_id)

    result = dispatcher.dispatch_task(
        kernel=kernel,
        resource_mgr=res_mgr,
        task_id=task_id,
        invoker=invoker,
        expected_plan_version=ver,
        is_tainted=True,  # Input is tainted
    )
    assert result.taint is True


def test_sec_16_duplicate_execution_request() -> None:
    """Security 16: Duplicate dispatch request returned idempotently without re-execution."""
    _, kernel, res_mgr, dispatcher, invoker = _setup_pipeline_env()
    task_id = "sec-task-16"
    _add_task(
        kernel,
        task_id,
        capability="python.eval_sandboxed",
        state="ready",
        params={"code": "print('once')"},
    )
    ver, _ = _lease_task(dispatcher, kernel, res_mgr, task_id)

    # First dispatch
    res1 = dispatcher.dispatch_task(kernel, res_mgr, task_id, invoker, expected_plan_version=ver)
    assert res1.status == "ok"
    assert not res1.cached

    # Duplicate dispatch
    res2 = dispatcher.dispatch_task(kernel, res_mgr, task_id, invoker, expected_plan_version=ver)
    assert res2.status == "ok"
    assert res2.cached is True


def test_sec_17_duplicate_execution_result() -> None:
    """Security 17: Duplicate CAS state transition rejected."""
    _, kernel, _, _, _ = _setup_pipeline_env()
    task_id = "sec-task-17"
    _add_task(kernel, task_id, capability="python.eval_sandboxed", state="ready")
    cur_ver = kernel.get_plan_version()

    ok, new_ver, _ = kernel.propose_task_transition(task_id, TaskState.ADMISSION_PENDING.value, cur_ver)
    assert ok

    # Duplicate call with stale version
    ok2, _, err = kernel.propose_task_transition(task_id, TaskState.ADMISSION_PENDING.value, cur_ver)
    assert not ok2
    assert err is not None


def test_sec_18_malformed_execution_request() -> None:
    """Security 18: Malformed ExecutionRequest returns clean error without crashing."""
    invoker = RuntimeWorkerInvoker()
    req = TaskExecutionRequest(
        request_id="req-malformed",
        task_id="task-1",
        space_id="space-1",
        plan_id="plan-1",
        plan_version=1,
        attempt=1,
        capability="python.eval_sandboxed",
        arguments={},  # Missing 'code' parameter
    )
    res = invoker.invoke(req)
    assert res.status == "failed"
    assert res.error_class == "terminal.invalid_params"


def test_sec_19_worker_crash() -> None:
    """Security 19: Unhandled python exception in subprocess captured, transitions task to FAILED, releases lease."""
    bus, kernel, res_mgr, dispatcher, invoker = _setup_pipeline_env()
    task_id = "sec-task-19"
    _add_task(
        kernel,
        task_id,
        capability="python.eval_sandboxed",
        state="ready",
        params={"code": "raise ZeroDivisionError('boom')\n"},
    )
    ver, lease_token = _lease_task(dispatcher, kernel, res_mgr, task_id)

    res = dispatcher.dispatch_task(kernel, res_mgr, task_id, invoker, expected_plan_version=ver)
    assert res.status == "failed"
    assert res.terminal_state == TaskState.FAILED.value
    assert res.error is not None and "ZeroDivisionError" in res.error

    node = kernel.get_task_graph().get_node(task_id)
    assert node is not None
    assert node.state == TaskState.FAILED.value

    # task.failed pulse emitted
    fail_pulses = bus.find_by_type("task.failed")
    assert len(fail_pulses) == 1

    # Lease released
    lease = res_mgr.get_lease(lease_token)
    assert lease is None or lease.state.value == "released"


def test_sec_20_worker_timeout() -> None:
    """Security 20: Worker process exceeding execution limits terminated, transitions to TIMED_OUT, releases lease."""
    bus, kernel, res_mgr, dispatcher, invoker = _setup_pipeline_env()
    task_id = "sec-task-20"
    _add_task(
        kernel,
        task_id,
        capability="python.eval_sandboxed",
        state="ready",
        params={"code": "import time\ntime.sleep(10)\n"},
    )
    ver, lease_token = _lease_task(dispatcher, kernel, res_mgr, task_id)

    # Set 0.5s timeout
    res = dispatcher.dispatch_task(
        kernel,
        res_mgr,
        task_id,
        invoker,
        expected_plan_version=ver,
        timeout_seconds=0.5,
    )
    assert res.status == "timeout"
    assert res.terminal_state == TaskState.TIMED_OUT.value

    node = kernel.get_task_graph().get_node(task_id)
    assert node is not None
    assert node.state == TaskState.TIMED_OUT.value

    fail_pulses = bus.find_by_type("task.failed")
    assert len(fail_pulses) == 1

    lease = res_mgr.get_lease(lease_token)
    assert lease is None or lease.state.value == "released"
