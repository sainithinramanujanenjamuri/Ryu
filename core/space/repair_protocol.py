"""Domain-neutral protocol models for Bounded Test-Repair Loop (ADR-0044, REPAIR-001..004).

Defines structured diagnostic models, deterministic failure fingerprinting,
failure classification, bounded repair proposals, and repair iteration bounds.

Strict Core Boundaries (AGENTS.md §7):
- MUST NOT import from agents/, workers/, skills/, workflows/, llm/, channels/, memory/.
- Pure deterministic representations only.
- Space-scoped, immutable data contracts.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

# ADR-0044 / REPAIR-001 / REPAIR-004 Bounds
MAX_REPAIR_ITERATIONS: int = 3
MAX_CHANGED_FILES: int = 5
MAX_DIFF_LINES: int = 500

# Sensitive path patterns blocked from automated repair mutations (REPO-003, REPAIR-002)
_SENSITIVE_PATH_PATTERNS = re.compile(
    r"(?:^|/|\\)(?:"
    r"\.env(?:\..*)?"
    r"|id_rsa(?:|\.pub)"
    r"|id_ed25519(?:|\.pub)"
    r"|.*\.pem"
    r"|.*\.key"
    r"|.*\.p12"
    r"|credentials\.json"
    r"|token"
    r"|\.aws(?:/|\\).*"
    r"|\.ssh(?:/|\\).*"
    r"|\.kube(?:/|\\).*"
    r")$",
    re.IGNORECASE,
)

# Traversal / injection pattern in paths
_PATH_ESCAPE_PATTERN = re.compile(
    r"(?:\.\.[\\/]|[\\/]\.\.|^[a-zA-Z]:|^[\\/]{1,2}|\x00)",
)


# Patterns for normalising tracebacks
_MEM_ADDR_PATTERN = re.compile(r"0x[0-9a-fA-F]+")
_TIMESTAMP_PATTERN = re.compile(
    r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?\b"
)
_DURATION_PATTERN = re.compile(r"\bin \d+(?:\.\d+)?s\b", re.IGNORECASE)
_PID_PATTERN = re.compile(r"\bpid\s*=?\s*\d+\b", re.IGNORECASE)


# ── Exceptions ────────────────────────────────────────────────────────────────

class RepairError(Exception):
    """Base exception for repair protocol errors."""


class RepairValidationError(RepairError):
    """Raised when a repair proposal or diagnostic fails structural validation."""


class RepairCeilingExceededError(RepairError):
    """Raised when repair attempt exceeds iteration or patch size ceilings (REPAIR-001, REPAIR-004)."""


class RepairLoopDetectedError(RepairError):
    """Raised when repeated or oscillating failure fingerprints are detected (REPAIR-002)."""


class RepairInconclusiveError(RepairError):
    """Raised when failure evidence is contradictory or inconclusive (EVIDENCE-001, REPAIR-004)."""


class RepairSecurityViolationError(RepairError):
    """Raised when a repair proposal attempts path traversal or sensitive path access."""


# ── Enums ─────────────────────────────────────────────────────────────────────

class FailureClassification(str, Enum):
    """Deterministic classification of test execution failures (REPAIR-001)."""

    ASSERTION_FAILURE = "assertion_failure"
    SYNTAX_FAILURE = "syntax_failure"
    IMPORT_FAILURE = "import_failure"
    RUNTIME_FAILURE = "runtime_failure"
    TIMEOUT = "timeout"
    INFRASTRUCTURE_FAILURE = "infrastructure_failure"
    SANDBOX_FAILURE = "sandbox_failure"
    PERMISSION_FAILURE = "permission_failure"
    EVIDENCE_INCONSISTENCY = "evidence_inconsistency"
    REPOSITORY_STATE_MISMATCH = "repository_state_mismatch"
    UNKNOWN_INCONCLUSIVE = "unknown_inconclusive"


# ── Trace Normalization & Fingerprinting ──────────────────────────────────────

def normalize_failure_trace(raw_trace: str, repo_root: str | None = None) -> str:
    """Deterministically normalize failure traces for stable fingerprinting (ADR-0044, REPAIR-002).

    Strips:
    - Absolute repository path prefixes (replaces with canonical relative paths).
    - Windows vs Unix path separator discrepancies (all normalized to '/').
    - Nondeterministic process IDs, memory addresses, timestamps, and execution durations.
    - Superfluous trailing whitespace and extra blank lines.
    """
    if not raw_trace:
        return ""

    # 1. Normalize path separators to Unix style
    text = raw_trace.replace("\\", "/")

    # 2. Canonicalize repository root prefix
    if repo_root:
        norm_root = str(Path(repo_root).resolve()).replace("\\", "/")
        if not norm_root.endswith("/"):
            norm_root += "/"
        text = text.replace(norm_root, "./")

    # 3. Strip memory addresses (0x...)
    text = _MEM_ADDR_PATTERN.sub("<mem_addr>", text)

    # 4. Strip timestamps
    text = _TIMESTAMP_PATTERN.sub("<timestamp>", text)

    # 5. Strip execution durations (e.g. "in 0.42s")
    text = _DURATION_PATTERN.sub("in <dur>s", text)

    # 6. Strip process IDs
    text = _PID_PATTERN.sub("pid=<pid>", text)

    # 7. Strip line trailing whitespace and collapse consecutive empty lines
    lines = [line.rstrip() for line in text.splitlines()]
    collapsed_lines: list[str] = []
    prev_empty = False
    for line in lines:
        if not line:
            if not prev_empty:
                collapsed_lines.append("")
                prev_empty = True
        else:
            collapsed_lines.append(line)
            prev_empty = False

    return "\n".join(collapsed_lines).strip()


def compute_repair_fingerprint(
    space_id: str,
    task_id: str,
    normalized_failure_trace: str,
) -> str:
    """Derive deterministic SHA-256 failure fingerprint (ADR-0044 §10, REPAIR-002).

    Fingerprint = SHA256(space_id : task_id : normalized_failure_trace)
    """
    raw = f"{space_id}:{task_id}:{normalized_failure_trace}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def classify_failure(
    exit_code: int,
    stdout: str,
    stderr: str,
    is_timeout: bool = False,
    parser_status: str = "ok",
) -> FailureClassification:
    """Deterministically classify test runner outcome (REPAIR-001)."""
    if parser_status == "inconclusive":
        return FailureClassification.UNKNOWN_INCONCLUSIVE

    if is_timeout or exit_code == 124:
        return FailureClassification.TIMEOUT

    combined = f"{stdout}\n{stderr}"

    # Check for contradictions
    if exit_code == 0 and ("FAILED" in combined or "ERROR" in combined):
        return FailureClassification.EVIDENCE_INCONSISTENCY

    # Check syntax / import failures
    if "SyntaxError" in combined:
        return FailureClassification.SYNTAX_FAILURE
    if "ImportError" in combined or "ModuleNotFoundError" in combined:
        return FailureClassification.IMPORT_FAILURE
    if "PermissionError" in combined:
        return FailureClassification.PERMISSION_FAILURE
    if "SandboxSecurityViolation" in combined or "SandboxViolation" in combined:
        return FailureClassification.SANDBOX_FAILURE
    if "AssertionError" in combined:
        return FailureClassification.ASSERTION_FAILURE

    common_runtime_exceptions = (
        "ZeroDivisionError",
        "IndexError",
        "KeyError",
        "TypeError",
        "ValueError",
        "AttributeError",
        "RuntimeError",
        "NameError",
        "FileNotFoundError",
    )
    for exc in common_runtime_exceptions:
        if exc in combined:
            return FailureClassification.RUNTIME_FAILURE

    if exit_code != 0:
        if "FAILED" in combined or "Traceback" in combined:
            return FailureClassification.RUNTIME_FAILURE
        return FailureClassification.UNKNOWN_INCONCLUSIVE

    return FailureClassification.ASSERTION_FAILURE


# ── Data Contracts ────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class RepairDiagnostic:
    """Structured, deterministic representation of a failed test execution (REPAIR-001)."""

    space_id: str
    plan_version: int
    task_id: str
    attempt_id: str = ""
    test_execution_id: str = ""
    repository_id: str = ""
    repository_pre_hash: str = ""
    test_command: str = ""
    test_framework: str = "pytest"
    failed_test_ids: tuple[str, ...] = ()
    failure_class: FailureClassification = FailureClassification.ASSERTION_FAILURE
    normalized_failure_trace: str = ""
    raw_stdout_summary: str = ""
    raw_stderr_summary: str = ""
    exit_code: int = 1
    is_timeout: bool = False
    artifact_ids: tuple[str, ...] = ()
    provenance_ids: tuple[str, ...] = ()
    taint: bool = True
    failure_fingerprint: str = ""

    def __post_init__(self) -> None:
        if not self.space_id:
            raise RepairValidationError("space_id cannot be empty")
        if not self.task_id:
            raise RepairValidationError("task_id cannot be empty")
        if not self.failure_fingerprint:
            fp = compute_repair_fingerprint(
                self.space_id,
                self.task_id,
                self.normalized_failure_trace,
            )
            object.__setattr__(self, "failure_fingerprint", fp)


@dataclass(frozen=True)
class RepairProposal:
    """Bounded, immutable repair proposal input to ConvergenceEngine (REPAIR-002, REPAIR-003).

    Zero Authority Invariant:
    A RepairProposal has zero independent execution authority. It does NOT mutate
    Plan, Space, or repository files directly; it is evaluated by ConvergenceEngine
    and committed exclusively via SpaceKernel CAS.
    """

    space_id: str
    plan_version: int
    task_id: str
    failure_fingerprint: str
    iteration: int
    target_files: tuple[str, ...]
    proposed_patch: str
    patch_id: str
    patch_sha256: str = ""
    reason: str = ""
    expected_validation_command: str = ""
    provenance_references: tuple[str, ...] = ()
    evidence_references: tuple[str, ...] = ()
    taint: bool = True
    precondition_repo_hash: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.space_id:
            raise RepairValidationError("space_id cannot be empty")
        if not self.task_id:
            raise RepairValidationError("task_id cannot be empty")
        if not (1 <= self.iteration <= MAX_REPAIR_ITERATIONS):
            raise RepairCeilingExceededError(
                f"Repair iteration {self.iteration} exceeds ceiling MAX_REPAIR_ITERATIONS={MAX_REPAIR_ITERATIONS}"
            )
        if len(self.target_files) > MAX_CHANGED_FILES:
            raise RepairCeilingExceededError(
                f"Repair proposal targets {len(self.target_files)} files; exceeds MAX_CHANGED_FILES={MAX_CHANGED_FILES}"
            )
        if not self.patch_sha256 and self.proposed_patch:
            sha = hashlib.sha256(self.proposed_patch.encode("utf-8")).hexdigest()
            object.__setattr__(self, "patch_sha256", sha)


@dataclass(frozen=True)
class RepairLoopHistory:
    """Durable history of repair attempts and failure fingerprints (REPAIR-002, REPAIR-003)."""

    space_id: str
    task_id: str
    current_iteration: int = 0
    fingerprint_history: tuple[str, ...] = ()
    applied_patches: tuple[str, ...] = ()
    diagnostics: tuple[str, ...] = ()

    def is_loop_detected(self, fingerprint: str) -> bool:
        """Return True if fingerprint was previously seen (repeated failure or oscillation)."""
        return fingerprint in self.fingerprint_history

    def is_limit_exceeded(self) -> bool:
        """Return True if repair iterations have reached or exceeded MAX_REPAIR_ITERATIONS."""
        return self.current_iteration >= MAX_REPAIR_ITERATIONS

    def record_iteration(
        self,
        fingerprint: str,
        patch_id: str = "",
        diagnostic_id: str = "",
    ) -> RepairLoopHistory:
        """Return updated immutable history record with advanced iteration."""
        return RepairLoopHistory(
            space_id=self.space_id,
            task_id=self.task_id,
            current_iteration=self.current_iteration + 1,
            fingerprint_history=(*self.fingerprint_history, fingerprint),
            applied_patches=(*self.applied_patches, patch_id) if patch_id else self.applied_patches,
            diagnostics=(*self.diagnostics, diagnostic_id) if diagnostic_id else self.diagnostics,
        )


# ── Proposal Validator ────────────────────────────────────────────────────────

def validate_repair_proposal(
    proposal: RepairProposal,
    repo_root: Path | str | None = None,
) -> tuple[bool, str | None]:
    """Validate a repair proposal against architectural bounds and security rules (REPAIR-002).

    Rules:
    1. iteration <= MAX_REPAIR_ITERATIONS (3)
    2. target_files count <= MAX_CHANGED_FILES (5)
    3. patch diff line count <= MAX_DIFF_LINES (500)
    4. zero sensitive path modifications
    5. zero path traversal escapes
    """
    if proposal.iteration > MAX_REPAIR_ITERATIONS:
        return False, f"Iteration {proposal.iteration} exceeds MAX_REPAIR_ITERATIONS ({MAX_REPAIR_ITERATIONS})"

    if len(proposal.target_files) > MAX_CHANGED_FILES:
        return False, f"Target file count {len(proposal.target_files)} exceeds ceiling ({MAX_CHANGED_FILES})"

    # Check diff line count
    lines = proposal.proposed_patch.splitlines()
    if len(lines) > MAX_DIFF_LINES:
        return False, f"Diff line count {len(lines)} exceeds ceiling ({MAX_DIFF_LINES})"

    # Validate target file paths
    resolved_root = Path(repo_root).resolve() if repo_root else None
    for tf in proposal.target_files:
        norm_tf = tf.replace("\\", "/")
        if _PATH_ESCAPE_PATTERN.search(norm_tf):
            return False, f"Target file '{tf}' contains forbidden path traversal"
        if _SENSITIVE_PATH_PATTERNS.search(norm_tf):
            return False, f"Target file '{tf}' is in sensitive path denylist"

        if resolved_root:
            try:
                candidate = (resolved_root / norm_tf).resolve()
                if not candidate.is_relative_to(resolved_root):
                    return False, f"Target file '{tf}' resolves outside repository root"
            except Exception as e:
                return False, f"Target file '{tf}' path resolution failed: {e}"

    return True, None


# Prevent pytest from collecting protocol data models
RepairDiagnostic.__test__ = False  # type: ignore[attr-defined]
RepairProposal.__test__ = False  # type: ignore[attr-defined]
RepairLoopHistory.__test__ = False  # type: ignore[attr-defined]
