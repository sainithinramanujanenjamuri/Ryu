"""Local repository inspector implementing RepositoryProtocol with deterministic traversal and AST parsing (ADR-0044, REPO-001).

Enforces:
- Deterministic lexicographical traversal of repository files.
- Bounded file reading, size ceilings, and binary file classification.
- Cryptographic SHA-256 content hashing.
- Static, non-executing AST parsing for Python source files.
- Project metadata and test discovery (without executing build or test tools).
"""

from __future__ import annotations

import ast
import os
import posixpath
from datetime import datetime, timezone
from pathlib import Path

from core.space.repository_protocol import (
    ASTInspectionReport,
    ASTNodeSummary,
    FileAccessPolicy,
    FileCategory,
    FileMetadata,
    FileTooLargeError,
    ProjectMetadata,
    RepositoryIdentity,
    RepositoryLimitExceededError,
    RepositoryProtocol,
    RepositorySnapshot,
    SecretAccessDeniedError,
)
from core.space.research_protocol import compute_sha256
from workers.repository.security import (
    evaluate_file_policy,
    resolve_safe_path,
)


class LocalRepositoryInspector(RepositoryProtocol):
    """Concrete repository inspector operating strictly within an authorized filesystem root."""

    def __init__(
        self,
        root_path: Path | str | None = None,
        identity: RepositoryIdentity | None = None,
        allow_symlinks: bool = False,
        max_file_size: int = 10 * 1024 * 1024,   # 10 MB
        max_total_bytes: int = 50 * 1024 * 1024, # 50 MB
        max_files: int = 5000,
        max_depth: int = 20,
    ) -> None:
        self.identity: RepositoryIdentity | None = identity
        if identity is not None:
            self.root_path = Path(identity.canonical_root).resolve()
        elif root_path is not None:
            self.root_path = Path(root_path).resolve()
        else:
            raise ValueError("Either root_path or identity must be provided")
        self.allow_symlinks = allow_symlinks
        self.max_file_size = max_file_size
        self.max_total_bytes = max_total_bytes
        self.max_files = max_files
        self.max_depth = max_depth

    def identify_repository(self, space_id: str = "default-space") -> RepositoryIdentity:
        """Return the immutable identity of the authorized repository."""
        if self.identity is not None:
            return self.identity
        repo_id = f"repo-{compute_sha256(str(self.root_path))[:12]}"
        return RepositoryIdentity(
            repository_id=repo_id,
            space_id=space_id,
            canonical_root=str(self.root_path),
            metadata={"root_name": self.root_path.name},
        )

    def inspect_tree(
        self,
        space_id: str = "default-space",
        max_depth: int | None = None,
        max_files: int | None = None,
    ) -> RepositorySnapshot:
        """Deterministically inspect repository structure and generate an immutable snapshot."""
        effective_depth = max_depth if max_depth is not None else self.max_depth
        effective_max_files = max_files if max_files is not None else self.max_files
        repo_ident = self.identify_repository(space_id)
        now = datetime.now(timezone.utc)

        file_inventory: list[FileMetadata] = []
        excluded_paths: list[str] = []
        total_bytes = 0

        # Deterministic walk
        for root_dir, dirnames, filenames in os.walk(self.root_path, followlinks=self.allow_symlinks):
            # Sort in-place for determinism
            dirnames.sort()
            filenames.sort()

            current_dir = Path(root_dir)
            rel_dir = current_dir.relative_to(self.root_path).as_posix()
            depth = len(rel_dir.split("/")) if rel_dir != "." else 0

            # Exclude ignored directories before descending
            to_remove = []
            for d in dirnames:
                dir_rel = f"{rel_dir}/{d}".lstrip("./")
                policy, _ = evaluate_file_policy(f"{dir_rel}/file.txt")
                if policy == FileAccessPolicy.IGNORED:
                    to_remove.append(d)
                    excluded_paths.append(dir_rel)
            for d in to_remove:
                dirnames.remove(d)

            if depth > effective_depth:
                dirnames.clear()
                continue

            for fname in filenames:
                rel_file = f"{rel_dir}/{fname}".lstrip("./")
                policy, category = evaluate_file_policy(rel_file)

                if policy == FileAccessPolicy.IGNORED:
                    excluded_paths.append(rel_file)
                    continue

                if len(file_inventory) >= effective_max_files:
                    raise RepositoryLimitExceededError(
                        f"Repository inspection file count ceiling ({effective_max_files}) exceeded"
                    )

                full_path = current_dir / fname
                is_sym = full_path.is_symlink()
                sym_target = os.readlink(full_path) if is_sym else None

                # Compute size and hash
                try:
                    stat = full_path.stat()
                    size = stat.st_size
                except OSError:
                    size = 0

                total_bytes += size
                if total_bytes > self.max_total_bytes:
                    raise RepositoryLimitExceededError(
                        f"Repository total byte inspection ceiling ({self.max_total_bytes}) exceeded"
                    )

                # Compute content hash from bytes if file is readable
                content_hash = ""
                is_bin = category == FileCategory.BINARY
                if size <= self.max_file_size and not is_sym:
                    try:
                        raw_bytes = full_path.read_bytes()
                        content_hash = compute_sha256(raw_bytes)
                        # Check for binary if not already determined
                        if not is_bin and b"\x00" in raw_bytes[:1024]:
                            is_bin = True
                            if category == FileCategory.UNKNOWN:
                                category = FileCategory.BINARY
                    except OSError:
                        content_hash = "unreadable"

                meta = FileMetadata(
                    relative_path=rel_file,
                    size_bytes=size,
                    content_hash=content_hash,
                    category=category,
                    access_policy=policy,
                    is_binary=is_bin,
                    is_symlink=is_sym,
                    symlink_target=sym_target,
                )
                file_inventory.append(meta)

        # Deterministic global sorting
        file_inventory.sort(key=lambda f: f.relative_path)
        excluded_paths.sort()

        # Discovered tests and project metadata
        discovered_tests = sorted([f.relative_path for f in file_inventory if f.category == FileCategory.TEST])
        project_meta = self.discover_project_metadata(file_inventory)

        return RepositorySnapshot(
            snapshot_id=f"snap-{repo_ident.repository_id}-{int(now.timestamp())}",
            repository_identity=repo_ident,
            inspected_at=now,
            file_inventory=file_inventory,
            excluded_paths=excluded_paths,
            total_files=len(file_inventory),
            total_bytes=total_bytes,
            project_metadata=project_meta,
            discovered_tests=discovered_tests,
            status="ok",
        )

    def read_file(
        self,
        relative_path: str,
        space_id: str = "default-space",
        max_bytes: int = 10 * 1024 * 1024,
    ) -> tuple[bytes, str]:
        """Read a single permitted file under strict sandbox and size constraints."""
        policy, _ = evaluate_file_policy(relative_path)
        if policy == FileAccessPolicy.DENIED:
            raise SecretAccessDeniedError(f"Access to file '{relative_path}' is denied by policy")
        if policy == FileAccessPolicy.MASKED:
            raise SecretAccessDeniedError(
                f"Direct read access to sensitive credential file '{relative_path}' is blocked"
            )

        safe_path = resolve_safe_path(self.root_path, relative_path, allow_symlinks=self.allow_symlinks)
        if not safe_path.is_file():
            raise FileNotFoundError(f"File not found: '{relative_path}'")

        size = safe_path.stat().st_size
        if size > max_bytes:
            raise FileTooLargeError(
                f"File '{relative_path}' size ({size} bytes) exceeds limit ({max_bytes} bytes)"
            )

        raw_bytes = safe_path.read_bytes()
        return raw_bytes, compute_sha256(raw_bytes)

    def inspect_ast(
        self,
        relative_path: str,
        space_id: str = "default-space",
    ) -> ASTInspectionReport:
        """Perform static AST parsing on a source file without executing code (REPO-001)."""
        safe_path = resolve_safe_path(self.root_path, relative_path, allow_symlinks=self.allow_symlinks)

        if not relative_path.endswith(".py"):
            return ASTInspectionReport(
                relative_path=relative_path,
                language="unknown",
                parse_status="unsupported_language",
                error_message=f"AST parsing is not supported for '{relative_path}'",
            )

        # Size check for AST parsing (1 MB max)
        size = safe_path.stat().st_size
        if size > 1024 * 1024:
            return ASTInspectionReport(
                relative_path=relative_path,
                language="python",
                parse_status="syntax_error",
                error_message="File too large for static AST parsing",
            )

        raw_text = safe_path.read_text(encoding="utf-8", errors="replace")
        try:
            tree = ast.parse(raw_text, filename=relative_path)
        except SyntaxError as exc:
            return ASTInspectionReport(
                relative_path=relative_path,
                language="python",
                parse_status="syntax_error",
                error_message=f"Syntax error at line {exc.lineno}: {exc.msg}",
            )

        classes: list[ASTNodeSummary] = []
        functions: list[ASTNodeSummary] = []
        imports: list[str] = []

        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.ClassDef):
                doc = ast.get_docstring(node)
                classes.append(
                    ASTNodeSummary(name=node.name, node_type="ClassDef", line_number=node.lineno, docstring=doc)
                )
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                doc = ast.get_docstring(node)
                functions.append(
                    ASTNodeSummary(name=node.name, node_type=type(node).__name__, line_number=node.lineno, docstring=doc)
                )
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append(alias.name)
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                for alias in node.names:
                    imports.append(f"{mod}.{alias.name}")

        return ASTInspectionReport(
            relative_path=relative_path,
            language="python",
            classes=classes,
            functions=functions,
            imports=imports,
            parse_status="ok",
        )

    def discover_tests(self, space_id: str = "default-space") -> list[str]:
        """Discover test files in the repository (discovery only; no execution)."""
        snapshot = self.inspect_tree(space_id)
        return snapshot.discovered_tests

    def get_project_metadata(self, space_id: str = "default-space") -> ProjectMetadata:
        """Detect and return project configuration metadata."""
        snapshot = self.inspect_tree(space_id)
        return snapshot.project_metadata or ProjectMetadata(project_type="unknown")

    def discover_project_metadata(self, file_inventory: list[FileMetadata]) -> ProjectMetadata:
        """Detect project configuration files and extract high-level metadata as passive data."""
        config_basenames = {posixpath.basename(f.relative_path).lower(): f.relative_path for f in file_inventory}
        config_files: list[str] = []
        project_type = "unknown"
        test_framework: str | None = None

        if "pyproject.toml" in config_basenames or "setup.py" in config_basenames:
            project_type = "python"
            test_framework = "pytest"
            for k in ("pyproject.toml", "setup.py", "setup.cfg", "requirements.txt"):
                if k in config_basenames:
                    config_files.append(config_basenames[k])

        elif "cargo.toml" in config_basenames:
            project_type = "rust"
            test_framework = "cargo test"
            config_files.append(config_basenames["cargo.toml"])

        elif "package.json" in config_basenames:
            project_type = "node"
            test_framework = "npm test"
            config_files.append(config_basenames["package.json"])

        return ProjectMetadata(
            project_type=project_type,
            config_files=config_files,
            dependencies=[],
            test_framework=test_framework,
        )
