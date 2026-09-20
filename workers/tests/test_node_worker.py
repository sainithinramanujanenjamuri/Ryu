"""Unit tests for NodeWorker device-bound capability execution.

CONTRACT_MATRIX NODE-001..NODE-004, WORKER-001..WORKER-003
ADR-0017, ADR-0018
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from ryu.pulse_bus.bus import PulseBus

from core.resources.identity import ResourceIdentity
from core.resources.manager import ResourceManager
from core.resources.store import InMemoryResourceStore
from node.audit import DeviceAuditLog
from node.contract import (
    DeviceInfo,
    DeviceState,
    DeviceType,
    NodeInfo,
    NodeState,
)
from node.grants import DeviceGrantManager
from node.registry import NodeRegistry
from node.runtime import NodeRuntime
from workers.contract import ExecutionRequest, WorkerIdentity
from workers.node.worker import NodeWorker


@pytest.fixture
def worker_env(tmp_path: Path) -> tuple[Any, ...]:
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

    worker = NodeWorker(
        node_runtime=runtime,
        identity=WorkerIdentity(
            worker_id="node-worker-01",
            capability="gpu.cuda",
            space_id="space-main",
        ),
        bus=bus,
        resource_manager=rm,
    )

    return bus, rm, registry, gm, runtime, worker, secret


def test_node_worker_execution_success(worker_env: tuple[Any, ...]) -> None:
    bus, rm, registry, gm, runtime, worker, secret = worker_env

    # 1. Acquire lease
    res_ident = ResourceIdentity("gpu", "node-test-01", "gpu-0")
    acq = rm.acquire(
        space_id="space-main",
        requester_id="node-worker-01",
        identity=res_ident,
        units=1,
    )
    assert acq.lease is not None

    # 2. Materialize grant
    grant = gm.create_grant(
        space_id="space-main",
        worker_id="node-worker-01",
        node_id="node-test-01",
        device_id="gpu-0",
        capability="gpu.cuda",
        lease_token=acq.lease.lease_token,
    )

    # 3. Worker executes request
    req = ExecutionRequest(
        request_id="req-node-01",
        correlation_id="corr-node-01",
        space_id="space-main",
        worker_id="node-worker-01",
        capability="gpu.cuda",
        lease_id=acq.lease.lease_token,
        arguments={
            "device_id": "gpu-0",
            "grant": grant.to_dict(),
            "operation": "matrix_multiply",
        },
    )

    result = worker.execute(req)
    assert result.status == "ok"
    assert result.output_data["status"] == "success"
    assert result.output_data["device_id"] == "gpu-0"

    # Verify device was automatically released back to ONLINE
    dev = registry.get_device("gpu-0")
    assert dev.availability_state == DeviceState.ONLINE
