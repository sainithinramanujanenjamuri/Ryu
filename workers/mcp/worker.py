"""MCP Worker: executes sandboxed Model Context Protocol capabilities.

spec §5 (Extensibility Layer), §7 (Execution Layer), §16 (Component Contracts),
CONTRACT_MATRIX WORKER-001..005, REG-006, ADR-0029, ADR-0031 — Phase 9
"""

from __future__ import annotations

import time

from ryu.pulse_bus.bus import PulseBus

from core.resources.manager import ResourceManager
from skills.contract import SkillError
from skills.mcp.client import MCPClient
from skills.mcp.server_registry import MCPServerRegistration
from workers.base import BaseWorker
from workers.contract import (
    ExecutionError,
    ExecutionMetrics,
    ExecutionRequest,
    ExecutionResult,
    WorkerIdentity,
)


class MCPWorker(BaseWorker):
    """
    Executes MCP tools within sandboxed stdio subprocesses.

    Enforces:
    - Pre-dispatch capability check
    - Sandboxed execution (ADR-0031)
    - Output taint marking (ADR-0032)
    - Pulse publication (worker.tool.called / succeeded / failed)
    """

    def __init__(
        self,
        server_config: MCPServerRegistration,
        identity: WorkerIdentity | None = None,
        bus: PulseBus | None = None,
        resource_manager: ResourceManager | None = None,
        client: MCPClient | None = None,
    ) -> None:
        ident = identity or WorkerIdentity(
            worker_id=f"mcp-worker-{server_config.server_id}",
            capability=f"mcp.{server_config.server_id}.*",
            space_id="default-space",
        )
        super().__init__(identity=ident, bus=bus, resource_manager=resource_manager)
        self.server_config = server_config
        self._client = client or MCPClient(server_config)

    def _execute_sandboxed(self, request: ExecutionRequest) -> ExecutionResult:
        start_time = time.time()

        # Parse tool name from request capability (mcp.<server_id>.<tool_name>) or arguments
        tool_name = request.arguments.get("tool_name")
        if not tool_name:
            if request.capability.startswith(f"mcp.{self.server_config.server_id}."):
                tool_name = request.capability.split(".", 2)[2]
            else:
                err = ExecutionError(
                    error_class="terminal.invalid_params",
                    message=f"Missing tool_name in execution request for capability '{request.capability}'",
                    recoverable=False,
                )
                return ExecutionResult(
                    request_id=request.request_id,
                    status="failed",
                    error=err,
                )

        tool_args = request.arguments.get("parameters", {})
        timeout = request.execution_limits.timeout_seconds

        try:
            raw_result = self._client.call_tool(
                name=tool_name,
                arguments=tool_args,
                timeout=timeout,
            )
            duration = time.time() - start_time
            metrics = ExecutionMetrics(
                duration_seconds=duration,
                process_count=1,
            )

            return ExecutionResult(
                request_id=request.request_id,
                status="ok",
                output_data=raw_result,
                taint=True,  # Untrusted external tool output (ADR-0032)
                metrics=metrics,
            )
        except SkillError as exc:
            duration = time.time() - start_time
            err = ExecutionError(
                error_class=exc.error_class,
                message=exc.message,
                retryable=exc.retryable,
                details=exc.details,
            )
            return ExecutionResult(
                request_id=request.request_id,
                status="failed",
                error=err,
                taint=True,
                metrics=ExecutionMetrics(duration_seconds=duration),
            )
        except Exception as exc:
            duration = time.time() - start_time
            err = ExecutionError(
                error_class="terminal.tool_failure",
                message=f"Unexpected error in MCPWorker: {exc}",
                recoverable=False,
            )
            return ExecutionResult(
                request_id=request.request_id,
                status="failed",
                error=err,
                taint=True,
                metrics=ExecutionMetrics(duration_seconds=duration),
            )

    def close(self) -> None:
        """Clean up client and subprocess."""
        if self._client:
            self._client.stop()

