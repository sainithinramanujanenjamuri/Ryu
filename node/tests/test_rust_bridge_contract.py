"""Unit tests for RustNodeBridge.

Covers typed subcommands, structural command exclusion, and native execution.
CONTRACT_MATRIX NODE-001, NODE-002, NODE-008
ADR-0017, ADR-0019, ADR-0020
"""

from __future__ import annotations

import pytest

from node.audit import DeviceAuditLog
from node.bridge import RustNodeBridge
from node.contract import DeviceGrant, RiskTier, RustBridgeError


@pytest.fixture
def bridge():
    return RustNodeBridge()


def test_unauthorized_command_structurally_blocked(bridge) -> None:
    # Attempting to execute unauthorized or arbitrary shell/native command
    with pytest.raises(RustBridgeError, match="Unauthorized bridge subcommand"):
        bridge._invoke("exec", ["whoami"])

    with pytest.raises(RustBridgeError, match="Unauthorized bridge subcommand"):
        bridge._invoke("shell", ["rm -rf /"])


def test_bridge_version(bridge) -> None:
    version = bridge.version()
    assert "ryu-node v" in version


def test_bridge_inspect_and_devices(bridge) -> None:
    info = bridge.inspect(node_id="node-test-bridge")
    assert info.node_id == "node-test-bridge"
    assert info.platform in ("windows", "linux")
    assert info.cpu_cores >= 1

    devices = bridge.inspect_devices(node_id="node-test-bridge")
    assert len(devices) >= 1
    # Must have at least CPU
    cpu_devs = [d for d in devices if d.device_type.value == "cpu"]
    assert len(cpu_devs) >= 1


def test_bridge_validate_grant_native(bridge) -> None:
    secret = "high-entropy-paired-secret-key-123456"
    grant = DeviceGrant(
        grant_id="grant-bridge-01",
        space_id="space-alpha",
        worker_id="worker-01",
        node_id="node-bridge",
        device_id="gpu-0",
        capability="gpu.cuda",
        lease_token="lease-token-abc",
        nonce="nonce-9999",
        issued_at="2026-09-20T12:00:00Z",
        expiry="2026-09-20T13:00:00Z",
        risk_tier=RiskTier.LOW,
        grant_schema_version="1.0",
    )
    grant.sign(secret)

    # Valid grant check
    valid, err = bridge.validate_grant(
        node_id="node-bridge",
        secret=secret,
        grant=grant,
        current_time="2026-09-20T12:30:00Z",
    )
    assert valid is True
    assert err is None

    # Invalid secret check
    valid_bad, err_bad = bridge.validate_grant(
        node_id="node-bridge",
        secret="wrong-secret",
        grant=grant,
        current_time="2026-09-20T12:30:00Z",
    )
    assert valid_bad is False
    assert "HMAC" in str(err_bad)


def test_bridge_audit_verify_native(bridge, tmp_path) -> None:
    log_file = tmp_path / "bridge_audit.log.jsonl"
    audit = DeviceAuditLog(log_path=log_file)
    audit.append(
        "node-01", "space-alpha", "DEVICE_BOUND", "grant-01", "gpu-0", "bind-01", "SUCCESS"
    )
    audit.append(
        "node-01", "space-alpha", "DEVICE_RELEASED", "grant-01", "gpu-0", "bind-01", "SUCCESS"
    )

    # Call native Rust audit-verify
    valid, count, err = bridge.audit_verify(log_file)
    assert valid is True
    assert count == 2
    assert err is None
