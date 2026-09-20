"""Unit tests for Process Sandbox, environment stripping, timeout, and process-tree termination.

spec §7, §10, CONTRACT_MATRIX WORKER-003, ADR-0014
"""

import os
import sys
import tempfile
from pathlib import Path

import pytest

from workers.contract import ExecutionLimits
from workers.sandbox.process import ProcessSandbox, sanitize_environment, terminate_process_tree


def test_sanitize_environment_strips_sensitive_keys() -> None:
    # Set a sensitive host key
    os.environ["OPENAI_API_KEY"] = "sk-test-secret-12345"
    os.environ["AWS_SECRET_ACCESS_KEY"] = "aws-test-secret"
    try:
        clean = sanitize_environment(allowlist=["PATH", "TEMP"])
        assert "OPENAI_API_KEY" not in clean
        assert "AWS_SECRET_ACCESS_KEY" not in clean
        assert "PATH" in clean or "TEMP" in clean
    finally:
        os.environ.pop("OPENAI_API_KEY", None)
        os.environ.pop("AWS_SECRET_ACCESS_KEY", None)


def test_process_runs_in_isolated_directory() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        sandbox = ProcessSandbox(working_dir=tmpdir)
        code = "import os; print(os.getcwd())"
        ret, stdout_str, stderr_str = sandbox.run_command([sys.executable, "-c", code])
        assert ret == 0
        assert Path(stdout_str.strip()).resolve() == Path(tmpdir).resolve()


def test_process_watchdog_timeout() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        limits = ExecutionLimits(timeout_seconds=0.5)
        sandbox = ProcessSandbox(working_dir=tmpdir, limits=limits)
        code = "import time; time.sleep(5)"
        with pytest.raises(TimeoutError, match="exceeded timeout limit"):
            sandbox.run_command([sys.executable, "-c", code])


def test_terminate_process_tree_handles_invalid_pid() -> None:
    # Verify no crash on invalid or non-existent PID
    terminate_process_tree(-1)
    terminate_process_tree(99999999)
