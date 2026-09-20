"""Unit tests for NodeRuntime: device binding, exclusivity, and mid-call revocation.

CONTRACT_MATRIX NODE-002, NODE-003, NODE-004, NODE-008
ADR-0017, ADR-0018
"""

from __future__ import annotations

import pytest
from ryu.pulse_bus.bus import PulseBus

from core.resources.identity import ResourceIdentity
from core.resources.manager import ResourceManager
from core.resources.store import InMemoryResourceStore
from node.audit import DeviceAuditLog
from node.contract import (
    DeviceBindingError,
    DeviceInfo,
    DeviceState,
    DeviceType,
    GrantInvalidError,
    GrantRevokedError,
    GrantState,
    NodeInfo,
    NodeState,
)
from node.grants import DeviceGrantManager
from node.registry import NodeRegistry
from node.runtime import NodeRuntime


@pytest.fixture
def runtime_env(tmp_path):
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

    device = DeviceInfo(
        device_id="gpu-0",
        node_id="node-test-01",
        device_type=DeviceType.GPU,
        total_capacity=1,
    )
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

    return bus, rm, registry, gm, runtime, secret


def test_bind_device_success_and_release(runtime_env) -> None:
    bus, rm, registry, gm, runtime, secret = runtime_env

    # 1. Acquire lease
    res_ident = ResourceIdentity("gpu", "node-test-01", "gpu-0")
    acq = rm.acquire(space_id="space-main", requester_id="worker-01", identity=res_ident, units=1)
    assert acq.lease is not None

    # 2. Issue grant
    grant = gm.create_grant(
        space_id="space-main",
        worker_id="worker-01",
        node_id="node-test-01",
        device_id="gpu-0",
        capability="gpu.cuda",
        lease_token=acq.lease.lease_token,
    )

    # 3. Bind device
    binding = runtime.bind_device(
        worker_id="worker-01",
        space_id="space-main",
        grant=grant,
        device_id="gpu-0",
    )
    assert binding.is_active is True
    assert binding.grant_id == grant.grant_id
    assert grant.state == GrantState.BOUND

    # Device should now be BUSY
    dev = registry.get_device("gpu-0")
    assert dev is not None
    assert dev.availability_state == DeviceState.BUSY

    # 4. Release device
    runtime.release_device(binding.binding_id)
    assert binding.is_active is False
    assert dev.availability_state == DeviceState.ONLINE
    assert grant.state == GrantState.RELEASED


def test_bind_device_conflict_on_busy_device(runtime_env) -> None:
    bus, rm, registry, gm, runtime, secret = runtime_env

    # Acquire lease 1
    res_ident = ResourceIdentity("gpu", "node-test-01", "gpu-0")
    acq1 = rm.acquire(
        space_id="space-main", requester_id="worker-01", identity=res_ident, units=1
    )
    grant1 = gm.create_grant(
        "space-main", "worker-01", "node-test-01", "gpu-0", "gpu.cuda", acq1.lease.lease_token
    )
    runtime.bind_device("worker-01", "space-main", grant1, "gpu-0")

    # Second worker attempts concurrent binding to the same physical device
    # Even if lease could somehow exist, NodeRuntime enforces exclusive device binding table
    grant2 = gm.create_grant(
        "space-main", "worker-01", "node-test-01", "gpu-0", "gpu.cuda", acq1.lease.lease_token
    )
    # Tamper grant2 to claim worker-02
    grant2.worker_id = "worker-02"
    grant2.sign(secret)

    with pytest.raises(DeviceBindingError, match="already actively bound"):
        runtime.bind_device("worker-02", "space-main", grant2, "gpu-0")


def test_in_flight_revocation_halts_binding(runtime_env) -> None:
    bus, rm, registry, gm, runtime, secret = runtime_env

    res_ident = ResourceIdentity("gpu", "node-test-01", "gpu-0")
    acq = rm.acquire(
        space_id="space-main", requester_id="worker-01", identity=res_ident, units=1
    )
    grant = gm.create_grant(
        "space-main", "worker-01", "node-test-01", "gpu-0", "gpu.cuda", acq.lease.lease_token
    )

    binding = runtime.bind_device("worker-01", "space-main", grant, "gpu-0")
    assert binding.is_active is True

    # Mid-call revocation triggered
    runtime.revoke_in_flight(grant.grant_id)

    # In-flight binding must be halted and marked inactive
    assert binding.is_active is False
    assert registry.get_device("gpu-0").availability_state == DeviceState.ONLINE

    # Re-binding with revoked grant must fail
    with pytest.raises(GrantRevokedError, match="has been revoked"):
        runtime.bind_device("worker-01", "space-main", grant, "gpu-0")


def test_replayed_released_grant_rejected(runtime_env) -> None:
    bus, rm, registry, gm, runtime, secret = runtime_env

    res_ident = ResourceIdentity("gpu", "node-test-01", "gpu-0")
    acq = rm.acquire(
        space_id="space-main", requester_id="worker-01", identity=res_ident, units=1
    )
    grant = gm.create_grant(
        "space-main", "worker-01", "node-test-01", "gpu-0", "gpu.cuda", acq.lease.lease_token
    )

    binding = runtime.bind_device("worker-01", "space-main", grant, "gpu-0")
    runtime.release_device(binding.binding_id)

    # Replaying the released grant
    with pytest.raises(GrantInvalidError, match="already been released"):
        runtime.bind_device("worker-01", "space-main", grant, "gpu-0")
