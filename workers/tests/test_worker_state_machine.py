"""Unit tests for Worker deterministic state machine and lifecycle transitions.

spec §7, §16, CONTRACT_MATRIX WORKER-001, ADR-0013, ADR-0016
"""

import uuid

from ryu.pulse_bus.bus import PulseBus

from workers.contract import (
    ExecutionRequest,
    WorkerIdentity,
    WorkerState,
)
from workers.python.worker import PythonWorker


def test_worker_initial_state() -> None:
    ident = WorkerIdentity(
        worker_id="test-worker-01",
        capability="python.eval_sandboxed",
        space_id="space-1",
    )
    worker = PythonWorker(identity=ident)
    assert worker.state == WorkerState.READY
    assert worker.worker_id == "test-worker-01"
    assert worker.space_id == "space-1"
    assert worker.capability == "python.eval_sandboxed"


def test_worker_successful_execution_lifecycle() -> None:
    bus = PulseBus()
    ident = WorkerIdentity(
        worker_id="test-worker-02",
        capability="python.eval_sandboxed",
        space_id="space-1",
    )
    worker = PythonWorker(identity=ident, bus=bus)

    req = ExecutionRequest(
        request_id=f"req-{uuid.uuid4().hex[:8]}",
        correlation_id="corr-1",
        space_id="space-1",
        worker_id="test-worker-02",
        capability="python.eval_sandboxed",
        arguments={"code": "print('lifecycle test')"},
    )

    res = worker.execute(req)
    assert res.is_success
    assert worker.state == WorkerState.COMPLETED
    assert res.output_data["exit_code"] == 0
    assert "lifecycle test" in res.output_data["stdout"]


def test_worker_cross_space_denial_lifecycle() -> None:
    bus = PulseBus()
    ident = WorkerIdentity(
        worker_id="test-worker-03",
        capability="python.eval_sandboxed",
        space_id="space-A",
    )
    worker = PythonWorker(identity=ident, bus=bus)

    req = ExecutionRequest(
        request_id="req-cross-space",
        correlation_id="corr-2",
        space_id="space-B",  # Mismatched space
        worker_id="test-worker-03",
        capability="python.eval_sandboxed",
        arguments={"code": "print('should fail')"},
    )

    res = worker.execute(req)
    assert not res.is_success
    assert res.status == "denied"
    assert worker.state == WorkerState.FAILED
    assert res.error is not None
    assert "cross-space" in res.error.message.lower()


def test_worker_capability_mismatch_lifecycle() -> None:
    bus = PulseBus()
    ident = WorkerIdentity(
        worker_id="test-worker-04",
        capability="python.eval_sandboxed",
        space_id="space-1",
    )
    worker = PythonWorker(identity=ident, bus=bus)

    req = ExecutionRequest(
        request_id="req-mismatch",
        correlation_id="corr-3",
        space_id="space-1",
        worker_id="test-worker-04",
        capability="terminal.exec",  # Mismatch capability
        arguments={"command": "echo test"},
    )

    res = worker.execute(req)
    assert not res.is_success
    assert res.status == "denied"
    assert worker.state == WorkerState.FAILED
    assert res.error is not None
    assert "mismatch" in res.error.message.lower()


def test_worker_cancellation_lifecycle() -> None:
    ident = WorkerIdentity(
        worker_id="test-worker-05",
        capability="python.eval_sandboxed",
        space_id="space-1",
    )
    worker = PythonWorker(identity=ident)
    worker.cancel()

    req = ExecutionRequest(
        request_id="req-cancelled",
        correlation_id="corr-4",
        space_id="space-1",
        worker_id="test-worker-05",
        capability="python.eval_sandboxed",
        arguments={"code": "import time; time.sleep(1)"},
    )

    res = worker.execute(req)
    assert not res.is_success
    assert worker.state in (WorkerState.CANCELLED, WorkerState.TIMED_OUT)
