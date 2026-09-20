"""End-to-End Capability Execution Pipeline Integration Test.

Verifies the complete deterministic execution chain:
Human -> Space Orchestrator -> Agent -> Proposal -> Validation
-> Admission -> ResourceManager -> Worker -> Sandbox -> Execution.
spec §7, §9, §10, §14, §16, CONTRACT_MATRIX WORKER-001..WORKER-005, ADR-0013..0016
"""

from __future__ import annotations

from ryu.pulse_bus.bus import PulseBus
from ryu.pulse_bus.pulse import Pulse

from agents.base import AgentProposal, ProposalValidator
from core.capabilities.admission import CapabilityRequest
from core.resources.identity import Resource, ResourceIdentity
from core.resources.manager import ResourceManager
from core.space.kernel import SpaceKernel
from workers.contract import (
    ExecutionRequest,
    WorkerIdentity,
    WorkerState,
)
from workers.python.worker import PythonWorker


def test_e2e_full_execution_pipeline_success() -> None:
    """Full execution pipeline from Orchestrator/Agent through Kernel,
    ResourceManager, and Worker Sandbox.
    """
    bus = PulseBus()
    published_pulses: list[Pulse] = []
    bus.subscribe(lambda p: published_pulses.append(p))

    space_id = "space-e2e-1"
    kernel = SpaceKernel(space_id=space_id, owner_id="human-owner", bus=bus, budget=100.0)
    rm = ResourceManager(bus=bus)

    # 1. Register compute resource in Space
    r_ident = ResourceIdentity("compute", "local-host", "cpu-e2e-1")
    rm.register_resource(Resource(identity=r_ident, space_id=space_id, total_capacity=5))

    # 2. Kernel evaluates and admits capability request
    cap_req = CapabilityRequest(
        requester_id="python-worker-01",
        space_id=space_id,
        capability="python.eval_sandboxed",
        budget=10.0,
    )
    cap_res = kernel.request_capability(cap_req)
    assert cap_res.status == "ok"

    # 3. Agent creates a Proposal for execution and validates it deterministically
    validator = ProposalValidator(current_space_id=space_id)
    proposal = AgentProposal(
        intent="Calculate numbers",
        reasoning="Need sum of evens",
        requested_action="execute_worker_task",
        required_capabilities=["python.eval_sandboxed"],
    )
    valid, reason = validator.validate(proposal)
    assert valid

    # 4. Resource acquisition via ResourceManager
    acq = rm.acquire(
        space_id=space_id,
        requester_id="python-worker-01",
        identity=r_ident,
        duration_seconds=60,
    )
    assert acq.granted
    assert acq.lease is not None
    lease_token = acq.lease.lease_token

    # 5. Worker executes capability inside Sandbox
    worker = PythonWorker(
        identity=WorkerIdentity("python-worker-01", "python.eval_sandboxed", space_id),
        bus=bus,
        resource_manager=rm,
    )

    req = ExecutionRequest(
        request_id="req-e2e-exec-1",
        correlation_id="corr-e2e-1",
        space_id=space_id,
        worker_id="python-worker-01",
        capability="python.eval_sandboxed",
        lease_id=lease_token,
        arguments={"code": "data = [i*2 for i in range(5)]; print(f'data={data}')"},
    )

    result = worker.execute(req)
    assert result.is_success
    assert worker.state == WorkerState.COMPLETED
    assert "data=[0, 2, 4, 6, 8]" in result.output_data["stdout"]

    # 6. Release Lease
    released = rm.release(
        space_id=space_id,
        requester_id="python-worker-01",
        lease_token=lease_token,
    )
    assert released

    # 7. Verify typed audit trail on Pulse Bus
    pulse_types = [p.type for p in published_pulses]
    assert "space.created" in pulse_types
    assert "resource.requested" in pulse_types
    assert "resource.granted" in pulse_types
    assert "worker.tool.called" in pulse_types
    assert "worker.tool.succeeded" in pulse_types
    assert "resource.released" in pulse_types


def test_e2e_pipeline_denies_unadmitted_or_unleased_execution() -> None:
    """Demonstrates that a Worker cannot execute without valid Space lease and identity."""
    bus = PulseBus()
    space_id = "space-e2e-2"
    rm = ResourceManager(bus=bus)

    worker = PythonWorker(
        identity=WorkerIdentity("python-worker-02", "python.eval_sandboxed", space_id),
        bus=bus,
        resource_manager=rm,
    )

    # Attempt 1: Bypassing lease
    unleased_req = ExecutionRequest(
        request_id="req-e2e-no-lease",
        correlation_id="corr-e2e-2",
        space_id=space_id,
        worker_id="python-worker-02",
        capability="python.eval_sandboxed",
        lease_id=None,
        arguments={"code": "print('fail')"},
    )
    res1 = worker.execute(unleased_req)
    assert not res1.is_success
    assert res1.status == "denied"
    assert res1.error is not None
    assert "missing" in res1.error.message.lower()

    # Attempt 2: Bypassing Space boundary (request from space-evil)
    cross_req = ExecutionRequest(
        request_id="req-e2e-cross",
        correlation_id="corr-e2e-3",
        space_id="space-evil",
        worker_id="python-worker-02",
        capability="python.eval_sandboxed",
        lease_id="fake",
        arguments={"code": "print('fail')"},
    )
    res2 = worker.execute(cross_req)
    assert not res2.is_success
    assert res2.status == "denied"
    assert res2.error is not None
    assert "cross-space" in res2.error.message.lower()
