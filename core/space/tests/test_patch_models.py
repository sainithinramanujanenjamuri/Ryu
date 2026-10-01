"""Unit tests for Phase 14.4 repository patch protocol data models and bounds (REPO-002, REPO-004)."""

import pytest

from core.space.repository_protocol import (
    MAX_CHANGED_FILES,
    MAX_DIFF_LINES,
    CodePatch,
    FileDiff,
    FilePatchOperation,
    Hunk,
    PatchBoundsExceededError,
    PatchTransaction,
    PatchTransactionState,
    RepositoryProtocol,
)


def test_patch_bounds_constants() -> None:
    """Verify ADR-0044 ceiling constants."""
    assert MAX_CHANGED_FILES == 5
    assert MAX_DIFF_LINES == 500


def test_code_patch_valid() -> None:
    """Verify CodePatch dataclass creation with valid bounds."""
    patch = CodePatch(
        patch_id="patch-01",
        target_files=("file1.py", "file2.py"),
        diff_text="@@ -1 +1 @@\n-a\n+b\n",
        changed_line_count=2,
        space_id="test-space",
    )
    assert patch.patch_id == "patch-01"
    assert len(patch.target_files) == 2
    assert patch.changed_line_count == 2
    assert patch.space_id == "test-space"


def test_code_patch_empty_id_rejected() -> None:
    """Verify empty patch_id raises ValueError."""
    with pytest.raises(ValueError, match="patch_id must not be empty"):
        CodePatch(
            patch_id="",
            target_files=("file1.py",),
            diff_text="diff",
        )


def test_code_patch_exceeds_file_ceiling() -> None:
    """Verify exceeding MAX_CHANGED_FILES (5) raises PatchBoundsExceededError (REPO-004)."""
    with pytest.raises(PatchBoundsExceededError, match="exceeding ceiling of 5"):
        CodePatch(
            patch_id="patch-overflow",
            target_files=("f1.py", "f2.py", "f3.py", "f4.py", "f5.py", "f6.py"),
            diff_text="diff",
        )


def test_code_patch_exceeds_line_ceiling() -> None:
    """Verify exceeding MAX_DIFF_LINES (500) raises PatchBoundsExceededError (REPO-004)."""
    with pytest.raises(PatchBoundsExceededError, match="exceeding ceiling of 500"):
        CodePatch(
            patch_id="patch-line-overflow",
            target_files=("f1.py",),
            diff_text="diff",
            changed_line_count=501,
        )


def test_patch_transaction_valid() -> None:
    """Verify PatchTransaction dataclass creation."""
    tx = PatchTransaction(
        transaction_id="tx-01",
        patch_id="patch-01",
        space_id="test-space",
        affected_files=("f1.py",),
        state=PatchTransactionState.VERIFIED,
    )
    assert tx.transaction_id == "tx-01"
    assert tx.state == PatchTransactionState.VERIFIED
    assert tx.affected_files == ("f1.py",)


def test_patch_transaction_empty_id_rejected() -> None:
    """Verify empty transaction_id raises ValueError."""
    with pytest.raises(ValueError, match="transaction_id must not be empty"):
        PatchTransaction(
            transaction_id="",
            patch_id="patch-01",
            space_id="test-space",
        )


def test_file_diff_target_path() -> None:
    """Verify FileDiff target_path property for create, modify, delete."""
    hunk = Hunk(old_start=1, old_count=1, new_start=1, new_count=1, lines=(" @@",))

    create_diff = FileDiff(
        old_path=None,
        new_path="new.py",
        operation=FilePatchOperation.CREATE,
        hunks=(hunk,),
    )
    assert create_diff.target_path == "new.py"

    mod_diff = FileDiff(
        old_path="mod.py",
        new_path="mod.py",
        operation=FilePatchOperation.MODIFY,
        hunks=(hunk,),
    )
    assert mod_diff.target_path == "mod.py"

    del_diff = FileDiff(
        old_path="del.py",
        new_path=None,
        operation=FilePatchOperation.DELETE,
        hunks=(hunk,),
    )
    assert del_diff.target_path == "del.py"


class DummyRepoImplementation:
    def identify_repository(self, space_id: str): ...
    def inspect_tree(self, space_id: str, max_depth: int = 20, max_files: int = 5000): ...
    def read_file(self, relative_path: str, space_id: str, max_bytes: int = 10 * 1024 * 1024): ...
    def inspect_ast(self, relative_path: str, space_id: str): ...
    def discover_tests(self, space_id: str): ...
    def apply_patch(self, space_id: str, patch: CodePatch, expected_before_hashes: dict[str, str] | None = None): ...
    def revert_patch(self, space_id: str, patch_id: str): ...


def test_repository_protocol_runtime_checkable() -> None:
    """Verify RepositoryProtocol is runtime checkable without importing higher layers."""
    dummy = DummyRepoImplementation()
    assert isinstance(dummy, RepositoryProtocol)

