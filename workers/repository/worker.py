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
from ryu.pulse_bus.pulse import Pulse, Severity

from core.resources.manager import ResourceManager
from core.space.artifact_paths import get_space_artifact_dir, resolve_artifact_path
from core.space.repository_protocol import (
    CodePatch,
    FileTooLargeError,
    PatchBoundsExceededError,
    PatchConflictError,
    PatchContextMismatchError,
    PatchError,
    PatchRollbackError,
    PatchSyntaxError,
    PatchTargetInvalidError,
    PatchVerificationError,
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
from core.space.research_protocol import (
    ProvenanceRecord,
    SourceIdentity,
    TransformationStage,
    compute_sha256,
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

        # Check capability permissions: inspection capability cannot perform modification
        is_modification_action = action in ("apply_patch", "patch", "revert_patch", "revert", "rollback")
        worker_cap = self.identity.capability
        req_cap = request.capability

        def _is_write_authorized(cap: str) -> bool:
            return cap in ("repository.patch", "repo.patch", "repository.*", "repo.*", "repository", "repo")

        if is_modification_action and not (_is_write_authorized(worker_cap) or _is_write_authorized(req_cap)):
            err = ExecutionError(
                error_class="terminal.permission_denied",
                message=f"Capability '{worker_cap}' is inspection-only and not authorized to modify code (action '{action}')",
                recoverable=False,
            )
            return ExecutionResult(
                request_id=request.request_id,
                status="denied",
                error=err,
                metrics=ExecutionMetrics(),
            )

        if repo_ident.space_id != request.space_id:
            err = ExecutionError(
                error_class="terminal.permission_denied",
                message=f"Repository '{repo_ident.repository_id}' is authorized for space '{repo_ident.space_id}', not authorized for worker space '{request.space_id}'",
                recoverable=False,
            )
            return ExecutionResult(
                request_id=request.request_id,
                status="denied",
                error=err,
                metrics=ExecutionMetrics(),
            )

        # 2. Dispatch requested action
        try:
            if action in ("inspect_tree", "snapshot"):
                return self._handle_inspect_tree(request, inspector, repo_ident, args)
            elif action == "read_file":
                return self._handle_read_file(request, inspector, repo_ident, args)
            elif action == "inspect_ast":
                return self._handle_inspect_ast(request, inspector, repo_ident, args)
            elif action == "discover_tests":
                return self._handle_discover_tests(request, inspector, repo_ident, args)
            elif action in ("apply_patch", "patch"):
                return self._handle_apply_patch(request, inspector, repo_ident, args)
            elif action in ("revert_patch", "revert", "rollback"):
                return self._handle_revert_patch(request, inspector, repo_ident, args)
            else:
                err = ExecutionError(
                    error_class="terminal.invalid_params",
                    message=f"Unknown repository action: '{action}'",
                    recoverable=False,
                )
                return ExecutionResult(
                    request_id=request.request_id,
                    status="failed",
                    error=err,
                    metrics=ExecutionMetrics(),
                )

        except PatchBoundsExceededError as exc:
            err = ExecutionError(
                error_class="terminal.resource_limit",
                message=sanitize_text(f"Patch bounds ceiling exceeded: {exc}"),
                recoverable=False,
            )
            return ExecutionResult(
                request_id=request.request_id,
                status="failed",
                error=err,
                metrics=ExecutionMetrics(),
            )
        except (PatchSyntaxError, PatchContextMismatchError) as exc:
            err = ExecutionError(
                error_class="terminal.invalid_params",
                message=sanitize_text(f"Patch syntax or context mismatch error: {exc}"),
                recoverable=False,
            )
            return ExecutionResult(
                request_id=request.request_id,
                status="failed",
                error=err,
                metrics=ExecutionMetrics(),
            )
        except PatchConflictError as exc:
            err = ExecutionError(
                error_class="transient.conflict",
                message=sanitize_text(f"Concurrent patch conflict: {exc}"),
                recoverable=True,
            )
            return ExecutionResult(
                request_id=request.request_id,
                status="failed",
                error=err,
                metrics=ExecutionMetrics(),
            )
        except (PatchTargetInvalidError, PathTraversalError) as exc:
            err = ExecutionError(
                error_class="terminal.security_violation",
                message=sanitize_text(f"Target invalid or traversal detected: {exc}"),
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
        except (FileNotFoundError, RepositoryError, PatchError, PatchVerificationError, PatchRollbackError, Exception) as exc:
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
            art_dir = get_space_artifact_dir(self.base_working_dir, request.space_id, "repository")
            art_dir.mkdir(parents=True, exist_ok=True)
            manifest_path = resolve_artifact_path(
                self.base_working_dir, request.space_id, "repository", f"{request.task_id}_manifest.json"
            )

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
            manifest_bytes = manifest_json.encode("utf-8")
            manifest_path.write_bytes(manifest_bytes)
            manifest_art = Artifact(
                artifact_id=f"art-{request.task_id}-manifest",
                name=f"{request.task_id}_manifest.json",
                path=str(manifest_path),
                mime_type="application/json",
                size_bytes=len(manifest_bytes),
                sha256=compute_sha256(manifest_bytes),
                space_id=request.space_id,
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

    def _handle_apply_patch(
        self,
        request: ExecutionRequest,
        inspector: LocalRepositoryInspector,
        repo_ident: RepositoryIdentity,
        args: dict[str, Any],
    ) -> ExecutionResult:
        diff_text = args.get("diff_text") or args.get("patch") or args.get("diff")
        if not diff_text or not isinstance(diff_text, str) or not diff_text.strip():
            err = ExecutionError(
                error_class="terminal.invalid_params",
                message="apply_patch action requires non-empty 'diff_text'",
                recoverable=False,
            )
            return ExecutionResult(request_id=request.request_id, status="failed", error=err)

        patch_id = args.get("patch_id") or f"patch-{request.task_id}"
        author = args.get("author") or self.worker_id
        expected_before_hashes = args.get("expected_before_hashes") or args.get("before_hashes")
        if expected_before_hashes and not isinstance(expected_before_hashes, dict):
            expected_before_hashes = None

        code_patch = CodePatch(
            patch_id=patch_id,
            target_files=(),
            diff_text=diff_text,
            author=author,
            space_id=request.space_id,
            before_hashes=expected_before_hashes or {},
        )

        result = inspector.apply_patch(
            space_id=request.space_id,
            patch=code_patch,
            expected_before_hashes=expected_before_hashes,
        )

        # Build Provenance Record for the patch operation (PROVENANCE-001..003)
        plan_ver = max(1, int(request.plan_version or 1))
        diff_bytes = diff_text.encode("utf-8")
        diff_sha = compute_sha256(diff_bytes)
        patch_source_ident = SourceIdentity(
            source_id=repo_ident.repository_id,
            source_type="repository_patch",
            locator=repo_ident.canonical_root,
            space_id=request.space_id,
        )
        patch_prov = ProvenanceRecord(
            provenance_id=f"prov-{request.task_id}-patch",
            source_identity=patch_source_ident,
            space_id=request.space_id,
            task_id=request.task_id,
            plan_version=plan_ver,
            producer=self.worker_id,
            content_hash=diff_sha,
            transformation_stage=TransformationStage.RAW,
        )

        artifacts: list[Artifact] = []
        if self.base_working_dir:
            art_dir = get_space_artifact_dir(self.base_working_dir, request.space_id, "repository")
            art_dir.mkdir(parents=True, exist_ok=True)

            # 1. Raw diff artifact
            diff_path = resolve_artifact_path(
                self.base_working_dir, request.space_id, "repository", f"{request.task_id}_patch.diff"
            )
            diff_path.write_bytes(diff_bytes)
            artifacts.append(
                Artifact(
                    artifact_id=f"art-{request.task_id}-patch-diff",
                    name=f"{request.task_id}_patch.diff",
                    path=str(diff_path),
                    mime_type="text/x-diff",
                    size_bytes=len(diff_bytes),
                    sha256=diff_sha,
                    space_id=request.space_id,
                    metadata={"patch_id": patch_id, "provenance_id": patch_prov.provenance_id},
                )
            )

            # 2. Patch manifest artifact
            manifest_dict = {
                "patch_id": result.patch_id,
                "transaction_id": result.transaction_id,
                "provenance_id": patch_prov.provenance_id,
                "provenance_canonical_hash": patch_prov.canonical_hash,
                "success": result.success,
                "state": result.state.value,
                "applied_files": list(result.applied_files),
                "changed_line_count": result.changed_line_count,
                "before_hashes": result.before_hashes,
                "after_hashes": result.after_hashes,
                "rolled_back": result.rolled_back,
                "rollback_verified": result.rollback_verified,
                "error": result.error,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            manifest_path = resolve_artifact_path(
                self.base_working_dir, request.space_id, "repository", f"{request.task_id}_patch_manifest.json"
            )
            manifest_json = json.dumps(manifest_dict, indent=2)
            manifest_bytes = manifest_json.encode("utf-8")
            manifest_path.write_bytes(manifest_bytes)
            artifacts.append(
                Artifact(
                    artifact_id=f"art-{request.task_id}-patch-manifest",
                    name=f"{request.task_id}_patch_manifest.json",
                    path=str(manifest_path),
                    mime_type="application/json",
                    size_bytes=len(manifest_bytes),
                    sha256=compute_sha256(manifest_bytes),
                    space_id=request.space_id,
                    metadata={"patch_id": patch_id, "state": result.state.value, "provenance_id": patch_prov.provenance_id},
                )
            )

            # 3. Rollback manifest artifact if rolled back
            if result.rolled_back:
                rb_dict = {
                    "patch_id": result.patch_id,
                    "transaction_id": result.transaction_id,
                    "rolled_back": result.rolled_back,
                    "rollback_verified": result.rollback_verified,
                    "restored_files": list(result.applied_files),
                    "reason": result.error or "Automatic rollback triggered by patch verification failure",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
                rb_path = resolve_artifact_path(
                    self.base_working_dir, request.space_id, "repository", f"{request.task_id}_rollback_manifest.json"
                )
                rb_json = json.dumps(rb_dict, indent=2)
                rb_bytes = rb_json.encode("utf-8")
                rb_path.write_bytes(rb_bytes)
                artifacts.append(
                    Artifact(
                        artifact_id=f"art-{request.task_id}-rollback-manifest",
                        name=f"{request.task_id}_rollback_manifest.json",
                        path=str(rb_path),
                        mime_type="application/json",
                        size_bytes=len(rb_bytes),
                        sha256=compute_sha256(rb_bytes),
                        space_id=request.space_id,
                        metadata={"patch_id": patch_id, "rollback_verified": result.rollback_verified},
                    )
                )

        # Pulse emissions
        plan_ver = int(request.plan_version or 0)
        if result.success:
            pulse = Pulse(
                type="repo.patch_applied",
                payload={
                    "patch_id": result.patch_id,
                    "target_files": list(result.applied_files),
                    "changed_line_count": result.changed_line_count,
                    "before_hashes": result.before_hashes,
                    "after_hashes": result.after_hashes,
                    "task_id": request.task_id,
                    "plan_version": plan_ver,
                },
                space_id=request.space_id,
                source=self.worker_id,
                correlation_id=request.correlation_id or request.task_id,
                severity=Severity.INFO,
                taint=True,
            )
            if self.bus:
                self.bus.publish(pulse)

            return ExecutionResult(
                request_id=request.request_id,
                status="ok",
                artifacts=artifacts,
                output_data={
                    "patch_id": result.patch_id,
                    "transaction_id": result.transaction_id,
                    "provenance_id": patch_prov.provenance_id,
                    "provenance_canonical_hash": patch_prov.canonical_hash,
                    "applied_files": list(result.applied_files),
                    "changed_line_count": result.changed_line_count,
                    "before_hashes": result.before_hashes,
                    "after_hashes": result.after_hashes,
                    "state": result.state.value,
                    "taint": True,
                },
                taint=True,
                metrics=ExecutionMetrics(),
                logs=[
                    f"Applied patch '{result.patch_id}' across {len(result.applied_files)} files ({result.changed_line_count} changed lines)"
                ],
            )
        else:
            if result.rolled_back:
                # Emit repo.patch_reverted on rollback
                pulse = Pulse(
                    type="repo.patch_reverted",
                    payload={
                        "patch_id": result.patch_id,
                        "target_files": list(result.applied_files),
                        "reason": result.error or "Patch verification failed; rolled back",
                        "task_id": request.task_id,
                        "plan_version": plan_ver,
                    },
                    space_id=request.space_id,
                    source=self.worker_id,
                    correlation_id=request.correlation_id or request.task_id,
                    severity=Severity.WARNING,
                    taint=True,
                )
                if self.bus:
                    self.bus.publish(pulse)

            error_cls = "terminal.invalid_params"
            err_msg_lower = (result.error or "").lower()
            if "bounds" in err_msg_lower or "ceiling" in err_msg_lower:
                error_cls = "terminal.resource_limit"
            elif "traversal" in err_msg_lower or "sensitive" in err_msg_lower or "denied" in err_msg_lower:
                error_cls = "terminal.security_violation"
            elif "conflict" in err_msg_lower:
                error_cls = "transient.conflict"

            err = ExecutionError(
                error_class=error_cls,
                message=sanitize_text(result.error or "Patch application failed"),
                recoverable=False,
            )
            return ExecutionResult(
                request_id=request.request_id,
                status="failed",
                artifacts=artifacts,
                error=err,
                output_data={
                    "patch_id": result.patch_id,
                    "transaction_id": result.transaction_id,
                    "provenance_id": patch_prov.provenance_id,
                    "provenance_canonical_hash": patch_prov.canonical_hash,
                    "state": result.state.value,
                    "rolled_back": result.rolled_back,
                    "rollback_verified": result.rollback_verified,
                    "taint": True,
                },
                taint=True,
                metrics=ExecutionMetrics(),
                logs=[f"Patch '{result.patch_id}' failed: {result.error}"],
            )

    def _handle_revert_patch(
        self,
        request: ExecutionRequest,
        inspector: LocalRepositoryInspector,
        repo_ident: RepositoryIdentity,
        args: dict[str, Any],
    ) -> ExecutionResult:
        patch_id = args.get("patch_id")
        if not patch_id:
            err = ExecutionError(
                error_class="terminal.invalid_params",
                message="revert_patch action requires 'patch_id'",
                recoverable=False,
            )
            return ExecutionResult(request_id=request.request_id, status="failed", error=err)

        reason = args.get("reason", "Explicit revert requested")
        result = inspector.revert_patch(space_id=request.space_id, patch_id=patch_id)

        # Build Provenance Record for the revert operation (PROVENANCE-001..003)
        plan_ver = max(1, int(request.plan_version or 1))
        revert_source_ident = SourceIdentity(
            source_id=repo_ident.repository_id,
            source_type="repository_revert",
            locator=repo_ident.canonical_root,
            space_id=request.space_id,
        )
        revert_hash = compute_sha256(f"revert:{patch_id}:{reason}".encode("utf-8"))
        revert_prov = ProvenanceRecord(
            provenance_id=f"prov-{request.task_id}-revert",
            source_identity=revert_source_ident,
            space_id=request.space_id,
            task_id=request.task_id,
            plan_version=plan_ver,
            producer=self.worker_id,
            content_hash=revert_hash,
            transformation_stage=TransformationStage.RAW,
        )

        artifacts: list[Artifact] = []
        if self.base_working_dir:
            art_dir = get_space_artifact_dir(self.base_working_dir, request.space_id, "repository")
            art_dir.mkdir(parents=True, exist_ok=True)
            rb_dict = {
                "patch_id": result.patch_id,
                "transaction_id": result.transaction_id,
                "provenance_id": revert_prov.provenance_id,
                "provenance_canonical_hash": revert_prov.canonical_hash,
                "rolled_back": result.rolled_back,
                "rollback_verified": result.rollback_verified,
                "restored_files": list(result.applied_files),
                "reason": reason,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            rb_path = resolve_artifact_path(
                self.base_working_dir, request.space_id, "repository", f"{request.task_id}_rollback_manifest.json"
            )
            rb_json = json.dumps(rb_dict, indent=2)
            rb_bytes = rb_json.encode("utf-8")
            rb_path.write_bytes(rb_bytes)
            artifacts.append(
                Artifact(
                    artifact_id=f"art-{request.task_id}-rollback-manifest",
                    name=f"{request.task_id}_rollback_manifest.json",
                    path=str(rb_path),
                    mime_type="application/json",
                    size_bytes=len(rb_bytes),
                    sha256=compute_sha256(rb_bytes),
                    space_id=request.space_id,
                    metadata={
                        "patch_id": patch_id,
                        "rollback_verified": result.rollback_verified,
                        "provenance_id": revert_prov.provenance_id,
                    },
                )
            )

        pulse_plan_ver = int(request.plan_version or 0)
        if result.success:
            pulse = Pulse(
                type="repo.patch_reverted",
                payload={
                    "patch_id": result.patch_id,
                    "target_files": list(result.applied_files),
                    "reason": reason,
                    "task_id": request.task_id,
                    "plan_version": pulse_plan_ver,
                },
                space_id=request.space_id,
                source=self.worker_id,
                correlation_id=request.correlation_id or request.task_id,
                severity=Severity.INFO,
                taint=True,
            )
            if self.bus:
                self.bus.publish(pulse)

            return ExecutionResult(
                request_id=request.request_id,
                status="ok",
                artifacts=artifacts,
                output_data={
                    "patch_id": result.patch_id,
                    "transaction_id": result.transaction_id,
                    "provenance_id": revert_prov.provenance_id,
                    "provenance_canonical_hash": revert_prov.canonical_hash,
                    "rolled_back": True,
                    "rollback_verified": True,
                    "restored_files": list(result.applied_files),
                    "state": result.state.value,
                    "taint": True,
                },
                taint=True,
                metrics=ExecutionMetrics(),
                logs=[f"Successfully reverted patch '{patch_id}' ({len(result.applied_files)} files restored)"],
            )
        else:
            err = ExecutionError(
                error_class="transient.io",
                message=sanitize_text(result.error or "Patch revert failed"),
                recoverable=False,
            )
            return ExecutionResult(
                request_id=request.request_id,
                status="failed",
                error=err,
                output_data={
                    "patch_id": result.patch_id,
                    "rolled_back": False,
                    "state": result.state.value,
                    "taint": True,
                },
                taint=True,
                metrics=ExecutionMetrics(),
                logs=[f"Failed to revert patch '{patch_id}': {result.error}"],
            )

