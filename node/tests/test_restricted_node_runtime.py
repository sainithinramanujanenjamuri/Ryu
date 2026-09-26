"""Unit and security tests for Restricted Node Runtime and MDM allow-lists.

CONTRACT_MATRIX NODE-012, ADR-0039 — Phase 11
INVARIANT:
MDM_ALLOW != Authentication.
MDM is an additional local policy constraint.
MDM_ALLOW ∧ valid_DeviceGrant ∧ valid_Space ∧ valid_Node ∧ valid_Lease -> binding permitted.
MDM_ALLOW alone -> NOT sufficient.
valid_DeviceGrant alone -> NOT sufficient on Restricted node if MDM denies.
MDM_DENY -> binding denied.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from ryu.pulse_bus.bus import PulseBus

from core.resources.identity import ResourceIdentity
from core.resources.manager import ResourceManager
from core.resources.store import InMemoryResourceStore
from node.audit import DeviceAuditLog
from node.contract import (
    DeviceGrant,
    DeviceInfo,
    DeviceState,
    DeviceType,
    GrantExpiredError,
    GrantInvalidError,
    NodeInfo,
    NodeState,
    NodeTrustTier,
    RestrictedNodePolicy,
    RiskTier,
)
from node.grants import DeviceGrantManager
from node.registry import NodeRegistry
from node.runtime import NodeRuntime


@pytest.fixture
def test_setup(tmp_path: Path):
    bus = PulseBus()
    rm = ResourceManager(bus=bus, store=InMemoryResourceStore())
    registry = NodeRegistry()
    secret = "high-entropy-node-secret-32bytes"

    policy = RestrictedNodePolicy(
        policy_id="corp-mdm-allowlist",
        allowed_capabilities={"compute.cpu", "storage.workspace"},
        denied_capabilities={"gpu.cuda", "terminal.admin"},
    )

    node = NodeInfo(
        node_id="node-restricted-01",
        platform="windows",
        architecture="x86_64",
        environment_profile="windows_host",
        runtime_state=NodeState.READY,
        trust_tier=NodeTrustTier.RESTRICTED,
        policy=policy,
    )
    registry.register_node(node, secret)

    cpu_device = DeviceInfo(
        device_id="cpu-0",
        node_id="node-restricted-01",
        device_type=DeviceType.CPU,
        availability_state=DeviceState.ONLINE,
    )
    gpu_device = DeviceInfo(
        device_id="gpu-0",
        node_id="node-restricted-01",
        device_type=DeviceType.GPU,
        availability_state=DeviceState.ONLINE,
    )
    registry.register_device(cpu_device)
    registry.register_device(gpu_device)
    registry.sync_resources_to_manager(rm, space_id="space-main")

    gm = DeviceGrantManager(registry=registry, resource_manager=rm, bus=bus)
    audit_file = tmp_path / "restricted_audit.log.jsonl"
    audit_log = DeviceAuditLog(log_path=audit_file)

    runtime = NodeRuntime(
        node_id="node-restricted-01",
        shared_secret=secret,
        registry=registry,
        audit_log=audit_log,
        bus=bus,
        trust_tier=NodeTrustTier.RESTRICTED,
        policy=policy,
    )

    return {
        "bus": bus,
        "rm": rm,
        "registry": registry,
        "secret": secret,
        "policy": policy,
        "gm": gm,
        "audit_log": audit_log,
        "runtime": runtime,
    }


def test_valid_grant_with_mdm_allow_permitted(test_setup) -> None:
    """Proves: MDM_ALLOW ∧ valid_DeviceGrant ∧ valid_Space ∧ valid_Node ∧ valid_Lease -> permitted."""
    rm = test_setup["rm"]
    gm = test_setup["gm"]
    runtime = test_setup["runtime"]

    # Acquire lease for CPU
    ident = ResourceIdentity("cpu", "node-restricted-01", "cpu-0")
    acq = rm.acquire(space_id="space-main", requester_id="worker-01", identity=ident, units=1)
    assert acq.lease is not None

    grant = gm.create_grant(
        space_id="space-main",
        worker_id="worker-01",
        node_id="node-restricted-01",
        device_id="cpu-0",
        capability="compute.cpu",  # IN ALLOW-LIST
        lease_token=acq.lease.lease_token,
    )

    binding = runtime.bind_device("worker-01", "space-main", grant, "cpu-0")
    assert binding.is_active is True
    assert binding.device_id == "cpu-0"

    # Audit verification
    valid, count, err = test_setup["audit_log"].verify_chain()
    assert valid is True
    assert count >= 1
    assert err is None


def test_valid_grant_with_mdm_deny_rejected(test_setup) -> None:
    """Proves: valid_DeviceGrant alone -> NOT sufficient on Restricted node if MDM denies."""
    rm = test_setup["rm"]
    gm = test_setup["gm"]
    runtime = test_setup["runtime"]

    # Space Kernel and ResourceManager allow GPU lease
    ident = ResourceIdentity("gpu", "node-restricted-01", "gpu-0")
    acq = rm.acquire(space_id="space-main", requester_id="worker-01", identity=ident, units=1)
    assert acq.lease is not None

    # Space Kernel creates valid cryptographically signed grant
    grant = gm.create_grant(
        space_id="space-main",
        worker_id="worker-01",
        node_id="node-restricted-01",
        device_id="gpu-0",
        capability="gpu.cuda",  # IN MDM DENIED-LIST
        lease_token=acq.lease.lease_token,
    )

    # Restricted Node Runtime enforces device-local MDM policy and rejects binding
    with pytest.raises(GrantInvalidError) as exc_info:
        runtime.bind_device("worker-01", "space-main", grant, "gpu-0")

    assert "MDM policy violation" in str(exc_info.value)
    assert "explicitly denied" in str(exc_info.value)

    # Audit log independently records BIND_DENIED
    valid, count, err = test_setup["audit_log"].verify_chain()
    assert valid is True
    last_rec = test_setup["audit_log"].records[-1]
    assert last_rec.event_type == "BIND_DENIED"
    assert "MDM policy restriction" in last_rec.result


def test_mdm_allow_with_forged_grant_rejected(test_setup) -> None:
    """Proves: MDM_ALLOW alone is NOT sufficient; forged DeviceGrant fails HMAC."""
    runtime = test_setup["runtime"]

    forged_grant = DeviceGrant(
        grant_id="grant-forged-001",
        space_id="space-main",
        worker_id="worker-01",
        node_id="node-restricted-01",
        device_id="cpu-0",
        capability="compute.cpu",  # IN MDM ALLOW-LIST!
        lease_token="fake-lease-token",
        nonce="nonce-123",
        issued_at="2026-09-23T12:00:00Z",
        expiry="2026-09-23T13:00:00Z",
        risk_tier=RiskTier.LOW,
    )
    forged_grant.sign("attacker-wrong-secret-key-12345678")

    with pytest.raises(GrantInvalidError) as exc_info:
        runtime.bind_device(
            "worker-01",
            "space-main",
            forged_grant,
            "cpu-0",
            current_time=datetime(2026, 9, 23, 12, 30, tzinfo=timezone.utc),
        )

    assert "Cryptographic signature verification failed" in str(exc_info.value)

    last_rec = test_setup["audit_log"].records[-1]
    assert last_rec.event_type == "BIND_DENIED"
    assert "Invalid HMAC signature" in last_rec.result


def test_mdm_allow_with_wrong_space_rejected(test_setup) -> None:
    """Proves: MDM_ALLOW + cross-space grant rejected."""
    rm = test_setup["rm"]
    gm = test_setup["gm"]
    runtime = test_setup["runtime"]

    ident = ResourceIdentity("cpu", "node-restricted-01", "cpu-0")
    acq = rm.acquire(space_id="space-main", requester_id="worker-01", identity=ident, units=1)
    assert acq.lease is not None

    grant = gm.create_grant(
        space_id="space-main",
        worker_id="worker-01",
        node_id="node-restricted-01",
        device_id="cpu-0",
        capability="compute.cpu",  # IN ALLOW-LIST
        lease_token=acq.lease.lease_token,
    )

    # Calling from space-other with space-main grant
    with pytest.raises(GrantInvalidError) as exc_info:
        runtime.bind_device("worker-01", "space-other", grant, "cpu-0")

    assert "Cross-space grant rejected" in str(exc_info.value)


def test_mdm_allow_with_wrong_node_rejected(test_setup) -> None:
    """Proves: MDM_ALLOW + cross-node grant rejected."""
    rm = test_setup["rm"]
    runtime = test_setup["runtime"]

    ident = ResourceIdentity("cpu", "node-restricted-01", "cpu-0")
    acq = rm.acquire(space_id="space-main", requester_id="worker-01", identity=ident, units=1)
    assert acq.lease is not None

    grant = DeviceGrant(
        grant_id="grant-wrong-node",
        space_id="space-main",
        worker_id="worker-01",
        node_id="node-other",  # WRONG NODE
        device_id="cpu-0",
        capability="compute.cpu",
        lease_token=acq.lease.lease_token,
        nonce="nonce-abc",
        issued_at="2026-09-23T12:00:00Z",
        expiry="2026-09-23T13:00:00Z",
        risk_tier=RiskTier.LOW,
    )
    grant.sign(test_setup["secret"])

    with pytest.raises(GrantInvalidError) as exc_info:
        runtime.bind_device("worker-01", "space-main", grant, "cpu-0")

    assert "Cross-node grant rejected" in str(exc_info.value)


def test_mdm_allow_with_expired_grant_rejected(test_setup) -> None:
    """Proves: MDM_ALLOW + expired grant rejected."""
    rm = test_setup["rm"]
    gm = test_setup["gm"]
    runtime = test_setup["runtime"]

    ident = ResourceIdentity("cpu", "node-restricted-01", "cpu-0")
    acq = rm.acquire(space_id="space-main", requester_id="worker-01", identity=ident, units=1)
    assert acq.lease is not None

    grant = gm.create_grant(
        space_id="space-main",
        worker_id="worker-01",
        node_id="node-restricted-01",
        device_id="cpu-0",
        capability="compute.cpu",
        lease_token=acq.lease.lease_token,
    )
    # Set expired timestamp
    grant.expiry = "2026-09-20T00:00:00Z"
    grant.sign(test_setup["secret"])

    with pytest.raises(GrantExpiredError) as exc_info:
        runtime.bind_device("worker-01", "space-main", grant, "cpu-0")

    assert "has expired" in str(exc_info.value)
