"""Model Context Protocol (MCP) sandboxed stdio client.

Manages subprocess execution, JSON-RPC 2.0 message dispatch, handshake,
tool discovery, tool invocation, and timeout containment.

spec §5 (Extensibility Layer), docs/CONTRACT_MATRIX.md REG-006, ADR-0029 — Phase 9
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
import time
from typing import Any

from skills.contract import SkillError
from skills.mcp.protocol import (
    MCP_PROTOCOL_VERSION,
    JsonRpcRequest,
    JsonRpcResponse,
    MCPToolDefinition,
)
from skills.mcp.server_registry import MCPServerRegistration


class MCPClient:
    """
    Client managing a local sandboxed stdio MCP server subprocess.

    Enforces request-response correlation, per-call timeout bounds,
    and clean subprocess termination.
    """

    def __init__(
        self,
        server_config: MCPServerRegistration,
        default_timeout: float = 30.0,
        env: dict[str, str] | None = None,
        cwd: str | None = None,
    ) -> None:
        self.config = server_config
        self.default_timeout = default_timeout
        self.env = env
        self.cwd = cwd

        self._process: subprocess.Popen[str] | None = None
        self._lock = threading.Lock()
        self._request_counter = 0
        self._is_initialized = False

        self._reader_thread: threading.Thread | None = None
        self._response_queue: queue.Queue[str] = queue.Queue()
        self._stop_reader = threading.Event()

    @property
    def is_running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def start(self) -> None:
        """Spawn the MCP server process and perform initialize handshake."""
        with self._lock:
            if self.is_running:
                return

            cmd = [self.config.command] + list(self.config.args)

            # Restrict environment to allowed keys
            sub_env: dict[str, str] = {}
            if self.config.env_allowlist:
                for k in self.config.env_allowlist:
                    if k in os.environ:
                        sub_env[k] = os.environ[k]
            else:
                # Default minimal safe environment
                for k in ("PATH", "SYSTEMROOT", "TEMP", "TMP", "PYTHONPATH"):
                    if k in os.environ:
                        sub_env[k] = os.environ[k]

            if self.env:
                sub_env.update(self.env)

            try:
                self._process = subprocess.Popen(
                    cmd,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    bufsize=1,
                    cwd=self.cwd,
                    env=sub_env,
                )
            except Exception as e:
                raise SkillError(
                    error_class="terminal.process_crash",
                    message=f"Failed to spawn MCP server process '{self.config.server_id}': {e}",
                    retryable=False,
                )

            # Start background stdout reader thread
            self._stop_reader.clear()
            self._reader_thread = threading.Thread(
                target=self._read_stdout_loop,
                daemon=True,
                name=f"mcp-reader-{self.config.server_id}",
            )
            self._reader_thread.start()

            # Execute protocol handshake
            self._perform_handshake()

    def _read_stdout_loop(self) -> None:
        """Continually read JSON lines from stdout pipe and enqueue."""
        if not self._process or not self._process.stdout:
            return
        while not self._stop_reader.is_set():
            line = self._process.stdout.readline()
            if not line:
                break
            stripped = line.strip()
            if stripped:
                self._response_queue.put(stripped)

    def _perform_handshake(self) -> None:
        """Perform MCP initialize request and initialized notification."""
        init_req = JsonRpcRequest(
            id=self._next_id(),
            method="initialize",
            params={
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "ryu-ai", "version": "0.1.0"},
            },
        )
        resp = self._send_and_wait(init_req, timeout=10.0)
        if resp.is_error:
            self.stop()
            raise SkillError(
                error_class="terminal.protocol_violation",
                message=f"MCP handshake failed: {resp.error.message if resp.error else 'unknown'}",
                details=resp.error.to_dict() if resp.error else {},
            )

        # Send notifications/initialized (one-way notification)
        notif = JsonRpcRequest(method="notifications/initialized", params={})
        self._write_request(notif)
        self._is_initialized = True

    def _next_id(self) -> int:
        self._request_counter += 1
        return self._request_counter

    def _write_request(self, req: JsonRpcRequest) -> None:
        """Write a JSON-RPC request line to subprocess stdin."""
        if not self.is_running or not self._process or not self._process.stdin:
            raise SkillError(
                error_class="terminal.process_crash",
                message=f"MCP server '{self.config.server_id}' is not running.",
            )
        try:
            self._process.stdin.write(req.to_json() + "\n")
            self._process.stdin.flush()
        except (BrokenPipeError, OSError) as e:
            raise SkillError(
                error_class="terminal.process_crash",
                message=f"Failed writing to MCP stdin: {e}",
            )

    def _send_and_wait(self, req: JsonRpcRequest, timeout: float | None = None) -> JsonRpcResponse:
        """Send a request and wait for matching response line with timeout."""
        effective_timeout = timeout if timeout is not None else self.default_timeout
        self._write_request(req)

        deadline = time.time() + effective_timeout
        while time.time() < deadline:
            remaining = max(0.05, deadline - time.time())
            try:
                raw_line = self._response_queue.get(timeout=min(remaining, 1.0))
            except queue.Empty:
                if not self.is_running:
                    stderr_msg = ""
                    if self._process and self._process.stderr:
                        try:
                            stderr_msg = self._process.stderr.read()
                        except Exception:
                            pass
                    raise SkillError(
                        error_class="terminal.process_crash",
                        message=f"MCP server exited unexpectedly. Stderr: {stderr_msg}",
                    )
                continue

            try:
                data = json.loads(raw_line)
                # Ensure it matches our request ID
                if data.get("id") == req.id:
                    return JsonRpcResponse.from_dict(data)
                else:
                    # Ignore notifications or other messages
                    continue
            except json.JSONDecodeError as exc:
                raise SkillError(
                    error_class="terminal.protocol_violation",
                    message=f"Invalid JSON received from MCP server: {exc}",
                )

        # Timeout breached
        self.stop()
        raise SkillError(
            error_class="transient.timeout",
            message=f"MCP call timed out after {effective_timeout}s for method '{req.method}'.",
            retryable=True,
        )

    def list_tools(self, timeout: float | None = None) -> list[MCPToolDefinition]:
        """Discover tools exposed by connected MCP server via tools/list."""
        with self._lock:
            if not self.is_running:
                self.start()

            req = JsonRpcRequest(id=self._next_id(), method="tools/list", params={})
            resp = self._send_and_wait(req, timeout=timeout)
            if resp.is_error:
                raise SkillError(
                    error_class="terminal.tool_failure",
                    message=f"tools/list failed: {resp.error.message if resp.error else 'unknown'}",
                )

            tools_raw = resp.result.get("tools", []) if isinstance(resp.result, dict) else []
            return [MCPToolDefinition.from_dict(t) for t in tools_raw]

    def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Execute a tool via tools/call."""
        with self._lock:
            if not self.is_running:
                self.start()

            req = JsonRpcRequest(
                id=self._next_id(),
                method="tools/call",
                params={"name": name, "arguments": arguments},
            )
            resp = self._send_and_wait(req, timeout=timeout)
            if resp.is_error:
                err_msg = resp.error.message if resp.error else "Unknown MCP error"
                raise SkillError(
                    error_class="terminal.tool_failure",
                    message=f"Tool '{name}' failed: {err_msg}",
                    details=resp.error.to_dict() if resp.error else {},
                )

            return resp.result if isinstance(resp.result, dict) else {"result": resp.result}

    def stop(self) -> None:
        """Terminate the server process and clean up reader thread."""
        self._stop_reader.set()
        if self._process:
            try:
                if self._process.poll() is None:
                    self._process.terminate()
                    try:
                        self._process.wait(timeout=2.0)
                    except subprocess.TimeoutExpired:
                        self._process.kill()
                        self._process.wait(timeout=1.0)
            except Exception:
                pass
            finally:
                self._process = None
        self._is_initialized = False

    def __enter__(self) -> MCPClient:
        self.start()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.stop()

