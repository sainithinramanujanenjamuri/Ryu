"""Sandboxed Test Executor (ADR-0044, EVIDENCE-001, EVIDENCE-002).

Executes repository tests inside a strictly controlled process sandbox with
stripped environment variables, default-disabled network access, repository boundary
containment, and bounded timeout enforcement.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from pathlib import Path

from core.space.research_protocol import compute_sha256
from core.space.test_execution_protocol import (
    TestCommandValidationError,
    TestExecutionError,
    TestExecutionProtocol,
    TestExecutionReport,
    TestExecutionRequest,
    TestExecutionStatus,
)
from workers.contract import ExecutionLimits, NetworkPolicy, NetworkPolicyMode, SandboxPolicy
from workers.sandbox.manager import SandboxManager
from workers.test_runner.command_validator import validate_and_resolve_test_command
from workers.test_runner.parser import TestOutputParser

logger = logging.getLogger(__name__)


class SandboxedtestExecutor(TestExecutionProtocol):
    """Executes repository tests within a bounded sandbox adhering to SCCA Law 1 and Law 2."""

    def __init__(
        self,
        default_timeout: float = 60.0,
        allow_network: bool = False,
    ) -> None:
        self.default_timeout = default_timeout
        self.allow_network = allow_network

    def execute_tests(
        self,
        space_id: str,
        request: TestExecutionRequest,
    ) -> TestExecutionReport:
        """Execute sandboxed tests and return a verified TestExecutionReport (EVIDENCE-001)."""
        exec_id = f"test-exec-{request.task_id}-{int(time.time())}"
        started_at = datetime.now(timezone.utc)
        repo_root = Path(request.repository_root).resolve()

        # 1. Space Isolation check (SCCA Law 1)
        if space_id != request.space_id:
            raise TestExecutionError(
                f"Space isolation violation: requested space '{space_id}' mismatches request '{request.space_id}'"
            )

        # 2. Patch -> Test State Continuity check (Prompt §21)
        current_repo_hash = self._compute_repo_state_hash(repo_root)
        if request.expected_repo_hash and current_repo_hash != request.expected_repo_hash:
            return TestExecutionReport(
                execution_id=exec_id,
                task_id=request.task_id,
                space_id=space_id,
                plan_version=request.plan_version,
                repository_id=request.repository_id,
                runner=request.command.runner,
                status=TestExecutionStatus.REJECTED,
                exit_code=-1,
                duration_seconds=0.0,
                total_tests=0,
                passed_tests=0,
                failed_tests=0,
                skipped_tests=0,
                errored_tests=0,
                failures=(),
                stdout_summary="",
                stderr_summary=f"Repository state divergence: expected hash '{request.expected_repo_hash}', observed '{current_repo_hash}'",
                parser_status="inconclusive",
                sandbox_status="ok",
                associated_patch_id=request.associated_patch_id,
                tested_repo_hash=current_repo_hash,
                started_at=started_at,
                completed_at=datetime.now(timezone.utc),
            )

        # 3. Validate command and resolve execution arguments
        try:
            argv, working_dir = validate_and_resolve_test_command(request.command, repo_root)
        except TestCommandValidationError as exc:
            return TestExecutionReport(
                execution_id=exec_id,
                task_id=request.task_id,
                space_id=space_id,
                plan_version=request.plan_version,
                repository_id=request.repository_id,
                runner=request.command.runner,
                status=TestExecutionStatus.REJECTED,
                exit_code=-1,
                duration_seconds=0.0,
                total_tests=0,
                passed_tests=0,
                failed_tests=0,
                skipped_tests=0,
                errored_tests=0,
                failures=(),
                stdout_summary="",
                stderr_summary=f"Command validation failed: {exc}",
                parser_status="inconclusive",
                sandbox_status="violation",
                associated_patch_id=request.associated_patch_id,
                tested_repo_hash=current_repo_hash,
                started_at=started_at,
                completed_at=datetime.now(timezone.utc),
            )

        # 4. Configure Sandboxed Process execution
        effective_timeout = min(
            request.timeout_seconds,
            request.command.timeout_seconds or self.default_timeout,
        )

        net_mode = NetworkPolicyMode.ALLOWED if self.allow_network else NetworkPolicyMode.DISABLED
        sandbox_policy = SandboxPolicy(
            network_policy=NetworkPolicy(mode=net_mode, allow_loopback=False),
        )
        exec_limits = ExecutionLimits(
            timeout_seconds=effective_timeout,
            max_output_bytes=request.command.limits.max_output_bytes,
        )

        sandbox = SandboxManager(
            working_dir=working_dir,
            policy=sandbox_policy,
            limits=exec_limits,
        )

        # 5. Execute Command under Sandbox
        start_mono = time.monotonic()
        timed_out = False
        exit_code = -1
        stdout_str = ""
        stderr_str = ""

        try:
            exit_code, stdout_str, stderr_str = sandbox.run_command(
                command=argv,
                input_data=None,
            )
        except TimeoutError:
            timed_out = True
            exit_code = 124  # Standard timeout exit code
            stderr_str = f"Execution timed out after {effective_timeout}s"
        except PermissionError as exc:
            return TestExecutionReport(
                execution_id=exec_id,
                task_id=request.task_id,
                space_id=space_id,
                plan_version=request.plan_version,
                repository_id=request.repository_id,
                runner=request.command.runner,
                status=TestExecutionStatus.SANDBOX_VIOLATION,
                exit_code=-1,
                duration_seconds=time.monotonic() - start_mono,
                total_tests=0,
                passed_tests=0,
                failed_tests=0,
                skipped_tests=0,
                errored_tests=0,
                failures=(),
                stdout_summary="",
                stderr_summary=f"Sandbox security violation: {exc}",
                parser_status="inconclusive",
                sandbox_status="violation",
                associated_patch_id=request.associated_patch_id,
                tested_repo_hash=current_repo_hash,
                started_at=started_at,
                completed_at=datetime.now(timezone.utc),
            )
        except Exception as exc:
            logger.error(f"Test execution infrastructure failed: {exc}", exc_info=True)
            exit_code = 1
            stderr_str = f"Infrastructure error: {exc}"

        elapsed = time.monotonic() - start_mono
        completed_at = datetime.now(timezone.utc)

        # 6. Parse Output & Determine Status
        if timed_out:
            return TestExecutionReport(
                execution_id=exec_id,
                task_id=request.task_id,
                space_id=space_id,
                plan_version=request.plan_version,
                repository_id=request.repository_id,
                runner=request.command.runner,
                status=TestExecutionStatus.TIMED_OUT,
                exit_code=exit_code,
                duration_seconds=elapsed,
                total_tests=0,
                passed_tests=0,
                failed_tests=0,
                skipped_tests=0,
                errored_tests=0,
                failures=(),
                stdout_summary=stdout_str[:1000],
                stderr_summary=stderr_str[:1000],
                parser_status="inconclusive",
                sandbox_status="ok",
                associated_patch_id=request.associated_patch_id,
                tested_repo_hash=current_repo_hash,
                started_at=started_at,
                completed_at=completed_at,
            )

        total, passed, failed, skipped, errors, dur, failures, p_status = TestOutputParser.parse(
            runner=request.command.runner,
            exit_code=exit_code,
            stdout=stdout_str,
            stderr=stderr_str,
            elapsed_duration=elapsed,
        )

        # Determine terminal status
        if p_status != "ok":
            status = TestExecutionStatus.INCONCLUSIVE
        elif exit_code == 0 and total > 0 and failed == 0 and errors == 0:
            status = TestExecutionStatus.VERIFIED
        elif exit_code != 0 and (failed > 0 or errors > 0):
            status = TestExecutionStatus.PROCESS_FAILED
        elif exit_code != 0:
            status = TestExecutionStatus.PROCESS_FAILED
        else:
            status = TestExecutionStatus.INCONCLUSIVE

        return TestExecutionReport(
            execution_id=exec_id,
            task_id=request.task_id,
            space_id=space_id,
            plan_version=request.plan_version,
            repository_id=request.repository_id,
            runner=request.command.runner,
            status=status,
            exit_code=exit_code,
            duration_seconds=dur,
            total_tests=total,
            passed_tests=passed,
            failed_tests=failed,
            skipped_tests=skipped,
            errored_tests=errors,
            failures=failures,
            stdout_summary=stdout_str[:2000],
            stderr_summary=stderr_str[:2000],
            parser_status=p_status,
            sandbox_status="ok",
            associated_patch_id=request.associated_patch_id,
            tested_repo_hash=current_repo_hash,
            started_at=started_at,
            completed_at=completed_at,
        )

    def _compute_repo_state_hash(self, repo_root: Path) -> str:
        """Compute lightweight deterministic fingerprint of repository files."""
        items: list[str] = []
        for p in sorted(repo_root.rglob("*.py")):
            if ".git" in p.parts or "__pycache__" in p.parts:
                continue
            try:
                rel = p.relative_to(repo_root).as_posix()
                items.append(f"{rel}:{compute_sha256(p.read_bytes())}")
            except OSError:
                pass
        return compute_sha256("\n".join(items).encode("utf-8"))
