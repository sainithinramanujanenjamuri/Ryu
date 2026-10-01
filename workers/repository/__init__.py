"""
Repository Worker & Software Engineering Protocol Components (Phase 14.3, 14.4).

Exports:
- RepositoryWorker
- LocalRepositoryInspector
- Safe Path & Policy Security primitives
- AtomicPatchApplicator & Unified Diff Patcher
"""

from workers.repository.inspector import LocalRepositoryInspector
from workers.repository.patcher import (
    AtomicPatchApplicator,
    apply_hunks_to_content,
    parse_unified_diff,
    validate_patch_bounds,
    validate_patch_paths,
)
from workers.repository.security import (
    evaluate_file_policy,
    is_sensitive_path,
    resolve_safe_path,
)
from workers.repository.worker import RepositoryWorker

__all__ = [
    "AtomicPatchApplicator",
    "LocalRepositoryInspector",
    "RepositoryWorker",
    "apply_hunks_to_content",
    "evaluate_file_policy",
    "is_sensitive_path",
    "parse_unified_diff",
    "resolve_safe_path",
    "validate_patch_bounds",
    "validate_patch_paths",
]


