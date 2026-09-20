"""Unit tests for Worker lease validation and enforcement.

spec §7, §9, §16, CONTRACT_MATRIX RESOURCE-002, RESOURCE-003, ADR-0013
"""

from ryu.pulse_bus.bus import PulseBus

from core.resources.identity import Resource, ResourceIdentity
from core.resources.manager import ResourceManager
from workers.contract import ExecutionRequest, WorkerIdentity, WorkerState
from workers.python.worker import PythonWorker


def _setup_manager(
    space_id: str = "space-lease-1",
) -> tuple[ResourceManager, PulseBus, ResourceIdentity]:
    bus = PulseBus()
    rm = ResourceManager(bus=bus)
    ident = ResourceIdentity(
        resource_type="compute",
        provider_id="local-host",
        instance_id="cpu-1",
    )
    res = Resource(
        identity=ident,
        space_id=space_id,
        total_capacity=10,
        allocated_capacity=0,
    )
    rm.register_resource(res)
    return rm, bus, ident


def test_worker_valid_lease_allowed() -> None:
    space_id = "space-lease-1"
    rm, bus, r_ident = _setup_manager(space_id=space_id)
    worker_id = "worker-01"

    # Acquire valid lease
    res_acq = rm.acquire(
        space_id=space_id,
        requester_id=worker_id,
        identity=r_ident,
        duration_seconds=60,
    )
    assert res_acq.granted
    assert res_acq.lease is not None

    ident = WorkerIdentity(
        worker_id=worker_id,
        capability="python.eval_sandboxed",
        space_id=space_id,
    )
    worker = PythonWorker(identity=ident, bus=bus, resource_manager=rm)

    req = ExecutionRequest(
        request_id="req-lease-valid",
        correlation_id="corr-1",
        space_id=space_id,
        worker_id=worker_id,
        capability="python.eval_sandboxed",
        lease_id=res_acq.lease.lease_token,
        arguments={"code": "print('lease ok')"},
    )

    result = worker.execute(req)
    assert result.is_success
    assert worker.state == WorkerState.COMPLETED


def test_worker_missing_lease_rejected() -> None:
    space_id = "space-lease-2"
    rm, bus, _ = _setup_manager(space_id=space_id)
    worker_id = "worker-02"

    ident = WorkerIdentity(
        worker_id=worker_id,
        capability="python.eval_sandboxed",
        space_id=space_id,
    )
    worker = PythonWorker(identity=ident, bus=bus, resource_manager=rm)

    req = ExecutionRequest(
        request_id="req-lease-missing",
        correlation_id="corr-2",
        space_id=space_id,
        worker_id=worker_id,
        capability="python.eval_sandboxed",
        lease_id=None,  # Missing lease
        arguments={"code": "print('should fail')"},
    )

    result = worker.execute(req)
    assert not result.is_success
    assert result.status == "denied"
    assert result.error is not None
    assert "missing" in result.error.message.lower()


def test_worker_forged_lease_rejected() -> None:
    space_id = "space-lease-3"
    rm, bus, _ = _setup_manager(space_id=space_id)
    worker_id = "worker-03"

    ident = WorkerIdentity(
        worker_id=worker_id,
        capability="python.eval_sandboxed",
        space_id=space_id,
    )
    worker = PythonWorker(identity=ident, bus=bus, resource_manager=rm)

    req = ExecutionRequest(
        request_id="req-lease-forged",
        correlation_id="corr-3",
        space_id=space_id,
        worker_id=worker_id,
        capability="python.eval_sandboxed",
        lease_id="fake-forged-token-9999",
        arguments={"code": "print('should fail')"},
    )

    result = worker.execute(req)
    assert not result.is_success
    assert result.status == "denied"
    assert result.error is not None
    assert "not found" in result.error.message.lower()


def test_worker_cross_space_lease_rejected() -> None:
    space_A = "space-A"
    space_B = "space-B"
    rm, bus, r_ident = _setup_manager(space_id=space_A)
    worker_id = "worker-04"

    # Acquire lease in space_A
    res_acq = rm.acquire(
        space_id=space_A,
        requester_id=worker_id,
        identity=r_ident,
        duration_seconds=60,
    )
    assert res_acq.granted
    assert res_acq.lease is not None

    # Worker belongs to space_B
    ident = WorkerIdentity(
        worker_id=worker_id,
        capability="python.eval_sandboxed",
        space_id=space_B,
    )
    worker = PythonWorker(identity=ident, bus=bus, resource_manager=rm)

    req = ExecutionRequest(
        request_id="req-cross-lease",
        correlation_id="corr-4",
        space_id=space_B,
        worker_id=worker_id,
        capability="python.eval_sandboxed",
        lease_id=res_acq.lease.lease_token,
        arguments={"code": "print('should fail')"},
    )

    result = worker.execute(req)
    assert not result.is_success
    assert result.status == "denied"
    assert result.error is not None
    assert "cross-space" in result.error.message.lower()


def test_worker_released_lease_rejected() -> None:
    space_id = "space-lease-5"
    rm, bus, r_ident = _setup_manager(space_id=space_id)
    worker_id = "worker-05"

    res_acq = rm.acquire(
        space_id=space_id,
        requester_id=worker_id,
        identity=r_ident,
        duration_seconds=60,
    )
    assert res_acq.granted
    assert res_acq.lease is not None
    token = res_acq.lease.lease_token

    # Explicitly release lease
    rm.release(space_id=space_id, requester_id=worker_id, lease_token=token)

    ident = WorkerIdentity(
        worker_id=worker_id,
        capability="python.eval_sandboxed",
        space_id=space_id,
    )
    worker = PythonWorker(identity=ident, bus=bus, resource_manager=rm)

    req = ExecutionRequest(
        request_id="req-released-lease",
        correlation_id="corr-5",
        space_id=space_id,
        worker_id=worker_id,
        capability="python.eval_sandboxed",
        lease_id=token,
        arguments={"code": "print('should fail')"},
    )

    result = worker.execute(req)
    assert not result.is_success
    assert result.status == "denied"
