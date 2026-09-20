"""Unit tests for Filesystem Sandbox and path canonicalization.

spec §7, §10, CONTRACT_MATRIX WORKER-003, ADR-0014
"""

import tempfile
from pathlib import Path

import pytest

from workers.contract import FilesystemPolicy
from workers.sandbox.filesystem import FilesystemSandbox


def test_fs_read_allowed_within_boundary() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        test_file = tmp_path / "hello.txt"
        test_file.write_text("world", encoding="utf-8")

        policy = FilesystemPolicy(read_paths=[str(tmp_path)])
        sandbox = FilesystemSandbox(policy)

        canonical = sandbox.validate_read(test_file)
        assert canonical == test_file.resolve()
        assert sandbox.is_read_allowed(test_file)


def test_fs_read_denied_outside_boundary() -> None:
    with tempfile.TemporaryDirectory() as allowed_dir:
        with tempfile.TemporaryDirectory() as outside_dir:
            outside_file = Path(outside_dir) / "secret.txt"
            outside_file.write_text("secret", encoding="utf-8")

            policy = FilesystemPolicy(read_paths=[allowed_dir])
            sandbox = FilesystemSandbox(policy)

            assert not sandbox.is_read_allowed(outside_file)
            with pytest.raises(PermissionError, match="outside allowed read boundaries"):
                sandbox.validate_read(outside_file)


def test_fs_parent_traversal_blocked() -> None:
    with tempfile.TemporaryDirectory() as allowed_dir:
        policy = FilesystemPolicy(read_paths=[allowed_dir])
        sandbox = FilesystemSandbox(policy)

        # Attempt parent traversal out of allowed directory
        traversal_path = Path(allowed_dir) / ".." / ".." / "some_file.txt"
        with pytest.raises(PermissionError):
            sandbox.validate_read(traversal_path)


def test_fs_forbidden_path_patterns_blocked() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        config_file = git_dir / "config"
        config_file.write_text("credentials", encoding="utf-8")

        policy = FilesystemPolicy(read_paths=[str(tmp_path)])
        sandbox = FilesystemSandbox(policy)

        with pytest.raises(PermissionError, match="matches forbidden pattern"):
            sandbox.validate_read(config_file)


def test_fs_write_allowed_in_write_paths() -> None:
    with tempfile.TemporaryDirectory() as write_dir:
        policy = FilesystemPolicy(write_paths=[write_dir])
        sandbox = FilesystemSandbox(policy)

        target = Path(write_dir) / "output.txt"
        canonical = sandbox.validate_write(target)
        assert canonical == target.resolve()
        assert sandbox.is_write_allowed(target)


def test_fs_write_denied_outside_write_paths() -> None:
    with tempfile.TemporaryDirectory() as read_only_dir:
        with tempfile.TemporaryDirectory() as outside_dir:
            policy = FilesystemPolicy(
                read_paths=[read_only_dir],
                write_paths=[read_only_dir],
            )
            sandbox = FilesystemSandbox(policy)

            target = Path(outside_dir) / "output.txt"
            with pytest.raises(PermissionError):
                sandbox.validate_write(target)
