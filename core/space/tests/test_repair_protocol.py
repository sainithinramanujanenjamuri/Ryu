"""Unit tests for core/space/repair_protocol.py (REPAIR-001..004).

Verifies failure diagnostic models, trace normalization, deterministic fingerprinting,
failure classification, repair proposal validation, and loop history models.
"""

from __future__ import annotations

import pytest

from core.space.repair_protocol import (
    MAX_CHANGED_FILES,
    MAX_DIFF_LINES,
    MAX_REPAIR_ITERATIONS,
    FailureClassification,
    RepairCeilingExceededError,
    RepairDiagnostic,
    RepairLoopHistory,
    RepairProposal,
    RepairValidationError,
    classify_failure,
    compute_repair_fingerprint,
    normalize_failure_trace,
    validate_repair_proposal,
)

# ── 1. Diagnostic Models ──────────────────────────────────────────────────────

def test_repair_diagnostic_creation_and_fingerprint() -> None:
    """RepairDiagnostic creates successfully and computes fingerprint automatically."""
    diag = RepairDiagnostic(
        space_id="space-1",
        plan_version=1,
        task_id="task-test",
        normalized_failure_trace="AssertionError: 2 != 4\n  at test_calc.py:10",
        failure_class=FailureClassification.ASSERTION_FAILURE,
    )
    assert diag.space_id == "space-1"
    assert diag.task_id == "task-test"
    assert len(diag.failure_fingerprint) == 64
    assert diag.taint is True


def test_repair_diagnostic_validation() -> None:
    """RepairDiagnostic validates required space_id and task_id."""
    with pytest.raises(RepairValidationError, match="space_id"):
        RepairDiagnostic(space_id="", plan_version=1, task_id="task-1")

    with pytest.raises(RepairValidationError, match="task_id"):
        RepairDiagnostic(space_id="space-1", plan_version=1, task_id="")


# ── 2. Trace Normalization & Fingerprinting Determinism ───────────────────────

def test_trace_normalization_strips_nondeterministic_artifacts() -> None:
    """Normalization strips memory addresses, timestamps, durations, and process IDs."""
    raw = (
        "Traceback (most recent call last):\n"
        "  File 'C:\\repo\\project\\test_mod.py', line 45, in test_fn\n"
        "    obj = <MyClass object at 0x7f8b9a12bc40>\n"
        "  Timestamp: 2026-10-02T18:30:15.123Z pid=98432\n"
        "AssertionError: Expected true\n"
        "=== 1 failed in 0.45s ===\n"
    )
    norm = normalize_failure_trace(raw, repo_root="C:\\repo\\project")

    assert "0x7f8b9a12bc40" not in norm
    assert "<mem_addr>" in norm
    assert "2026-10-02T18:30:15.123Z" not in norm
    assert "<timestamp>" in norm
    assert "pid=98432" not in norm
    assert "pid=<pid>" in norm
    assert "in 0.45s" not in norm
    assert "in <dur>s" in norm
    assert "C:/repo/project" not in norm
    assert "./test_mod.py" in norm


def test_trace_normalization_idempotent() -> None:
    """Normalizing an already normalized trace produces identical output."""
    raw = "AssertionError: 1 != 2\n  File 'test_a.py', line 12\n"
    n1 = normalize_failure_trace(raw)
    n2 = normalize_failure_trace(n1)
    assert n1 == n2


def test_fingerprint_determinism_across_nondeterministic_fields() -> None:
    """Equivalent failures with different timestamps or memory addresses produce identical fingerprints."""
    trace_run1 = (
        "Traceback (most recent call last):\n"
        "  File '/app/repo/test_x.py', line 10\n"
        "    ref = <Worker at 0x10293847>\n"
        "  Timestamp: 2026-10-02T10:00:00Z pid=101\n"
        "AssertionError: failure in 0.12s\n"
    )
    trace_run2 = (
        "Traceback (most recent call last):\n"
        "  File '/app/repo/test_x.py', line 10\n"
        "    ref = <Worker at 0x99887766>\n"
        "  Timestamp: 2026-10-02T11:22:33Z pid=999\n"
        "AssertionError: failure in 0.88s\n"
    )
    n1 = normalize_failure_trace(trace_run1, repo_root="/app/repo")
    n2 = normalize_failure_trace(trace_run2, repo_root="/app/repo")
    assert n1 == n2

    fp1 = compute_repair_fingerprint("space-1", "task-t", n1)
    fp2 = compute_repair_fingerprint("space-1", "task-t", n2)
    assert fp1 == fp2


def test_fingerprint_collision_resistance_for_different_failures() -> None:
    """Genuinely different failure causes produce different fingerprints."""
    trace_a = "AssertionError: 1 == 2"
    trace_b = "AssertionError: 99 == 100"

    fp_a = compute_repair_fingerprint("space-1", "task-1", trace_a)
    fp_b = compute_repair_fingerprint("space-1", "task-1", trace_b)
    assert fp_a != fp_b


def test_fingerprint_isolation_across_spaces_and_tasks() -> None:
    """Same trace in different spaces or tasks produces distinct fingerprints (SCCA Law 1)."""
    trace = "AssertionError: 1 == 2"
    fp_s1 = compute_repair_fingerprint("space-1", "task-1", trace)
    fp_s2 = compute_repair_fingerprint("space-2", "task-1", trace)
    fp_t2 = compute_repair_fingerprint("space-1", "task-2", trace)

    assert fp_s1 != fp_s2
    assert fp_s1 != fp_t2


# ── 3. Failure Classification ─────────────────────────────────────────────────

@pytest.mark.parametrize(
    ("exit_code", "stdout", "stderr", "is_timeout", "parser_status", "expected"),
    [
        (1, "", "", False, "inconclusive", FailureClassification.UNKNOWN_INCONCLUSIVE),
        (124, "", "", True, "ok", FailureClassification.TIMEOUT),
        (1, "SyntaxError: invalid syntax", "", False, "ok", FailureClassification.SYNTAX_FAILURE),
        (1, "ModuleNotFoundError: No module named 'foo'", "", False, "ok", FailureClassification.IMPORT_FAILURE),
        (1, "PermissionError: [Errno 13] Permission denied", "", False, "ok", FailureClassification.PERMISSION_FAILURE),
        (1, "AssertionError: 4 != 5", "", False, "ok", FailureClassification.ASSERTION_FAILURE),
        (0, "FAILED tests/test_one.py", "", False, "ok", FailureClassification.EVIDENCE_INCONSISTENCY),
        (1, "ZeroDivisionError: division by zero", "", False, "ok", FailureClassification.RUNTIME_FAILURE),
        (1, "SandboxSecurityViolation: syscall blocked", "", False, "ok", FailureClassification.SANDBOX_FAILURE),
    ],
)
def test_classify_failure(
    exit_code: int,
    stdout: str,
    stderr: str,
    is_timeout: bool,
    parser_status: str,
    expected: FailureClassification,
) -> None:
    """Failure output is deterministically classified into standardized categories."""
    res = classify_failure(
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        is_timeout=is_timeout,
        parser_status=parser_status,
    )
    assert res == expected


# ── 4. Repair Proposal Validation ─────────────────────────────────────────────

def test_repair_proposal_valid() -> None:
    """Valid repair proposal instantiates and validates successfully."""
    proposal = RepairProposal(
        space_id="space-1",
        plan_version=1,
        task_id="task-1",
        failure_fingerprint="fp123",
        iteration=1,
        target_files=("src/app.py",),
        proposed_patch="--- a/src/app.py\n+++ b/src/app.py\n@@ -1 +1 @@\n-x = 1\n+x = 2\n",
        patch_id="patch-1",
        reason="Fix off by one",
    )
    assert proposal.iteration == 1
    assert len(proposal.patch_sha256) == 64

    is_ok, err = validate_repair_proposal(proposal)
    assert is_ok is True
    assert err is None


def test_repair_proposal_iteration_ceiling_rejected() -> None:
    """Proposals exceeding MAX_REPAIR_ITERATIONS (3) are rejected upon construction."""
    with pytest.raises(RepairCeilingExceededError, match="exceeds ceiling"):
        RepairProposal(
            space_id="space-1",
            plan_version=1,
            task_id="task-1",
            failure_fingerprint="fp123",
            iteration=MAX_REPAIR_ITERATIONS + 1,
            target_files=("src/app.py",),
            proposed_patch="diff",
            patch_id="patch-1",
        )


def test_repair_proposal_target_files_ceiling_rejected() -> None:
    """Proposals targeting > MAX_CHANGED_FILES (5) are rejected upon construction."""
    with pytest.raises(RepairCeilingExceededError, match="MAX_CHANGED_FILES"):
        RepairProposal(
            space_id="space-1",
            plan_version=1,
            task_id="task-1",
            failure_fingerprint="fp123",
            iteration=1,
            target_files=tuple(f"src/file_{i}.py" for i in range(MAX_CHANGED_FILES + 1)),
            proposed_patch="diff",
            patch_id="patch-1",
        )


def test_repair_proposal_diff_lines_ceiling_rejected() -> None:
    """Proposals with diff lines > MAX_DIFF_LINES (500) fail validation."""
    oversized_patch = "\n".join(["+ line"] * (MAX_DIFF_LINES + 10))
    proposal = RepairProposal(
        space_id="space-1",
        plan_version=1,
        task_id="task-1",
        failure_fingerprint="fp123",
        iteration=1,
        target_files=("src/app.py",),
        proposed_patch=oversized_patch,
        patch_id="patch-1",
    )
    is_ok, err = validate_repair_proposal(proposal)
    assert is_ok is False
    assert "exceeds ceiling" in (err or "")


@pytest.mark.parametrize(
    "bad_file",
    [
        "../../etc/passwd",
        "..\\..\\windows\\win.ini",
        "C:\\secrets.txt",
        "\\\\server\\share\\file.txt",
        ".env",
        ".env.local",
        "id_rsa",
        "server.key",
        "cert.pem",
        ".aws/credentials",
        ".ssh/authorized_keys",
    ],
)
def test_repair_proposal_security_violations_rejected(bad_file: str) -> None:
    """Target paths containing traversal or sensitive credential files are rejected."""
    proposal = RepairProposal(
        space_id="space-1",
        plan_version=1,
        task_id="task-1",
        failure_fingerprint="fp123",
        iteration=1,
        target_files=(bad_file,),
        proposed_patch="--- a/file\n+++ b/file\n",
        patch_id="patch-1",
    )
    is_ok, err = validate_repair_proposal(proposal)
    assert is_ok is False
    assert "traversal" in (err or "") or "sensitive" in (err or "")


# ── 5. Repair Loop History ───────────────────────────────────────────────────

def test_repair_loop_history_advancement_and_loop_detection() -> None:
    """RepairLoopHistory tracks iterations, detects repeats and oscillations."""
    history = RepairLoopHistory(space_id="space-1", task_id="task-1")
    assert history.current_iteration == 0
    assert history.is_loop_detected("fp-A") is False
    assert history.is_limit_exceeded() is False

    # Iteration 1
    h1 = history.record_iteration("fp-A", patch_id="p1")
    assert h1.current_iteration == 1
    assert h1.is_loop_detected("fp-A") is True
    assert h1.is_loop_detected("fp-B") is False
    assert h1.is_limit_exceeded() is False

    # Iteration 2
    h2 = h1.record_iteration("fp-B", patch_id="p2")
    assert h2.current_iteration == 2
    assert h2.is_loop_detected("fp-A") is True  # Detects oscillation if fp-A repeats
    assert h2.is_loop_detected("fp-B") is True
    assert h2.is_limit_exceeded() is False

    # Iteration 3
    h3 = h2.record_iteration("fp-C", patch_id="p3")
    assert h3.current_iteration == 3
    assert h3.is_limit_exceeded() is True
