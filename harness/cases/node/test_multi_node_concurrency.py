"""Executable harness verification for Node Runtime contracts NODE-010 and NODE-011.

Space-Centric Cognitive Architecture (SCCA) — Phase 11
spec §11 (Multi-Node Concurrency & Fault Isolation), CONTRACT_MATRIX NODE-010, NODE-011
ADR-0038

INVARIANTS:
Node A authorization != Node B authorization
Node A lease != Node B lease
Node A failure != Node B authority escalation
NodeCoordinator != Authority Root
DeviceGrantManager != Authority Root
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
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
    GrantInvalidError,
    LeaseNotFoundError,
    NodeInfo,
    NodeState,
    RiskTier,
)
from node.coordinator import NodeCoordinator
from node.grants import DeviceGrantManager
from node.registry import NodeRegistry
from node.runtime import NodeRuntime


@pytest.fixture
def multi_node_env(tmp_path: Path):
    bus = PulseBus()
    rm = ResourceManager(bus=bus, store=InMemoryResourceStore())
    registry = NodeRegistry()

    secret_win = "secret-key-node-win-01-32charslong"
    secret_linux = "secret-key-node-linux-01-32chars"

    node_win = NodeInfo(
        node_id="node-win-01",
        platform="windows",
        architecture="x86_64",
        environment_profile="windows_host",
        runtime_state=NodeState.READY,
    )
    node_linux = NodeInfo(
        node_id="node-linux-01",
        platform="linux",
        architecture="x86_64",
        environment_profile="linux_native",
        runtime_state=NodeState.READY,
    )
    registry.register_node(node_win, secret_win)
    registry.register_node(node_linux, secret_linux)

    dev_win_gpu = DeviceInfo(
        device_id="gpu-0",
        node_id="node-win-01",
        device_type=DeviceType.GPU,
        availability_state=DeviceState.ONLINE,
    )
    dev_linux_cpu = DeviceInfo(
        device_id="cpu-0",
        node_id="node-linux-01",
        device_type=DeviceType.CPU,
        availability_state=DeviceState.ONLINE,
    )
    registry.register_device(dev_win_gpu)
    registry.register_device(dev_linux_cpu)

    # Sync capacity to Space space-multi
    registry.sync_resources_to_manager(rm, space_id="space-multi")

    gm = DeviceGrantManager(registry=registry, resource_manager=rm, bus=bus)
    coord = NodeCoordinator(
        registry=registry,
        grant_manager=gm,
        resource_manager=rm,
        bus=bus,
        heartbeat_ttl_seconds=5.0,
    )

    audit_win = DeviceAuditLog(log_path=tmp_path / "audit_win.jsonl")
    audit_linux = DeviceAuditLog(log_path=tmp_path / "audit_linux.jsonl")

    runtime_win = NodeRuntime(
        node_id="node-win-01",
        shared_secret=secret_win,
        registry=registry,
        audit_log=audit_win,
        bus=bus,
    )
    runtime_linux = NodeRuntime(
        node_id="node-linux-01",
        shared_secret=secret_linux,
        registry=registry,
        audit_log=audit_linux,
        bus=bus,
    )

    return {
        "bus": bus,
        "rm": rm,
        "registry": registry,
        "gm": gm,
        "coord": coord,
        "secret_win": secret_win,
        "secret_linux": secret_linux,
        "runtime_win": runtime_win,
        "runtime_linux": runtime_linux,
        "audit_win": audit_win,
        "audit_linux": audit_linux,
    }


def test_multi_node_concurrent_execution_in_single_space(multi_node_env) -> None:
    """NODE-010: One Space using two Nodes concurrently; per-Node grants enforced independently."""
    rm = multi_node_env["rm"]
    gm = multi_node_env["gm"]
    runtime_win = multi_node_env["runtime_win"]
    runtime_linux = multi_node_env["runtime_linux"]

    # 1. Independent Leases in single Space (space-multi)
    res_win = ResourceIdentity("gpu", "node-win-01", "gpu-0")
    res_linux = ResourceIdentity("cpu", "node-linux-01", "cpu-0")

    acq_win = rm.acquire(space_id="space-multi", requester_id="worker-win-01", identity=res_win, units=1)
    acq_linux = rm.acquire(space_id="space-multi", requester_id="worker-lin-01", identity=res_linux, units=1)

    assert acq_win.lease is not None
    assert acq_linux.lease is not None
    assert acq_win.lease.lease_token != acq_linux.lease.lease_token

    # 2. Independent Device Grants
    grant_win = gm.create_grant(
        space_id="space-multi",
        worker_id="worker-win-01",
        node_id="node-win-01",
        device_id="gpu-0",
        capability="gpu.cuda",
        lease_token=acq_win.lease.lease_token,
    )
    grant_linux = gm.create_grant(
        space_id="space-multi",
        worker_id="worker-lin-01",
        node_id="node-linux-01",
        device_id="cpu-0",
        capability="compute.cpu",
        lease_token=acq_linux.lease.lease_token,
    )

    assert grant_win.node_id == "node-win-01"
    assert grant_linux.node_id == "node-linux-01"
    assert grant_win.signature != grant_linux.signature

    # 3. Concurrent Active Bindings
    bind_win = runtime_win.bind_device("worker-win-01", "space-multi", grant_win, "gpu-0")
    bind_linux = runtime_linux.bind_device("worker-lin-01", "space-multi", grant_linux, "cpu-0")

    assert bind_win.is_active is True
    assert bind_linux.is_active is True

    # 4. Cross-Node replay rejection (Node A grant cannot be presented to Node B)
    with pytest.raises(GrantInvalidError) as exc_cross:
        runtime_linux.bind_device("worker-win-01", "space-multi", grant_win, "cpu-0")
    assert "Cross-node grant rejected" in str(exc_cross.value)


def test_multi_node_per_node_grant_isolation_and_revocation(multi_node_env) -> None:
    """NODE-011: Revoking Node A grant does NOT disrupt or terminate Node B active execution."""
    rm = multi_node_env["rm"]
    gm = multi_node_env["gm"]
    runtime_win = multi_node_env["runtime_win"]
    runtime_linux = multi_node_env["runtime_linux"]

    res_win = ResourceIdentity("gpu", "node-win-01", "gpu-0")
    res_linux = ResourceIdentity("cpu", "node-linux-01", "cpu-0")

    acq_win = rm.acquire(space_id="space-multi", requester_id="worker-win-01", identity=res_win, units=1)
    acq_linux = rm.acquire(space_id="space-multi", requester_id="worker-lin-01", identity=res_linux, units=1)

    grant_win = gm.create_grant("space-multi", "worker-win-01", "node-win-01", "gpu-0", "gpu.cuda", acq_win.lease.lease_token)
    grant_linux = gm.create_grant("space-multi", "worker-lin-01", "node-linux-01", "cpu-0", "compute.cpu", acq_linux.lease.lease_token)

    bind_win = runtime_win.bind_device("worker-win-01", "space-multi", grant_win, "gpu-0")
    bind_linux = runtime_linux.bind_device("worker-lin-01", "space-multi", grant_linux, "cpu-0")

    # Mid-call revoke on node-win-01
    runtime_win.revoke_in_flight(grant_win.grant_id)
    assert bind_win.is_active is False

    # node-linux-01 execution remains fully active and unaffected
    assert bind_linux.is_active is True
    active_lin = runtime_linux.get_active_binding(bind_linux.binding_id)
    assert active_lin is not None and active_lin.is_active is True


def test_multi_node_fault_containment_and_zero_authority_transfer(multi_node_env) -> None:
    """NODE-011: Node A failure checkpoints Node A tasks; Node B continues and does NOT inherit authority."""
    rm = multi_node_env["rm"]
    gm = multi_node_env["gm"]
    coord = multi_node_env["coord"]
    registry = multi_node_env["registry"]
    runtime_linux = multi_node_env["runtime_linux"]

    t0 = datetime.now(timezone.utc)
    coord.record_heartbeat("node-win-01", timestamp=t0)
    coord.record_heartbeat("node-linux-01", timestamp=t0)

    res_win = ResourceIdentity("gpu", "node-win-01", "gpu-0")
    res_linux = ResourceIdentity("cpu", "node-linux-01", "cpu-0")

    acq_win = rm.acquire(space_id="space-multi", requester_id="worker-win-01", identity=res_win, units=1)
    acq_linux = rm.acquire(space_id="space-multi", requester_id="worker-lin-01", identity=res_linux, units=1)

    grant_win = gm.create_grant("space-multi", "worker-win-01", "node-win-01", "gpu-0", "gpu.cuda", acq_win.lease.lease_token)
    grant_linux = gm.create_grant("space-multi", "worker-lin-01", "node-linux-01", "cpu-0", "compute.cpu", acq_linux.lease.lease_token)

    coord.register_in_flight_task(
        "task-win-01", "space-multi", "worker-win-01", "node-win-01", "gpu-0", grant_win.grant_id, is_idempotent=True
    )
    coord.register_in_flight_task(
        "task-lin-01", "space-multi", "worker-lin-01", "node-linux-01", "cpu-0", grant_linux.grant_id, is_idempotent=True
    )

    # Advance time: node-linux-01 keeps sending heartbeats, but node-win-01 misses them
    t1 = t0 + timedelta(seconds=6)
    coord.record_heartbeat("node-linux-01", timestamp=t1)
    offline_nodes = coord.sweep_timeouts(current_time=t1)

    # Only node-win-01 went offline
    assert "node-win-01" in offline_nodes
    assert "node-linux-01" not in offline_nodes
    assert registry.get_node("node-win-01").runtime_state == NodeState.OFFLINE
    assert registry.get_node("node-linux-01").runtime_state == NodeState.READY

    # Task on node-win-01 is checkpointed
    cp_win = coord.get_task_checkpoint("task-win-01")
    assert cp_win is not None and cp_win.state == "checkpointed"

    # Task on node-linux-01 is still in_flight
    cp_lin = coord.get_task_checkpoint("task-lin-01")
    assert cp_lin is not None and cp_lin.state == "in_flight"

    # INVARIANT: Node B does NOT inherit Node A's grants or authority
    # Attempting to bind grant_win on node-linux-01 is rejected
    with pytest.raises(GrantInvalidError) as exc_inherit:
        runtime_linux.bind_device("worker-win-01", "space-multi", grant_win, "cpu-0")
    assert "Cross-node grant rejected" in str(exc_inherit.value)


def test_node_coordinator_downstream_authority_boundaries(multi_node_env) -> None:
    """Verify NodeCoordinator and DeviceGrantManager cannot invent authority or bypass SCCA rules."""
    coord = multi_node_env["coord"]
    gm = multi_node_env["gm"]

    # 1. NodeCoordinator has no grant issuance API
    assert not hasattr(coord, "grant_capability")
    assert not hasattr(coord, "create_grant")

    # 2. NodeCoordinator has no resource lease creation API (leases must come from ResourceManager)
    assert not hasattr(coord, "acquire_lease")
    assert not hasattr(coord, "create_lease")

    # 3. DeviceGrantManager cannot create grant without valid backing lease
    with pytest.raises(LeaseNotFoundError):
        gm.create_grant(
            space_id="space-multi",
            worker_id="worker-01",
            node_id="node-win-01",
            device_id="gpu-0",
            capability="gpu.cuda",
            lease_token="unregistered-bogus-lease-token",
        )

