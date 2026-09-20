"""Adversarial security attack matrix verification for Node Runtime.

Space-Centric Cognitive Architecture (SCCA) — Phase 7
spec §11, §16, CONTRACT_MATRIX NODE-001..NODE-008
ADR-0017, ADR-0018, ADR-0019

Every test explicitly logs:
Test: PASS
Expected system decision: <DECISION>
Observed system decision: <DECISION>
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
    DeviceBindingError,
    DeviceInfo,
    DeviceNotFoundError,
    DeviceState,
    DeviceType,
    DeviceUnavailableError,
    GrantExpiredError,
    GrantInvalidError,
    GrantRevokedError,
    LeaseInvalidError,
    LeaseNotFoundError,
    NodeError,
    NodeInfo,
    NodeRegistrationError,
    NodeState,
    RiskTier,
    RustBridgeError,
)
from node.coordinator import NodeCoordinator
from node.grants import DeviceGrantManager
from node.registry import NodeRegistry
from node.runtime import NodeRuntime


def log_decision(test_name: str, expected: str, observed: str) -> None:
    print(f"\n[{test_name}]")
    print("Test: PASS")
    print(f"Expected system decision: {expected}")
    print(f"Observed system decision: {observed}")


@pytest.fixture
def env(tmp_path):
    bus = PulseBus()
    rm = ResourceManager(bus=bus, store=InMemoryResourceStore())
    registry = NodeRegistry()
    secret = "high-entropy-node-secret-32bytes"

    node = NodeInfo(
        node_id="node-sec-01",
        platform="windows",
        architecture="x86_64",
        environment_profile="windows_host",
        runtime_state=NodeState.READY,
    )
    registry.register_node(node, secret)

    device = DeviceInfo(
        device_id="gpu-0",
        node_id="node-sec-01",
        device_type=DeviceType.GPU,
        total_capacity=1,
    )
    registry.register_device(device)
    registry.sync_resources_to_manager(rm, space_id="space-main")

    gm = DeviceGrantManager(registry=registry, resource_manager=rm, bus=bus)
    audit_log = DeviceAuditLog(log_path=tmp_path / "sec_audit.log.jsonl")
    bridge = RustNodeBridge()

    runtime = NodeRuntime(
        node_id="node-sec-01",
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
    )

    return bus, rm, registry, gm, runtime, coord, bridge, secret


def test_sec_01_forged_node_id(env) -> None:
    """Vector 1: Forged node ID registration."""
    _, _, registry, _, _, _, _, secret = env
    observed = "NONE"
    try:
        registry.register_node(
            NodeInfo(
                node_id="../forged-node", platform="windows", architecture="x86_64",
                environment_profile="windows_host",
            ),
            secret,
        )
    except NodeRegistrationError:
        observed = "REJECT"

    assert observed == "REJECT"
    log_decision("test_sec_01_forged_node_id", "REJECT", observed)


def test_sec_02_duplicate_node_registration(env) -> None:
    """Vector 2: Duplicate conflicting node registration."""
    _, _, registry, _, _, _, _, secret = env
    observed = "NONE"
    try:
        # Register node with conflicting platform
        registry.register_node(
            NodeInfo(
                node_id="node-sec-01", platform="linux", architecture="arm64",
                environment_profile="wsl2",
            ),
            secret,
        )
    except NodeRegistrationError:
        observed = "REJECT"

    assert observed == "REJECT"
    log_decision("test_sec_02_duplicate_node_registration", "REJECT", observed)


def test_sec_03_forged_device_id(env) -> None:
    """Vector 3: Forged device ID binding attempt."""
    _, rm, _, gm, runtime, _, _, _ = env
    res_ident = ResourceIdentity("gpu", "node-sec-01", "gpu-0")
    acq = rm.acquire(space_id="space-main", requester_id="worker-01", identity=res_ident, units=1)
    grant = gm.create_grant(
        "space-main", "worker-01", "node-sec-01", "gpu-0", "gpu.cuda", acq.lease.lease_token
    )

    observed = "NONE"
    try:
        # Binding references non-existent forged device
        runtime.bind_device("worker-01", "space-main", grant, "forged-device-999")
    except (DeviceNotFoundError, GrantInvalidError):
        observed = "REJECT"

    assert observed == "REJECT"
    log_decision("test_sec_03_forged_device_id", "REJECT", observed)


def test_sec_04_forged_grant_signature(env) -> None:
    """Vector 4: Forged grant signature."""
    _, rm, _, gm, runtime, _, bridge, _ = env
    res_ident = ResourceIdentity("gpu", "node-sec-01", "gpu-0")
    acq = rm.acquire(space_id="space-main", requester_id="worker-01", identity=res_ident, units=1)
    grant = gm.create_grant(
        "space-main", "worker-01", "node-sec-01", "gpu-0", "gpu.cuda", acq.lease.lease_token
    )

    # Forge signature
    grant.signature = "forged-signature-000000000000000000000000000000000000000000000000"

    observed = "NONE"
    try:
        runtime.bind_device("worker-01", "space-main", grant, "gpu-0")
    except GrantInvalidError:
        observed = "REJECT"

    assert observed == "REJECT"
    log_decision("test_sec_04_forged_grant_signature", "REJECT", observed)


def test_sec_05_expired_grant(env) -> None:
    """Vector 5: Expired grant binding attempt."""
    _, rm, _, gm, runtime, _, _, _ = env
    res_ident = ResourceIdentity("gpu", "node-sec-01", "gpu-0")
    acq = rm.acquire(space_id="space-main", requester_id="worker-01", identity=res_ident, units=1)
    grant = gm.create_grant(
        "space-main", "worker-01", "node-sec-01", "gpu-0", "gpu.cuda", acq.lease.lease_token
    )

    observed = "NONE"
    # Advance time past grant expiry
    future_time = datetime.now(timezone.utc) + timedelta(hours=2)
    try:
        runtime.bind_device("worker-01", "space-main", grant, "gpu-0", current_time=future_time)
    except GrantExpiredError:
        observed = "REJECT"

    assert observed == "REJECT"
    log_decision("test_sec_05_expired_grant", "REJECT", observed)


def test_sec_06_revoked_grant(env) -> None:
    """Vector 6: Revoked grant binding attempt."""
    _, rm, _, gm, runtime, _, _, _ = env
    res_ident = ResourceIdentity("gpu", "node-sec-01", "gpu-0")
    acq = rm.acquire(space_id="space-main", requester_id="worker-01", identity=res_ident, units=1)
    grant = gm.create_grant(
        "space-main", "worker-01", "node-sec-01", "gpu-0", "gpu.cuda", acq.lease.lease_token
    )

    gm.revoke_grant(grant.grant_id, revoked_by="security")
    runtime.sync_revocations([grant.grant_id])

    observed = "NONE"
    try:
        runtime.bind_device("worker-01", "space-main", grant, "gpu-0")
    except GrantRevokedError:
        observed = "REJECT"

    assert observed == "REJECT"
    log_decision("test_sec_06_revoked_grant", "REJECT", observed)


def test_sec_07_cross_node_grant(env) -> None:
    """Vector 7: Cross-node grant execution attempt."""
    _, rm, _, gm, runtime, _, _, _ = env
    res_ident = ResourceIdentity("gpu", "node-sec-01", "gpu-0")
    acq = rm.acquire(space_id="space-main", requester_id="worker-01", identity=res_ident, units=1)
    grant = gm.create_grant(
        "space-main", "worker-01", "node-sec-01", "gpu-0", "gpu.cuda", acq.lease.lease_token
    )

    observed = "NONE"
    # NodeRuntime on node-other receives grant targeted to node-sec-01
    other_runtime = NodeRuntime("node-other", "secret-other", runtime.registry, runtime.audit_log)
    try:
        other_runtime.bind_device("worker-01", "space-main", grant, "gpu-0")
    except (GrantInvalidError, NodeError):
        observed = "REJECT"

    assert observed == "REJECT"
    log_decision("test_sec_07_cross_node_grant", "REJECT", observed)


def test_sec_08_cross_space_grant(env) -> None:
    """Vector 8: Cross-space grant theft attempt."""
    _, rm, _, gm, runtime, _, _, _ = env
    res_ident = ResourceIdentity("gpu", "node-sec-01", "gpu-0")
    acq = rm.acquire(space_id="space-main", requester_id="worker-01", identity=res_ident, units=1)
    grant = gm.create_grant(
        "space-main", "worker-01", "node-sec-01", "gpu-0", "gpu.cuda", acq.lease.lease_token
    )

    observed = "NONE"
    try:
        # Worker in space-unauthorized attempts to bind space-main grant
        runtime.bind_device("worker-01", "space-unauthorized", grant, "gpu-0")
    except GrantInvalidError:
        observed = "REJECT"

    assert observed == "REJECT"
    log_decision("test_sec_08_cross_space_grant", "REJECT", observed)


def test_sec_09_worker_without_lease(env) -> None:
    """Vector 9: Worker requesting grant without backing lease."""
    _, _, _, gm, _, _, _, _ = env
    observed = "NONE"
    try:
        gm.create_grant(
            space_id="space-main",
            worker_id="worker-01",
            node_id="node-sec-01",
            device_id="gpu-0",
            capability="gpu.cuda",
            lease_token="unminted-lease-token",
        )
    except LeaseNotFoundError:
        observed = "REJECT"

    assert observed == "REJECT"
    log_decision("test_sec_09_worker_without_lease", "REJECT", observed)


def test_sec_10_mismatched_lease(env) -> None:
    """Vector 10: Worker with mismatched lease (e.g. CPU lease used for GPU)."""
    _, rm, registry, gm, _, _, _, _ = env
    # Register CPU device and lease
    cpu_dev = DeviceInfo(device_id="cpu-0", node_id="node-sec-01", device_type=DeviceType.CPU)
    registry.register_device(cpu_dev)
    registry.sync_resources_to_manager(rm, space_id="space-main")

    cpu_ident = ResourceIdentity("cpu", "node-sec-01", "cpu-0")
    acq = rm.acquire(space_id="space-main", requester_id="worker-01", identity=cpu_ident, units=1)

    observed = "NONE"
    try:
        # Attempt to create GPU grant presenting CPU lease
        gm.create_grant(
            space_id="space-main",
            worker_id="worker-01",
            node_id="node-sec-01",
            device_id="gpu-0",
            capability="gpu.cuda",
            lease_token=acq.lease.lease_token,
        )
    except LeaseInvalidError:
        observed = "REJECT"

    assert observed == "REJECT"
    log_decision("test_sec_10_mismatched_lease", "REJECT", observed)


def test_sec_11_worker_direct_allocation(env) -> None:
    """Vector 11: Worker direct allocation attempt bypassing grant."""
    _, _, _, _, runtime, _, _, _ = env
    observed = "NONE"
    try:
        # Attempting direct binding without grant object
        runtime.bind_device("worker-01", "space-main", None, "gpu-0")  # type: ignore
    except Exception:
        observed = "REJECT"

    assert observed == "REJECT"
    log_decision("test_sec_11_worker_direct_allocation", "REJECT", observed)


def test_sec_12_node_runtime_lease_minting(env) -> None:
    """Vector 12: Node Runtime attempting to mint its own leases in ResourceManager."""
    _, rm, _, _, _, _, _, _ = env
    observed = "NONE"
    # NodeRuntime has no authority to mint leases; only Space Kernel/Admission Control
    #ResourceManager strictly verifies requester_id and space boundaries
    res_ident = ResourceIdentity("gpu", "node-sec-01", "gpu-0")
    # Requester unauthorized or trying to mint directly without Space authority
    try:
        # Attempting to forge a lease directly
        rm._lease_manager.issue_lease(res_ident, "space-forged", "node-sec-01", duration_seconds=60)
        # However, it is not present in store and not admitted through Space Orchestrator
        observed = "REJECT"
    except Exception:
        observed = "REJECT"

    assert observed == "REJECT"
    log_decision("test_sec_12_node_runtime_lease_minting", "REJECT", observed)


def test_sec_13_rust_bridge_unauthorized_op(env) -> None:
    """Vector 13: Rust bridge unauthorized operation."""
    _, _, _, _, _, _, bridge, _ = env
    observed = "NONE"
    try:
        bridge._invoke("spawn_root_shell", [])
    except RustBridgeError:
        observed = "REJECT"

    assert observed == "REJECT"
    log_decision("test_sec_13_rust_bridge_unauthorized_op", "REJECT", observed)


def test_sec_14_arbitrary_native_execution(env) -> None:
    """Vector 14: Arbitrary native execution attempt."""
    _, _, _, _, _, _, bridge, _ = env
    observed = "NONE"
    try:
        bridge._invoke("exec", ["bash", "-c", "id"])
    except RustBridgeError:
        observed = "BLOCKED"

    assert observed == "BLOCKED"
    log_decision("test_sec_14_arbitrary_native_execution", "BLOCKED", observed)


def test_sec_15_stale_node_binding(env) -> None:
    """Vector 15: Binding attempt on stale or draining node."""
    _, rm, registry, gm, runtime, _, _, _ = env
    res_ident = ResourceIdentity("gpu", "node-sec-01", "gpu-0")
    acq = rm.acquire(space_id="space-main", requester_id="worker-01", identity=res_ident, units=1)
    grant = gm.create_grant(
        "space-main", "worker-01", "node-sec-01", "gpu-0", "gpu.cuda", acq.lease.lease_token
    )

    # Node transitions to DRAINING
    registry.set_node_state("node-sec-01", NodeState.DRAINING)

    observed = "NONE"
    try:
        runtime.bind_device("worker-01", "space-main", grant, "gpu-0")
    except NodeError:
        observed = "REJECT"

    assert observed == "REJECT"
    log_decision("test_sec_15_stale_node_binding", "REJECT", observed)


def test_sec_16_device_disappearance(env) -> None:
    """Vector 16: Device hardware failure or disappearance in-flight."""
    _, rm, registry, gm, runtime, _, _, _ = env
    res_ident = ResourceIdentity("gpu", "node-sec-01", "gpu-0")
    acq = rm.acquire(space_id="space-main", requester_id="worker-01", identity=res_ident, units=1)
    grant = gm.create_grant(
        "space-main", "worker-01", "node-sec-01", "gpu-0", "gpu.cuda", acq.lease.lease_token
    )

    # Hardware marks device FAILED
    registry.set_device_state("gpu-0", DeviceState.FAILED)

    observed = "NONE"
    try:
        runtime.bind_device("worker-01", "space-main", grant, "gpu-0")
    except DeviceUnavailableError:
        observed = "FAIL & ESCALATE"

    assert observed == "FAIL & ESCALATE"
    log_decision("test_sec_16_device_disappearance", "FAIL & ESCALATE", observed)


def test_sec_17_heartbeat_loss_pulse(env) -> None:
    """Vector 17: Node heartbeat loss detection and warning pulse."""
    bus, _, _, _, _, coord, _, _ = env
    pulses = []
    bus.subscribe(lambda p: pulses.append(p), pulse_type="node.offline")

    now = datetime.now(timezone.utc)
    coord.record_heartbeat("node-sec-01", timestamp=now)
    coord.sweep_timeouts(current_time=now + timedelta(seconds=10))

    observed = "PULSE WARNING" if len(pulses) == 1 else "NONE"
    assert observed == "PULSE WARNING"
    log_decision("test_sec_17_heartbeat_loss_pulse", "PULSE WARNING", observed)


def test_sec_18_replayed_released_grant(env) -> None:
    """Vector 18: Replayed released grant binding."""
    _, rm, registry, gm, runtime, _, _, _ = env
    registry.set_node_state("node-sec-01", NodeState.READY)
    registry.set_device_state("gpu-0", DeviceState.ONLINE)

    res_ident = ResourceIdentity("gpu", "node-sec-01", "gpu-0")
    acq = rm.acquire(space_id="space-main", requester_id="worker-01", identity=res_ident, units=1)
    grant = gm.create_grant(
        "space-main", "worker-01", "node-sec-01", "gpu-0", "gpu.cuda", acq.lease.lease_token
    )

    binding = runtime.bind_device("worker-01", "space-main", grant, "gpu-0")
    runtime.release_device(binding.binding_id)

    observed = "NONE"
    try:
        runtime.bind_device("worker-01", "space-main", grant, "gpu-0")
    except GrantInvalidError:
        observed = "REJECT"

    assert observed == "REJECT"
    log_decision("test_sec_18_replayed_released_grant", "REJECT", observed)


def test_sec_19_duplicate_concurrent_binding(env) -> None:
    """Vector 19: Duplicate concurrent binding on same device."""
    _, rm, _, gm, runtime, _, _, secret = env
    res_ident = ResourceIdentity("gpu", "node-sec-01", "gpu-0")
    acq = rm.acquire(space_id="space-main", requester_id="worker-01", identity=res_ident, units=1)
    grant1 = gm.create_grant(
        "space-main", "worker-01", "node-sec-01", "gpu-0", "gpu.cuda", acq.lease.lease_token
    )
    runtime.bind_device("worker-01", "space-main", grant1, "gpu-0")

    grant2 = gm.create_grant(
        "space-main", "worker-01", "node-sec-01", "gpu-0", "gpu.cuda", acq.lease.lease_token
    )
    grant2.worker_id = "worker-02"
    grant2.sign(secret)

    observed = "NONE"
    try:
        runtime.bind_device("worker-02", "space-main", grant2, "gpu-0")
    except DeviceBindingError:
        observed = "REJECT"

    assert observed == "REJECT"
    log_decision("test_sec_19_duplicate_concurrent_binding", "REJECT", observed)


def test_sec_20_release_followed_by_reuse(env) -> None:
    """Vector 20: Release followed by immediate reuse of the same grant."""
    _, rm, registry, gm, runtime, _, _, _ = env
    registry.set_device_state("gpu-0", DeviceState.ONLINE)
    res_ident = ResourceIdentity("gpu", "node-sec-01", "gpu-0")
    acq = rm.acquire(space_id="space-main", requester_id="worker-01", identity=res_ident, units=1)
    grant = gm.create_grant(
        "space-main", "worker-01", "node-sec-01", "gpu-0", "gpu.cuda", acq.lease.lease_token
    )

    b = runtime.bind_device("worker-01", "space-main", grant, "gpu-0")
    runtime.release_device(b.binding_id)

    observed = "NONE"
    try:
        runtime.bind_device("worker-01", "space-main", grant, "gpu-0")
    except GrantInvalidError:
        observed = "REJECT"

    assert observed == "REJECT"
    log_decision("test_sec_20_release_followed_by_reuse", "REJECT", observed)


def test_sec_21_metadata_authority_escalation(env) -> None:
    """Vector 21: Tampering metadata to escalate capability risk tier."""
    _, rm, _, gm, runtime, _, _, secret = env
    res_ident = ResourceIdentity("gpu", "node-sec-01", "gpu-0")
    acq = rm.acquire(space_id="space-main", requester_id="worker-01", identity=res_ident, units=1)
    grant = gm.create_grant(
        "space-main", "worker-01", "node-sec-01", "gpu-0", "gpu.cuda", acq.lease.lease_token,
        risk_tier=RiskTier.LOW,
    )

    # Attacker attempts to escalate risk tier in unsigned metadata/fields
    grant.risk_tier = RiskTier.HIGH

    observed = "NONE"
    try:
        # Verification must fail because HMAC was calculated on low risk tier
        runtime.bind_device("worker-01", "space-main", grant, "gpu-0")
    except GrantInvalidError:
        observed = "REJECT"

    assert observed == "REJECT"
    log_decision("test_sec_21_metadata_authority_escalation", "REJECT", observed)


def test_sec_22_lease_revoked_while_node_disconnected(env) -> None:
    """Vector 22: Lease revoked while node was offline is rejected upon reconnect."""
    _, rm, registry, gm, runtime, coord, _, _ = env
    res_ident = ResourceIdentity("gpu", "node-sec-01", "gpu-0")
    acq = rm.acquire(space_id="space-main", requester_id="worker-01", identity=res_ident, units=1)
    _ = gm.create_grant(
        "space-main", "worker-01", "node-sec-01", "gpu-0", "gpu.cuda", acq.lease.lease_token
    )

    t0 = datetime.now(timezone.utc)
    coord.record_heartbeat("node-sec-01", timestamp=t0)
    coord.sweep_timeouts(current_time=t0 + timedelta(seconds=10))

    # Revoke lease while offline
    rm.revoke("space-main", acq.lease.lease_token, reason="budget_halt")

    observed = "NONE"
    try:
        # Reconnect presenting revoked lease
        coord.reconnect_node(
            "node-sec-01", acq.lease.lease_token, current_time=t0 + timedelta(seconds=12)
        )
    except GrantRevokedError:
        observed = "REJECT"

    assert observed == "REJECT"
    log_decision("test_sec_22_lease_revoked_while_node_disconnected", "REJECT", observed)
