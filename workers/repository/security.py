"""Repository path security, safe resolution, and secret denylist management (ADR-0044, REPO-001).

Enforces:
- Strict sandbox containment: all operations restricted to authorized repository root.
- Path traversal defenses: blocks ../, ..\\, absolute path escapes, drive escapes, and null-bytes.
- Symlink containment: prevents symlink escape outside the authorized repository root.
- Sensitive file protection: identifies and masks .env, private keys, certificates, and secrets.
"""

from __future__ import annotations

import fnmatch
import os
import posixpath
from pathlib import Path

from core.space.repository_protocol import (
    FileAccessPolicy,
    FileCategory,
    PathTraversalError,
    RepositoryRootInvalidError,
    SymlinkSecurityError,
)

# Denylist patterns for sensitive files that must be masked or blocked (SECRET-004, REPO-003)
SENSITIVE_FILE_PATTERNS = [
    ".env",
    ".env.*",
    "id_rsa",
    "id_rsa.*",
    "id_ed25519",
    "id_ed25519.*",
    "id_ecdsa",
    "id_dsa",
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
    "*.pkcs12",
    "*secret*",
    "*credential*",
    "*token*",
    ".aws/*",
    ".ssh/*",
    ".kube/*",
]


def resolve_safe_path(
    root_path: Path | str,
    relative_path: str,
    allow_symlinks: bool = False,
) -> Path:
    """Resolve a relative path against an authorized repository root with strict security checks.

    Args:
        root_path: The canonical repository root.
        relative_path: Relative path supplied by the task.
        allow_symlinks: If True, permits symlinks whose resolved target remains within root_path.

    Returns:
        Resolved Path guaranteed to reside strictly within root_path.

    Raises:
        RepositoryRootInvalidError: If root_path is invalid or non-directory.
        PathTraversalError: If relative_path attempts directory traversal outside root.
        SymlinkSecurityError: If symlinks violate boundary policy.
    """
    if not root_path:
        raise RepositoryRootInvalidError("Repository root path must not be empty")

    root = Path(root_path).resolve()
    if not root.exists():
        raise RepositoryRootInvalidError(f"Repository root does not exist: {root}")
    if not root.is_dir():
        raise RepositoryRootInvalidError(f"Repository root must be a directory, got: {root}")

    if not relative_path or not relative_path.strip():
        return root

    # 1. Reject null-byte injections
    if "\x00" in relative_path:
        raise PathTraversalError("Null-byte path injection detected")

    # 2. Reject Windows drive escapes (e.g., "C:", "D:\")
    clean = relative_path.strip()
    if len(clean) >= 2 and clean[1] == ":":
        raise PathTraversalError(f"Drive escape path rejected: '{relative_path}'")

    # 3. Reject UNC path escapes (e.g., "\\server\share")
    if clean.startswith(("\\\\", "//")):
        raise PathTraversalError(f"UNC path escape rejected: '{relative_path}'")

    # 4. Normalize separators to forward slashes and normalize path
    norm_rel = posixpath.normpath(clean.replace("\\", "/"))

    # Check for traversal escaping root
    if norm_rel == ".." or norm_rel.startswith("../"):
        raise PathTraversalError(f"Directory traversal escape rejected: '{relative_path}'")

    # Build target candidate
    target = (root / Path(norm_rel)).resolve()

    # 5. Verify containment within root
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise PathTraversalError(
            f"Path traversal detected: target '{target}' escapes root '{root}'"
        ) from exc

    # 6. Symlink evaluation
    raw_target = root / Path(norm_rel)
    if not allow_symlinks:
        # Check if the file itself or any parent directory is a symlink
        check_node = raw_target
        while check_node != root and check_node != check_node.parent:
            if check_node.is_symlink():
                raise SymlinkSecurityError(
                    f"Symlink rejected by policy: '{check_node.relative_to(root)}'"
                )
            check_node = check_node.parent
    else:
        # If allowed, verify the real target remains strictly within root
        real_target = raw_target.resolve()
        try:
            real_target.relative_to(root)
        except ValueError as exc:
            raise SymlinkSecurityError(
                f"Symlink target '{real_target}' escapes repository root '{root}'"
            ) from exc

    return target


def is_sensitive_path(relative_path: str | Path) -> bool:
    """Check if relative path matches known sensitive credential / secret file patterns."""
    norm_path = str(relative_path).replace("\\", "/").strip("/").lower()
    basename = os.path.basename(norm_path)

    for pattern in SENSITIVE_FILE_PATTERNS:
        pat_lower = pattern.lower()
        if fnmatch.fnmatch(basename, pat_lower) or fnmatch.fnmatch(norm_path, pat_lower):
            return True
    return False


def evaluate_file_policy(
    relative_path: str | Path,
    allow_sensitive_read: bool = False,
) -> tuple[FileAccessPolicy, FileCategory]:
    """Evaluate access policy and file classification for a repository path.

    Returns:
        tuple of (FileAccessPolicy, FileCategory)
    """
    norm_path = str(relative_path).replace("\\", "/").strip("/")

    # 1. Sensitive / Secret check
    if is_sensitive_path(norm_path):
        if allow_sensitive_read:
            return FileAccessPolicy.ALLOWED, FileCategory.SECRET
        # Masked: existence recorded in inventory, but content reads blocked
        return FileAccessPolicy.MASKED, FileCategory.SECRET

    # 2. Ignored directories
    ignored_prefixes = (
        ".git/",
        ".svn/",
        ".hg/",
        "node_modules/",
        ".venv/",
        "venv/",
        "__pycache__/",
        ".pytest_cache/",
        ".mypy_cache/",
        ".ruff_cache/",
        "build/",
        "dist/",
        "target/",
    )
    for prefix in ignored_prefixes:
        if norm_path.startswith(prefix) or f"/{prefix}" in f"/{norm_path}":
            return FileAccessPolicy.IGNORED, FileCategory.BUILD

    # 3. Test files
    basename = os.path.basename(norm_path).lower()
    if (
        basename.startswith("test_")
        or basename.endswith(("_test.py", "_test.ts", "_test.js", "_test.rs", "_test.go"))
        or basename.endswith(".test.ts")
        or basename.endswith(".test.js")
        or basename.endswith(".spec.ts")
        or basename.endswith(".spec.js")
        or norm_path.startswith("tests/")
        or norm_path.startswith("test/")
    ):
        return FileAccessPolicy.ALLOWED, FileCategory.TEST

    # 4. Configuration files
    config_names = (
        "pyproject.toml",
        "setup.py",
        "setup.cfg",
        "package.json",
        "cargo.toml",
        "pom.xml",
        "build.gradle",
        "makefile",
        "dockerfile",
        "tsconfig.json",
    )
    if basename in config_names or basename.endswith((".yml", ".yaml", ".toml", ".ini", ".cfg")):
        return FileAccessPolicy.ALLOWED, FileCategory.CONFIG

    # 5. Documentation
    if basename.startswith("readme") or basename.startswith("license") or basename.endswith((".md", ".rst", ".txt")):
        return FileAccessPolicy.ALLOWED, FileCategory.DOCUMENTATION

    # 6. Binary extensions
    binary_exts = (
        ".so",
        ".dll",
        ".dylib",
        ".exe",
        ".bin",
        ".pyc",
        ".pyo",
        ".o",
        ".a",
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".zip",
        ".tar",
        ".gz",
        ".7z",
    )
    if basename.endswith(binary_exts):
        return FileAccessPolicy.ALLOWED, FileCategory.BINARY

    # 7. Source code extensions
    source_exts = (
        ".py",
        ".rs",
        ".ts",
        ".js",
        ".go",
        ".c",
        ".cpp",
        ".h",
        ".hpp",
        ".java",
        ".rb",
        ".php",
        ".sh",
        ".ps1",
    )
    if basename.endswith(source_exts):
        return FileAccessPolicy.ALLOWED, FileCategory.SOURCE

    return FileAccessPolicy.ALLOWED, FileCategory.UNKNOWN
