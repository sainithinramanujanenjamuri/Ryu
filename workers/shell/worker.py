"""Shell Worker for sandboxed command execution.

spec §7 (Execution Layer), §10 (Prompt-Injection & Taint Model),
CONTRACT_MATRIX WORKER-001, WORKER-003, ADR-0013, ADR-0014
"""

from __future__ import annotations

import shlex
import sys
import tempfile
from pathlib import Path

from ryu.pulse_bus.bus import PulseBus

from core.resources.manager import ResourceManager
from workers.base import BaseWorker, sanitize_text
from workers.contract import (
    ExecutionError,
    ExecutionMetrics,
    ExecutionRequest,
    ExecutionResult,
    WorkerIdentity,
)
from workers.sandbox.manager import SandboxManager


class ShellWorker(BaseWorker):
    """Executes permitted command-line processes inside a sandbox."""

    DEFAULT_ALLOWED_COMMANDS = [
        "echo",
        "ls",
        "dir",
        "cat",
        "python",
        "git",
        "pytest",
        "ruff",
        "mypy",
    ]

    def __init__(
        self,
        identity: WorkerIdentity | None = None,
        bus: PulseBus | None = None,
        resource_manager: ResourceManager | None = None,
        base_working_dir: Path | str | None = None,
        allowed_commands: list[str] | None = None,
    ) -> None:
        ident = identity or WorkerIdentity(
            worker_id="shell-worker-01",
            capability="terminal.exec",
            space_id="default-space",
        )
        super().__init__(identity=ident, bus=bus, resource_manager=resource_manager)
        self.base_working_dir = Path(base_working_dir) if base_working_dir else None
        self.allowed_commands = allowed_commands or self.DEFAULT_ALLOWED_COMMANDS

    def _execute_sandboxed(self, request: ExecutionRequest) -> ExecutionResult:
        raw_command = request.arguments.get("command")
        if not raw_command:
            err = ExecutionError(
                error_class="terminal.invalid_params",
                message="Missing 'command' argument for shell execution",
                recoverable=False,
            )
            return ExecutionResult(request_id=request.request_id, status="failed", error=err)

        if isinstance(raw_command, str):
            cmd_parts = shlex.split(raw_command, posix=(sys.platform != "win32"))
        elif isinstance(raw_command, list):
            cmd_parts = [str(p) for p in raw_command]
        else:
            err = ExecutionError(
                error_class="terminal.invalid_params",
                message=f"Invalid command type '{type(raw_command).__name__}'",
                recoverable=False,
            )
            return ExecutionResult(request_id=request.request_id, status="failed", error=err)

        if not cmd_parts:
            err = ExecutionError(
                error_class="terminal.invalid_params",
                message="Empty command provided",
                recoverable=False,
            )
            return ExecutionResult(request_id=request.request_id, status="failed", error=err)

        binary_name = Path(cmd_parts[0]).name.lower()
        if binary_name.endswith(".exe"):
            binary_name = binary_name[:-4]

        # Security check: verify command is in allowlist
        if self.allowed_commands and binary_name not in self.allowed_commands:
            err = ExecutionError(
                error_class="terminal.permission_denied",
                message=f"Command '{binary_name}' is not in the shell execution allowlist",
                recoverable=False,
                details={"binary": binary_name, "allowed": self.allowed_commands},
            )
            return ExecutionResult(request_id=request.request_id, status="denied", error=err)

        working_dir = self.base_working_dir or Path(tempfile.mkdtemp(prefix="ryu_sh_"))
        working_dir.mkdir(parents=True, exist_ok=True)

        sandbox = SandboxManager(
            working_dir=working_dir,
            policy=request.sandbox_policy,
            limits=request.execution_limits,
        )

        input_data = request.arguments.get("input_data")
        exit_code, stdout_str, stderr_str = sandbox.run_command(
            command=cmd_parts,
            input_data=input_data,
        )

        metrics = ExecutionMetrics()

        if exit_code != 0:
            err = ExecutionError(
                error_class="terminal.invalid_params",
                message=sanitize_text(f"Command exited with code {exit_code}: {stderr_str}"),
                details={"exit_code": exit_code, "stderr": stderr_str},
                recoverable=False,
            )
            return ExecutionResult(
                request_id=request.request_id,
                status="failed",
                output_data={"stdout": stdout_str, "stderr": stderr_str, "exit_code": exit_code},
                error=err,
                metrics=metrics,
                logs=[stdout_str, stderr_str],
            )

        return ExecutionResult(
            request_id=request.request_id,
            status="ok",
            output_data={"stdout": stdout_str, "exit_code": 0},
            metrics=metrics,
            logs=[stdout_str],
        )
