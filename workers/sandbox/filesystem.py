"""Filesystem sandbox isolation and path canonicalization.

spec §7 (Execution Layer), §10 (Prompt-Injection & Taint Model),
CONTRACT_MATRIX WORKER-003, ADR-0014
"""

from __future__ import annotations

from pathlib import Path

from workers.contract import FilesystemPolicy


class FilesystemSandbox:
    """Enforces strict path isolation and prevents path traversal / symlink escapes."""

    def __init__(self, policy: FilesystemPolicy | None = None) -> None:
        self.policy = policy or FilesystemPolicy()

    def _canonicalize(self, raw_path: str | Path) -> Path:
        """Resolve all symlinks and parent traversals (..) into an absolute Path."""
        p = Path(raw_path)
        try:
            # resolve() eliminates '..' and follows symlinks
            return p.resolve()
        except Exception:
            return p.absolute()

    def _is_forbidden(self, canonical_path: Path) -> bool:
        """Check if path matches any forbidden system or credential pattern."""
        path_str = str(canonical_path).replace("\\", "/").lower()
        for forbidden in self.policy.forbidden_paths:
            f_norm = forbidden.replace("\\", "/").lower()
            # Direct match or subdirectory match
            if f_norm in path_str.split("/"):
                return True
            if f_norm in path_str:
                return True
        return False

    def _is_subpath_of(self, path: Path, parent: Path) -> bool:
        """Check whether path is inside parent directory."""
        try:
            path.relative_to(parent)
            return True
        except ValueError:
            return False

    def is_read_allowed(self, raw_path: str | Path) -> bool:
        """Check if read access to raw_path is permitted."""
        try:
            self.validate_read(raw_path)
            return True
        except PermissionError:
            return False

    def is_write_allowed(self, raw_path: str | Path) -> bool:
        """Check if write access to raw_path is permitted."""
        try:
            self.validate_write(raw_path)
            return True
        except PermissionError:
            return False

    def validate_read(self, raw_path: str | Path) -> Path:
        """Validate read access to path. Raises PermissionError on sandbox violation."""
        canonical = self._canonicalize(raw_path)

        # 1. Denylist check
        if self._is_forbidden(canonical):
            raise PermissionError(
                f"Filesystem sandbox access denied: path '{canonical}' matches forbidden pattern"
            )

        # If no explicit read paths are set and no temp/artifact dirs exist, default deny
        allowed_parents = [
            self._canonicalize(p)
            for p in self.policy.read_paths
            + self.policy.write_paths
            + ([self.policy.temp_dir] if self.policy.temp_dir else [])
            + ([self.policy.artifact_dir] if self.policy.artifact_dir else [])
        ]

        if not allowed_parents:
            raise PermissionError(
                f"Filesystem sandbox access denied: no read paths configured; '{canonical}' denied"
            )

        # Check if canonical path falls within at least one allowed parent
        for allowed in allowed_parents:
            if canonical == allowed or self._is_subpath_of(canonical, allowed):
                return canonical

        raise PermissionError(
            f"Filesystem sandbox access denied: path '{canonical}' "
            f"is outside allowed read boundaries"
        )

    def validate_write(self, raw_path: str | Path) -> Path:
        """Validate write access to path. Raises PermissionError on sandbox violation."""
        canonical = self._canonicalize(raw_path)

        # 1. Denylist check
        if self._is_forbidden(canonical):
            raise PermissionError(
                f"Filesystem sandbox write denied: path '{canonical}' matches forbidden pattern"
            )

        # Allowed write parents
        allowed_parents = [
            self._canonicalize(p)
            for p in self.policy.write_paths
            + ([self.policy.temp_dir] if self.policy.temp_dir else [])
            + ([self.policy.artifact_dir] if self.policy.artifact_dir else [])
        ]

        if not allowed_parents:
            raise PermissionError(
                f"Filesystem sandbox write denied: no write paths configured; '{canonical}' denied"
            )

        # Check containment
        for allowed in allowed_parents:
            if canonical == allowed or self._is_subpath_of(canonical, allowed):
                return canonical

        raise PermissionError(
            f"Filesystem sandbox write denied: path '{canonical}' "
            f"is outside allowed write boundaries"
        )
