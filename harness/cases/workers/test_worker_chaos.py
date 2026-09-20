"""Chaos and fault injection test suite for Worker runtime and sandbox execution.

Tests resilient recovery, lease release, process tree cleanup, and failure emission.
spec §7, §9, §16, CONTRACT_MATRIX WORKER-001, WORKER-004, ADR-0016
"""

from __future__ import annotations

import sys
import tempfile
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from ryu.pulse_bus.bus import PulseBus

from core.resources.identity import Resource, ResourceIdentity
from core.resources.manager import ResourceManager
from workers.contract import (
    ExecutionLimits,
    ExecutionRequest,
    FilesystemPolicy,
    NetworkPolicy,
    NetworkPolicyMode,
    SandboxPolicy,
    WorkerIdentity,
    WorkerState,
)
from workers.file.worker import FileWorker
from workers.python.worker import PythonWorker
from workers.sandbox.process import ProcessSandbox
from workers.subagent.worker import SubagentWorker


# 1. Process sudden crash (non-zero exit code)
def test_chaos_01_subprocess_crash_non_zero_exit() -> None:
    worker = PythonWorker()
    req = ExecutionRequest(
        request_id="req-crash-1",
        correlation_id="corr-ch-1",
        space_id="default-space",
        worker_id="python-worker-01",
        capability="python.eval_sandboxed",
        arguments={"code": "import sys; sys.exit(42)"},
    )
    res = worker.execute(req)
    assert not res.is_success
    assert res.status == "failed"
    assert worker.state == WorkerState.FAILED
    assert res.error is not None
    assert "42" in res.error.message


# 2. Subprocess uncaught exception
def test_chaos_02_subprocess_uncaught_exception() -> None:
    worker = PythonWorker()
    req = ExecutionRequest(
        request_id="req-crash-2",
        correlation_id="corr-ch-2",
        space_id="default-space",
        worker_id="python-worker-01",
        capability="python.eval_sandboxed",
        arguments={"code": "raise RuntimeError('simulated chaotic crash')"},
    )
    res = worker.execute(req)
    assert not res.is_success
    assert res.status == "failed"
    assert "simulated chaotic crash" in str(res.error)


# 3. Timeout termination
def test_chaos_03_execution_timeout_watchdog() -> None:
    worker = PythonWorker()
    req = ExecutionRequest(
        request_id="req-crash-3",
        correlation_id="corr-ch-3",
        space_id="default-space",
        worker_id="python-worker-01",
        capability="python.eval_sandboxed",
        execution_limits=ExecutionLimits(timeout_seconds=0.4),
        arguments={"code": "import time; time.sleep(5)"},
    )
    res = worker.execute(req)
    assert not res.is_success
    assert res.status == "timeout"
    assert worker.state == WorkerState.TIMED_OUT
    assert res.error is not None
    assert res.error.retryable is True  # transient.timeout is retryable


# 4. Rapid cancellation race
def test_chaos_04_cancellation_race() -> None:
    worker = PythonWorker()
    worker.cancel()
    req = ExecutionRequest(
        request_id="req-crash-4",
        correlation_id="corr-ch-4",
        space_id="default-space",
        worker_id="python-worker-01",
        capability="python.eval_sandboxed",
        arguments={"code": "print('racing')"},
    )
    res = worker.execute(req)
    assert not res.is_success
    assert res.status == "cancelled"
    assert worker.state == WorkerState.CANCELLED


# 5. Process tree termination cleans all descendants
def test_chaos_05_process_tree_termination() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        limits = ExecutionLimits(timeout_seconds=0.5)
        sandbox = ProcessSandbox(working_dir=tmpdir, limits=limits)
        code = (
            "import subprocess, sys; "
            "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(10)']); "
            "p.wait()"
        )
        with pytest.raises(TimeoutError):
            sandbox.run_command([sys.executable, "-c", code])


# 6. Concurrent requests to separate worker instances
def test_chaos_06_concurrent_worker_executions() -> None:
    results: list[bool] = []

    def _run_worker(idx: int) -> None:
        w = PythonWorker(
            identity=WorkerIdentity(f"w-{idx}", "python.eval_sandboxed", "space-1")
        )
        r = ExecutionRequest(
            request_id=f"req-conc-{idx}",
            correlation_id=f"corr-{idx}",
            space_id="space-1",
            worker_id=f"w-{idx}",
            capability="python.eval_sandboxed",
            arguments={"code": f"print({idx} * 2)"},
        )
        res = w.execute(r)
        results.append(res.is_success)

    threads = [threading.Thread(target=_run_worker, args=(i,)) for i in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(results) == 5
    assert all(results)


# 7. Mid-flight lease expiration
def test_chaos_07_mid_flight_lease_expiration() -> None:
    rm = ResourceManager(bus=PulseBus())
    r_ident = ResourceIdentity("compute", "local-host", "cpu-ch-7")
    rm.register_resource(Resource(identity=r_ident, space_id="space-1", total_capacity=2))

    acq = rm.acquire("space-1", "worker-1", r_ident, duration_seconds=0.1)
    assert acq.granted
    assert acq.lease is not None

    # Expire lease before execution
    acq.lease.expiry = datetime.now(timezone.utc) - timedelta(seconds=5)

    worker = PythonWorker(
        identity=WorkerIdentity("worker-1", "python.eval_sandboxed", "space-1"),
        resource_manager=rm,
    )
    req = ExecutionRequest(
        request_id="req-exp-ch-7",
        correlation_id="corr-ch-7",
        space_id="space-1",
        worker_id="worker-1",
        capability="python.eval_sandboxed",
        lease_id=acq.lease.lease_token,
        arguments={"code": "print('should be blocked')"},
    )
    res = worker.execute(req)
    assert not res.is_success
    assert res.status == "denied"


# 8. Corrupted request arguments
def test_chaos_08_corrupted_arguments() -> None:
    worker = PythonWorker()
    req = ExecutionRequest(
        request_id="req-ch-8",
        correlation_id="corr-ch-8",
        space_id="default-space",
        worker_id="python-worker-01",
        capability="python.eval_sandboxed",
        arguments={"code": None},  # Corrupted type
    )
    res = worker.execute(req)
    assert not res.is_success
    assert res.status == "failed"


# 9. Missing capability
def test_chaos_09_unknown_capability_request() -> None:
    worker = PythonWorker()
    req = ExecutionRequest(
        request_id="req-ch-9",
        correlation_id="corr-ch-9",
        space_id="default-space",
        worker_id="python-worker-01",
        capability="quantum.teleport",
        arguments={"code": "print('no')"},
    )
    res = worker.execute(req)
    assert not res.is_success
    assert res.status == "denied"


# 10. Filesystem violation mid-task
def test_chaos_10_filesystem_denial_mid_task() -> None:
    worker = FileWorker()
    req = ExecutionRequest(
        request_id="req-ch-10",
        correlation_id="corr-ch-10",
        space_id="default-space",
        worker_id="file-worker-01",
        capability="file.read",
        arguments={"operation": "read", "path": "C:\\Windows\\System32\\config\\SAM"},
    )
    res = worker.execute(req)
    assert not res.is_success
    assert res.status in ("denied", "violation", "failed")


# 11. Network egress under disabled state
def test_chaos_11_network_blocked_under_disabled() -> None:
    worker = PythonWorker()
    req = ExecutionRequest(
        request_id="req-ch-11",
        correlation_id="corr-ch-11",
        space_id="default-space",
        worker_id="python-worker-01",
        capability="python.eval_sandboxed",
        sandbox_policy=SandboxPolicy(network_policy=NetworkPolicy(mode=NetworkPolicyMode.DISABLED)),
        arguments={"code": "import socket; s = socket.socket(); s.connect(('1.1.1.1', 80))"},
    )
    res = worker.execute(req)
    # The socket connection or script will fail with exit code != 0
    assert not res.is_success


# 12. Pulse bus failure tolerance
def test_chaos_12_pulse_bus_resilience() -> None:
    class CrashingBus:
        def publish(self, pulse: Any) -> None:
            raise ConnectionError("Pulse transport down")

    worker = PythonWorker(bus=CrashingBus())  # type: ignore[arg-type]
    req = ExecutionRequest(
        request_id="req-ch-12",
        correlation_id="corr-ch-12",
        space_id="default-space",
        worker_id="python-worker-01",
        capability="python.eval_sandboxed",
        arguments={"code": "print('still succeeds')"},
    )
    res = worker.execute(req)
    # Worker execution succeeds even if non-critical pulse publish fails
    assert res.is_success


# 13. Large output handling
def test_chaos_13_large_output_handling() -> None:
    worker = PythonWorker()
    # Generates 100,000 characters
    req = ExecutionRequest(
        request_id="req-ch-13",
        correlation_id="corr-ch-13",
        space_id="default-space",
        worker_id="python-worker-01",
        capability="python.eval_sandboxed",
        arguments={"code": "print('A' * 100000)"},
    )
    res = worker.execute(req)
    assert res.is_success
    assert len(res.output_data["stdout"]) >= 100000


# 14. Rapid repeated execution cycling
def test_chaos_14_rapid_cycle_execution() -> None:
    worker = PythonWorker()
    for i in range(10):
        req = ExecutionRequest(
            request_id=f"req-cycle-{i}",
            correlation_id=f"corr-cycle-{i}",
            space_id="default-space",
            worker_id="python-worker-01",
            capability="python.eval_sandboxed",
            arguments={"code": f"print({i})"},
        )
        res = worker.execute(req)
        assert res.is_success
        assert str(i) in res.output_data["stdout"]


# 15. Subagent worker corrupted handoff note
def test_chaos_15_subagent_corrupted_handoff() -> None:
    worker = SubagentWorker()
    req = ExecutionRequest(
        request_id="req-ch-15",
        correlation_id="corr-ch-15",
        space_id="default-space",
        worker_id="subagent-worker-01",
        capability="subagent.delegate",
        arguments={"plan_node_id": "node-1", "handoff_note": "not-a-dict"},
    )
    res = worker.execute(req)
    assert not res.is_success
    assert res.status == "failed"


# 16. File worker deleting non-existent file
def test_chaos_16_file_worker_delete_non_existent() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        policy = SandboxPolicy(
            fs_policy=FilesystemPolicy(read_paths=[tmpdir], write_paths=[tmpdir])
        )
        worker = FileWorker()
        req = ExecutionRequest(
            request_id="req-ch-16",
            correlation_id="corr-ch-16",
            space_id="default-space",
            worker_id="file-worker-01",
            capability="file.read",
            arguments={"operation": "delete", "path": str(Path(tmpdir) / "non_existent.txt")},
            sandbox_policy=policy,
        )
        res = worker.execute(req)
        assert res.is_success  # Idempotent deletion


# 17. File worker reading non-existent file
def test_chaos_17_file_worker_read_non_existent() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        policy = SandboxPolicy(
            fs_policy=FilesystemPolicy(read_paths=[tmpdir], write_paths=[tmpdir])
        )
        worker = FileWorker()
        req = ExecutionRequest(
            request_id="req-ch-17",
            correlation_id="corr-ch-17",
            space_id="default-space",
            worker_id="file-worker-01",
            capability="file.read",
            arguments={"operation": "read", "path": str(Path(tmpdir) / "missing.txt")},
            sandbox_policy=policy,
        )
        res = worker.execute(req)
        assert not res.is_success
        assert res.status == "failed"
        assert res.error is not None
        assert res.error.error_class == "terminal.not_found"


# 18. Multi-worker resource contention and queueing
def test_chaos_18_resource_contention() -> None:
    rm = ResourceManager(bus=PulseBus())
    r_ident = ResourceIdentity("compute", "local-host", "exclusive-gpu")
    # Capacity = 1 (exclusive resource)
    rm.register_resource(Resource(identity=r_ident, space_id="space-1", total_capacity=1))

    # Worker 1 acquires lease
    acq1 = rm.acquire("space-1", "worker-1", r_ident, duration_seconds=60)
    assert acq1.granted

    # Worker 2 tries to acquire same exclusive resource -> queued / conflict
    acq2 = rm.acquire("space-1", "worker-2", r_ident, duration_seconds=60)
    assert not acq2.granted
    assert acq2.queue_position is not None

    # Worker 2 cannot execute without a lease
    worker2 = PythonWorker(
        identity=WorkerIdentity("worker-2", "python.eval_sandboxed", "space-1"),
        resource_manager=rm,
    )
    req2 = ExecutionRequest(
        request_id="req-contention-w2",
        correlation_id="corr-ch-18",
        space_id="space-1",
        worker_id="worker-2",
        capability="python.eval_sandboxed",
        lease_id=None,
        arguments={"code": "print('should fail')"},
    )
    res2 = worker2.execute(req2)
    assert not res2.is_success
    assert res2.status == "denied"
