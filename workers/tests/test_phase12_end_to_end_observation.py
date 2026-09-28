"""Phase 12.5 Execution Observation, Evidence Verification & DAG Dependency Unblocking.

Authoritative Specification: docs/PHASE_12_EXECUTION_ENGINE_SPEC.md (§9, §10, §15, §16)
Authoritative Architectural Decision: adr/0041-autonomous-task-dispatcher-dag-traversal-and-plan-convergence-engine.md
SCCA Laws:
    - Law 1: Everything Happens Inside a Space
    - Law 3: Components Communicate Through Pulses
    - Law 6: Failures Are Contained, Escalated, and Never Silent
Core Boundary: AGENTS.md §7 (Deterministic Core Independence)

Verifies genuine worker execution observation, artifact cryptographic hash verification,
DAG dependency unblocking via Kernel CAS, and the full 17-point Security Battery & Chaos tests.
"""

from __future__ import annotations

import hashlib
import tempfile
import threading
from pathlib import Path
from typing import Any

from ryu.pulse_bus.bus import PulseBus
from ryu.pulse_bus.pulse import Pulse

from core.orchestrator.dispatch_model import (
    DeterministicDispatcher,
    EvidenceStatus,
    TaskExecutionResult,
)
from core.plans.delta import PlanDelta
from core.resources.identity import Resource, ResourceIdentity
from core.resources.manager import ResourceManager
from core.resources.store import InMemoryResourceStore
from core.space.kernel import SpaceKernel
from workers.invoker import RuntimeWorkerInvoker


class SpyPulseBus(PulseBus):
    """In-memory bus recording all pulses for audit verification."""

    def __init__(self) -> None:
        super().__init__()
        self.published: list[Pulse] = []
        self._lock = threading.Lock()

    def publish(self, pulse: Pulse) -> Pulse:
        with self._lock:
            self.published.append(pulse)
        return pulse

    def find_by_type(self, pulse_type: str) -> list[Pulse]:
        with self._lock:
            return [p for p in self.published if p.type == pulse_type]


def _setup_env(
    space_id: str = "space-obs-e2e",
    owner_id: str = "owner-e2e",
    budget: float = 100.0,
    policy: str = "hard_stop",
) -> tuple[SpyPulseBus, SpaceKernel, ResourceManager, DeterministicDispatcher, RuntimeWorkerInvoker]:
    bus = SpyPulseBus()
    kernel = SpaceKernel(
        space_id=space_id,
        owner_id=owner_id,
        bus=bus,
        budget=budget,
        budget_policy=policy,
    )
    res_mgr = ResourceManager(bus=bus, store=InMemoryResourceStore())
    invoker = RuntimeWorkerInvoker(bus=bus, resource_manager=res_mgr)
    dispatcher = DeterministicDispatcher()
    return bus, kernel, res_mgr, dispatcher, invoker


def _add_task(
    kernel: SpaceKernel,
    task_id: str,
    capability: str = "code.python",
    state: str = "ready",
    dependencies: list[str] | None = None,
    params: dict[str, Any] | None = None,
) -> int:
    cur_ver = kernel.get_plan_version()
    delta = PlanDelta(
        space_id=kernel.space_id,
        base_version=cur_ver,
        resulting_version=cur_ver + 1,
        ops=[
            {
                "op": "add",
                "target_node_id": task_id,
                "capability": capability,
                "params": params or {},
                "dependencies": dependencies or [],
                "state": state,
            }
        ],
    )
    ok, new_ver, _ = kernel.commit_plan_delta(delta, proposal_id=f"add-{task_id}")
    assert ok, f"Failed to add task '{task_id}'"
    return new_ver


# ============================================================================
# 1. Genuine Execution & Observation End-to-End Tests
# ============================================================================


def test_e2e_python_execution_and_observation() -> None:
    """Genuine Python execution observed, verified, and transitioned to COMPLETED."""
    bus, kernel, res_mgr, dispatcher, invoker = _setup_env(space_id="sp-py-e2e")
    _add_task(
        kernel,
        "task-py",
        capability="python.eval_sandboxed",
        params={"code": "x = 21 * 2\nprint(f'RESULT={x}')\nresult = x"},
    )

    ident = ResourceIdentity("compute", "host-1", "core-0")
    res_mgr.register_resource(Resource(identity=ident, space_id=kernel.space_id, total_capacity=100))

    comp_res = dispatcher.execute_task_full_pipeline(
        kernel=kernel,
        resource_mgr=res_mgr,
        task_id="task-py",
        resource_identity=ident,
        invoker=invoker,
        units=10,
    )

    assert comp_res.completed is True
    assert comp_res.status == "completed"
    assert comp_res.terminal_state == "completed"
    assert comp_res.verification is not None
    assert comp_res.verification.is_valid is True

    # Pulses emitted: task.started, task.completed
    assert len(bus.find_by_type("task.started")) == 1
    assert len(bus.find_by_type("task.completed")) == 1

    # Authoritative kernel plan updated
    node = kernel.get_task_graph().get_node("task-py")
    assert node is not None
    assert node.state == "completed"


def test_e2e_python_artifact_generation_and_unblocking() -> None:
    """Task-A generates physical file artifact on disk; Dispatcher verifies SHA-256, completes Task-A, and unblocks Task-B to READY."""
    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)
        space_id = "sp-art-dag"
        bus, kernel, res_mgr, dispatcher, invoker = _setup_env(space_id=space_id)

        # Set invoker base working dir for space
        invoker.base_working_dir = base / space_id

        # Task-A: writes artifact 'calc_summary.txt'
        _add_task(
            kernel,
            "task-writer",
            capability="python.eval_sandboxed",
            params={
                "code": (
                    "import hashlib\n"
                    "content = 'RYU_PHASE_12_5_ARTIFACT_OK'\n"
                    "with open('calc_summary.txt', 'w') as f:\n"
                    "    f.write(content)\n"
                ),
            },
        )
        # Task-B: depends on Task-A
        _add_task(
            kernel,
            "task-reader",
            capability="python.eval_sandboxed",
            state="pending",
            dependencies=["task-writer"],
        )

        ident = ResourceIdentity("compute", "host-1", "core-0")
        res_mgr.register_resource(Resource(identity=ident, space_id=space_id, total_capacity=100))

        # Execute Task-A
        comp_res = dispatcher.execute_task_full_pipeline(
            kernel=kernel,
            resource_mgr=res_mgr,
            task_id="task-writer",
            resource_identity=ident,
            invoker=invoker,
            units=10,
            base_dir=base,
        )

        assert comp_res.completed is True
        assert "task-reader" in comp_res.unblocked_tasks

        # Verify physical file existence in space sandbox directory
        space_root = base / space_id
        created_file = space_root / "calc_summary.txt"
        assert created_file.exists()
        actual_sha = hashlib.sha256(created_file.read_bytes()).hexdigest()
        expected_sha = hashlib.sha256(b"RYU_PHASE_12_5_ARTIFACT_OK").hexdigest()
        assert actual_sha == expected_sha

        # Verify DAG state in SpaceKernel
        graph = kernel.get_task_graph()
        n_w = graph.get_node("task-writer")
        n_r = graph.get_node("task-reader")
        assert n_w is not None and n_w.state == "completed"
        assert n_r is not None and n_r.state == "ready"


def test_e2e_shell_execution_and_observation() -> None:
    """Genuine Shell execution observed, telemetry verified, and completed."""
    bus, kernel, res_mgr, dispatcher, invoker = _setup_env(space_id="sp-shell-e2e")
    _add_task(
        kernel,
        "task-sh",
        capability="terminal.exec",
        params={"command": ["python", "-c", "print('SHELL_OBSERVED_P12_5')"]},
    )

    ident = ResourceIdentity("compute", "host-1", "core-0")
    res_mgr.register_resource(Resource(identity=ident, space_id=kernel.space_id, total_capacity=100))

    comp_res = dispatcher.execute_task_full_pipeline(
        kernel=kernel,
        resource_mgr=res_mgr,
        task_id="task-sh",
        resource_identity=ident,
        invoker=invoker,
        units=10,
    )

    assert comp_res.completed is True
    assert comp_res.status == "completed"
    assert comp_res.terminal_state == "completed"


# ============================================================================
# 2. Mandatory 17-Point Security Battery (Step 21)
# ============================================================================


def test_sec_01_missing_evidence() -> None:
    """SEC-01: Required artifact declared in task contract is missing -> rejected."""
    _, kernel, _, dispatcher, _ = _setup_env(space_id="sp-sec-1")
    _add_task(
        kernel,
        "t1",
        state="observing",
        params={"required_artifacts": ["non_existent_file.bin"]},
    )

    exec_res = TaskExecutionResult(
        request_id="req-1",
        status="ok",
        task_id="t1",
        space_id="sp-sec-1",
        plan_version=kernel.get_plan_version(),
        artifacts=[],
    )

    res = dispatcher.observe_and_evaluate_task(kernel, "t1", exec_res)
    assert res.completed is False
    assert res.status == "failed"
    assert res.verification is not None
    assert res.verification.status == EvidenceStatus.MISSING


def test_sec_02_malformed_evidence() -> None:
    """SEC-02: Structured output missing mandatory keys -> rejected."""
    _, kernel, _, dispatcher, _ = _setup_env(space_id="sp-sec-2")
    _add_task(
        kernel,
        "t1",
        state="observing",
        params={"required_output_keys": ["score", "threshold"]},
    )

    exec_res = TaskExecutionResult(
        request_id="req-2",
        status="ok",
        task_id="t1",
        space_id="sp-sec-2",
        plan_version=kernel.get_plan_version(),
        output_data={"score": 85},  # Missing threshold
    )

    res = dispatcher.observe_and_evaluate_task(kernel, "t1", exec_res)
    assert res.completed is False
    assert res.status == "failed"
    assert res.verification is not None
    assert res.verification.status == EvidenceStatus.MISSING


def test_sec_03_forged_evidence() -> None:
    """SEC-03: Forged artifact SHA-256 digest is rejected as TAMPERED."""
    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)
        space_dir = base / "sp-sec-3"
        space_dir.mkdir(parents=True, exist_ok=True)
        target_file = space_dir / "evidence.txt"
        target_file.write_text("REAL_DATA", encoding="utf-8")

        _, kernel, _, dispatcher, _ = _setup_env(space_id="sp-sec-3")
        _add_task(kernel, "t1", state="observing")

        forged_sha = "0000000000000000000000000000000000000000000000000000000000000000"

        exec_res = TaskExecutionResult(
            request_id="req-3",
            status="ok",
            task_id="t1",
            space_id="sp-sec-3",
            plan_version=kernel.get_plan_version(),
            artifacts=[{"name": "evidence.txt", "path": "evidence.txt", "sha256": forged_sha, "space_id": "sp-sec-3"}],
        )

        res = dispatcher.observe_and_evaluate_task(kernel, "t1", exec_res, base_dir=base)
        assert res.completed is False
        assert res.status == "failed"
        assert res.verification is not None
        assert res.verification.status == EvidenceStatus.TAMPERED


def test_sec_04_artifact_hash_mismatch() -> None:
    """SEC-04: Modified artifact bytes produce hash mismatch -> rejected."""
    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)
        space_dir = base / "sp-sec-4"
        space_dir.mkdir(parents=True, exist_ok=True)
        f = space_dir / "log.txt"
        f.write_text("MODIFIED_BYTES", encoding="utf-8")

        _, kernel, _, dispatcher, _ = _setup_env(space_id="sp-sec-4")
        _add_task(kernel, "t1", state="observing")

        original_sha = hashlib.sha256(b"ORIGINAL_BYTES").hexdigest()

        exec_res = TaskExecutionResult(
            request_id="req-4",
            status="ok",
            task_id="t1",
            space_id="sp-sec-4",
            plan_version=kernel.get_plan_version(),
            artifacts=[{"name": "log.txt", "path": "log.txt", "sha256": original_sha, "space_id": "sp-sec-4"}],
        )

        res = dispatcher.observe_and_evaluate_task(kernel, "t1", exec_res, base_dir=base)
        assert res.completed is False
        assert res.verification is not None
        assert res.verification.status == EvidenceStatus.TAMPERED


def test_sec_05_artifact_traversal() -> None:
    """SEC-05: Artifact with '..' directory traversal rejected."""
    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)
        _, kernel, _, dispatcher, _ = _setup_env(space_id="sp-sec-5")
        _add_task(kernel, "t1", state="observing")

        exec_res = TaskExecutionResult(
            request_id="req-5",
            status="ok",
            task_id="t1",
            space_id="sp-sec-5",
            plan_version=kernel.get_plan_version(),
            artifacts=[{"name": "../../outside.txt", "path": "../../outside.txt", "sha256": "abc", "space_id": "sp-sec-5"}],
        )

        res = dispatcher.observe_and_evaluate_task(kernel, "t1", exec_res, base_dir=base)
        assert res.completed is False
        assert res.verification is not None
        assert res.verification.status == EvidenceStatus.TAMPERED
        assert any("traversal" in r for r in res.verification.failure_reasons)


def test_sec_06_cross_space_artifact() -> None:
    """SEC-06: Artifact claiming another Space is rejected."""
    _, kernel, _, dispatcher, _ = _setup_env(space_id="sp-sec-6")
    _add_task(kernel, "t1", state="observing")

    exec_res = TaskExecutionResult(
        request_id="req-6",
        status="ok",
        task_id="t1",
        space_id="sp-sec-6",
        plan_version=kernel.get_plan_version(),
        artifacts=[{"name": "data.bin", "path": "data.bin", "sha256": "abc", "space_id": "sp-rogue"}],
    )

    res = dispatcher.observe_and_evaluate_task(kernel, "t1", exec_res)
    assert res.completed is False
    assert res.verification is not None
    assert res.verification.status == EvidenceStatus.MISMATCHED


def test_sec_07_wrong_task_result() -> None:
    """SEC-07: Worker result for task-other presented for task-1 -> rejected."""
    _, kernel, _, dispatcher, _ = _setup_env(space_id="sp-sec-7")
    _add_task(kernel, "t1", state="observing")

    exec_res = TaskExecutionResult(
        request_id="req-7",
        status="ok",
        task_id="task-impersonator",
        space_id="sp-sec-7",
        plan_version=kernel.get_plan_version(),
    )

    res = dispatcher.observe_and_evaluate_task(kernel, "t1", exec_res)
    assert res.completed is False
    assert res.verification is not None
    assert res.verification.status == EvidenceStatus.MISMATCHED


def test_sec_08_wrong_plan_version() -> None:
    """SEC-08: Stale plan version result is rejected."""
    _, kernel, _, dispatcher, _ = _setup_env(space_id="sp-sec-8")
    _add_task(kernel, "t1", state="observing")

    exec_res = TaskExecutionResult(
        request_id="req-8",
        status="ok",
        task_id="t1",
        space_id="sp-sec-8",
        plan_version=99,
    )

    res = dispatcher.observe_and_evaluate_task(kernel, "t1", exec_res)
    assert res.completed is False
    assert res.verification is not None
    assert res.verification.status == EvidenceStatus.MISMATCHED


def test_sec_09_wrong_execution_attempt() -> None:
    """SEC-09: Task attempt mismatch rejected."""
    _, kernel, _, dispatcher, _ = _setup_env(space_id="sp-sec-9")
    _add_task(kernel, "t1", state="observing")

    # Result presented for attempt 2, while task attempt in graph is 1
    exec_res = TaskExecutionResult(
        request_id="req-9",
        status="ok",
        task_id="t1",
        space_id="sp-sec-9",
        plan_version=kernel.get_plan_version(),
        details={"attempt": 2},
    )

    res = dispatcher.observe_and_evaluate_task(kernel, "t1", exec_res)
    assert res.completed is False
    assert res.verification is not None
    assert res.verification.status == EvidenceStatus.MISMATCHED
    assert any("attempt mismatch" in r.lower() for r in res.verification.failure_reasons)


def test_sec_10_duplicate_result() -> None:
    """SEC-10: Submitting the same result twice returns cached=True."""
    _, kernel, _, dispatcher, _ = _setup_env(space_id="sp-sec-10")
    _add_task(kernel, "t1", state="observing")

    exec_res = TaskExecutionResult(
        request_id="req-10",
        status="ok",
        task_id="t1",
        space_id="sp-sec-10",
        plan_version=kernel.get_plan_version(),
    )

    res1 = dispatcher.observe_and_evaluate_task(kernel, "t1", exec_res)
    assert res1.completed is True

    res2 = dispatcher.observe_and_evaluate_task(kernel, "t1", exec_res)
    assert res2.completed is True
    assert res2.cached is True


def test_sec_11_duplicate_completion() -> None:
    """SEC-11: Attempting to complete an already completed task is idempotent."""
    _, kernel, _, dispatcher, _ = _setup_env(space_id="sp-sec-11")
    _add_task(kernel, "t1", state="completed")

    exec_res = TaskExecutionResult(
        request_id="req-11",
        status="ok",
        task_id="t1",
        space_id="sp-sec-11",
        plan_version=kernel.get_plan_version(),
    )

    res = dispatcher.observe_and_evaluate_task(kernel, "t1", exec_res)
    assert res.completed is True
    assert res.cached is True
    assert res.terminal_state == "completed"


def test_sec_12_tainted_result() -> None:
    """SEC-12: Tainted result produces tainted evidence and pulse, preserving taint boundary."""
    bus, kernel, _, dispatcher, _ = _setup_env(space_id="sp-sec-12")
    _add_task(kernel, "t1", state="observing")

    exec_res = TaskExecutionResult(
        request_id="req-12",
        status="ok",
        task_id="t1",
        space_id="sp-sec-12",
        plan_version=kernel.get_plan_version(),
        taint=True,
    )

    res = dispatcher.observe_and_evaluate_task(kernel, "t1", exec_res)
    assert res.completed is True
    assert res.verification is not None
    assert res.verification.tainted is True

    completed_pulses = bus.find_by_type("task.completed")
    assert len(completed_pulses) == 1
    assert completed_pulses[0].taint is True


def test_sec_13_concurrent_completion() -> None:
    """SEC-13: Stale plan version execution result is rejected during concurrent updates."""
    _, kernel, _, dispatcher, _ = _setup_env(space_id="sp-sec-13")
    _add_task(kernel, "t1", state="observing")
    _add_task(kernel, "t2", state="observing")

    # t1 completes, advancing version
    exec_res_1 = TaskExecutionResult(
        request_id="req-13-1",
        status="ok",
        task_id="t1",
        space_id="sp-sec-13",
        plan_version=kernel.get_plan_version(),
    )
    dispatcher.observe_and_evaluate_task(kernel, "t1", exec_res_1)

    # t2 presents result computed at stale version 1
    exec_res_2 = TaskExecutionResult(
        request_id="req-13-2",
        status="ok",
        task_id="t2",
        space_id="sp-sec-13",
        plan_version=1,
    )
    res_2 = dispatcher.observe_and_evaluate_task(kernel, "t2", exec_res_2)
    assert res_2.completed is False
    assert res_2.status == "failed"


def test_sec_14_stale_plan_completion() -> None:
    """SEC-14: Calling observe on invalid state rejects cleanly."""
    _, kernel, _, dispatcher, _ = _setup_env(space_id="sp-sec-14")
    _add_task(kernel, "t1", state="pending")

    exec_res = TaskExecutionResult(
        request_id="req-14",
        status="ok",
        task_id="t1",
        space_id="sp-sec-14",
        plan_version=kernel.get_plan_version(),
    )

    res = dispatcher.observe_and_evaluate_task(kernel, "t1", exec_res)
    assert res.completed is False
    assert res.status == "rejected"
    assert "expected 'observing'" in str(res.error)


def test_sec_15_dependency_bypass() -> None:
    """SEC-15: Downstream task cannot unblock if upstream is not completed."""
    _, kernel, _, dispatcher, _ = _setup_env(space_id="sp-sec-15")
    _add_task(kernel, "t1", state="running")
    _add_task(kernel, "t2", state="pending", dependencies=["t1"])

    # Attempt to unblock dependencies for t1 while t1 is not completed
    ok, ver, unblocked, _, err = dispatcher.unblock_dependencies(kernel, "t1")
    assert ok is True
    assert unblocked == []
    n2 = kernel.get_task_graph().get_node("t2")
    assert n2 is not None and n2.state == "pending"


def test_sec_16_failed_dependency_unblocking() -> None:
    """SEC-16: When upstream task fails, downstream transitions to BLOCKED."""
    _, kernel, _, dispatcher, _ = _setup_env(space_id="sp-sec-16")
    _add_task(kernel, "t1", state="observing")
    _add_task(kernel, "t2", state="pending", dependencies=["t1"])

    exec_res = TaskExecutionResult(
        request_id="req-16",
        status="failed",
        task_id="t1",
        space_id="sp-sec-16",
        plan_version=kernel.get_plan_version(),
        error="Terminal hardware fault",
    )

    res = dispatcher.observe_and_evaluate_task(kernel, "t1", exec_res)
    assert res.completed is False
    assert "t2" in res.blocked_tasks
    n2 = kernel.get_task_graph().get_node("t2")
    assert n2 is not None and n2.state == "blocked"


def test_sec_17_replay_mismatch() -> None:
    """SEC-17: Replay mode rejects artifacts missing cryptographic hash."""
    _, kernel, _, dispatcher, _ = _setup_env(space_id="sp-sec-17")
    _add_task(kernel, "t1", state="observing")

    exec_res = TaskExecutionResult(
        request_id="req-17",
        status="ok",
        task_id="t1",
        space_id="sp-sec-17",
        plan_version=kernel.get_plan_version(),
        artifacts=[{"name": "dump.bin", "path": "dump.bin", "sha256": "", "space_id": "sp-sec-17"}],
    )

    res = dispatcher.observe_and_evaluate_task(kernel, "t1", exec_res, replay_mode=True)
    assert res.completed is False
    assert res.verification is not None
    assert res.verification.status == EvidenceStatus.MISSING


# ============================================================================
# 3. Chaos Tests (Step 22)
# ============================================================================


def test_chaos_worker_result_arrives_twice() -> None:
    """Worker result arrives twice (e.g. Redis redelivery); processed with zero duplicate CAS commits."""
    _, kernel, _, dispatcher, _ = _setup_env(space_id="sp-chaos-1")
    _add_task(kernel, "t1", state="observing")

    exec_res = TaskExecutionResult(
        request_id="req-chaos-1",
        status="ok",
        task_id="t1",
        space_id="sp-chaos-1",
        plan_version=kernel.get_plan_version(),
    )

    res1 = dispatcher.observe_and_evaluate_task(kernel, "t1", exec_res)
    assert res1.completed is True
    version_after_first = res1.plan_version

    # Redelivery
    res2 = dispatcher.observe_and_evaluate_task(kernel, "t1", exec_res)
    assert res2.completed is True
    assert res2.cached is True
    assert res2.plan_version == version_after_first


def test_chaos_artifact_appears_twice() -> None:
    """Duplicate artifact entries in worker result handled deterministically."""
    _, kernel, _, dispatcher, _ = _setup_env(space_id="sp-chaos-2")
    _add_task(kernel, "t1", state="observing")

    exec_res = TaskExecutionResult(
        request_id="req-chaos-2",
        status="ok",
        task_id="t1",
        space_id="sp-chaos-2",
        plan_version=kernel.get_plan_version(),
        artifacts=[
            {"name": "rep.txt", "path": "rep.txt", "sha256": "h123", "space_id": "sp-chaos-2"},
            {"name": "rep.txt", "path": "rep.txt", "sha256": "h123", "space_id": "sp-chaos-2"},
        ],
    )

    res = dispatcher.observe_and_evaluate_task(kernel, "t1", exec_res, replay_mode=True)
    assert res.completed is True
    assert res.verification is not None
    assert len(res.verification.evidence_items) >= 2


def test_chaos_completion_interrupted_recovery() -> None:
    """Simulate recovery: node in completed state with downstream tasks ready can be rehydrated safely."""
    _, kernel, _, dispatcher, _ = _setup_env(space_id="sp-chaos-3")
    _add_task(kernel, "t1", state="observing")
    _add_task(kernel, "t2", state="pending", dependencies=["t1"])

    exec_res = TaskExecutionResult(
        request_id="req-chaos-3",
        status="ok",
        task_id="t1",
        space_id="sp-chaos-3",
        plan_version=kernel.get_plan_version(),
    )

    res = dispatcher.observe_and_evaluate_task(kernel, "t1", exec_res)
    assert res.completed is True

    # Re-run dependency unblocking explicitly; already ready tasks remain untouched
    ok, ver, unblocked, _, _ = dispatcher.unblock_dependencies(kernel, "t1")
    assert ok is True
    assert unblocked == []  # Already unblocked
