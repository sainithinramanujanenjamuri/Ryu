"""Repository Protocol & Software-Engineering Foundation: Core abstractions, data models, and contracts.

Defines the boundary between the deterministic Space Kernel / Orchestrator and
concrete repository inspection implementations (ADR-0044, spec §16 Component Contracts).
Enforces Space isolation (SPACE-001, ARC-004) and Repository Inspection Scoping (REPO-001).

Governing Architecture: Space-Centric Cognitive Architecture (SCCA)
Governing Rule: AGENTS.md §7 (Deterministic Core Independence — zero higher-layer imports)
Phase: 14.3
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Protocol, runtime_checkable

# Pattern to detect embedded credentials in repository metadata or configuration
_SECRET_PATTERN = re.compile(
    r"(?i)(?:\b|_)(password|secret|token|api[_-]?key|bearer|credential|session)(?:\b|_)"
)


# ── Exceptions ───────────────────────────────────────────────────────────────

class RepositoryError(Exception):
    """Base exception for all repository protocol operations."""


class RepositoryNotAuthorizedError(RepositoryError):
    """Raised when repository access is not authorized for the Space."""


class RepositoryNotFoundError(RepositoryError):
    """Raised when an authorized repository cannot be found on the filesystem."""


class RepositoryRootInvalidError(RepositoryError):
    """Raised when the specified repository root is invalid (empty, non-directory, etc.)."""


class PathTraversalError(RepositoryError):
    """Raised when path resolution attempts to escape the authorized repository root (REPO-001)."""


class SymlinkSecurityError(RepositoryError):
    """Raised when a symlink attempts to escape the authorized repository boundary."""


class FileAccessDeniedError(RepositoryError):
    """Raised when access to a restricted or sensitive repository file is blocked."""


class FileTooLargeError(RepositoryError):
    """Raised when a file exceeds the maximum allowed inspection size."""


class RepositoryLimitExceededError(RepositoryError):
    """Raised when total repository file count, depth, or byte inspection limit is exceeded."""


class SecretAccessDeniedError(RepositoryError):
    """Raised when unmasked access to a sensitive credential or secret file is rejected."""


class ASTParseError(RepositoryError):
    """Raised when static AST parsing encounters a syntax or structural failure."""


class UnsupportedASTLanguageError(RepositoryError):
    """Raised when AST parsing is requested for an unsupported programming language."""


class RepositorySpaceIsolationViolation(RepositoryError):
    """Attempted cross-space repository access without formal promotion grant (SCCA Law 1, Law 4)."""

    def __init__(self, requesting_space: str, target_space: str, entity_id: str = "") -> None:
        super().__init__(
            f"Space isolation violation: requesting space '{requesting_space}' "
            f"cannot access repository entity of space '{target_space}' (entity: '{entity_id}')."
        )
        self.requesting_space = requesting_space
        self.target_space = target_space
        self.entity_id = entity_id


# ── Phase 14.4 Patching Exceptions (ADR-0044, REPO-002..005) ─────────────────

# ADR-0044 Phase 14.4 Patch Ceilings (REPO-004)
MAX_CHANGED_FILES: int = 5
MAX_DIFF_LINES: int = 500


class PatchError(RepositoryError):
    """Base exception for code modification and patching failures."""


class PatchSyntaxError(PatchError):
    """Raised when a unified diff is malformed, ambiguous, or contains invalid syntax."""


class PatchBoundsExceededError(PatchError):
    """Raised when patch lines or changed file count exceed ADR-0044 ceilings (REPO-004)."""


class PatchTargetInvalidError(PatchError):
    """Raised when patch target file is invalid, traversal escape, or sensitive (REPO-003)."""


class PatchContextMismatchError(PatchError):
    """Raised when file content does not match diff context hunks."""


class PatchConflictError(PatchError):
    """Raised when pre-patch file state changed concurrently (pre-hash mismatch)."""


class PatchRollbackError(PatchError):
    """Raised when atomic rollback fails to restore bitwise identical prior state (REPO-005)."""


class PatchVerificationError(PatchError):
    """Raised when post-patch file state does not match expected post-patch hashes."""


# ── Data Models ──────────────────────────────────────────────────────────────

class FileCategory(str, Enum):
    """Classification of repository files for policy and inspection filtering."""

    SOURCE = "source"
    TEST = "test"
    CONFIG = "config"
    DOCUMENTATION = "documentation"
    SECRET = "secret"
    BINARY = "binary"
    BUILD = "build"
    UNKNOWN = "unknown"


class FileAccessPolicy(str, Enum):
    """Access policy decision for individual repository paths."""

    ALLOWED = "allowed"
    DENIED = "denied"
    MASKED = "masked"      # Evidence of existence recorded, raw content withheld (SECRET-004)
    IGNORED = "ignored"    # Omitted from inspection by default (.git, node_modules)


@dataclass(frozen=True)
class RepositoryIdentity:
    """Immutable identity of an authorized software repository (REPO-001)."""

    repository_id: str
    space_id: str
    canonical_root: str
    ref: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.repository_id or not self.repository_id.strip():
            raise ValueError("repository_id must not be empty")
        if not self.space_id or not self.space_id.strip():
            raise ValueError("space_id must not be empty (SCCA Law 1)")
        if not self.canonical_root or not self.canonical_root.strip():
            raise ValueError("canonical_root must not be empty")

        # Security check: metadata must not embed raw credentials
        for k, v in self.metadata.items():
            if _SECRET_PATTERN.search(str(k)):
                raise ValueError(f"Security violation: repository metadata key '{k}' contains credential label")
            if isinstance(v, str) and _SECRET_PATTERN.search(v):
                raise ValueError(f"Security violation: repository metadata value for '{k}' contains secret token")


@dataclass(frozen=True)
class FileMetadata:
    """Metadata describing an observed file within an authorized repository."""

    relative_path: str
    size_bytes: int
    content_hash: str
    category: FileCategory
    access_policy: FileAccessPolicy
    is_binary: bool = False
    is_symlink: bool = False
    symlink_target: str | None = None

    def __post_init__(self) -> None:
        if not self.relative_path or not self.relative_path.strip():
            raise ValueError("relative_path must not be empty")
        if self.size_bytes < 0:
            raise ValueError("size_bytes must be >= 0")


@dataclass(frozen=True)
class ProjectMetadata:
    """Neutral representation of observed repository build and project configuration."""

    project_type: str  # "python" | "rust" | "node" | "unknown"
    config_files: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    test_framework: str | None = None


@dataclass(frozen=True)
class ASTNodeSummary:
    """Lightweight structural summary of a parsed AST declaration."""

    name: str
    node_type: str  # "FunctionDef" | "AsyncFunctionDef" | "ClassDef"
    line_number: int
    docstring: str | None = None


@dataclass(frozen=True)
class ASTInspectionReport:
    """Structured report of static AST parsing without code execution (REPO-001)."""

    relative_path: str
    language: str
    classes: list[ASTNodeSummary] = field(default_factory=list)
    functions: list[ASTNodeSummary] = field(default_factory=list)
    imports: list[str] = field(default_factory=list)
    parse_status: str = "ok"  # "ok" | "unsupported_language" | "syntax_error"
    error_message: str | None = None


@dataclass(frozen=True)
class RepositorySnapshot:
    """Deterministic, immutable snapshot of an inspected repository state."""

    snapshot_id: str
    repository_identity: RepositoryIdentity
    inspected_at: datetime
    file_inventory: list[FileMetadata] = field(default_factory=list)
    excluded_paths: list[str] = field(default_factory=list)
    total_files: int = 0
    total_bytes: int = 0
    project_metadata: ProjectMetadata | None = None
    discovered_tests: list[str] = field(default_factory=list)
    status: str = "ok"

    def __post_init__(self) -> None:
        if not self.snapshot_id:
            raise ValueError("snapshot_id must not be empty")
        if self.total_files < 0 or self.total_bytes < 0:
            raise ValueError("total_files and total_bytes must be >= 0")


@dataclass(frozen=True)
class RepositoryInspectionResult:
    """Output contract produced by repository inspection (REPO-001)."""

    result_id: str
    space_id: str
    task_id: str
    plan_version: int
    repository_identity: RepositoryIdentity
    snapshot: RepositorySnapshot
    provenance_id: str
    taint: bool = True  # Untrusted external code by default (TAINT-001)

    def __post_init__(self) -> None:
        if not self.result_id:
            raise ValueError("result_id must not be empty")
        if self.space_id != self.repository_identity.space_id:
            raise RepositorySpaceIsolationViolation(
                requesting_space=self.space_id,
                target_space=self.repository_identity.space_id,
                entity_id=self.result_id,
            )


# ── Phase 14.4 Patch Data Models (ADR-0044, REPO-002..005) ───────────────────

class PatchTransactionState(str, Enum):
    """Lifecycle state of an atomic patch transaction."""

    VALIDATING = "validating"
    PREPARED = "prepared"
    APPLYING = "applying"
    APPLIED = "applied"
    VERIFIED = "verified"
    ROLLBACK_PENDING = "rollback_pending"
    ROLLED_BACK = "rolled_back"
    ROLLBACK_VERIFIED = "rollback_verified"
    ROLLBACK_FAILED = "rollback_failed"
    FAILED = "failed"
    ESCALATED = "escalated"


class FilePatchOperation(str, Enum):
    """File-level operation represented in a unified diff."""

    MODIFY = "modify"
    CREATE = "create"
    DELETE = "delete"


@dataclass(frozen=True)
class Hunk:
    """Individual modification hunk within a unified diff."""

    old_start: int
    old_count: int
    new_start: int
    new_count: int
    lines: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.old_start < 0 or self.new_start < 0:
            raise ValueError("Hunk line offsets must be non-negative")
        if self.old_count < 0 or self.new_count < 0:
            raise ValueError("Hunk line counts must be non-negative")


@dataclass(frozen=True)
class FileDiff:
    """Parsed file-level diff comprising one or more hunks."""

    old_path: str | None
    new_path: str | None
    operation: FilePatchOperation
    hunks: tuple[Hunk, ...] = field(default_factory=tuple)
    additions: int = 0
    deletions: int = 0
    changed_lines: int = 0

    @property
    def target_path(self) -> str:
        """Effective repository relative target path."""
        if self.operation == FilePatchOperation.CREATE:
            return self.new_path or ""
        return self.old_path or self.new_path or ""


@dataclass(frozen=True)
class CodePatch:
    """Immutable representation of a multi-file unified diff patch (REPO-002, REPO-004)."""

    patch_id: str
    target_files: tuple[str, ...]
    diff_text: str
    changed_line_count: int = 0
    before_hashes: dict[str, str] = field(default_factory=dict)
    after_hashes: dict[str, str] = field(default_factory=dict)
    task_id: str = ""
    plan_version: int = 1
    space_id: str = ""
    author: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        if not self.patch_id or not self.patch_id.strip():
            raise ValueError("patch_id must not be empty")
        if len(self.target_files) > MAX_CHANGED_FILES:
            raise PatchBoundsExceededError(
                f"Patch targets {len(self.target_files)} files, exceeding ceiling of {MAX_CHANGED_FILES} (REPO-004)"
            )
        if self.changed_line_count > MAX_DIFF_LINES:
            raise PatchBoundsExceededError(
                f"Patch changes {self.changed_line_count} lines, exceeding ceiling of {MAX_DIFF_LINES} (REPO-004)"
            )


@dataclass(frozen=True)
class PatchTransaction:
    """Durable record of a patch execution transaction with full hash manifests (REPO-005)."""

    transaction_id: str
    patch_id: str
    space_id: str
    task_id: str = ""
    plan_version: int = 1
    repository_id: str = ""
    state: PatchTransactionState = PatchTransactionState.VALIDATING
    affected_files: tuple[str, ...] = field(default_factory=tuple)
    before_hashes: dict[str, str] = field(default_factory=dict)
    after_hashes: dict[str, str] = field(default_factory=dict)
    rollback_hashes: dict[str, str] = field(default_factory=dict)
    changed_line_count: int = 0
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    error_message: str | None = None
    rollback_verified: bool = False

    def __post_init__(self) -> None:
        if not self.transaction_id:
            raise ValueError("transaction_id must not be empty")


@dataclass(frozen=True)
class PatchResult:
    """Deterministic outcome of a patch application attempt."""

    patch_id: str
    transaction_id: str
    success: bool
    applied_files: tuple[str, ...] = field(default_factory=tuple)
    changed_line_count: int = 0
    before_hashes: dict[str, str] = field(default_factory=dict)
    after_hashes: dict[str, str] = field(default_factory=dict)
    state: PatchTransactionState = PatchTransactionState.VALIDATING
    error: str | None = None
    rolled_back: bool = False
    rollback_verified: bool = False


# ── Protocols ───────────────────────────────────────────────────────────────

@runtime_checkable
class RepositoryPolicyProtocol(Protocol):
    """Protocol governing path classification and security access policies."""

    def evaluate_path(self, relative_path: str, space_id: str) -> tuple[FileAccessPolicy, FileCategory]:
        """Evaluate access policy and file category for a relative repository path."""
        ...


@runtime_checkable
class RepositoryProtocol(Protocol):
    """Abstract interface defining the boundary for repository operations (ADR-0044, REPO-001..005).

    Enforces:
    - Bounded inspection and mutation strictly within authorized workspace root.
    - Atomic patch application with pre/post hash verification and automatic rollback.
    - Sensitive path protection and line/file count ceilings.
    - Static analysis without executing repository code.
    - Neutral cross-language contracts.
    """

    def identify_repository(self, space_id: str) -> RepositoryIdentity:
        """Return the immutable identity of the authorized repository."""
        ...

    def inspect_tree(
        self,
        space_id: str,
        max_depth: int = 20,
        max_files: int = 5000,
    ) -> RepositorySnapshot:
        """Inspect repository structure and generate a deterministic snapshot."""
        ...

    def read_file(
        self,
        relative_path: str,
        space_id: str,
        max_bytes: int = 10 * 1024 * 1024,
    ) -> tuple[bytes, str]:
        """Read a single permitted file, returning raw bytes and content hash."""
        ...

    def inspect_ast(
        self,
        relative_path: str,
        space_id: str,
    ) -> ASTInspectionReport:
        """Perform bounded static AST parsing without executing code."""
        ...

    def discover_tests(self, space_id: str) -> list[str]:
        """Discover test files matching repository conventions (discovery only; no execution)."""
        ...

    def apply_patch(
        self,
        space_id: str,
        patch: CodePatch,
        expected_before_hashes: dict[str, str] | None = None,
    ) -> PatchResult:
        """Apply an atomic unified diff patch with pre/post hash verification (REPO-002, REPO-005)."""
        ...

    def revert_patch(
        self,
        space_id: str,
        patch_id: str,
    ) -> PatchResult:
        """Revert a previously applied patch back to exact prior state with hash verification (REPO-005)."""
        ...

