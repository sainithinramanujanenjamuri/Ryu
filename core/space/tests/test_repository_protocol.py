"""Unit tests for Repository Protocol & Software-Engineering Foundation in core/space."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
import pytest

from core.space.repository_protocol import (
    ASTInspectionReport,
    ASTNodeSummary,
    CodePatch,
    FileAccessPolicy,
    FileCategory,
    FileMetadata,
    PatchResult,
    ProjectMetadata,
    RepositoryIdentity,
    RepositoryInspectionResult,
    RepositoryPolicyProtocol,
    RepositoryProtocol,
    RepositorySnapshot,
    RepositorySpaceIsolationViolation,
)


def test_repository_identity_valid() -> None:
    ident = RepositoryIdentity(
        repository_id="repo-ryu-01",
        space_id="space-se-01",
        canonical_root="/var/repos/ryu",
        ref="main",
        metadata={"language": "python"},
    )
    assert ident.repository_id == "repo-ryu-01"
    assert ident.space_id == "space-se-01"
    assert ident.canonical_root == "/var/repos/ryu"


def test_repository_identity_immutable() -> None:
    ident = RepositoryIdentity(
        repository_id="repo-ryu-01",
        space_id="space-se-01",
        canonical_root="/var/repos/ryu",
    )
    with pytest.raises(FrozenInstanceError):
        ident.canonical_root = "/escaped/root"  # type: ignore


def test_repository_identity_rejects_empty_fields() -> None:
    with pytest.raises(ValueError, match="repository_id"):
        RepositoryIdentity(repository_id="", space_id="space-1", canonical_root="/root")
    with pytest.raises(ValueError, match="space_id"):
        RepositoryIdentity(repository_id="r1", space_id="", canonical_root="/root")
    with pytest.raises(ValueError, match="canonical_root"):
        RepositoryIdentity(repository_id="r1", space_id="space-1", canonical_root="")


def test_repository_identity_rejects_credential_labels_in_metadata() -> None:
    with pytest.raises(ValueError, match="credential label"):
        RepositoryIdentity(
            repository_id="repo-sec",
            space_id="space-1",
            canonical_root="/root",
            metadata={"api_token": "val"},
        )

    with pytest.raises(ValueError, match="secret token"):
        RepositoryIdentity(
            repository_id="repo-sec",
            space_id="space-1",
            canonical_root="/root",
            metadata={"notes": "bearer token: abcdef12345"},
        )


def test_file_metadata_valid_and_immutable() -> None:
    meta = FileMetadata(
        relative_path="src/main.py",
        size_bytes=1024,
        content_hash="abc123hash",
        category=FileCategory.SOURCE,
        access_policy=FileAccessPolicy.ALLOWED,
    )
    assert meta.relative_path == "src/main.py"
    assert meta.category == FileCategory.SOURCE

    with pytest.raises(FrozenInstanceError):
        meta.size_bytes = 2048  # type: ignore


def test_file_metadata_rejects_negative_size() -> None:
    with pytest.raises(ValueError, match="size_bytes"):
        FileMetadata(
            relative_path="file.txt",
            size_bytes=-1,
            content_hash="hash",
            category=FileCategory.DOCUMENTATION,
            access_policy=FileAccessPolicy.ALLOWED,
        )


def test_repository_snapshot_valid() -> None:
    ident = RepositoryIdentity(
        repository_id="repo-snap",
        space_id="space-snap",
        canonical_root="/root",
    )
    now = datetime.now(timezone.utc)
    snap = RepositorySnapshot(
        snapshot_id="snap-01",
        repository_identity=ident,
        inspected_at=now,
        file_inventory=[],
        excluded_paths=[".git"],
        total_files=0,
        total_bytes=0,
    )
    assert snap.snapshot_id == "snap-01"
    assert snap.total_files == 0


def test_repository_inspection_result_space_isolation() -> None:
    ident = RepositoryIdentity(
        repository_id="repo-iso",
        space_id="space-A",
        canonical_root="/root",
    )
    snap = RepositorySnapshot(
        snapshot_id="snap-iso",
        repository_identity=ident,
        inspected_at=datetime.now(timezone.utc),
    )
    # Attempt to assign inspection result to space-B when identity belongs to space-A
    with pytest.raises(RepositorySpaceIsolationViolation):
        RepositoryInspectionResult(
            result_id="res-iso",
            space_id="space-B",
            task_id="task-1",
            plan_version=1,
            repository_identity=ident,
            snapshot=snap,
            provenance_id="prov-1",
        )


def test_ast_inspection_report_valid() -> None:
    report = ASTInspectionReport(
        relative_path="core/kernel.py",
        language="python",
        classes=[ASTNodeSummary(name="SpaceKernel", node_type="ClassDef", line_number=20)],
        functions=[ASTNodeSummary(name="get_plan_version", node_type="FunctionDef", line_number=45)],
        imports=["typing", "sys"],
    )
    assert report.language == "python"
    assert len(report.classes) == 1
    assert report.classes[0].name == "SpaceKernel"
    assert len(report.functions) == 1
    assert report.parse_status == "ok"


class MockRepositoryPolicy:
    def evaluate_path(self, relative_path: str, space_id: str) -> tuple[FileAccessPolicy, FileCategory]:
        if relative_path.endswith(".py"):
            return FileAccessPolicy.ALLOWED, FileCategory.SOURCE
        return FileAccessPolicy.DENIED, FileCategory.UNKNOWN


class MockRepository:
    def identify_repository(self, space_id: str) -> RepositoryIdentity:
        return RepositoryIdentity("mock-repo", space_id, "/mock")

    def inspect_tree(self, space_id: str, max_depth: int = 20, max_files: int = 5000) -> RepositorySnapshot:
        return RepositorySnapshot("snap-mock", self.identify_repository(space_id), datetime.now(timezone.utc))

    def read_file(self, relative_path: str, space_id: str, max_bytes: int = 10 * 1024 * 1024) -> tuple[bytes, str]:
        return b"content", "hash"

    def inspect_ast(self, relative_path: str, space_id: str) -> ASTInspectionReport:
        return ASTInspectionReport(relative_path, "python")

    def discover_tests(self, space_id: str) -> list[str]:
        return ["tests/test_mock.py"]

    def apply_patch(
        self,
        space_id: str,
        patch: CodePatch,
        expected_before_hashes: dict[str, str] | None = None,
    ) -> PatchResult:
        return PatchResult(patch.patch_id, "mock-tx", True)

    def revert_patch(self, space_id: str, patch_id: str) -> PatchResult:
        return PatchResult(patch_id, "mock-tx", True, rolled_back=True)


def test_runtime_checkable_protocols() -> None:
    mock_policy = MockRepositoryPolicy()
    mock_repo = MockRepository()

    assert isinstance(mock_policy, RepositoryPolicyProtocol)
    assert isinstance(mock_repo, RepositoryProtocol)

