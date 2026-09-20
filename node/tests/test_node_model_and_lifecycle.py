"""Unit tests for Node data models, enums, serialization, and HMAC signing.

CONTRACT_MATRIX NODE-001, NODE-002, NODE-003
ADR-0017, ADR-0018
"""

from __future__ import annotations

from datetime import datetime

from node.contract import (
    DeviceGrant,
    DeviceInfo,
    DeviceState,
    DeviceType,
    NodeInfo,
    NodeState,
    RiskTier,
)


def test_node_info_serialization() -> None:
    node = NodeInfo(
        node_id="node-test-01",
        platform="windows",
        architecture="x86_64",
        environment_profile="windows_host",
        runtime_state=NodeState.READY,
        cpu_cores=16,
        memory_total_bytes=34359738368,
        storage_total_bytes=1000000000000,
        capabilities=["cpu.compute", "gpu.cuda"],
        labels={"tier": "high-performance", "zone": "local"},
    )

    data = node.to_dict()
    assert data["node_id"] == "node-test-01"
    assert data["runtime_state"] == "ready"
    assert data["cpu_cores"] == 16
    assert "gpu.cuda" in data["capabilities"]

    restored = NodeInfo.from_dict(data)
    assert restored.node_id == node.node_id
    assert restored.platform == node.platform
    assert restored.runtime_state == NodeState.READY
    assert restored.labels["tier"] == "high-performance"


def test_device_info_serialization() -> None:
    device = DeviceInfo(
        device_id="gpu-0",
        node_id="node-test-01",
        device_type=DeviceType.GPU,
        capability_metadata={"cuda_compute_capability": "8.6", "vram_bytes": "12884901888"},
        availability_state=DeviceState.ONLINE,
        total_capacity=1,
    )

    data = device.to_dict()
    assert data["device_id"] == "gpu-0"
    assert data["device_type"] == "gpu"
    assert data["availability_state"] == "online"

    restored = DeviceInfo.from_dict(data)
    assert restored.device_id == device.device_id
    assert restored.device_type == DeviceType.GPU
    assert restored.capability_metadata["cuda_compute_capability"] == "8.6"


def test_device_grant_canonical_payload_and_hmac() -> None:
    secret = "high-entropy-paired-secret-key-123456"
    grant = DeviceGrant(
        grant_id="grant-101",
        space_id="space-alpha",
        worker_id="worker-01",
        node_id="node-01",
        device_id="gpu-0",
        capability="gpu.cuda",
        lease_token="lease-token-abc",
        nonce="nonce-9999",
        issued_at="2026-09-20T12:00:00Z",
        expiry="2026-09-20T13:00:00Z",
        risk_tier=RiskTier.LOW,
        grant_schema_version="1.0",
        revocation_token="rev-token-777",
    )

    expected_payload = (
        "1.0|grant-101|space-alpha|worker-01|node-01|gpu-0|gpu.cuda|"
        "lease-token-abc|nonce-9999|2026-09-20T12:00:00Z|2026-09-20T13:00:00Z|low"
    )
    assert grant.canonical_payload() == expected_payload

    # Sign grant
    grant.sign(secret)
    assert grant.signature != ""

    # Verify signature
    assert grant.verify(secret) is True
    assert grant.verify("wrong-secret-key-1234567890") is False

    # Tampering any field must invalidate HMAC
    grant.space_id = "space-beta"
    assert grant.verify(secret) is False


def test_device_grant_expiry_calculation() -> None:
    grant = DeviceGrant(
        grant_id="grant-102",
        space_id="space-alpha",
        worker_id="worker-01",
        node_id="node-01",
        device_id="gpu-0",
        capability="gpu.cuda",
        lease_token="lease-token-abc",
        nonce="nonce-9999",
        issued_at="2026-09-20T12:00:00+00:00",
        expiry="2026-09-20T13:00:00+00:00",
        risk_tier=RiskTier.LOW,
    )

    before_exp = datetime.fromisoformat("2026-09-20T12:30:00+00:00")
    at_exp = datetime.fromisoformat("2026-09-20T13:00:00+00:00")
    after_exp = datetime.fromisoformat("2026-09-20T13:05:00+00:00")

    assert grant.is_expired(before_exp) is False
    assert grant.is_expired(at_exp) is True
    assert grant.is_expired(after_exp) is True
