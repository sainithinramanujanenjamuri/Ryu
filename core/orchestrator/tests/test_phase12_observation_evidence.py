"""Unit tests for Phase 12.5: Execution Observation, Evidence Verification & DAG Dependency Unblocking.

Contracts: DISPATCH-001, DISPATCH-002, DISPATCH-003, DISPATCH-004, DISPATCH-005
Invariants:
- SCCA Law 1 (Everything happens inside a Space)
- SCCA Law 3 (Components communicate through Pulses)
- SCCA Law 6 (Failures are contained, escalated, and never silent)
- AGENTS.md §7 (Deterministic Core Independence — ZERO imports from workers/, agents/, etc.)
"""

from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path

from core.orchestrator.dispatch_model import (
    DeterministicDispatcher,
    EvidenceStatus,
    EvidenceType,
    TaskExecutionResult,
    VerifiedExecutionEvidence,
)
from core.plans.task_graph import TaskNode
from core.space.kernel import SpaceKernel


def _setup_kernel_and_graph(
    space_id: str = "sp-obs-1",
    nodes: list[TaskNode] | None = None,
) -> tuple[SpaceKernel, DeterministicDispatcher]:
    """Helper to initialize SpaceKernel with an initial TaskGraph."""
    kernel = SpaceKernel(space_id=space_id, owner_id="owner-1")
    init_nodes = nodes or [TaskNode(id="t1", capability="compute.unit", state="observing")]
    kernel.plan_store.init_space_plan(space_id, init_nodes)
    dispatcher = DeterministicDispatcher()
    return kernel, dispatcher


# ============================================================================
# 1. Evidence Models & Enums
# ============================================================================


def test_evidence_status_and_type_enums() -> None:
    assert EvidenceStatus.UNSEEN.value == "unseen"
    assert EvidenceStatus.COLLECTED.value == "collected"
    assert EvidenceStatus.VERIFIED.value == "verified"
    assert EvidenceStatus.INVALID.value == "invalid"
    assert EvidenceStatus.TAMPERED.value == "tampered"
    assert EvidenceStatus.MISMATCHED.value == "mismatched"
    assert EvidenceStatus.MISSING.value == "missing"
    assert EvidenceStatus.UNTRUSTED.value == "untrusted"

    assert EvidenceType.ARTIFACT.value == "artifact"
    assert EvidenceType.STRUCTURED_OUTPUT.value == "structured_output"
    assert EvidenceType.SIGNED_TOOL_OUTPUT.value == "signed_tool_output"
    assert EvidenceType.PROCESS_EXIT.value == "process_exit"
    assert EvidenceType.TELEMETRY.value == "telemetry"


def test_verified_execution_evidence_model() -> None:
    ev = VerifiedExecutionEvidence(
        task_id="t1",
        evidence_type="artifact",
        verified=True,
        sha256="abcdef123456",
        duration_seconds=1.5,
        status="verified",
    )
    assert ev.is_verified is True
    assert ev.task_id == "t1"
    assert ev.sha256 == "abcdef123456"

    ev_unverified = VerifiedExecutionEvidence(
        task_id="t1",
        evidence_type="artifact",
        verified=False,
        status="invalid",
    )
    assert ev_unverified.is_verified is False


# ============================================================================
# 2. Artifact Path Safety & SHA-256 Verification
# ============================================================================


def test_artifact_path_safety_clean() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)
        safe, res_path, err = DeterministicDispatcher._is_safe_artifact_path(
            "output/report.txt", base_dir=base, space_id="sp-1"
        )
        assert safe is True
        assert err is None
        assert res_path is not None
        assert res_path.resolve() == (base / "artifacts" / "sp-1" / "output" / "report.txt").resolve()


def test_artifact_path_traversal_rejection() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)
        safe, res_path, err = DeterministicDispatcher._is_safe_artifact_path(
            "../../etc/passwd", base_dir=base, space_id="sp-1"
        )
        assert safe is False
        assert "Path traversal forbidden" in str(err)


def test_artifact_path_outside_sandbox_rejection() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)
        other_dir = Path(tempfile.gettempdir()) / "outside_sandbox.txt"
        safe, res_path, err = DeterministicDispatcher._is_safe_artifact_path(
            str(other_dir), base_dir=base, space_id="sp-1"
        )
        assert safe is False
        assert "escapes space sandbox boundary" in str(err)


def test_artifact_sha256_verification_match() -> None:
    with tempfile.NamedTemporaryFile("w+", delete=False, encoding="utf-8") as f:
        f.write("RYU_PHASE_12_5_EVIDENCE_PAYLOAD")
        f_path = Path(f.name)

    try:
        expected_sha = hashlib.sha256("RYU_PHASE_12_5_EVIDENCE_PAYLOAD".encode("utf-8")).hexdigest()
        ok, actual_sha, err = DeterministicDispatcher._verify_artifact_sha256(f_path, expected_sha)
        assert ok is True
        assert actual_sha == expected_sha
        assert err is None
    finally:
        if f_path.exists():
            f_path.unlink()


def test_artifact_sha256_verification_mismatch() -> None:
    with tempfile.NamedTemporaryFile("w+", delete=False, encoding="utf-8") as f:
        f.write("MODIFIED_CONTENT")
        f_path = Path(f.name)

    try:
        forged_sha = hashlib.sha256(b"ORIGINAL_CONTENT").hexdigest()
        ok, actual_sha, err = DeterministicDispatcher._verify_artifact_sha256(f_path, forged_sha)
        assert ok is False
        assert "SHA-256 mismatch" in str(err)
    finally:
        if f_path.exists():
            f_path.unlink()


# ============================================================================
# 3. Observation, Verification & Task Completion (Lifecycle Progression)
# ============================================================================


def test_observe_and_evaluate_success_no_artifacts() -> None:
    kernel, dispatcher = _setup_kernel_and_graph(space_id="sp-test", nodes=[
        TaskNode(id="t1", capability="compute.unit", state="observing")
    ])

    exec_res = TaskExecutionResult(
        request_id="req-t1-1",
        status="ok",
        task_id="t1",
        space_id="sp-test",
        plan_version=1,
        output_data={"result": 42},
        duration_seconds=0.25,
        details={"exit_code": 0},
    )

    res = dispatcher.observe_and_evaluate_task(
        kernel=kernel,
        task_id="t1",
        execution_result=exec_res,
    )

    assert res.completed is True
    assert res.status == "completed"
    assert res.terminal_state == "completed"
    assert res.verification is not None
    assert res.verification.is_valid is True

    # Check authoritative graph state in SpaceKernel
    graph = kernel.get_task_graph()
    node = graph.get_node("t1")
    assert node is not None
    assert node.state == "completed"
    assert node.result_ref == "req-t1-1"


def test_observe_and_evaluate_with_verified_artifact() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)
        space_dir = base / "sp-art"
        space_dir.mkdir(parents=True, exist_ok=True)
        art_file = space_dir / "data.csv"
        art_content = b"col1,col2\nval1,val2\n"
        art_file.write_bytes(art_content)
        art_sha = hashlib.sha256(art_content).hexdigest()

        kernel, dispatcher = _setup_kernel_and_graph(space_id="sp-art", nodes=[
            TaskNode(id="t1", capability="file.write", state="observing")
        ])

        exec_res = TaskExecutionResult(
            request_id="req-t1-art",
            status="ok",
            task_id="t1",
            space_id="sp-art",
            plan_version=1,
            artifacts=[{"name": "data.csv", "path": "data.csv", "sha256": art_sha, "space_id": "sp-art"}],
            duration_seconds=0.1,
            details={"exit_code": 0},
        )

        res = dispatcher.observe_and_evaluate_task(
            kernel=kernel,
            task_id="t1",
            execution_result=exec_res,
            base_dir=base,
        )

        assert res.completed is True
        assert res.status == "completed"
        assert res.verification is not None
        assert res.verification.status == EvidenceStatus.VERIFIED


def test_observe_and_evaluate_tampered_artifact_fails() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)
        space_dir = base / "sp-tamper"
        space_dir.mkdir(parents=True, exist_ok=True)
        art_file = space_dir / "file.txt"
        art_file.write_text("TAMPERED_CONTENT", encoding="utf-8")

        forged_sha = hashlib.sha256(b"AUTHENTIC_CONTENT").hexdigest()

        kernel, dispatcher = _setup_kernel_and_graph(space_id="sp-tamper", nodes=[
            TaskNode(id="t1", capability="file.write", state="observing")
        ])

        exec_res = TaskExecutionResult(
            request_id="req-t1-tamper",
            status="ok",
            task_id="t1",
            space_id="sp-tamper",
            plan_version=1,
            artifacts=[{"name": "file.txt", "path": "file.txt", "sha256": forged_sha, "space_id": "sp-tamper"}],
        )

        res = dispatcher.observe_and_evaluate_task(
            kernel=kernel,
            task_id="t1",
            execution_result=exec_res,
            base_dir=base,
        )

        assert res.completed is False
        assert res.status == "failed"
        assert res.terminal_state == "failed"
        assert res.verification is not None
        assert res.verification.status == EvidenceStatus.TAMPERED
        assert any("mismatch" in r for r in res.verification.failure_reasons)

        # Space node marked failed
        node = kernel.get_task_graph().get_node("t1")
        assert node is not None
        assert node.state == "failed"


def test_observe_and_evaluate_missing_required_artifact_fails() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)
        kernel, dispatcher = _setup_kernel_and_graph(space_id="sp-req-art", nodes=[
            TaskNode(
                id="t1",
                capability="file.write",
                state="observing",
                params={"required_artifacts": ["mandatory.pdf"]},
            )
        ])

        exec_res = TaskExecutionResult(
            request_id="req-t1-missing",
            status="ok",
            task_id="t1",
            space_id="sp-req-art",
            plan_version=1,
            artifacts=[],  # Worker produced no artifacts
        )

        res = dispatcher.observe_and_evaluate_task(
            kernel=kernel,
            task_id="t1",
            execution_result=exec_res,
            base_dir=base,
        )

        assert res.completed is False
        assert res.status == "failed"
        assert res.verification is not None
        assert res.verification.status == EvidenceStatus.MISSING
        assert any("mandatory.pdf" in r for r in res.verification.failure_reasons)


# ============================================================================
# 4. Identity & Cross-Space Security Rejection
# ============================================================================


def test_observe_and_evaluate_cross_space_result_rejection() -> None:
    kernel, dispatcher = _setup_kernel_and_graph(space_id="space-primary", nodes=[
        TaskNode(id="t1", capability="compute.unit", state="observing")
    ])

    # Result belongs to a rogue space
    exec_res = TaskExecutionResult(
        request_id="req-t1-rogue",
        status="ok",
        task_id="t1",
        space_id="space-rogue",
        plan_version=1,
    )

    res = dispatcher.observe_and_evaluate_task(
        kernel=kernel,
        task_id="t1",
        execution_result=exec_res,
    )

    assert res.completed is False
    assert res.status == "failed"
    assert res.verification is not None
    assert res.verification.status == EvidenceStatus.MISMATCHED
    assert any("Cross-space" in r for r in res.verification.failure_reasons)


def test_observe_and_evaluate_cross_task_result_rejection() -> None:
    kernel, dispatcher = _setup_kernel_and_graph(space_id="sp-1", nodes=[
        TaskNode(id="t1", capability="compute.unit", state="observing")
    ])

    exec_res = TaskExecutionResult(
        request_id="req-other-1",
        status="ok",
        task_id="task-999",  # Wrong task ID
        space_id="sp-1",
        plan_version=1,
    )

    res = dispatcher.observe_and_evaluate_task(
        kernel=kernel,
        task_id="t1",
        execution_result=exec_res,
    )

    assert res.completed is False
    assert res.status == "failed"
    assert res.verification is not None
    assert res.verification.status == EvidenceStatus.MISMATCHED
    assert any("Cross-task" in r for r in res.verification.failure_reasons)


def test_observe_and_evaluate_plan_version_mismatch() -> None:
    kernel, dispatcher = _setup_kernel_and_graph(space_id="sp-1", nodes=[
        TaskNode(id="t1", capability="compute.unit", state="observing")
    ])

    # Result was generated against stale plan version
    exec_res = TaskExecutionResult(
        request_id="req-v99",
        status="ok",
        task_id="t1",
        space_id="sp-1",
        plan_version=99,
    )

    res = dispatcher.observe_and_evaluate_task(
        kernel=kernel,
        task_id="t1",
        execution_result=exec_res,
    )

    assert res.completed is False
    assert res.status == "failed"
    assert res.verification is not None
    assert any("version mismatch" in r for r in res.verification.failure_reasons)


def test_observe_and_evaluate_idempotency_duplicate() -> None:
    kernel, dispatcher = _setup_kernel_and_graph(space_id="sp-idemp", nodes=[
        TaskNode(id="t1", capability="compute.unit", state="completed")
    ])

    exec_res = TaskExecutionResult(
        request_id="req-t1",
        status="ok",
        task_id="t1",
        space_id="sp-idemp",
        plan_version=1,
    )

    # Calling observe on an already completed task must return cached=True and succeed without re-executing
    res = dispatcher.observe_and_evaluate_task(
        kernel=kernel,
        task_id="t1",
        execution_result=exec_res,
    )
    assert res.completed is True
    assert res.cached is True
    assert res.terminal_state == "completed"


# ============================================================================
# 5. DAG Dependency Unblocking & Parallel Execution
# ============================================================================


def test_dag_unblock_single_dependent() -> None:
    t1 = TaskNode(id="t1", capability="fetch", state="observing")
    t2 = TaskNode(id="t2", capability="process", state="pending", dependencies=["t1"])

    kernel, dispatcher = _setup_kernel_and_graph(space_id="sp-dag-1", nodes=[t1, t2])

    exec_res = TaskExecutionResult(
        request_id="req-t1-ok",
        status="ok",
        task_id="t1",
        space_id="sp-dag-1",
        plan_version=1,
    )

    res = dispatcher.observe_and_evaluate_task(
        kernel=kernel,
        task_id="t1",
        execution_result=exec_res,
    )

    assert res.completed is True
    assert "t2" in res.unblocked_tasks

    # Verify authoritative graph in kernel has t2 in READY
    graph = kernel.get_task_graph()
    node1 = graph.get_node("t1")
    node2 = graph.get_node("t2")
    assert node1 is not None and node1.state == "completed"
    assert node2 is not None and node2.state == "ready"


def test_dag_unblock_branching_parallel() -> None:
    """A -> (B, C, D)"""
    a = TaskNode(id="a", capability="root", state="observing")
    b = TaskNode(id="b", capability="child1", state="pending", dependencies=["a"])
    c = TaskNode(id="c", capability="child2", state="pending", dependencies=["a"])
    d = TaskNode(id="d", capability="child3", state="pending", dependencies=["a"])

    kernel, dispatcher = _setup_kernel_and_graph(space_id="sp-branch", nodes=[a, b, c, d])

    exec_res = TaskExecutionResult(
        request_id="req-a-ok",
        status="ok",
        task_id="a",
        space_id="sp-branch",
        plan_version=1,
    )

    res = dispatcher.observe_and_evaluate_task(
        kernel=kernel,
        task_id="a",
        execution_result=exec_res,
    )

    assert res.completed is True
    assert set(res.unblocked_tasks) == {"b", "c", "d"}

    graph = kernel.get_task_graph()
    na = graph.get_node("a")
    nb = graph.get_node("b")
    nc = graph.get_node("c")
    nd = graph.get_node("d")
    assert na is not None and na.state == "completed"
    assert nb is not None and nb.state == "ready"
    assert nc is not None and nc.state == "ready"
    assert nd is not None and nd.state == "ready"


def test_dag_unblock_merging_dependencies() -> None:
    """(A, B) -> C. When A completes, C remains pending. When B completes, C unblocks."""
    a = TaskNode(id="a", capability="comp_a", state="observing")
    b = TaskNode(id="b", capability="comp_b", state="observing")
    c = TaskNode(id="c", capability="join", state="pending", dependencies=["a", "b"])

    kernel, dispatcher = _setup_kernel_and_graph(space_id="sp-merge", nodes=[a, b, c])

    exec_res_a = TaskExecutionResult(
        request_id="req-a-ok",
        status="ok",
        task_id="a",
        space_id="sp-merge",
        plan_version=1,
    )

    # Step 1: A completes; C must NOT become ready because B is still observing (not completed)
    res_a = dispatcher.observe_and_evaluate_task(
        kernel=kernel,
        task_id="a",
        execution_result=exec_res_a,
    )
    assert res_a.completed is True
    assert "c" not in res_a.unblocked_tasks
    node_c1 = kernel.get_task_graph().get_node("c")
    assert node_c1 is not None and node_c1.state == "pending"

    # Step 2: Now B completes
    exec_res_b = TaskExecutionResult(
        request_id="req-b-ok",
        status="ok",
        task_id="b",
        space_id="sp-merge",
        plan_version=res_a.plan_version,
    )

    res_b = dispatcher.observe_and_evaluate_task(
        kernel=kernel,
        task_id="b",
        execution_result=exec_res_b,
    )

    assert res_b.completed is True
    assert "c" in res_b.unblocked_tasks
    node_c2 = kernel.get_task_graph().get_node("c")
    assert node_c2 is not None and node_c2.state == "ready"


# ============================================================================
# 6. Failed Upstream Dependencies & Failure Containment (SCCA Law 6)
# ============================================================================


def test_failed_upstream_blocks_downstream_task() -> None:
    """Mandatory dependency failure transitions downstream to BLOCKED."""
    t1 = TaskNode(id="t1", capability="fetch", state="observing")
    t2 = TaskNode(id="t2", capability="process", state="pending", dependencies=["t1"])

    kernel, dispatcher = _setup_kernel_and_graph(space_id="sp-fail-block", nodes=[t1, t2])

    exec_res = TaskExecutionResult(
        request_id="req-t1-failed",
        status="failed",
        task_id="t1",
        space_id="sp-fail-block",
        plan_version=1,
        error="Network timeout",
    )

    res = dispatcher.observe_and_evaluate_task(
        kernel=kernel,
        task_id="t1",
        execution_result=exec_res,
    )

    assert res.completed is False
    assert res.status == "failed"
    assert "t2" in res.blocked_tasks

    graph = kernel.get_task_graph()
    n1 = graph.get_node("t1")
    n2 = graph.get_node("t2")
    assert n1 is not None and n1.state == "failed"
    assert n2 is not None and n2.state == "blocked"
    assert "Upstream mandatory dependency 't1' failed" in str(n2.error)


def test_failed_upstream_optional_dependency() -> None:
    """Optional dependency failure does NOT block downstream task."""
    opt_dep = TaskNode(id="opt", capability="cache.fetch", state="observing", optional=True)
    downstream = TaskNode(id="calc", capability="compute.main", state="pending", dependencies=["opt"])

    kernel, dispatcher = _setup_kernel_and_graph(space_id="sp-opt-fail", nodes=[opt_dep, downstream])

    exec_res = TaskExecutionResult(
        request_id="req-opt-failed",
        status="failed",
        task_id="opt",
        space_id="sp-opt-fail",
        plan_version=1,
        error="Cache miss error",
    )

    res = dispatcher.observe_and_evaluate_task(
        kernel=kernel,
        task_id="opt",
        execution_result=exec_res,
    )

    assert res.completed is False
    assert res.status == "failed"
    # Optional dep failure does NOT block downstream
    assert "calc" not in res.blocked_tasks


# ============================================================================
# 7. Replay Mode & Taint Handling
# ============================================================================


def test_replay_mode_verification() -> None:
    kernel, dispatcher = _setup_kernel_and_graph(space_id="sp-replay", nodes=[
        TaskNode(id="t1", capability="report.gen", state="observing")
    ])

    exec_res = TaskExecutionResult(
        request_id="req-replay-1",
        status="ok",
        task_id="t1",
        space_id="sp-replay",
        plan_version=1,
        artifacts=[{"name": "rep.txt", "path": "rep.txt", "sha256": "hash123", "space_id": "sp-replay"}],
    )

    # In replay mode: no disk access required, recorded hash is accepted
    res = dispatcher.observe_and_evaluate_task(
        kernel=kernel,
        task_id="t1",
        execution_result=exec_res,
        replay_mode=True,
    )

    assert res.completed is True
    assert res.status == "completed"
    assert res.verification is not None
    assert res.verification.status == EvidenceStatus.VERIFIED


def test_taint_propagation_in_evidence() -> None:
    kernel, dispatcher = _setup_kernel_and_graph(space_id="sp-taint", nodes=[
        TaskNode(id="t1", capability="web.scrape", state="observing")
    ])

    exec_res = TaskExecutionResult(
        request_id="req-taint-1",
        status="ok",
        task_id="t1",
        space_id="sp-taint",
        plan_version=1,
        taint=True,
        output_data={"raw_html": "<untrusted>"},
    )

    res = dispatcher.observe_and_evaluate_task(
        kernel=kernel,
        task_id="t1",
        execution_result=exec_res,
    )

    assert res.completed is True
    assert res.verification is not None
    assert res.verification.tainted is True


def test_stale_plan_version_rejection_threat_t03() -> None:
    """Threat T-03: Result from Plan v1 presented when plan was updated to v2 is rejected as stale."""
    t1 = TaskNode(id="t1", capability="comp1", state="observing")
    t2 = TaskNode(id="t2", capability="comp2", state="observing")

    kernel, dispatcher = _setup_kernel_and_graph(space_id="sp-t03", nodes=[t1, t2])

    exec_res_1 = TaskExecutionResult(
        request_id="req-1",
        status="ok",
        task_id="t1",
        space_id="sp-t03",
        plan_version=1,
    )
    exec_res_2 = TaskExecutionResult(
        request_id="req-2",
        status="ok",
        task_id="t2",
        space_id="sp-t03",
        plan_version=1,  # Stale v1 result
    )

    # Task 1 completes first, advancing plan version
    res_1 = dispatcher.observe_and_evaluate_task(
        kernel=kernel,
        task_id="t1",
        execution_result=exec_res_1,
        expected_plan_version=1,
    )
    assert res_1.completed is True

    # Now plan is at higher version. Task 2 presenting v1 result must be rejected per Threat T-03
    res_2 = dispatcher.observe_and_evaluate_task(
        kernel=kernel,
        task_id="t2",
        execution_result=exec_res_2,
    )
    assert res_2.completed is False
    assert res_2.status == "failed"
    assert res_2.verification is not None
    assert any("version mismatch" in r for r in res_2.verification.failure_reasons)
