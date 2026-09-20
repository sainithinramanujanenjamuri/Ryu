"""Unit tests for NodeCoordinator: heartbeat tracking, offline checkpointing, and validated resume.

CONTRACT_MATRIX NODE-005, NODE-006, NODE-007
ADR-0017, ADR-0018
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from ryu.pulse_bus.bus import PulseBus

from core.resources.identity import ResourceIdentity
from core.resources.manager import ResourceManager
from core.resources.store import InMemoryResourceStore
from node.contract import (
    DeviceInfo,
    DeviceType,
    GrantRevokedError,
    NodeInfo,
    NodeOfflineError,
    NodeState,
)
from node.coordinator import NodeCoordinator
from node.grants import DeviceGrantManager
from node.registry import NodeRegistry


@pytest.fixture
def coordinator_env():
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
    coord = NodeCoordinator(
        registry=registry,
        grant_manager=gm,
        resource_manager=rm,
        bus=bus,
        heartbeat_ttl_seconds=10.0,
        flapping_threshold_seconds=0.5,
    )

    return bus, rm, registry, gm, coord, secret


def test_heartbeat_timeout_transitions_to_offline(coordinator_env) -> None:
    bus, rm, registry, gm, coord, secret = coordinator_env
    emitted_pulses = []
    bus.subscribe(lambda p: emitted_pulses.append(p), pulse_type="node.offline")

    t0 = datetime.now(timezone.utc)
    coord.record_heartbeat("node-test-01", timestamp=t0)

    # Register in-flight tasks (one idempotent, one non-idempotent)
    coord.register_in_flight_task(
        task_id="task-idem-01",
        space_id="space-main",
        worker_id="worker-01",
        node_id="node-test-01",
        device_id="gpu-0",
        grant_id="grant-01",
        is_idempotent=True,
    )
    coord.register_in_flight_task(
        task_id="task-nonidem-02",
        space_id="space-main",
        worker_id="worker-01",
        node_id="node-test-01",
        device_id="gpu-0",
        grant_id="grant-01",
        is_idempotent=False,
    )

    # Advance time past TTL (15s > 10s)
    t1 = t0 + timedelta(seconds=15)
    offline_nodes = coord.sweep_timeouts(current_time=t1)

    assert "node-test-01" in offline_nodes
    node = registry.get_node("node-test-01")
    assert node.runtime_state == NodeState.OFFLINE

    # Verify node.offline Pulse was emitted
    assert len(emitted_pulses) == 1
    assert emitted_pulses[0].payload["node_id"] == "node-test-01"

    # Verify task checkpoints
    cp_idem = coord.get_task_checkpoint("task-idem-01")
    assert cp_idem.state == "checkpointed"

    cp_nonidem = coord.get_task_checkpoint("task-nonidem-02")
    assert cp_nonidem.state == "indeterminate"


def test_reconnection_and_validated_resume(coordinator_env) -> None:
    bus, rm, registry, gm, coord, secret = coordinator_env
    reconnect_pulses = []
    bus.subscribe(lambda p: reconnect_pulses.append(p), pulse_type="node.reconnected")

    t0 = datetime.now(timezone.utc)
    coord.record_heartbeat("node-test-01", timestamp=t0)

    # Acquire lease and issue grant
    res_ident = ResourceIdentity("gpu", "node-test-01", "gpu-0")
    acq = rm.acquire(
        space_id="space-main",
        requester_id="worker-01",
        identity=res_ident,
        units=1,
        duration_seconds=3600.0,
    )
    grant = gm.create_grant(
        "space-main", "worker-01", "node-test-01", "gpu-0", "gpu.cuda", acq.lease.lease_token
    )

    # Register idempotent task
    coord.register_in_flight_task(
        task_id="task-idem-01",
        space_id="space-main",
        worker_id="worker-01",
        node_id="node-test-01",
        device_id="gpu-0",
        grant_id=grant.grant_id,
        is_idempotent=True,
    )

    # Disconnect node
    t1 = t0 + timedelta(seconds=15)
    coord.sweep_timeouts(current_time=t1)
    assert registry.get_node("node-test-01").runtime_state == NodeState.OFFLINE

    # Cannot resume while node is offline
    with pytest.raises(NodeOfflineError, match="is not in READY/ACTIVE state"):
        coord.validate_and_resume_task("task-idem-01", current_time=t1)

    # Reconnect node
    t2 = t1 + timedelta(seconds=5)
    success = coord.reconnect_node("node-test-01", acq.lease.lease_token, current_time=t2)
    assert success is True
    assert registry.get_node("node-test-01").runtime_state == NodeState.READY
    assert len(reconnect_pulses) == 1

    # Validated resume of idempotent task -> SUCCESS
    resumed = coord.validate_and_resume_task("task-idem-01", current_time=t2)
    assert resumed.state == "resumed"


def test_reconnection_rejected_if_lease_revoked_while_offline(coordinator_env) -> None:
    bus, rm, registry, gm, coord, secret = coordinator_env

    t0 = datetime.now(timezone.utc)
    coord.record_heartbeat("node-test-01", timestamp=t0)

    res_ident = ResourceIdentity("gpu", "node-test-01", "gpu-0")
    acq = rm.acquire(
        space_id="space-main", requester_id="worker-01", identity=res_ident, units=1
    )
    grant = gm.create_grant(
        "space-main", "worker-01", "node-test-01", "gpu-0", "gpu.cuda", acq.lease.lease_token
    )

    # Register idempotent task
    coord.register_in_flight_task(
        task_id="task-idem-01",
        space_id="space-main",
        worker_id="worker-01",
        node_id="node-test-01",
        device_id="gpu-0",
        grant_id=grant.grant_id,
        is_idempotent=True,
    )

    # Node disconnects
    t1 = t0 + timedelta(seconds=15)
    coord.sweep_timeouts(current_time=t1)

    # While node was offline, lease is revoked by Kernel / human
    rm.revoke(
        space_id="space-main", lease_token=acq.lease.lease_token, reason="human_abort"
    )

    # Node attempts reconnection presenting stale/revoked lease
    t2 = t1 + timedelta(seconds=5)
    match_msg = "backing lease '.*' expired or revoked while offline"
    with pytest.raises(GrantRevokedError, match=match_msg):
        coord.reconnect_node("node-test-01", acq.lease.lease_token, current_time=t2)
