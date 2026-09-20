"""File Worker for sandboxed filesystem operations.

spec §7 (Execution Layer), §10 (Prompt-Injection & Taint Model),
CONTRACT_MATRIX WORKER-001, WORKER-003, ADR-0013, ADR-0014
"""

from __future__ import annotations

import hashlib
import uuid

from ryu.pulse_bus.bus import PulseBus

from core.resources.manager import ResourceManager
from workers.base import BaseWorker, sanitize_text
from workers.contract import (
    Artifact,
    ExecutionError,
    ExecutionMetrics,
    ExecutionRequest,
    ExecutionResult,
    WorkerIdentity,
)
from workers.sandbox.filesystem import FilesystemSandbox


class FileWorker(BaseWorker):
    """Executes sandboxed file operations (read, write, delete, list)."""

    def __init__(
        self,
        identity: WorkerIdentity | None = None,
        bus: PulseBus | None = None,
        resource_manager: ResourceManager | None = None,
    ) -> None:
        ident = identity or WorkerIdentity(
            worker_id="file-worker-01",
            capability="file.*",
            space_id="default-space",
        )
        super().__init__(identity=ident, bus=bus, resource_manager=resource_manager)

    def _is_capability_supported(self, requested_capability: str) -> bool:
        if self.capability == "file.*":
            return requested_capability in ("file.read", "file.write", "file.delete", "file.list")
        return requested_capability == self.capability

    def _execute_sandboxed(self, request: ExecutionRequest) -> ExecutionResult:
        operation = request.arguments.get("operation", "read")
        raw_path = request.arguments.get("path")

        if not raw_path:
            err = ExecutionError(
                error_class="terminal.invalid_params",
                message="Missing 'path' argument for file operation",
                recoverable=False,
            )
            return ExecutionResult(request_id=request.request_id, status="failed", error=err)

        fs = FilesystemSandbox(request.sandbox_policy.fs_policy)

        if operation == "read":
            canonical = fs.validate_read(raw_path)
            if not canonical.exists() or not canonical.is_file():
                err = ExecutionError(
                    error_class="terminal.not_found",
                    message=f"File not found: {raw_path}",
                    recoverable=False,
                )
                return ExecutionResult(request_id=request.request_id, status="failed", error=err)

            content = canonical.read_text(encoding="utf-8", errors="replace")
            content_sanitized = sanitize_text(content)
            return ExecutionResult(
                request_id=request.request_id,
                status="ok",
                output_data=content_sanitized,
                metrics=ExecutionMetrics(),
            )

        elif operation == "write":
            content = request.arguments.get("content", "")
            canonical = fs.validate_write(raw_path)
            canonical.parent.mkdir(parents=True, exist_ok=True)
            canonical.write_text(content, encoding="utf-8")

            # Create artifact
            size = canonical.stat().st_size
            sha = hashlib.sha256(content.encode("utf-8")).hexdigest()
            artifact = Artifact(
                artifact_id=f"art-{uuid.uuid4().hex[:10]}",
                name=canonical.name,
                path=str(canonical),
                mime_type="text/plain",
                size_bytes=size,
                sha256=sha,
            )
            return ExecutionResult(
                request_id=request.request_id,
                status="ok",
                artifacts=[artifact],
                output_data={"path": str(canonical), "size_bytes": size, "sha256": sha},
                metrics=ExecutionMetrics(),
            )

        elif operation == "delete":
            canonical = fs.validate_write(raw_path)
            if canonical.exists():
                canonical.unlink()
            return ExecutionResult(
                request_id=request.request_id,
                status="ok",
                output_data={"deleted": str(canonical)},
                metrics=ExecutionMetrics(),
            )

        elif operation == "list":
            canonical = fs.validate_read(raw_path)
            if not canonical.exists() or not canonical.is_dir():
                err = ExecutionError(
                    error_class="terminal.not_found",
                    message=f"Directory not found: {raw_path}",
                    recoverable=False,
                )
                return ExecutionResult(request_id=request.request_id, status="failed", error=err)

            entries = [p.name for p in canonical.iterdir()]
            return ExecutionResult(
                request_id=request.request_id,
                status="ok",
                output_data={"entries": entries},
                metrics=ExecutionMetrics(),
            )

        else:
            err = ExecutionError(
                error_class="terminal.invalid_params",
                message=f"Unsupported file operation '{operation}'",
                recoverable=False,
            )
            return ExecutionResult(request_id=request.request_id, status="failed", error=err)
