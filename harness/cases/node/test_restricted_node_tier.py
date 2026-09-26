"""Executable harness verification for Node Runtime contract NODE-012.

Space-Centric Cognitive Architecture (SCCA) — Phase 11
spec §11 (Restricted Node Tier - MDM Policy Constraint), CONTRACT_MATRIX NODE-012
ADR-0039

INVARIANTS:
MDM_ALLOW != Authentication.
MDM policy enforcement is an additional local policy constraint.
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
from node.bridge import RustNodeBridge
from node.contract import (
    DeviceGrant,
    DeviceInfo,
    DeviceType,
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
def restricted_setup(tmp_path: Path):
    bus = PulseBus()
    rm = ResourceManager(bus=bus, store=InMemoryResourceStore())
    registry = NodeRegistry()
    secret = "high-entropy-paired-secret-restricted"

    policy = RestrictedNodePolicy(
        policy_id="corp-zero-trust-mdm",
        allowed_capabilities={"compute.cpu", "storage.workspace"},
        denied_capabilities={"gpu.cuda", "terminal.admin"},
        allowed_storage_paths=["/tmp/safe_scratch", "C:/Temp/safe_scratch"],
    )

    node = NodeInfo(
        node_id="node-mdm-01",
        platform="windows",
        architecture="x86_64",
        environment_profile="windows_host",
        runtime_state=NodeState.READY,
        trust_tier=NodeTrustTier.RESTRICTED,
        policy=policy,
    )
    registry.register_node(node, secret)

    cpu_dev = DeviceInfo(device_id="cpu-0", node_id="node-mdm-01", device_type=DeviceType.CPU)
    gpu_dev = DeviceInfo(device_id="gpu-0", node_id="node-mdm-01", device_type=DeviceType.GPU)
    registry.register_device(cpu_dev)
    registry.register_device(gpu_dev)
    registry.sync_resources_to_manager(rm, space_id="space-sec")

    gm = DeviceGrantManager(registry=registry, resource_manager=rm, bus=bus)
    audit_file = tmp_path / "restricted_audit.jsonl"
    audit_log = DeviceAuditLog(log_path=audit_file)

    runtime = NodeRuntime(
        node_id="node-mdm-01",
        shared_secret=secret,
        registry=registry,
        audit_log=audit_log,
        bus=bus,
        trust_tier=NodeTrustTier.RESTRICTED,
        policy=policy,
    )

    bridge = RustNodeBridge()

    return {
        "bus": bus,
        "rm": rm,
        "registry": registry,
        "gm": gm,
        "secret": secret,
        "policy": policy,
        "audit_file": audit_file,
        "audit_log": audit_log,
        "runtime": runtime,
        "bridge": bridge,
    }


def test_node_mdm_policy_conjunctive_authorization(restricted_setup) -> None:
    """NODE-012: MDM_ALLOW ∧ valid_DeviceGrant ∧ valid_Space ∧ valid_Node ∧ valid_Lease -> permitted."""
    rm = restricted_setup["rm"]
    gm = restricted_setup["gm"]
    runtime = restricted_setup["runtime"]

    # 1. Acquire lease
    res_ident = ResourceIdentity("cpu", "node-mdm-01", "cpu-0")
    acq = rm.acquire(space_id="space-sec", requester_id="worker-01", identity=res_ident, units=1)
    assert acq.lease is not None

    # 2. Issue valid grant with allowed capability
    grant = gm.create_grant(
        space_id="space-sec",
        worker_id="worker-01",
        node_id="node-mdm-01",
        device_id="cpu-0",
        capability="compute.cpu",  # IN MDM ALLOW-LIST
        lease_token=acq.lease.lease_token,
    )

    # 3. Binding succeeds
    binding = runtime.bind_device("worker-01", "space-sec", grant, "cpu-0")
    assert binding.is_active is True
    assert binding.device_id == "cpu-0"


def test_node_mdm_policy_denies_unauthorized_even_if_space_approves(restricted_setup) -> None:
    """NODE-012: Valid DeviceGrant alone is NOT sufficient if MDM denies."""
    rm = restricted_setup["rm"]
    gm = restricted_setup["gm"]
    runtime = restricted_setup["runtime"]

    # Space Kernel authorizes GPU lease
    res_ident = ResourceIdentity("gpu", "node-mdm-01", "gpu-0")
    acq = rm.acquire(space_id="space-sec", requester_id="worker-01", identity=res_ident, units=1)
    assert acq.lease is not None

    # Space Kernel creates valid cryptographically signed grant
    grant = gm.create_grant(
        space_id="space-sec",
        worker_id="worker-01",
        node_id="node-mdm-01",
        device_id="gpu-0",
        capability="gpu.cuda",  # IN MDM DENIED-LIST
        lease_token=acq.lease.lease_token,
    )

    # Device runtime rejects binding with MDM violation
    with pytest.raises(GrantInvalidError) as exc_info:
        runtime.bind_device("worker-01", "space-sec", grant, "gpu-0")

    assert "MDM policy violation" in str(exc_info.value)

    # Audit verification proves independent record
    audit_log = restricted_setup["audit_log"]
    valid, count, err = audit_log.verify_chain()
    assert valid is True
    last_rec = audit_log.records[-1]
    assert last_rec.event_type == "BIND_DENIED"
    assert "MDM policy restriction" in last_rec.result


def test_node_mdm_allow_cannot_authenticate_forged_grant(restricted_setup) -> None:
    """NODE-012: MDM_ALLOW alone is NOT sufficient; forged grant fails HMAC."""
    runtime = restricted_setup["runtime"]

    forged_grant = DeviceGrant(
        grant_id="grant-forged",
        space_id="space-sec",
        worker_id="worker-01",
        node_id="node-mdm-01",
        device_id="cpu-0",
        capability="compute.cpu",  # IN MDM ALLOW-LIST!
        lease_token="bogus-lease",
        nonce="nonce-999",
        issued_at="2026-09-23T12:00:00Z",
        expiry="2026-09-23T13:00:00Z",
        risk_tier=RiskTier.LOW,
    )
    forged_grant.sign("attacker-invalid-secret-key")

    with pytest.raises(GrantInvalidError) as exc_info:
        runtime.bind_device(
            "worker-01",
            "space-sec",
            forged_grant,
            "cpu-0",
            current_time=datetime(2026, 9, 23, 12, 30, tzinfo=timezone.utc),
        )

    assert "Cryptographic signature verification failed" in str(exc_info.value)


def test_native_rust_bridge_enforces_mdm_policy(restricted_setup) -> None:
    """NODE-012: Native Rust binary CLI directly enforces MDM policy via --trust-tier and --policy."""
    bridge = restricted_setup["bridge"]
    secret = restricted_setup["secret"]
    audit_file = restricted_setup["audit_file"]
    policy = restricted_setup["policy"]

    # 1. Valid grant for CPU (Allowed)
    grant_ok = DeviceGrant(
        grant_id="grant-rust-ok",
        space_id="space-sec",
        worker_id="worker-01",
        node_id="node-mdm-01",
        device_id="cpu-0",
        capability="compute.cpu",
        lease_token="lease-tok-rust-01",
        nonce="nonce-rust-1",
        issued_at="2026-09-23T12:00:00Z",
        expiry="2026-09-23T13:00:00Z",
        risk_tier=RiskTier.LOW,
    )
    grant_ok.sign(secret)

    req_ok = {
        "grant": grant_ok.to_dict(),
        "binding_id": "bind-rust-01",
        "device_id": "cpu-0",
        "worker_id": "worker-01",
        "timestamp": "2026-09-23T12:30:00Z",
    }

    res_ok = bridge.bind(
        node_id="node-mdm-01",
        secret=secret,
        audit_log_path=audit_file,
        req=req_ok,
        current_time="2026-09-23T12:30:00Z",
        trust_tier="restricted",
        policy=policy.to_dict(),
    )
    assert res_ok.get("bound") is True

    # 2. Valid grant for GPU (Denied by MDM policy in Rust)
    grant_denied = DeviceGrant(
        grant_id="grant-rust-denied",
        space_id="space-sec",
        worker_id="worker-01",
        node_id="node-mdm-01",
        device_id="gpu-0",
        capability="gpu.cuda",  # In denied list
        lease_token="lease-tok-rust-02",
        nonce="nonce-rust-2",
        issued_at="2026-09-23T12:00:00Z",
        expiry="2026-09-23T13:00:00Z",
        risk_tier=RiskTier.HIGH,
    )
    grant_denied.sign(secret)

    req_denied = {
        "grant": grant_denied.to_dict(),
        "binding_id": "bind-rust-02",
        "device_id": "gpu-0",
        "worker_id": "worker-01",
        "timestamp": "2026-09-23T12:30:00Z",
    }

    res_denied = bridge.bind(
        node_id="node-mdm-01",
        secret=secret,
        audit_log_path=audit_file,
        req=req_denied,
        current_time="2026-09-23T12:30:00Z",
        trust_tier="restricted",
        policy=policy.to_dict(),
    )
    assert res_denied.get("bound") is False
    assert "MDM policy violation" in str(res_denied.get("error"))

    # Verify native audit chain integrity
    v, count, err = bridge.audit_verify(audit_file)
    assert v is True
    assert count >= 2
    assert err is None

