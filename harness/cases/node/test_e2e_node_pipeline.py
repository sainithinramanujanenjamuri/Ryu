"""End-to-end integration pipeline verification for Node Runtime, Device Grants, and Workers.

Space-Centric Cognitive Architecture (SCCA) — Phase 7
spec §11 (Node Runtime), §16 (Lease & Grants), CONTRACT_MATRIX NODE-001..NODE-008
ADR-0017, ADR-0018, ADR-0019, ADR-0020

Governing Invariant:
Nodes and devices provide capability, but possession of a node or device does not grant authority.
Workers execute through Node Runtime only with an authoritative, space-scoped Device Grant
backed by a valid Resource Manager Lease.
"""

from __future__ import annotations

from pathlib import Path

from ryu.pulse_bus.bus import PulseBus

from core.resources.identity import ResourceIdentity
from core.resources.manager import ResourceManager
from core.resources.store import InMemoryResourceStore
from node.audit import DeviceAuditLog
from node.bridge import RustNodeBridge
from node.contract import (
    DeviceInfo,
    DeviceState,
    DeviceType,
    GrantState,
    NodeInfo,
    NodeState,
    RiskTier,
)
from node.grants import DeviceGrantManager
from node.registry import NodeRegistry
from node.runtime import NodeRuntime
from workers.contract import ExecutionRequest, WorkerIdentity
from workers.node.worker import NodeWorker


def test_full_e2e_node_pipeline(tmp_path: Path) -> None:
    """Complete End-to-End Node capability execution pipeline:

    Goal -> Admission Control -> Resource Manager Lease -> DeviceGrantManager
    -> NodeRuntime Binding -> Worker Execution -> Device Release -> Native Rust Audit Verification.
    """
    bus = PulseBus()
    events = []
    bus.subscribe(lambda p: events.append(p))

    # 1. Initialize Resource & Node Infrastructure
    rm = ResourceManager(bus=bus, store=InMemoryResourceStore())
    registry = NodeRegistry()
    secret = "high-entropy-node-pairing-secret-key-32bytes"

    # Register physical compute node
    node = NodeInfo(
        node_id="node-prod-01",
        platform="windows",
        architecture="x86_64",
        environment_profile="windows_host",
        runtime_state=NodeState.READY,
        cpu_cores=16,
        memory_total_bytes=34359738368,
        capabilities=["gpu.cuda", "cpu.compute"],
    )
    registry.register_node(node, secret)

    # Register hosted hardware devices
    device_gpu = DeviceInfo(
        device_id="gpu-0",
        node_id="node-prod-01",
        device_type=DeviceType.GPU,
        total_capacity=1,
        capability_metadata={"vram": "24GB", "cuda_compute": "8.9"},
    )
    registry.register_device(device_gpu)

    # Export node devices into Space ResourceManager inventory (without holding authority)
    space_id = "space-enterprise-analytics"
    registry.sync_resources_to_manager(rm, space_id=space_id)

    # Setup GrantManager, Local Audit Logger, Native Rust Bridge, and NodeRuntime
    gm = DeviceGrantManager(registry=registry, resource_manager=rm, bus=bus)
    audit_log_path = tmp_path / "e2e_audit.log.jsonl"
    audit_log = DeviceAuditLog(log_path=audit_log_path)
    bridge = RustNodeBridge()

    runtime = NodeRuntime(
        node_id="node-prod-01",
        shared_secret=secret,
        registry=registry,
        audit_log=audit_log,
        bridge=bridge,
        bus=bus,
    )

    # 2. Authoritative Resource Allocation (ResourceManager Lease)
    res_ident = ResourceIdentity("gpu", "node-prod-01", "gpu-0")
    worker_id = "worker-cuda-01"
    acq = rm.acquire(
        space_id=space_id,
        requester_id=worker_id,
        identity=res_ident,
        units=1,
        duration_seconds=3600.0,
    )
    assert acq.granted is True
    assert acq.lease is not None
    lease_token = acq.lease.lease_token

    # 3. Derive Signed Execution Credential (DeviceGrantManager)
    grant = gm.create_grant(
        space_id=space_id,
        worker_id=worker_id,
        node_id="node-prod-01",
        device_id="gpu-0",
        capability="gpu.cuda",
        lease_token=lease_token,
        risk_tier=RiskTier.LOW,
    )
    assert grant.state == GrantState.GRANTED
    assert grant.signature != ""

    # Verify native Rust bridge can cryptographically validate this grant
    rust_valid, rust_err = bridge.validate_grant("node-prod-01", secret, grant)
    assert rust_valid is True
    assert rust_err is None

    # 4. Instantiate Worker and Execute Task on Node
    worker = NodeWorker(
        node_runtime=runtime,
        identity=WorkerIdentity(
            worker_id=worker_id,
            capability="gpu.cuda",
            space_id=space_id,
        ),
        bus=bus,
        resource_manager=rm,
    )

    req = ExecutionRequest(
        request_id="req-e2e-cuda-01",
        correlation_id=f"corr-{space_id}-01",
        space_id=space_id,
        worker_id=worker_id,
        capability="gpu.cuda",
        lease_id=lease_token,
        arguments={
            "device_id": "gpu-0",
            "grant": grant,
            "operation": "deep_learning_inference",
        },
    )

    res = worker.execute(req)
    assert res.is_success is True
    assert res.status == "ok"
    assert res.output_data["status"] == "success"
    assert res.output_data["operation"] == "deep_learning_inference"

    # 5. Verify Post-Execution States
    # Device should be released back to ONLINE
    dev_state = registry.get_device("gpu-0")
    assert dev_state is not None
    assert dev_state.availability_state == DeviceState.ONLINE

    # Grant should be in RELEASED state
    assert grant.state == GrantState.RELEASED

    # Release lease in ResourceManager
    rm.release(space_id=space_id, requester_id=worker_id, lease_token=lease_token)

    # 6. Verify Device-Local Audit Log Integrity with Native Rust Binary
    rust_verified, entry_count, verify_err = bridge.audit_verify(audit_log_path)
    assert rust_verified is True
    assert entry_count == 2  # DEVICE_BOUND + DEVICE_RELEASED
    assert verify_err is None

    # Python audit verification
    py_verified, py_count, py_err = audit_log.verify_chain()
    assert py_verified is True
    assert py_count == 2

    # 7. Verify Schema-Compliant Pulses Emitted along the Pipeline
    pulse_types = [p.type for p in events]
    assert "resource.requested" in pulse_types
    assert "resource.granted" in pulse_types
    assert "node.capability.granted" in pulse_types
    assert "worker.tool.called" in pulse_types
    assert "worker.tool.succeeded" in pulse_types
    assert "resource.released" in pulse_types
