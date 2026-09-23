"""Executable harness verification for Node Runtime contract NODE-009.

Space-Centric Cognitive Architecture (SCCA) — Phase 11
spec §11 (Node Runtime - Linux Profile), CONTRACT_MATRIX NODE-009
ADR-0020, ADR-0037

INVARIANT:
WSL2 != Native Linux Device.
WSL2 provides Linux compatibility validation; it must never be represented as
proof of native Linux hardware/device independence.
Second platform profile passes the entire Phase 7 contract suite (NODE-001..008).
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
from node.platforms.linux import LinuxHostProfile, WSL2Profile
from node.registry import NodeRegistry
from node.runtime import NodeRuntime


def test_node_linux_platform_profile_identity() -> None:
    """NODE-009: Verify Linux platform profiles and explicit separation of WSL2 vs Native Linux."""
    native = LinuxHostProfile()
    wsl = WSL2Profile()

    assert native.platform_name == "linux"
    assert wsl.platform_name == "linux"

    # INVARIANT: WSL2 != Native Linux Device
    assert native.environment_profile == "linux_native"
    assert wsl.environment_profile == "wsl2"
    assert native.environment_profile != wsl.environment_profile

    # Evidence labels must strictly distinguish validation type
    assert native.evidence_label == "Native Linux host"
    assert wsl.evidence_label == "Linux compatibility validation via WSL2"

    native_info = native.inspect_system("node-linux-native-01")
    wsl_info = wsl.inspect_system("node-linux-wsl-01")
    assert native_info.labels["evidence_type"] == "Native Linux host"
    assert wsl_info.labels["evidence_type"] == "Linux compatibility validation via WSL2"


def test_node_linux_profile_passes_phase7_contracts(tmp_path: Path) -> None:
    """NODE-009: Second platform target passes entire Phase 7 contract suite (NODE-001 through NODE-008)."""
    # 1. NODE-001: Cargo workspace is compilable and Rust binary inspectable
    repo_root = Path(__file__).resolve().parents[3]
    manifest_path = repo_root / "node_runtime" / "Cargo.toml"
    assert manifest_path.exists()

    res = subprocess.run(
        ["cargo", "check", "--manifest-path", str(manifest_path)],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, f"cargo check failed: {res.stderr}"

    bridge = RustNodeBridge()
    assert bridge.binary_path.exists()
    assert "ryu-node v0.1.0" in bridge.version()

    # 2. NODE-002: Device grant enforcement & HMAC-SHA256 verification
    secret = "high-entropy-paired-secret-linux-node"
    grant = DeviceGrant(
        grant_id="grant-linux-002",
        space_id="space-linux-test",
        worker_id="worker-linux-01",
        node_id="node-linux-01",
        device_id="cpu-0",
        capability="compute.cpu",
        lease_token="lease-linux-12345",
        nonce="nonce-linux-abc",
        issued_at="2026-09-23T12:00:00Z",
        expiry="2026-09-23T13:00:00Z",
        risk_tier=RiskTier.LOW,
    )
    grant.sign(secret)

    valid, err = bridge.validate_grant(
        node_id="node-linux-01",
        secret=secret,
        grant=grant,
        current_time="2026-09-23T12:30:00Z",
    )
    assert valid is True
    assert err is None

    # Forged secret rejected
    valid_bad, err_bad = bridge.validate_grant(
        node_id="node-linux-01",
        secret="forged-secret-key-wrong",
        grant=grant,
        current_time="2026-09-23T12:30:00Z",
    )
    assert valid_bad is False
    assert "HMAC" in str(err_bad)

    # 3. NODE-003: Space and session binding
    bus = PulseBus()
    rm = ResourceManager(bus=bus, store=InMemoryResourceStore())
    registry = NodeRegistry()

    linux_profile = LinuxHostProfile()
    linux_node = linux_profile.inspect_system("node-linux-01")
    registry.register_node(linux_node, secret)

    linux_devices = linux_profile.discover_devices("node-linux-01")
    for d in linux_devices:
        registry.register_device(d)
    registry.sync_resources_to_manager(rm, space_id="space-linux-test")

    gm = DeviceGrantManager(registry=registry, resource_manager=rm, bus=bus)

    res_ident = ResourceIdentity("cpu", "node-linux-01", "node-linux-01-cpu-0")
    acq = rm.acquire(space_id="space-linux-test", requester_id="worker-linux-01", identity=res_ident, units=1)
    assert acq.lease is not None

    linux_grant = gm.create_grant(
        space_id="space-linux-test",
        worker_id="worker-linux-01",
        node_id="node-linux-01",
        device_id="node-linux-01-cpu-0",
        capability="compute.cpu",
        lease_token=acq.lease.lease_token,
    )
    assert linux_grant.space_id == "space-linux-test"
    assert linux_grant.lease_token == acq.lease.lease_token

    # Cross-space creation rejected
    with pytest.raises(Exception):
        gm.create_grant(
            space_id="space-other",
            worker_id="worker-linux-01",
            node_id="node-linux-01",
            device_id="node-linux-01-cpu-0",
            capability="compute.cpu",
            lease_token=acq.lease.lease_token,
        )

    # 4. NODE-004: Revocation during execution
    audit_log = DeviceAuditLog(log_path=tmp_path / "linux_audit.log.jsonl")
    runtime = NodeRuntime(
        node_id="node-linux-01",
        shared_secret=secret,
        registry=registry,
        audit_log=audit_log,
        bus=bus,
    )

    binding = runtime.bind_device("worker-linux-01", "space-linux-test", linux_grant, "node-linux-01-cpu-0")
    assert binding.is_active is True

    # Mid-call revocation enforced
    runtime.revoke_in_flight(linux_grant.grant_id)
    assert binding.is_active is False
    with pytest.raises(GrantRevokedError):
        runtime.bind_device("worker-linux-01", "space-linux-test", linux_grant, "node-linux-01-cpu-0")

    # 5. NODE-005: Heartbeat lease monitoring
    coord = NodeCoordinator(
        registry=registry,
        grant_manager=gm,
        resource_manager=rm,
        bus=bus,
        heartbeat_ttl_seconds=5.0,
    )
    now = datetime.now(timezone.utc)
    coord.record_heartbeat("node-linux-01", timestamp=now)
    coord.sweep_timeouts(current_time=now + timedelta(seconds=2))
    assert registry.get_node("node-linux-01").runtime_state == NodeState.READY

    offline = coord.sweep_timeouts(current_time=now + timedelta(seconds=10))
    assert "node-linux-01" in offline
    assert registry.get_node("node-linux-01").runtime_state == NodeState.OFFLINE

    # 6. NODE-006: Offline task checkpointing
    t0 = datetime.now(timezone.utc) + timedelta(seconds=20)
    coord.reconnect_node("node-linux-01", acq.lease.lease_token, current_time=t0)
    coord.record_heartbeat("node-linux-01", timestamp=t0)
    coord.register_in_flight_task(
        "task-lin-chk", "space-linux-test", "worker-linux-01", "node-linux-01",
        "node-linux-01-cpu-0", linux_grant.grant_id, is_idempotent=True
    )
    coord.sweep_timeouts(current_time=t0 + timedelta(seconds=10))
    cp = coord.get_task_checkpoint("task-lin-chk")
    assert cp is not None and cp.state == "checkpointed"

    # 7. NODE-007: Validated resume upon reconnection
    t1 = t0 + timedelta(seconds=12)
    coord.reconnect_node("node-linux-01", acq.lease.lease_token, current_time=t1)
    resumed = coord.validate_and_resume_task("task-lin-chk", current_time=t1)
    assert resumed.state == "resumed"

    # 8. NODE-008: Independent device audit verification
    audit_log.append(
        "node-linux-01", "space-linux-test", "TASK_RESUMED", linux_grant.grant_id,
        "node-linux-01-cpu-0", "op-lin-1", "SUCCESS"
    )
    py_valid, py_count, py_err = audit_log.verify_chain()
    assert py_valid is True
    assert py_count >= 2
    assert py_err is None

    rust_valid, rust_count, rust_err = bridge.audit_verify(tmp_path / "linux_audit.log.jsonl")
    assert rust_valid is True
    assert rust_count == py_count
    assert rust_err is None
