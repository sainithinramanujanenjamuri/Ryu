"""Executable harness verification for Node Runtime contracts NODE-001 through NODE-008.

Space-Centric Cognitive Architecture (SCCA) — Phase 7
spec §11 (Node Runtime), §16 (Lease & Grants), CONTRACT_MATRIX NODE-001..NODE-008
ADR-0017, ADR-0018, ADR-0019, ADR-0020
"""

from __future__ import annotations

import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from ryu.pulse_bus.bus import PulseBus

from core.resources.identity import ResourceIdentity
from core.resources.manager import ResourceManager
from core.resources.store import InMemoryResourceStore
from node.audit import DeviceAuditLog
from node.bridge import RustNodeBridge
from node.contract import (
    DeviceGrant,
    DeviceInfo,
    DeviceState,
    DeviceType,
    GrantRevokedError,
    NodeInfo,
    NodeState,
    RiskTier,
)
from node.coordinator import NodeCoordinator
from node.grants import DeviceGrantManager
from node.registry import NodeRegistry
from node.runtime import NodeRuntime


def test_node_cargo_workspace_valid() -> None:
    """NODE-001: Node Runtime is Rust, not Python; workspace is compilable and verifiable."""
    repo_root = Path(__file__).resolve().parents[3]
    manifest_path = repo_root / "node_runtime" / "Cargo.toml"
    assert manifest_path.exists(), f"Cargo manifest missing at {manifest_path}"

    # Verify cargo check passes cleanly
    res = subprocess.run(
        ["cargo", "check", "--manifest-path", str(manifest_path)],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, f"cargo check failed: {res.stderr}"

    bridge = RustNodeBridge()
    assert bridge.binary_path.exists(), f"Native binary {bridge.binary_path} does not exist"
    version = bridge.version()
    assert "ryu-node v0.1.0" in version

    # Platform inspection via Rust
    info = bridge.inspect("node-node-001")
    assert info.node_id == "node-node-001"
    assert info.cpu_cores >= 1


def test_node_grant_enforcement() -> None:
    """NODE-002: Device itself validates grants cryptographically via HMAC-SHA256."""
    bridge = RustNodeBridge()
    secret = "high-entropy-paired-secret-key-node002"

    grant = DeviceGrant(
        grant_id="grant-n002",
        space_id="space-sec",
        worker_id="worker-sec-01",
        node_id="node-n002",
        device_id="gpu-0",
        capability="gpu.cuda",
        lease_token="lease-token-12345",
        nonce="nonce-abc-123",
        issued_at="2026-09-20T12:00:00Z",
        expiry="2026-09-20T13:00:00Z",
        risk_tier=RiskTier.LOW,
        grant_schema_version="1.0",
    )
    grant.sign(secret)

    # Native Rust verification with genuine secret -> VALID
    valid, err = bridge.validate_grant(
        node_id="node-n002",
        secret=secret,
        grant=grant,
        current_time="2026-09-20T12:30:00Z",
    )
    assert valid is True
    assert err is None

    # Native Rust verification with forged/wrong secret -> REJECT
    valid_bad, err_bad = bridge.validate_grant(
        node_id="node-n002",
        secret="forged-secret-key-wrong",
        grant=grant,
        current_time="2026-09-20T12:30:00Z",
    )
    assert valid_bad is False
    assert "HMAC" in str(err_bad)


def test_node_grant_space_and_session_binding() -> None:
    """NODE-003: Grant bound to Space, capability, and session lease."""
    bus = PulseBus()
    rm = ResourceManager(bus=bus, store=InMemoryResourceStore())
    registry = NodeRegistry()
    secret = "high-entropy-node-secret-32bytes"

    node = NodeInfo(
        node_id="node-test-01",
        platform="windows",
        architecture="x86_64",
        environment_profile="windows_host",
    )
    registry.register_node(node, secret)

    device = DeviceInfo(
        device_id="gpu-0",
        node_id="node-test-01",
        device_type=DeviceType.GPU,
        total_capacity=1,
    )
    registry.register_device(device)
    registry.sync_resources_to_manager(rm, space_id="space-main")

    gm = DeviceGrantManager(registry=registry, resource_manager=rm, bus=bus)

    # Acquire lease for space-main
    res_ident = ResourceIdentity("gpu", "node-test-01", "gpu-0")
    acq = rm.acquire(
        space_id="space-main",
        requester_id="worker-01",
        identity=res_ident,
        units=1,
    )
    assert acq.lease is not None

    # Grant succeeds with matching space and lease
    grant = gm.create_grant(
        space_id="space-main",
        worker_id="worker-01",
        node_id="node-test-01",
        device_id="gpu-0",
        capability="gpu.cuda",
        lease_token=acq.lease.lease_token,
    )
    assert grant.space_id == "space-main"
    assert grant.lease_token == acq.lease.lease_token

    # Attempt cross-space grant creation with this lease -> REJECT
    with pytest.raises(Exception):
        gm.create_grant(
            space_id="space-other",
            worker_id="worker-01",
            node_id="node-test-01",
            device_id="gpu-0",
            capability="gpu.cuda",
            lease_token=acq.lease.lease_token,
        )


def test_node_revocation_during_execution(tmp_path: Path) -> None:
    """NODE-004: Device enforces revocation mid-call."""
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
    device = DeviceInfo(device_id="gpu-0", node_id="node-test-01", device_type=DeviceType.GPU)
    registry.register_device(device)
    registry.sync_resources_to_manager(rm, space_id="space-main")

    gm = DeviceGrantManager(registry=registry, resource_manager=rm, bus=bus)
    audit_log = DeviceAuditLog(log_path=tmp_path / "audit.log.jsonl")
    runtime = NodeRuntime(
        node_id="node-test-01",
        shared_secret=secret,
        registry=registry,
        audit_log=audit_log,
        bus=bus,
    )

    res_ident = ResourceIdentity("gpu", "node-test-01", "gpu-0")
    acq = rm.acquire(
        space_id="space-main", requester_id="worker-01", identity=res_ident, units=1
    )
    assert acq.lease is not None
    grant = gm.create_grant(
        "space-main", "worker-01", "node-test-01", "gpu-0", "gpu.cuda", acq.lease.lease_token
    )

    binding = runtime.bind_device("worker-01", "space-main", grant, "gpu-0")
    assert binding.is_active is True

    # Mid-call revocation enforced
    runtime.revoke_in_flight(grant.grant_id)
    assert binding.is_active is False
    dev = registry.get_device("gpu-0")
    assert dev is not None and dev.availability_state == DeviceState.ONLINE

    # Re-binding with revoked grant is blocked
    with pytest.raises(GrantRevokedError):
        runtime.bind_device("worker-01", "space-main", grant, "gpu-0")


def test_node_heartbeat_lease_monitoring() -> None:
    """NODE-005: Heartbeat lease between Kernel and Node Runtime."""
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

    gm = DeviceGrantManager(registry=registry, resource_manager=rm, bus=bus)
    coord = NodeCoordinator(
        registry=registry,
        grant_manager=gm,
        resource_manager=rm,
        bus=bus,
        heartbeat_ttl_seconds=5.0,
    )

    now = datetime.now(timezone.utc)
    coord.record_heartbeat("node-test-01", timestamp=now)

    # Advance time within TTL -> still READY
    coord.sweep_timeouts(current_time=now + timedelta(seconds=2))
    n1 = registry.get_node("node-test-01")
    assert n1 is not None and n1.runtime_state == NodeState.READY

    # Advance time beyond TTL -> OFFLINE
    offline = coord.sweep_timeouts(current_time=now + timedelta(seconds=10))
    assert "node-test-01" in offline
    n2 = registry.get_node("node-test-01")
    assert n2 is not None and n2.runtime_state == NodeState.OFFLINE


def test_node_offline_checkpointing() -> None:
    """NODE-006: Node disconnect creates checkpointable offline state."""
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
    gm = DeviceGrantManager(registry=registry, resource_manager=rm, bus=bus)
    coord = NodeCoordinator(
        registry=registry,
        grant_manager=gm,
        resource_manager=rm,
        bus=bus,
        heartbeat_ttl_seconds=5.0,
    )

    now = datetime.now(timezone.utc)
    coord.record_heartbeat("node-test-01", timestamp=now)
    coord.register_in_flight_task(
        "task-chk-01", "space-main", "worker-01", "node-test-01", "gpu-0", "grant-01",
        is_idempotent=True,
    )

    coord.sweep_timeouts(current_time=now + timedelta(seconds=10))
    cp = coord.get_task_checkpoint("task-chk-01")
    assert cp is not None
    assert cp.state == "checkpointed"


def test_node_offline_recovery() -> None:
    """NODE-007: Reconnection resumes with idempotency semantics."""
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
    device = DeviceInfo(device_id="gpu-0", node_id="node-test-01", device_type=DeviceType.GPU)
    registry.register_device(device)
    registry.sync_resources_to_manager(rm, space_id="space-main")

    gm = DeviceGrantManager(registry=registry, resource_manager=rm, bus=bus)
    coord = NodeCoordinator(
        registry=registry,
        grant_manager=gm,
        resource_manager=rm,
        bus=bus,
        heartbeat_ttl_seconds=5.0,
    )

    t0 = datetime.now(timezone.utc)
    coord.record_heartbeat("node-test-01", timestamp=t0)

    res_ident = ResourceIdentity("gpu", "node-test-01", "gpu-0")
    acq = rm.acquire(
        space_id="space-main",
        requester_id="worker-01",
        identity=res_ident,
        units=1,
        duration_seconds=3600.0,
    )
    assert acq.lease is not None
    grant = gm.create_grant(
        "space-main", "worker-01", "node-test-01", "gpu-0", "gpu.cuda", acq.lease.lease_token
    )

    coord.register_in_flight_task(
        "task-rec-01", "space-main", "worker-01", "node-test-01", "gpu-0",
        grant.grant_id, is_idempotent=True,
    )

    # Disconnect
    coord.sweep_timeouts(current_time=t0 + timedelta(seconds=10))
    n1 = registry.get_node("node-test-01")
    assert n1 is not None and n1.runtime_state == NodeState.OFFLINE

    # Reconnect
    t1 = t0 + timedelta(seconds=12)
    coord.reconnect_node("node-test-01", acq.lease.lease_token, current_time=t1)
    n2 = registry.get_node("node-test-01")
    assert n2 is not None and n2.runtime_state == NodeState.READY

    # Validated resume
    resumed = coord.validate_and_resume_task("task-rec-01", current_time=t1)
    assert resumed.state == "resumed"


def test_node_audit_verification_independent(tmp_path: Path) -> None:
    """NODE-008: Device audit log readable independently of Ryu server."""
    log_file = tmp_path / "node_audit.log.jsonl"
    audit = DeviceAuditLog(log_path=log_file)

    audit.append(
        "node-test-01", "space-alpha", "DEVICE_BOUND", "grant-1", "gpu-0", "op-1", "SUCCESS"
    )
    audit.append(
        "node-test-01", "space-alpha", "DEVICE_RELEASED", "grant-1", "gpu-0", "op-1", "SUCCESS"
    )

    # Python audit verification
    py_valid, py_count, py_err = audit.verify_chain()
    assert py_valid is True
    assert py_count == 2
    assert py_err is None

    # Native Rust audit verification
    bridge = RustNodeBridge()
    rust_valid, rust_count, rust_err = bridge.audit_verify(log_file)
    assert rust_valid is True
    assert rust_count == 2
    assert rust_err is None
