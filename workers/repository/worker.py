"""Autonomous Repository Worker for capability-controlled repository inspection.

spec §7 (Execution Layer), §10 (Prompt-Injection & Taint Model), ADR-0044,
CONTRACT_MATRIX REPO-001 — Phase 14.3

Enforces:
1. SCCA Law 1 (Space Isolation): Repository access and artifacts are strictly space-bound.
2. SCCA Law 2 (Capabilities Requested, Never Owned): Requires explicit capability authorization (repository.inspect).
3. SCCA Law 4 (Knowledge Belongs to Space First): Repository snapshots and AST reports belong to Space.
4. SCCA Law 6 (Deterministic Containment): Traversal or security failures map to failure taxonomy.
5. TAINT-001: External repository contents enter with taint: True unconditionally.
6. WORKER-002: Repository files, comments, and READMEs are strictly passive data, never executable instructions.
7. Zero Modification: Inspection phase is strictly read-only; no files, git state, or branches are altered.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ryu.pulse_bus.bus import PulseBus

from core.resources.manager import ResourceManager
from core.space.research_protocol import (
    ProvenanceRecord,
    SourceIdentity,
    TransformationStage,
    compute_sha256,
)
from core.space.repository_protocol import (
    FileTooLargeError,
    PathTraversalError,
    RepositoryError,
    RepositoryIdentity,
    RepositoryInspectionResult,
    RepositoryLimitExceededError,
    RepositoryNotAuthorizedError,
    RepositoryNotFoundError,
    RepositoryRootInvalidError,
    SecretAccessDeniedError,
    SymlinkSecurityError,
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
from workers.repository.inspector import LocalRepositoryInspector

logger = logging.getLogger(__name__)


class RepositoryWorker(BaseWorker):
    """Capability worker executing bounded repository inspection under SCCA governance.

    Capabilities supported:
    - repository.inspect
    - repo.inspect
    - repository.*
    - repo.*
    """

    def __init__(
        self,
        identity: WorkerIdentity | None = None,
        bus: PulseBus | None = None,
        resource_manager: ResourceManager | None = None,
        base_working_dir: Path | str | None = None,
        inspector: LocalRepositoryInspector | None = None,
        repository_identity: RepositoryIdentity | None = None,
    ) -> None:
        ident = identity or WorkerIdentity(
            worker_id="repository-worker-01",
            capability="repository.inspect",
            space_id="default-space",
        )
        if repository_identity is not None and repository_identity.space_id != ident.space_id:
            raise RepositoryNotAuthorizedError(
                f"Repository '{repository_identity.repository_id}' is authorized for space '{repository_identity.space_id}', not authorized for worker space '{ident.space_id}'"
            )
        super().__init__(identity=ident, bus=bus, resource_manager=resource_manager)
        self.base_working_dir = Path(base_working_dir) if base_working_dir else None
        self.inspector = inspector
        self.repository_identity = repository_identity

    def _is_capability_supported(self, requested_capability: str) -> bool:
        if requested_capability in ("repository.inspect", "repo.inspect", "repository", "repo"):
            return True
        if requested_capability.startswith("repository.") or requested_capability.startswith("repo."):
            return True
        return super()._is_capability_supported(requested_capability)

    def _execute_sandboxed(self, request: ExecutionRequest) -> ExecutionResult:
        """Execute sandboxed repository inspection without modifying repository content."""
        args = request.arguments or {}

        # 1. Resolve repository root and identity
        raw_root = (
            args.get("repository_root")
            or args.get("root")
            or args.get("path")
            or (self.repository_identity.canonical_root if self.repository_identity else None)
        )
        if not raw_root:
            err = ExecutionError(
                error_class="terminal.invalid_params",
                message="Repository request requires 'repository_root' argument or authorized repository_identity",
                recoverable=False,
            )
            return ExecutionResult(
                request_id=request.request_id,
                status="failed",
                error=err,
                metrics=ExecutionMetrics(),
            )

        action = args.get("action", "inspect_tree")
        allow_symlinks = bool(args.get("allow_symlinks", False))

        try:
            inspector = self.inspector or LocalRepositoryInspector(
                root_path=raw_root,
                allow_symlinks=allow_symlinks,
            )
            repo_ident = self.repository_identity or inspector.identify_repository(request.space_id)
        except (RepositoryRootInvalidError, RepositoryNotFoundError) as exc:
            err = ExecutionError(
                error_class="terminal.invalid_params",
                message=sanitize_text(str(exc)),
                recoverable=False,
            )
            return ExecutionResult(
                request_id=request.request_id,
                status="failed",
                error=err,
                metrics=ExecutionMetrics(),
            )

        # 2. Dispatch requested inspection action
        try:
            if action == "inspect_tree" or action == "snapshot":
                return self._handle_inspect_tree(request, inspector, repo_ident, args)
            elif action == "read_file":
                return self._handle_read_file(request, inspector, repo_ident, args)
            elif action == "inspect_ast":
                return self._handle_inspect_ast(request, inspector, repo_ident, args)
            elif action == "discover_tests":
                return self._handle_discover_tests(request, inspector, repo_ident, args)
            else:
                err = ExecutionError(
                    error_class="terminal.invalid_params",
                    message=f"Unknown repository inspection action: '{action}'",
                    recoverable=False,
                )
                return ExecutionResult(
                    request_id=request.request_id,
                    status="failed",
                    error=err,
                    metrics=ExecutionMetrics(),
                )

        except PathTraversalError as exc:
            err = ExecutionError(
                error_class="terminal.security_violation",
                message=sanitize_text(f"Path traversal detected: {exc}"),
                recoverable=False,
            )
            return ExecutionResult(
                request_id=request.request_id,
                status="denied",
                error=err,
                metrics=ExecutionMetrics(),
            )
        except SymlinkSecurityError as exc:
            err = ExecutionError(
                error_class="terminal.security_violation",
                message=sanitize_text(f"Symlink security violation: {exc}"),
                recoverable=False,
            )
            return ExecutionResult(
                request_id=request.request_id,
                status="denied",
                error=err,
                metrics=ExecutionMetrics(),
            )
        except SecretAccessDeniedError as exc:
            err = ExecutionError(
                error_class="terminal.permission_denied",
                message=sanitize_text(f"Sensitive secret access denied: {exc}"),
                recoverable=False,
            )
            return ExecutionResult(
                request_id=request.request_id,
                status="denied",
                error=err,
                metrics=ExecutionMetrics(),
            )
        except (FileTooLargeError, RepositoryLimitExceededError) as exc:
            err = ExecutionError(
                error_class="terminal.resource_limit",
                message=sanitize_text(str(exc)),
                recoverable=False,
            )
            return ExecutionResult(
                request_id=request.request_id,
                status="failed",
                error=err,
                metrics=ExecutionMetrics(),
            )
        except (FileNotFoundError, RepositoryError, Exception) as exc:
            err = ExecutionError(
                error_class="terminal.not_found" if isinstance(exc, FileNotFoundError) else "transient.io",
                message=sanitize_text(str(exc)),
                recoverable=False if isinstance(exc, FileNotFoundError) else True,
            )
            return ExecutionResult(
                request_id=request.request_id,
                status="failed",
                error=err,
                metrics=ExecutionMetrics(),
            )

    def _handle_inspect_tree(
        self,
        request: ExecutionRequest,
        inspector: LocalRepositoryInspector,
        repo_ident: RepositoryIdentity,
        args: dict[str, Any],
    ) -> ExecutionResult:
        max_depth = int(args.get("max_depth", 20))
        max_files = int(args.get("max_files", 5000))

        snapshot = inspector.inspect_tree(request.space_id, max_depth=max_depth, max_files=max_files)

        # Build Provenance Record
        raw_source_ident = SourceIdentity(
            source_id=repo_ident.repository_id,
            source_type="repository_file",
            locator=repo_ident.canonical_root,
            space_id=request.space_id,
        )
        snapshot_bytes = json.dumps([f.relative_path for f in snapshot.file_inventory], sort_keys=True).encode("utf-8")
        snapshot_hash = compute_sha256(snapshot_bytes)

        prov = ProvenanceRecord(
            provenance_id=f"prov-{request.task_id}-repo",
            source_identity=raw_source_ident,
            space_id=request.space_id,
            task_id=request.task_id,
            plan_version=request.plan_version,
            producer=self.worker_id,
            content_hash=snapshot_hash,
            transformation_stage=TransformationStage.RAW,
        )

        artifacts: list[Artifact] = []
        if self.base_working_dir:
            art_dir = self.base_working_dir / "artifacts" / "repository"
            art_dir.mkdir(parents=True, exist_ok=True)
            manifest_path = art_dir / f"{request.task_id}_manifest.json"

            manifest_dict = {
                "snapshot_id": snapshot.snapshot_id,
                "repository_id": repo_ident.repository_id,
                "canonical_root": repo_ident.canonical_root,
                "total_files": snapshot.total_files,
                "total_bytes": snapshot.total_bytes,
                "inspected_at": snapshot.inspected_at.isoformat(),
                "file_inventory": [
                    {
                        "path": f.relative_path,
                        "size": f.size_bytes,
                        "hash": f.content_hash,
                        "category": f.category.value,
                        "policy": f.access_policy.value,
                    }
                    for f in snapshot.file_inventory
                ],
                "discovered_tests": snapshot.discovered_tests,
                "project_metadata": {
                    "type": snapshot.project_metadata.project_type if snapshot.project_metadata else "unknown",
                    "config_files": snapshot.project_metadata.config_files if snapshot.project_metadata else [],
                },
            }
            manifest_json = json.dumps(manifest_dict, indent=2)
            manifest_path.write_text(manifest_json, encoding="utf-8")
            manifest_art = Artifact(
                artifact_id=f"art-{request.task_id}-manifest",
                name=f"{request.task_id}_manifest.json",
                path=str(manifest_path),
                mime_type="application/json",
                size_bytes=len(manifest_json.encode("utf-8")),
                sha256=compute_sha256(manifest_json),
                metadata={"provenance_id": prov.provenance_id},
            )
            artifacts.append(manifest_art)

        # Assemble Output Contract
        inspection_res = RepositoryInspectionResult(
            result_id=f"res-{request.task_id}",
            space_id=request.space_id,
            task_id=request.task_id,
            plan_version=request.plan_version,
            repository_identity=repo_ident,
            snapshot=snapshot,
            provenance_id=prov.provenance_id,
            taint=True,
        )

        output_data = {
            "result_id": inspection_res.result_id,
            "repository_id": repo_ident.repository_id,
            "canonical_root": repo_ident.canonical_root,
            "total_files": snapshot.total_files,
            "total_bytes": snapshot.total_bytes,
            "snapshot_id": snapshot.snapshot_id,
            "provenance_id": prov.provenance_id,
            "discovered_tests_count": len(snapshot.discovered_tests),
            "project_type": snapshot.project_metadata.project_type if snapshot.project_metadata else "unknown",
            "taint": True,
        }

        return ExecutionResult(
            request_id=request.request_id,
            status="ok",
            artifacts=artifacts,
            output_data=output_data,
            taint=True,
            metrics=ExecutionMetrics(),
            logs=[
                f"Inspected repository '{repo_ident.repository_id}' ({snapshot.total_files} files, {snapshot.total_bytes} bytes)",
            ],
        )

    def _handle_read_file(
        self,
        request: ExecutionRequest,
        inspector: LocalRepositoryInspector,
        repo_ident: RepositoryIdentity,
        args: dict[str, Any],
    ) -> ExecutionResult:
        rel_path = args.get("relative_path") or args.get("file_path")
        if not rel_path:
            err = ExecutionError(
                error_class="terminal.invalid_params",
                message="read_file action requires 'relative_path'",
                recoverable=False,
            )
            return ExecutionResult(request_id=request.request_id, status="failed", error=err)

        raw_bytes, chash = inspector.read_file(rel_path, request.space_id)
        text_content = raw_bytes.decode("utf-8", errors="replace")

        return ExecutionResult(
            request_id=request.request_id,
            status="ok",
            output_data={
                "relative_path": rel_path,
                "size_bytes": len(raw_bytes),
                "content_hash": chash,
                "content": text_content,
                "taint": True,
            },
            taint=True,
            metrics=ExecutionMetrics(),
            logs=[f"Read file '{rel_path}' ({len(raw_bytes)} bytes)"],
        )

    def _handle_inspect_ast(
        self,
        request: ExecutionRequest,
        inspector: LocalRepositoryInspector,
        repo_ident: RepositoryIdentity,
        args: dict[str, Any],
    ) -> ExecutionResult:
        rel_path = args.get("relative_path") or args.get("file_path")
        if not rel_path:
            err = ExecutionError(
                error_class="terminal.invalid_params",
                message="inspect_ast action requires 'relative_path'",
                recoverable=False,
            )
            return ExecutionResult(request_id=request.request_id, status="failed", error=err)

        ast_report = inspector.inspect_ast(rel_path, request.space_id)

        return ExecutionResult(
            request_id=request.request_id,
            status="ok",
            output_data={
                "relative_path": ast_report.relative_path,
                "language": ast_report.language,
                "parse_status": ast_report.parse_status,
                "classes": [{"name": c.name, "line": c.line_number} for c in ast_report.classes],
                "functions": [{"name": f.name, "line": f.line_number} for f in ast_report.functions],
                "imports": ast_report.imports,
                "taint": True,
            },
            taint=True,
            metrics=ExecutionMetrics(),
            logs=[f"Parsed AST for '{rel_path}': status={ast_report.parse_status}"],
        )

    def _handle_discover_tests(
        self,
        request: ExecutionRequest,
        inspector: LocalRepositoryInspector,
        repo_ident: RepositoryIdentity,
        args: dict[str, Any],
    ) -> ExecutionResult:
        tests = inspector.discover_tests(request.space_id)
        return ExecutionResult(
            request_id=request.request_id,
            status="ok",
            output_data={
                "discovered_tests": tests,
                "test_count": len(tests),
                "taint": True,
            },
            taint=True,
            metrics=ExecutionMetrics(),
            logs=[f"Discovered {len(tests)} test files"],
        )
