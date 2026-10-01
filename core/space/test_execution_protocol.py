"""Test Execution Protocol: Core abstractions, data models, and contracts (ADR-0044, Phase 14.5).

Defines the boundary between the deterministic Space Kernel / Orchestrator and
concrete test execution workers and runners (EVIDENCE-001, EVIDENCE-002, EVIDENCE-003).

Governing Architecture: Space-Centric Cognitive Architecture (SCCA)
Governing Rule: AGENTS.md §7 (Deterministic Core Independence — zero higher-layer imports)
Phase: 14.5
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Protocol, runtime_checkable

# Pattern to detect dangerous shell metacharacters and injection vectors
_SHELL_INJECTION_PATTERN = re.compile(r"[;&|`$><\n\r\x00]")

# Denied commands / tools (package managers, compilers, arbitrary shells)
_DENIED_RUNNER_PATTERN = re.compile(
    r"(?i)\b(pip|npm|yarn|pnpm|cargo\s+install|apt|apt-get|brew|choco|winget|curl|wget|bash|sh|zsh|powershell|cmd|cscript|wscript)\b"
)


# ── Exceptions ───────────────────────────────────────────────────────────────

class TestExecutionError(Exception):
    """Base exception for all test execution operations."""


class TestCommandValidationError(TestExecutionError):
    """Raised when a test command violates policy or contains injection vectors."""


class TestSandboxSecurityViolation(TestExecutionError):
    """Raised when a test execution attempts forbidden sandbox or network operations."""


class TestTimeoutError(TestExecutionError):
    """Raised when test execution exceeds configured timeout bounds."""


class TestParserError(TestExecutionError):
    """Raised when test output cannot be parsed deterministically."""


class TestRepositoryStateMismatchError(TestExecutionError):
    """Raised when the tested repository state diverged from expected post-patch hashes."""


# ── Enums ───────────────────────────────────────────────────────────────────

class TestExecutionStatus(str, Enum):
    """Lifecycle and terminal states of sandboxed test execution."""

    QUEUED = "queued"
    VALIDATING = "validating"
    ADMITTED = "admitted"
    LEASED = "leased"
    RUNNING = "running"
    COLLECTING = "collecting"
    PARSING = "parsing"
    VERIFIED = "verified"
    REJECTED = "rejected"
    TIMED_OUT = "timed_out"
    PROCESS_FAILED = "process_failed"
    PARSE_FAILED = "parse_failed"
    SANDBOX_VIOLATION = "sandbox_violation"
    CANCELLED = "cancelled"
    INFRASTRUCTURE_FAILED = "infrastructure_failed"
    INCONCLUSIVE = "inconclusive"


class TestCaseStatus(str, Enum):
    """Standardized result status for an individual test case."""

    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"
    ERRORED = "errored"


class TestRunnerType(str, Enum):
    """Supported test runner ecosystems."""

    PYTEST = "pytest"
    PYTHON_UNITTEST = "unittest"


# ── Limits & Policy Models ──────────────────────────────────────────────────

@dataclass(frozen=True)
class TestExecutionLimits:
    """Execution quotas and bounding constraints for sandboxed test execution."""

    timeout_seconds: float = 60.0
    max_output_bytes: int = 2 * 1024 * 1024  # 2MB
    max_process_count: int = 4
    max_test_count: int = 1000

    def __post_init__(self) -> None:
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be > 0")
        if self.max_output_bytes <= 0:
            raise ValueError("max_output_bytes must be > 0")
        if self.max_process_count <= 0:
            raise ValueError("max_process_count must be > 0")
        if self.max_test_count <= 0:
            raise ValueError("max_test_count must be > 0")


@dataclass(frozen=True)
class TestCommand:
    """Structured, policy-validated test execution command (EVIDENCE-001).

    Never accepts raw shell command strings. Prohibits shell injection,
    command chaining, redirections, and package management.
    """

    runner: str  # "pytest", "python -m pytest", "unittest"
    arguments: tuple[str, ...] = ()
    target_paths: tuple[str, ...] = ()
    working_directory: str | None = None
    environment_policy: str = "default_deny"
    timeout_seconds: float = 60.0
    limits: TestExecutionLimits = field(default_factory=TestExecutionLimits)

    def __post_init__(self) -> None:
        if not self.runner or not self.runner.strip():
            raise TestCommandValidationError("Test runner command cannot be empty")

        clean_runner = self.runner.strip()

        # Validate runner against denied command list
        if _DENIED_RUNNER_PATTERN.search(clean_runner):
            raise TestCommandValidationError(
                f"Runner '{clean_runner}' contains prohibited executable or package manager"
            )

        # Disallow shell metacharacters in runner
        if _SHELL_INJECTION_PATTERN.search(clean_runner):
            raise TestCommandValidationError(
                f"Runner command '{clean_runner}' contains dangerous shell metacharacters"
            )

        # Validate arguments for shell metacharacters and dangerous flags
        for arg in self.arguments:
            if _SHELL_INJECTION_PATTERN.search(arg):
                raise TestCommandValidationError(
                    f"Test argument '{arg}' contains dangerous shell metacharacters"
                )
            if _DENIED_RUNNER_PATTERN.search(arg):
                raise TestCommandValidationError(
                    f"Test argument '{arg}' contains prohibited tool invocation"
                )

        # Validate target paths for path traversal
        for path in self.target_paths:
            if ".." in path.replace("\\", "/").split("/"):
                raise TestCommandValidationError(
                    f"Target path '{path}' contains path traversal escape ('..')"
                )
            if _SHELL_INJECTION_PATTERN.search(path):
                raise TestCommandValidationError(
                    f"Target path '{path}' contains shell metacharacters"
                )

        # Validate working directory
        if self.working_directory:
            if ".." in self.working_directory.replace("\\", "/").split("/"):
                raise TestCommandValidationError(
                    f"Working directory '{self.working_directory}' contains path traversal escape"
                )
            if _SHELL_INJECTION_PATTERN.search(self.working_directory):
                raise TestCommandValidationError(
                    f"Working directory '{self.working_directory}' contains shell metacharacters"
                )


# ── Execution Request & Report Models ───────────────────────────────────────

@dataclass(frozen=True)
class TestExecutionRequest:
    """Authorized request to execute repository tests inside the sandbox (EVIDENCE-001)."""

    request_id: str
    task_id: str
    space_id: str
    plan_version: int
    command: TestCommand
    repository_id: str
    repository_root: str
    expected_repo_hash: str | None = None
    associated_patch_id: str | None = None
    timeout_seconds: float = 60.0

    def __post_init__(self) -> None:
        if not self.request_id:
            raise ValueError("request_id must not be empty")
        if not self.task_id:
            raise ValueError("task_id must not be empty")
        if not self.space_id:
            raise ValueError("space_id must not be empty (SCCA Law 1)")
        if self.plan_version < 1:
            raise ValueError("plan_version must be >= 1")
        if not self.repository_id:
            raise ValueError("repository_id must not be empty")
        if not self.repository_root:
            raise ValueError("repository_root must not be empty")


@dataclass(frozen=True)
class TestFailure:
    """Structured details of an individual test failure or error."""

    test_node_id: str
    failure_type: str
    message: str
    traceback: str = ""
    file_path: str | None = None
    line_number: int | None = None


@dataclass(frozen=True)
class TestCaseResult:
    """Outcome of an individual test case execution."""

    node_id: str
    name: str
    status: TestCaseStatus
    duration_seconds: float = 0.0
    failure: TestFailure | None = None


@dataclass(frozen=True)
class TestSuiteResult:
    """Aggregated outcome of a test suite or test module."""

    suite_name: str
    total: int
    passed: int
    failed: int
    skipped: int
    errored: int
    duration_seconds: float = 0.0
    test_cases: tuple[TestCaseResult, ...] = ()


@dataclass(frozen=True)
class TestExecutionReport:
    """Comprehensive structured outcome of test execution (EVIDENCE-001, EVIDENCE-002).

    exit_code == 0 alone is insufficient to assert success; the parser must
    independently verify parsed counts match exit state.
    """

    execution_id: str
    task_id: str
    space_id: str
    plan_version: int
    repository_id: str
    runner: str
    status: TestExecutionStatus
    exit_code: int
    duration_seconds: float
    total_tests: int
    passed_tests: int
    failed_tests: int
    skipped_tests: int
    errored_tests: int
    failures: tuple[TestFailure, ...] = ()
    suites: tuple[TestSuiteResult, ...] = ()
    stdout_summary: str = ""
    stderr_summary: str = ""
    parser_status: str = "ok"  # "ok" | "inconclusive" | "failed"
    sandbox_status: str = "ok"  # "ok" | "violation"
    associated_patch_id: str | None = None
    tested_repo_hash: str | None = None
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def is_all_passed(self) -> bool:
        """EVIDENCE-001: Tests passed strictly requires exit code 0, status VERIFIED, and 0 failures/errors."""
        return (
            self.exit_code == 0
            and self.status == TestExecutionStatus.VERIFIED
            and self.parser_status == "ok"
            and self.total_tests > 0
            and self.failed_tests == 0
            and self.errored_tests == 0
        )


@dataclass(frozen=True)
class TestEvidence:
    """Content-addressed test evidence bound to artifacts and provenance (EVIDENCE-001..003)."""

    evidence_id: str
    task_id: str
    space_id: str
    plan_version: int
    report: TestExecutionReport
    stdout_hash: str
    stderr_hash: str
    report_hash: str
    provenance_id: str
    verified: bool
    lineage: dict[str, str] = field(default_factory=dict)  # "derived_from", "validates", etc.


# ── Protocol ────────────────────────────────────────────────────────────────

@runtime_checkable
class TestExecutionProtocol(Protocol):
    """Protocol for executing repository tests within a bounded sandbox (ADR-0044, EVIDENCE-001)."""

    def execute_tests(
        self,
        space_id: str,
        request: TestExecutionRequest,
    ) -> TestExecutionReport:
        """Execute sandboxed tests and return structured TestExecutionReport."""
        ...


# Prevent pytest from attempting to collect data models as test suites
TestCommand.__test__ = False  # type: ignore[attr-defined]
TestCaseResult.__test__ = False  # type: ignore[attr-defined]
TestCaseStatus.__test__ = False  # type: ignore[attr-defined]
TestCommandValidationError.__test__ = False  # type: ignore[attr-defined]
TestEvidence.__test__ = False  # type: ignore[attr-defined]
TestExecutionLimits.__test__ = False  # type: ignore[attr-defined]
TestExecutionReport.__test__ = False  # type: ignore[attr-defined]
TestExecutionRequest.__test__ = False  # type: ignore[attr-defined]
TestExecutionStatus.__test__ = False  # type: ignore[attr-defined]
TestFailure.__test__ = False  # type: ignore[attr-defined]
TestSuiteResult.__test__ = False  # type: ignore[attr-defined]

