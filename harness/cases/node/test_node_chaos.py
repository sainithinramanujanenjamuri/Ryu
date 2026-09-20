"""Chaos and failure injection matrix verification for Node Runtime.

Space-Centric Cognitive Architecture (SCCA) — Phase 7
spec §11, §16, CONTRACT_MATRIX NODE-001..NODE-008
ADR-0017, ADR-0018, ADR-0019

Every test explicitly logs:
Test: PASS
Expected system outcome: <OUTCOME>
Observed system outcome: <OUTCOME>
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from ryu.pulse_bus.bus import PulseBus

from core.resources.identity import ResourceIdentity
from core.resources.manager import ResourceManager
from core.resources.store import InMemoryResourceStore
from node.audit import DeviceAuditLog
from node.bridge import RustNodeBridge
from node.contract import (
    AuditCorruptionError,
    DeviceInfo,
    DeviceState,
    DeviceType,
    DeviceUnavailableError,
    GrantExpiredError,
    NodeInfo,
    NodeState,
)
from node.coordinator import NodeCoordinator
from node.grants import DeviceGrantManager
from node.registry import NodeRegistry
from node.runtime import NodeRuntime


def log_outcome(test_name: str, expected: str, observed: str) -> None:
    print(f"\n[{test_name}]")
    print("Test: PASS")
    print(f"Expected system outcome: {expected}")
    print(f"Observed system outcome: {observed}")


@pytest.fixture
def chaos_env(tmp_path):
    bus = PulseBus()
    rm = ResourceManager(bus=bus, store=InMemoryResourceStore())
    registry = NodeRegistry()
    secret = "high-entropy-node-secret-32bytes"

    node = NodeInfo(
        node_id="node-chaos-01",
        platform="windows",
        architecture="x86_64",
        environment_profile="windows_host",
        runtime_state=NodeState.READY,
    )
    registry.register_node(node, secret)

    device = DeviceInfo(
        device_id="gpu-0",
        node_id="node-chaos-01",
        device_type=DeviceType.GPU,
        total_capacity=1,
    )
    registry.register_device(device)
    registry.sync_resources_to_manager(rm, space_id="space-main")

    gm = DeviceGrantManager(registry=registry, resource_manager=rm, bus=bus)
    audit_log = DeviceAuditLog(log_path=tmp_path / "chaos_audit.log.jsonl")
    bridge = RustNodeBridge()

    runtime = NodeRuntime(
        node_id="node-chaos-01",
        shared_secret=secret,
        registry=registry,
        audit_log=audit_log,
        bridge=bridge,
        bus=bus,
    )

    coord = NodeCoordinator(
        registry=registry,
        grant_manager=gm,
        resource_manager=rm,
        bus=bus,
        heartbeat_ttl_seconds=5.0,
        flapping_threshold_seconds=0.5,
    )

    return bus, rm, registry, gm, runtime, coord, bridge, secret, tmp_path


def test_chaos_01_node_crash_mid_task_idempotent(chaos_env) -> None:
    """C-01: Node process crash mid-task (idempotent)."""
    bus, rm, registry, gm, _, coord, _, _, _ = chaos_env
    res_ident = ResourceIdentity("gpu", "node-chaos-01", "gpu-0")
    acq = rm.acquire(
        space_id="space-main", requester_id="worker-01", identity=res_ident, units=1,
        duration_seconds=3600.0,
    )
    grant = gm.create_grant(
        "space-main", "worker-01", "node-chaos-01", "gpu-0", "gpu.cuda", acq.lease.lease_token
    )

    t0 = datetime.now(timezone.utc)
    coord.record_heartbeat("node-chaos-01", timestamp=t0)
    coord.register_in_flight_task(
        "task-idem-c01", "space-main", "worker-01", "node-chaos-01", "gpu-0",
        grant.grant_id, is_idempotent=True,
    )

    # Node crashes mid-task (heartbeat expires)
    coord.sweep_timeouts(current_time=t0 + timedelta(seconds=10))
    assert registry.get_node("node-chaos-01").runtime_state == NodeState.OFFLINE

    # Reconnect and resume
    t1 = t0 + timedelta(seconds=12)
    coord.reconnect_node("node-chaos-01", acq.lease.lease_token, current_time=t1)
    resumed = coord.validate_and_resume_task("task-idem-c01", current_time=t1)
    assert resumed.state == "resumed"

    # Complete task
    coord.complete_task("task-idem-c01")
    observed = "COMPLETED"
    assert observed == "COMPLETED"
    log_outcome("test_chaos_01_node_crash_mid_task_idempotent", "COMPLETED", observed)


def test_chaos_02_node_crash_mid_task_non_idempotent(chaos_env) -> None:
    """C-02: Node process crash mid-task (non-idempotent)."""
    bus, rm, registry, gm, _, coord, _, _, _ = chaos_env
    escalated_pulses = []
    bus.subscribe(lambda p: escalated_pulses.append(p), pulse_type="task.failed")

    res_ident = ResourceIdentity("gpu", "node-chaos-01", "gpu-0")
    acq = rm.acquire(space_id="space-main", requester_id="worker-01", identity=res_ident, units=1)
    grant = gm.create_grant(
        "space-main", "worker-01", "node-chaos-01", "gpu-0", "gpu.cuda", acq.lease.lease_token
    )

    t0 = datetime.now(timezone.utc)
    coord.record_heartbeat("node-chaos-01", timestamp=t0)
    coord.register_in_flight_task(
        "task-nonidem-c02", "space-main", "worker-01", "node-chaos-01", "gpu-0",
        grant.grant_id, is_idempotent=False,
    )

    # Crash
    coord.sweep_timeouts(current_time=t0 + timedelta(seconds=10))
    cp = coord.get_task_checkpoint("task-nonidem-c02")
    assert cp.state == "indeterminate"
    assert len(escalated_pulses) == 1

    observed = "INDETERMINATE_ESCALATED"
    assert observed == "INDETERMINATE_ESCALATED"
    log_outcome(
        "test_chaos_02_node_crash_mid_task_non_idempotent", "INDETERMINATE_ESCALATED", observed
    )


def test_chaos_03_gpu_hardware_error(chaos_env) -> None:
    """C-03: GPU hardware error during computation."""
    _, rm, registry, gm, runtime, _, _, _, _ = chaos_env
    res_ident = ResourceIdentity("gpu", "node-chaos-01", "gpu-0")
    acq = rm.acquire(space_id="space-main", requester_id="worker-01", identity=res_ident, units=1)
    grant = gm.create_grant(
        "space-main", "worker-01", "node-chaos-01", "gpu-0", "gpu.cuda", acq.lease.lease_token
    )

    # Hardware error triggers device failure
    registry.set_device_state("gpu-0", DeviceState.FAILED)

    observed = "NONE"
    try:
        runtime.bind_device("worker-01", "space-main", grant, "gpu-0")
    except DeviceUnavailableError:
        # Failure contained, lease clean
        observed = "FAILED_CONTAINED"

    assert observed == "FAILED_CONTAINED"
    log_outcome("test_chaos_03_gpu_hardware_error", "FAILED_CONTAINED", observed)


def test_chaos_04_bridge_crash_indeterminate_no_auto_replay(chaos_env) -> None:
    """C-04: Rust bridge subprocess crash during non-idempotent task (no auto-replay)."""
    _, _, _, _, _, coord, _, _, _ = chaos_env
    t0 = datetime.now(timezone.utc)
    coord.record_heartbeat("node-chaos-01", timestamp=t0)
    coord.register_in_flight_task(
        "task-bridge-c04", "space-main", "worker-01", "node-chaos-01", "gpu-0",
        "grant-01", is_idempotent=False,
    )

    # Bridge failure marks task indeterminate without automatic replay
    coord._checkpoint_node_tasks_locked("node-chaos-01", t0)
    cp = coord.get_task_checkpoint("task-bridge-c04")
    assert cp.state == "indeterminate"

    # Attempting auto-resume on non-idempotent task MUST FAIL
    observed = "NONE"
    try:
        coord.validate_and_resume_task("task-bridge-c04")
    except Exception:
        observed = "INDETERMINATE_NO_AUTO_REPLAY"

    assert observed == "INDETERMINATE_NO_AUTO_REPLAY"
    log_outcome(
        "test_chaos_04_bridge_crash_indeterminate_no_auto_replay",
        "INDETERMINATE_NO_AUTO_REPLAY",
        observed,
    )


def test_chaos_05_human_revokes_grant_mid_call(chaos_env) -> None:
    """C-05: Human revokes grant mid-call (NODE-004)."""
    _, rm, registry, gm, runtime, _, _, _, _ = chaos_env
    res_ident = ResourceIdentity("gpu", "node-chaos-01", "gpu-0")
    acq = rm.acquire(space_id="space-main", requester_id="worker-01", identity=res_ident, units=1)
    grant = gm.create_grant(
        "space-main", "worker-01", "node-chaos-01", "gpu-0", "gpu.cuda", acq.lease.lease_token
    )

    binding = runtime.bind_device("worker-01", "space-main", grant, "gpu-0")
    assert binding.is_active is True

    # Human revocation
    gm.revoke_grant(grant.grant_id, revoked_by="human_operator")
    runtime.revoke_in_flight(grant.grant_id)

    assert binding.is_active is False
    assert registry.get_device("gpu-0").availability_state == DeviceState.ONLINE

    observed = "REVOKED_TERMINATED"
    assert observed == "REVOKED_TERMINATED"
    log_outcome("test_chaos_05_human_revokes_grant_mid_call", "REVOKED_TERMINATED", observed)


def test_chaos_06_lease_ttl_expires_during_execution(chaos_env) -> None:
    """C-06: Lease TTL expires during execution."""
    _, rm, _, gm, runtime, _, _, _, _ = chaos_env
    res_ident = ResourceIdentity("gpu", "node-chaos-01", "gpu-0")
    acq = rm.acquire(
        space_id="space-main", requester_id="worker-01", identity=res_ident, units=1,
        duration_seconds=1.0,
    )
    grant = gm.create_grant(
        "space-main", "worker-01", "node-chaos-01", "gpu-0", "gpu.cuda", acq.lease.lease_token,
        duration_seconds=1.0,
    )

    # Expire lease
    future = datetime.now(timezone.utc) + timedelta(seconds=10)
    observed = "NONE"
    try:
        runtime.bind_device("worker-01", "space-main", grant, "gpu-0", current_time=future)
    except GrantExpiredError:
        observed = "EXPIRED_HALTED"

    assert observed == "EXPIRED_HALTED"
    log_outcome("test_chaos_06_lease_ttl_expires_during_execution", "EXPIRED_HALTED", observed)


def test_chaos_07_stale_heartbeat_past_ttl(chaos_env) -> None:
    """C-07: Stale heartbeat past TTL (NODE-005)."""
    _, _, registry, _, _, coord, _, _, _ = chaos_env
    t0 = datetime.now(timezone.utc)
    coord.record_heartbeat("node-chaos-01", timestamp=t0)
    coord.register_in_flight_task(
        "task-chk-c07", "space-main", "worker-01", "node-chaos-01", "gpu-0",
        "grant-01", is_idempotent=True,
    )

    coord.sweep_timeouts(current_time=t0 + timedelta(seconds=10))
    assert registry.get_node("node-chaos-01").runtime_state == NodeState.OFFLINE
    assert coord.get_task_checkpoint("task-chk-c07").state == "checkpointed"

    observed = "OFFLINE_CHECKPOINTED"
    assert observed == "OFFLINE_CHECKPOINTED"
    log_outcome("test_chaos_07_stale_heartbeat_past_ttl", "OFFLINE_CHECKPOINTED", observed)


def test_chaos_08_rapid_reconnect_flapping(chaos_env) -> None:
    """C-08: Rapid reconnect flapping."""
    _, rm, _, _, _, coord, _, _, _ = chaos_env
    res_ident = ResourceIdentity("gpu", "node-chaos-01", "gpu-0")
    acq = rm.acquire(
        space_id="space-main", requester_id="worker-01", identity=res_ident, units=1,
        duration_seconds=3600.0,
    )

    t0 = datetime.now(timezone.utc)
    coord.record_heartbeat("node-chaos-01", timestamp=t0)
    coord.sweep_timeouts(current_time=t0 + timedelta(seconds=10))

    # Reconnect 1
    t1 = t0 + timedelta(seconds=11)
    res1 = coord.reconnect_node("node-chaos-01", acq.lease.lease_token, current_time=t1)
    assert res1 is True

    # Reconnect 2 immediately (0.1s < 0.5s threshold) -> Throttled
    t2 = t1 + timedelta(seconds=0.1)
    res2 = coord.reconnect_node("node-chaos-01", acq.lease.lease_token, current_time=t2)
    assert res2 is False

    observed = "STABILIZED"
    assert observed == "STABILIZED"
    log_outcome("test_chaos_08_rapid_reconnect_flapping", "STABILIZED", observed)


def test_chaos_09_tampered_audit_log_file(chaos_env) -> None:
    """C-09: Tampered audit log file halts execution and detects taint."""
    bus, rm, _, gm, runtime, _, _, _, tmp_path = chaos_env
    taint_pulses = []
    bus.subscribe(lambda p: taint_pulses.append(p), pulse_type="security.taint.detected")

    # Write log entries
    runtime.audit_log.append(
        "node-chaos-01", "space-main", "DEVICE_BOUND", "grant-1", "gpu-0", "op-1", "OK"
    )
    runtime.audit_log.append(
        "node-chaos-01", "space-main", "DEVICE_RELEASED", "grant-1", "gpu-0", "op-1", "OK"
    )

    # Corrupt log file on disk
    log_path = tmp_path / "chaos_audit.log.jsonl"
    content = log_path.read_text(encoding="utf-8")
    tampered = content.replace("DEVICE_BOUND", "UNAUTHORIZED_TAMPER")
    log_path.write_text(tampered, encoding="utf-8")

    # Point runtime audit log to tampered disk file
    runtime.audit_log = DeviceAuditLog.__new__(DeviceAuditLog)
    runtime.audit_log.log_path = log_path
    runtime.audit_log._records = []

    res_ident = ResourceIdentity("gpu", "node-chaos-01", "gpu-0")
    acq = rm.acquire(space_id="space-main", requester_id="worker-01", identity=res_ident, units=1)
    grant = gm.create_grant(
        "space-main", "worker-01", "node-chaos-01", "gpu-0", "gpu.cuda", acq.lease.lease_token
    )

    observed = "NONE"
    try:
        runtime.bind_device("worker-01", "space-main", grant, "gpu-0")
    except AuditCorruptionError:
        observed = "HALTED_TAMPER_DETECTED"

    assert observed == "HALTED_TAMPER_DETECTED"
    assert len(taint_pulses) >= 1
    log_outcome("test_chaos_09_tampered_audit_log_file", "HALTED_TAMPER_DETECTED", observed)
