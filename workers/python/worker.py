"""Python Worker for sandboxed code execution.

spec §7 (Execution Layer), §10 (Prompt-Injection & Taint Model),
CONTRACT_MATRIX WORKER-001, WORKER-003, ADR-0013, ADR-0014
"""

from __future__ import annotations

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
    NetworkPolicyMode,
    WorkerIdentity,
)
from workers.sandbox.manager import SandboxManager


class PythonWorker(BaseWorker):
    """Executes Python code snippets inside a sandboxed subprocess."""

    def __init__(
        self,
        identity: WorkerIdentity | None = None,
        bus: PulseBus | None = None,
        resource_manager: ResourceManager | None = None,
        base_working_dir: Path | str | None = None,
    ) -> None:
        ident = identity or WorkerIdentity(
            worker_id="python-worker-01",
            capability="python.eval_sandboxed",
            space_id="default-space",
        )
        super().__init__(identity=ident, bus=bus, resource_manager=resource_manager)
        self.base_working_dir = Path(base_working_dir) if base_working_dir else None

    def _execute_sandboxed(self, request: ExecutionRequest) -> ExecutionResult:
        code = request.arguments.get("code")
        if not code or not isinstance(code, str):
            err = ExecutionError(
                error_class="terminal.invalid_params",
                message="Missing or invalid 'code' parameter for Python execution",
                recoverable=False,
            )
            return ExecutionResult(
                request_id=request.request_id,
                status="failed",
                error=err,
            )

        working_dir = self.base_working_dir or Path(tempfile.mkdtemp(prefix="ryu_py_"))
        working_dir.mkdir(parents=True, exist_ok=True)

        sandbox = SandboxManager(
            working_dir=working_dir,
            policy=request.sandbox_policy,
            limits=request.execution_limits,
        )

        # Network policy preamble for child subprocess
        net_policy = request.sandbox_policy.network_policy
        preamble = ""
        if net_policy.mode == NetworkPolicyMode.DISABLED:
            preamble = (
                "import socket\n"
                "def _guard_connect(*args, **kwargs):\n"
                "    raise PermissionError('Network egress denied by policy: mode=disabled')\n"
                "socket.socket.connect = _guard_connect\n\n"
            )
        elif net_policy.mode == NetworkPolicyMode.RESTRICTED:
            allowed_hosts = net_policy.allowed_hosts
            allowed_ports = net_policy.allowed_ports
            allow_loopback = net_policy.allow_loopback
            preamble = (
                "import socket\n"
                f"_ALLOWED_HOSTS = {allowed_hosts!r}\n"
                f"_ALLOWED_PORTS = {allowed_ports!r}\n"
                f"_ALLOW_LOOPBACK = {allow_loopback!r}\n"
                "def _guard_connect(sock, addr):\n"
                "    host = addr[0] if isinstance(addr, tuple) else str(addr)\n"
                "    port = addr[1] if isinstance(addr, tuple) and len(addr) > 1 else 0\n"
                "    if host in ('127.0.0.1', 'localhost', '::1') and not _ALLOW_LOOPBACK:\n"
                "        raise PermissionError(f'Loopback network egress denied: {host}:{port}')\n"
                "    if _ALLOWED_HOSTS and host not in _ALLOWED_HOSTS:\n"
                "        raise PermissionError(f'Network egress host denied: {host}')\n"
                "    if _ALLOWED_PORTS and port not in _ALLOWED_PORTS:\n"
                "        raise PermissionError(f'Network egress port denied: {port}')\n"
                "    return _orig_connect(sock, addr)\n"
                "_orig_connect = socket.socket.connect\n"
                "socket.socket.connect = _guard_connect\n\n"
            )

        # Write code to isolated file
        script_path = working_dir / "execution_payload.py"
        script_path.write_text(preamble + code, encoding="utf-8")

        # Execute using the running Python interpreter
        cmd = [sys.executable, "-u", str(script_path)]
        input_data = request.arguments.get("input_data")

        try:
            exit_code, stdout_str, stderr_str = sandbox.run_command(
                command=cmd,
                input_data=input_data,
            )

            metrics = ExecutionMetrics()

            if exit_code != 0:
                err = ExecutionError(
                    error_class="terminal.invalid_params",
                    message=sanitize_text(
                        f"Python script exited with code {exit_code}: {stderr_str}"
                    ),
                    details={"exit_code": exit_code, "stderr": stderr_str},
                    recoverable=False,
                )
                return ExecutionResult(
                    request_id=request.request_id,
                    status="failed",
                    output_data={
                        "stdout": stdout_str,
                        "stderr": stderr_str,
                        "exit_code": exit_code,
                    },
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
        finally:
            # Clean up temp script if created
            try:
                if script_path.exists():
                    script_path.unlink()
            except Exception:
                pass
