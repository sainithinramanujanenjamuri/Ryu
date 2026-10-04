"""Autonomous Test Runner Worker (ADR-0044, Phase 14.5).

Coordinates sandboxed test execution, deterministic test report parsing,
evidence extraction, content-addressed artifact production, cryptographic
provenance tracking, and typed test.executed pulse emission (EVIDENCE-001..003).

Strict Boundaries:
- Does NOT diagnose failures.
- Does NOT propose repairs or replans.
- Does NOT call ConvergenceEngine.
- Strictly stops at structured evidence collection.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from ryu.pulse_bus.bus import PulseBus
from ryu.pulse_bus.pulse import Pulse, Severity

from core.space.artifact_paths import get_space_artifact_dir, resolve_artifact_path
from core.space.research_protocol import (
    ProvenanceRecord,
    SourceIdentity,
    TransformationStage,
    compute_sha256,
)
from core.space.test_execution_protocol import (
    TestCommand,
    TestCommandValidationError,
    TestExecutionProtocol,
    TestExecutionRequest,
    TestExecutionStatus,
)
from workers.base import BaseWorker, sanitize_text
from workers.contract import (
    Artifact,
    ExecutionError,
    ExecutionMetrics,
    ExecutionRequest,
    ExecutionResult,
    WorkerIdentity,
)
from workers.test_runner.executor import SandboxedtestExecutor

logger = logging.getLogger(__name__)


class TestRunnerWorker(BaseWorker):
    """Capability worker executing repository tests under strict SCCA sandbox governance."""

    __test__ = False

    def __init__(
        self,
        identity: WorkerIdentity | None = None,
        bus: PulseBus | None = None,
        resource_manager: Any | None = None,
        base_working_dir: Path | str | None = None,
        executor: TestExecutionProtocol | None = None,
    ) -> None:
        ident = identity or WorkerIdentity(
            worker_id="test-runner-worker-01",
            capability="test.execute",
            space_id="default-space",
        )
        super().__init__(identity=ident, bus=bus, resource_manager=resource_manager)
        self.base_working_dir = Path(base_working_dir) if base_working_dir else None
        self.executor = executor or SandboxedtestExecutor()

    def _is_capability_supported(self, requested_capability: str) -> bool:
        """Verify capability support for test execution."""
        if requested_capability in ("test.execute", "test.run", "test", "test.*"):
            return True
        if requested_capability.startswith("test.") or requested_capability.startswith("test_runner."):
            return True
        return False

    def _execute_sandboxed(self, request: ExecutionRequest) -> ExecutionResult:
        """Execute sandboxed repository test run without modifying repository files."""
        args = request.arguments or {}

        # 1. Resolve and validate repository root
        raw_root = args.get("repository_root") or args.get("root") or args.get("path")
        if not raw_root:
            err = ExecutionError(
                error_class="terminal.invalid_params",
                message="Test execution request requires 'repository_root' argument",
                recoverable=False,
            )
            return ExecutionResult(
                request_id=request.request_id,
                status="failed",
                error=err,
                metrics=ExecutionMetrics(),
            )

        repo_path = Path(raw_root).resolve()
        if not repo_path.exists() or not repo_path.is_dir():
            err = ExecutionError(
                error_class="terminal.invalid_params",
                message=f"Repository root '{raw_root}' does not exist or is not a directory",
                recoverable=False,
            )
            return ExecutionResult(
                request_id=request.request_id,
                status="failed",
                error=err,
                metrics=ExecutionMetrics(),
            )

        # 2. Build structured TestCommand
        runner = str(args.get("runner", "pytest"))
        raw_args = args.get("arguments", ["-v"])
        if isinstance(raw_args, list):
            cmd_args = tuple(str(a) for a in raw_args)
        elif isinstance(raw_args, tuple):
            cmd_args = tuple(str(a) for a in raw_args)
        elif isinstance(raw_args, str):
            cmd_args = tuple(raw_args.split())
        else:
            cmd_args = ("-v",)

        raw_targets = args.get("target_paths", [])
        if isinstance(raw_targets, list):
            target_paths = tuple(str(t) for t in raw_targets)
        elif isinstance(raw_targets, tuple):
            target_paths = tuple(str(t) for t in raw_targets)
        elif isinstance(raw_targets, str):
            target_paths = (raw_targets,)
        else:
            target_paths = ()

        timeout = float(args.get("timeout", args.get("timeout_seconds", 60.0)))
        working_directory = args.get("working_directory")

        try:
            cmd = TestCommand(
                runner=runner,
                arguments=cmd_args,
                target_paths=target_paths,
                working_directory=working_directory,
                timeout_seconds=timeout,
            )
        except TestCommandValidationError as exc:
            err = ExecutionError(
                error_class="terminal.security_violation",
                message=sanitize_text(f"Command validation failed: {exc}"),
                recoverable=False,
            )
            return ExecutionResult(
                request_id=request.request_id,
                status="denied",
                error=err,
                metrics=ExecutionMetrics(),
            )

        # 3. Construct TestExecutionRequest
        repo_id = str(args.get("repository_id", f"repo-{compute_sha256(str(repo_path))[:12]}"))
        expected_repo_hash = args.get("expected_repo_hash")
        associated_patch_id = args.get("associated_patch_id")
        plan_ver = max(1, int(request.plan_version or 1))

        test_req = TestExecutionRequest(
            request_id=request.request_id,
            task_id=request.task_id,
            space_id=request.space_id,
            plan_version=plan_ver,
            command=cmd,
            repository_id=repo_id,
            repository_root=str(repo_path),
            expected_repo_hash=expected_repo_hash,
            associated_patch_id=associated_patch_id,
            timeout_seconds=timeout,
        )

        # 4. Execute via Sandboxed Executor
        report = self.executor.execute_tests(request.space_id, test_req)

        # 5. Build Provenance Record (PROVENANCE-001..003)
        report_dict = {
            "execution_id": report.execution_id,
            "task_id": report.task_id,
            "space_id": report.space_id,
            "plan_version": report.plan_version,
            "repository_id": report.repository_id,
            "runner": report.runner,
            "status": report.status.value,
            "exit_code": report.exit_code,
            "duration_seconds": report.duration_seconds,
            "total_tests": report.total_tests,
            "passed_tests": report.passed_tests,
            "failed_tests": report.failed_tests,
            "skipped_tests": report.skipped_tests,
            "errored_tests": report.errored_tests,
            "parser_status": report.parser_status,
            "sandbox_status": report.sandbox_status,
            "is_all_passed": report.is_all_passed,
            "associated_patch_id": report.associated_patch_id,
            "tested_repo_hash": report.tested_repo_hash,
            "started_at": report.started_at.isoformat(),
            "completed_at": report.completed_at.isoformat(),
            "failures": [
                {
                    "test_node_id": f.test_node_id,
                    "failure_type": f.failure_type,
                    "message": f.message,
                    "file_path": f.file_path,
                }
                for f in report.failures
            ],
        }
        report_json = json.dumps(report_dict, indent=2)
        report_hash = compute_sha256(report_json)

        prov_source = SourceIdentity(
            source_id=repo_id,
            source_type="repository_test",
            locator=str(repo_path),
            space_id=request.space_id,
        )
        stage = TransformationStage.RAW
        parent_prov = None
        if associated_patch_id:
            stage = TransformationStage.EXTRACTED
            parent_prov = f"prov-{associated_patch_id}-patch"

        prov = ProvenanceRecord(
            provenance_id=f"prov-{request.task_id}-test",
            source_identity=prov_source,
            space_id=request.space_id,
            task_id=request.task_id,
            plan_version=plan_ver,
            producer=self.worker_id,
            content_hash=report_hash,
            transformation_stage=stage,
            parent_provenance_id=parent_prov,
        )

        # 6. Content-Addressed Artifacts & Lineage (EVIDENCE-003)
        artifacts: list[Artifact] = []
        if self.base_working_dir:
            art_dir = get_space_artifact_dir(self.base_working_dir, request.space_id, "test_runner")
            art_dir.mkdir(parents=True, exist_ok=True)

            # Report artifact
            rep_path = resolve_artifact_path(
                self.base_working_dir, request.space_id, "test_runner", f"{request.task_id}_test_report.json"
            )
            rep_bytes = report_json.encode("utf-8")
            rep_path.write_bytes(rep_bytes)
            artifacts.append(
                Artifact(
                    artifact_id=f"art-{request.task_id}-test-report",
                    name=f"{request.task_id}_test_report.json",
                    path=str(rep_path),
                    mime_type="application/json",
                    size_bytes=len(rep_bytes),
                    sha256=compute_sha256(rep_bytes),
                    space_id=request.space_id,
                    metadata={
                        "provenance_id": prov.provenance_id,
                        "status": report.status.value,
                        "lineage": {
                            "derived_from": f"repo:{repo_id}",
                            "validates": associated_patch_id or f"repo:{repo_id}",
                        },
                    },
                )
            )

            # Stdout artifact
            stdout_bytes = report.stdout_summary.encode("utf-8")
            stdout_path = resolve_artifact_path(
                self.base_working_dir, request.space_id, "test_runner", f"{request.task_id}_test_stdout.log"
            )
            stdout_path.write_bytes(stdout_bytes)
            artifacts.append(
                Artifact(
                    artifact_id=f"art-{request.task_id}-test-stdout",
                    name=f"{request.task_id}_test_stdout.log",
                    path=str(stdout_path),
                    mime_type="text/plain",
                    size_bytes=len(stdout_bytes),
                    sha256=compute_sha256(stdout_bytes),
                    space_id=request.space_id,
                    metadata={"provenance_id": prov.provenance_id},
                )
            )

            # Failures trace artifact (if failures occurred)
            if report.failures:
                fail_json = json.dumps(report_dict["failures"], indent=2)
                fail_path = resolve_artifact_path(
                    self.base_working_dir, request.space_id, "test_runner", f"{request.task_id}_test_failures.json"
                )
                fail_bytes = fail_json.encode("utf-8")
                fail_path.write_bytes(fail_bytes)
                artifacts.append(
                    Artifact(
                        artifact_id=f"art-{request.task_id}-test-failures",
                        name=f"{request.task_id}_test_failures.json",
                        path=str(fail_path),
                        mime_type="application/json",
                        size_bytes=len(fail_bytes),
                        sha256=compute_sha256(fail_bytes),
                        space_id=request.space_id,
                        metadata={"provenance_id": prov.provenance_id, "failed_count": len(report.failures)},
                    )
                )

        # 7. Pulse Emission (test.executed matching schema exactly)
        sev = Severity.INFO if report.is_all_passed else Severity.WARNING
        pulse = Pulse(
            type="test.executed",
            payload={
                "total_tests": report.total_tests,
                "passed_tests": report.passed_tests,
                "failed_tests": report.failed_tests,
                "skipped_tests": report.skipped_tests,
                "exit_code": report.exit_code,
                "duration_seconds": round(report.duration_seconds, 4),
                "task_id": request.task_id,
                "plan_version": plan_ver,
            },
            space_id=request.space_id,
            source=self.worker_id,
            correlation_id=request.correlation_id or request.task_id,
            severity=sev,
            taint=True,  # Untrusted execution output is tainted (Law 4)
        )
        if self.bus:
            self.bus.publish(pulse)

        # 8. Determine ExecutionResult status
        output_data = {
            "status": report.status.value,
            "exit_code": report.exit_code,
            "total_tests": report.total_tests,
            "passed_tests": report.passed_tests,
            "failed_tests": report.failed_tests,
            "skipped_tests": report.skipped_tests,
            "errored_tests": report.errored_tests,
            "is_all_passed": report.is_all_passed,
            "parser_status": report.parser_status,
            "sandbox_status": report.sandbox_status,
            "provenance_id": prov.provenance_id,
            "provenance_canonical_hash": prov.canonical_hash,
            "associated_patch_id": report.associated_patch_id,
            "tested_repo_hash": report.tested_repo_hash,
            "stdout": report.stdout_summary,
            "stderr": report.stderr_summary,
            "taint": True,
        }

        if report.status == TestExecutionStatus.TIMED_OUT:
            err = ExecutionError(
                error_class="transient.timeout",
                message=sanitize_text(f"Test execution timed out after {timeout}s"),
                recoverable=True,
            )
            return ExecutionResult(
                request_id=request.request_id,
                status="timeout",
                artifacts=artifacts,
                output_data=output_data,
                error=err,
                taint=True,
                metrics=ExecutionMetrics(duration_seconds=report.duration_seconds),
                logs=[f"Test runner timed out ({timeout}s)"],
            )

        if report.status == TestExecutionStatus.SANDBOX_VIOLATION:
            err = ExecutionError(
                error_class="terminal.security_violation",
                message=sanitize_text(report.stderr_summary or "Sandbox violation detected"),
                recoverable=False,
            )
            return ExecutionResult(
                request_id=request.request_id,
                status="denied",
                artifacts=artifacts,
                output_data=output_data,
                error=err,
                taint=True,
                metrics=ExecutionMetrics(duration_seconds=report.duration_seconds),
            )

        if report.status == TestExecutionStatus.REJECTED:
            err = ExecutionError(
                error_class="terminal.invalid_params",
                message=sanitize_text(report.stderr_summary or "Test execution request rejected"),
                recoverable=False,
            )
            return ExecutionResult(
                request_id=request.request_id,
                status="failed",
                artifacts=artifacts,
                output_data=output_data,
                error=err,
                taint=True,
                metrics=ExecutionMetrics(duration_seconds=report.duration_seconds),
            )

        # Both VERIFIED and PROCESS_FAILED count as successful capability execution (we produced evidence!)
        log_msg = (
            f"Test execution {report.status.value}: {report.passed_tests}/{report.total_tests} passed, "
            f"{report.failed_tests} failed ({report.duration_seconds:.2f}s)"
        )
        return ExecutionResult(
            request_id=request.request_id,
            status="ok",
            artifacts=artifacts,
            output_data=output_data,
            taint=True,
            metrics=ExecutionMetrics(duration_seconds=report.duration_seconds),
            logs=[log_msg],
        )
