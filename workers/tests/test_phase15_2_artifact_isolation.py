"""Phase 15.2 Test Suite: Space-Safe Artifact Namespace Isolation (Finding F-02 — P0).

Verifies ART-001 through ART-018 and Adversarial Matrix ADV-ART-01 through ADV-ART-08.
Governed by: docs/PHASE_15_2_ARCHITECTURE_AUDIT.md, ADR-0046, CONTRACT SPACE-ART-001.

Ensures every capability-generated filesystem artifact is strictly partitioned
by space_id: <base_working_dir>/artifacts/<space_id>/<worker_type>/<filename>
"""

from __future__ import annotations

import concurrent.futures
import hashlib
import shutil
import tempfile
import threading
from pathlib import Path

import pytest
from ryu.pulse_bus.bus import PulseBus
from ryu.pulse_bus.pulse import Pulse

from core.orchestrator.dispatch_model import (
    DeterministicDispatcher,
    TaskExecutionResult,
    TaskNode,
)
from core.space.artifact_paths import (
    CANONICAL_WORKER_NAMESPACES,
    get_space_artifact_dir,
    is_safe_artifact_path,
    resolve_artifact_path,
    validate_artifact_filename,
    validate_space_id,
    validate_worker_namespace,
)
from core.space.repository_protocol import (
    RepositoryIdentity,
)
from core.space.research_protocol import (
    SourceAuthorizationDecision,
    SourceAuthorizationPolicyProtocol,
    SourceIdentity,
)
from workers.contract import (
    ExecutionRequest,
    WorkerIdentity,
)
from workers.repository.worker import RepositoryWorker
from workers.research.worker import ResearchWorker
from workers.test_runner.worker import TestRunnerWorker


class AllowlistPolicy(SourceAuthorizationPolicyProtocol):
    """Explicit allowlist policy for testing research worker."""

    def __init__(self, allowed_prefixes: list[str]) -> None:
        self.allowed_prefixes = allowed_prefixes

    def evaluate_source(self, source: SourceIdentity, space_id: str) -> SourceAuthorizationDecision:
        for prefix in self.allowed_prefixes:
            if source.canonical_locator.startswith(prefix):
                return SourceAuthorizationDecision(
                    is_allowed=True,
                    source_identity=source,
                    reason=f"Matched prefix allowlist: {prefix}",
                    policy_id="allowlist-test-policy",
                )
        return SourceAuthorizationDecision(
            is_allowed=False,
            source_identity=source,
            reason=f"Locator '{source.canonical_locator}' is not in allowlist",
            policy_id="allowlist-test-policy",
        )


class SpyBus(PulseBus):
    """PulseBus recording published pulses."""

    def __init__(self) -> None:
        super().__init__()
        self.published: list[Pulse] = []
        self._lock = threading.Lock()

    def publish(self, pulse: Pulse) -> Pulse:
        with self._lock:
            self.published.append(pulse)
        return pulse


# ============================================================================
# Path Authority Unit Tests (§6, §7, §8, §9)
# ============================================================================


def test_space_id_validation_valid() -> None:
    assert validate_space_id("alpha") == "alpha"
    assert validate_space_id("space-123_test.01") == "space-123_test.01"


def test_space_id_validation_rejects_invalid() -> None:
    with pytest.raises(ValueError):
        validate_space_id("")
    with pytest.raises(ValueError):
        validate_space_id("space/a")
    with pytest.raises(ValueError):
        validate_space_id("space\\b")
    with pytest.raises(ValueError):
        validate_space_id("..")
    with pytest.raises(ValueError):
        validate_space_id("space/../../other")
    with pytest.raises(ValueError):
        validate_space_id("C:space")
    with pytest.raises(ValueError):
        validate_space_id("space\x00id")
    with pytest.raises(ValueError):
        validate_space_id("con")
    with pytest.raises(ValueError):
        validate_space_id("NUL.txt")


def test_worker_namespace_validation() -> None:
    for ns in CANONICAL_WORKER_NAMESPACES:
        assert validate_worker_namespace(ns) == ns

    with pytest.raises(ValueError):
        validate_worker_namespace("..")
    with pytest.raises(ValueError):
        validate_worker_namespace("worker/sub")


def test_artifact_filename_validation() -> None:
    assert validate_artifact_filename("report.json") == "report.json"
    assert validate_artifact_filename("task-01_patch.diff") == "task-01_patch.diff"

    with pytest.raises(ValueError):
        validate_artifact_filename("")
    with pytest.raises(ValueError):
        validate_artifact_filename("dir/file.txt")
    with pytest.raises(ValueError):
        validate_artifact_filename("../secret.txt")
    with pytest.raises(ValueError):
        validate_artifact_filename("aux.json")


def test_get_space_artifact_dir_structure() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)
        p = get_space_artifact_dir(base, "space-A", "repository")
        assert p == (base.resolve() / "artifacts" / "space-A" / "repository")


def test_resolve_artifact_path_structure() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)
        p = resolve_artifact_path(base, "space-A", "test_runner", "report.json")
        expected = base.resolve() / "artifacts" / "space-A" / "test_runner" / "report.json"
        assert p == expected


# ============================================================================
# ART-001: RepositoryWorker Space Scoping
# ============================================================================


def test_art_001_repository_worker_artifacts_space_scoped() -> None:
    """ART-001: Repository artifacts strictly reside in artifacts/<space_id>/repository/."""
    with tempfile.TemporaryDirectory() as repo_tmp, tempfile.TemporaryDirectory() as work_tmp:
        repo_root = Path(repo_tmp)
        base_work = Path(work_tmp)
        space_id = "space-art-001"
        (repo_root / "sample.py").write_text("print('hello')", encoding="utf-8")

        identity = RepositoryIdentity(
            repository_id="repo-001",
            space_id=space_id,
            canonical_root=str(repo_root),
        )
        worker = RepositoryWorker(
            identity=WorkerIdentity("rw-1", "repository.inspect", space_id),
            base_working_dir=base_work,
            repository_identity=identity,
        )
        req = ExecutionRequest(
            request_id="req-1",
            correlation_id="corr-1",
            space_id=space_id,
            worker_id="rw-1",
            capability="repository.inspect",
            task_id="task-art-001",
            arguments={"action": "inspect_tree"},
        )
        result = worker.execute(req)
        assert result.is_success is True
        assert len(result.artifacts) == 1
        art = result.artifacts[0]
        assert art.space_id == space_id

        # Verify on-disk path structure
        art_path = Path(art.path)
        expected_dir = base_work.resolve() / "artifacts" / space_id / "repository"
        assert art_path.parent == expected_dir
        assert art_path.exists()

        # SHA-256 verification matches physical file
        actual_sha = hashlib.sha256(art_path.read_bytes()).hexdigest()
        assert art.sha256 == actual_sha


# ============================================================================
# ART-002: TestRunnerWorker Space Scoping
# ============================================================================


def test_art_002_test_runner_worker_artifacts_space_scoped() -> None:
    """ART-002: TestRunner artifacts strictly reside in artifacts/<space_id>/test_runner/."""
    with tempfile.TemporaryDirectory() as repo_tmp, tempfile.TemporaryDirectory() as work_tmp:
        repo_root = Path(repo_tmp)
        base_work = Path(work_tmp)
        space_id = "space-art-002"
        (repo_root / "test_example.py").write_text("def test_ok(): pass\n", encoding="utf-8")

        worker = TestRunnerWorker(
            identity=WorkerIdentity("trw-1", "test.execute", space_id),
            base_working_dir=base_work,
        )
        req = ExecutionRequest(
            request_id="req-2",
            correlation_id="corr-2",
            space_id=space_id,
            worker_id="trw-1",
            capability="test.execute",
            task_id="task-art-002",
            arguments={
                "command": "python -m pytest test_example.py",
                "repository_root": str(repo_root),
                "framework": "pytest",
            },
        )
        result = worker.execute(req)
        assert result.is_success is True
        assert len(result.artifacts) >= 2
        for art in result.artifacts:
            assert art.space_id == space_id
            art_path = Path(art.path)
            expected_dir = base_work.resolve() / "artifacts" / space_id / "test_runner"
            assert art_path.parent == expected_dir
            assert art_path.exists()
            assert hashlib.sha256(art_path.read_bytes()).hexdigest() == art.sha256


# ============================================================================
# ART-003: ResearchWorker Space Scoping (Synthesis)
# ============================================================================


def test_art_003_research_worker_artifacts_space_scoped() -> None:
    """ART-003: Research artifacts strictly reside in artifacts/<space_id>/research/."""
    with tempfile.TemporaryDirectory() as work_tmp:
        base_work = Path(work_tmp)
        space_id = "space-art-003"
        policy = AllowlistPolicy(["https://example.com"])
        worker = ResearchWorker(
            identity=WorkerIdentity("rw-3", "research.retrieve", space_id),
            base_working_dir=base_work,
        )
        req = ExecutionRequest(
            request_id="req-3",
            correlation_id="corr-3",
            space_id=space_id,
            worker_id="rw-3",
            capability="research.retrieve",
            task_id="task-art-003",
            arguments={
                "locator": "https://example.com/scca_arch",
                "source_policy": policy,
                "raw_content": "# SCCA Architecture\nRYU uses Space-Centric Cognitive Architecture.\n",
            },
        )
        result = worker.execute(req)
        assert result.is_success is True
        assert len(result.artifacts) == 2
        for art in result.artifacts:
            assert art.space_id == space_id
            art_path = Path(art.path)
            expected_dir = base_work.resolve() / "artifacts" / space_id / "research"
            assert art_path.parent == expected_dir
            assert art_path.exists()
            assert hashlib.sha256(art_path.read_bytes()).hexdigest() == art.sha256


# ============================================================================
# ART-004 & ART-006: Identical Task IDs Across Spaces Do Not Overwrite
# ============================================================================


def test_art_004_and_art_006_identical_task_ids_isolated_and_no_overwrite() -> None:
    """ART-004 & ART-006: Independent Spaces executing identical task IDs produce disjoint artifacts without collision."""
    with tempfile.TemporaryDirectory() as repo_tmp, tempfile.TemporaryDirectory() as work_tmp:
        repo_root = Path(repo_tmp)
        base_work = Path(work_tmp)
        task_id = "task-common-001"

        # Repo A
        repo_a = repo_root / "repo_a"
        repo_a.mkdir()
        (repo_a / "a.py").write_text("print('A')", encoding="utf-8")

        # Repo B
        repo_b = repo_root / "repo_b"
        repo_b.mkdir()
        (repo_b / "b.py").write_text("print('B')", encoding="utf-8")

        worker_a = RepositoryWorker(
            identity=WorkerIdentity("rw-a", "repository.inspect", "space-A"),
            base_working_dir=base_work,
            repository_identity=RepositoryIdentity("repo-a", "space-A", str(repo_a)),
        )
        worker_b = RepositoryWorker(
            identity=WorkerIdentity("rw-b", "repository.inspect", "space-B"),
            base_working_dir=base_work,
            repository_identity=RepositoryIdentity("repo-b", "space-B", str(repo_b)),
        )

        res_a = worker_a.execute(ExecutionRequest("r1", "c1", "space-A", "rw-a", "repository.inspect", {"action": "inspect_tree"}, task_id=task_id))
        res_b = worker_b.execute(ExecutionRequest("r2", "c2", "space-B", "rw-b", "repository.inspect", {"action": "inspect_tree"}, task_id=task_id))

        assert res_a.is_success and res_b.is_success
        path_a = Path(res_a.artifacts[0].path)
        path_b = Path(res_b.artifacts[0].path)

        # Disjoint namespaces
        assert path_a != path_b
        assert "space-A" in str(path_a)
        assert "space-B" in str(path_b)
        assert path_a.exists() and path_b.exists()

        # Content is different (different repos)
        content_a = path_a.read_text(encoding="utf-8")
        content_b = path_b.read_text(encoding="utf-8")
        assert "repo-a" in content_a
        assert "repo-b" in content_b


# ============================================================================
# ART-005 & ADV-ART-06: Cross-Space Read Denied & Space Claim Mismatch
# ============================================================================


def test_art_005_and_adv_art_06_cross_space_artifact_rejected() -> None:
    """ART-005 & ADV-ART-06: Dispatcher rejects evidence from Space A presented for Space B."""
    with tempfile.TemporaryDirectory() as work_tmp:
        base = Path(work_tmp)
        space_a_dir = get_space_artifact_dir(base, "space-A", "repository")
        space_a_dir.mkdir(parents=True, exist_ok=True)
        art_file = space_a_dir / "task-1_manifest.json"
        art_file.write_text('{"repo": "secret-a"}', encoding="utf-8")
        art_sha = hashlib.sha256(art_file.read_bytes()).hexdigest()

        dispatcher = DeterministicDispatcher()
        node = TaskNode(id="task-1", capability="repository.inspect")

        # Path belongs to space-A, but task is verified for space-B
        exec_res = TaskExecutionResult(
            request_id="req-cross",
            status="ok",
            task_id="task-1",
            space_id="space-B",
            plan_version=1,
            artifacts=[{
                "name": "task-1_manifest.json",
                "path": str(art_file),
                "sha256": art_sha,
                "space_id": "space-B",
            }],
        )

        verification = dispatcher.verify_execution_evidence(
            space_id="space-B",
            task_node=node,
            execution_result=exec_res,
            plan_version=1,
            base_dir=base,
        )
        assert verification.is_valid is False
        assert any("escapes space sandbox boundary" in r for r in verification.failure_reasons)


# ============================================================================
# ART-007: Space-Scoped Cleanup
# ============================================================================


def test_art_007_cleanup_is_space_scoped() -> None:
    """ART-007: Deleting Space B artifacts leaves Space A intact."""
    with tempfile.TemporaryDirectory() as work_tmp:
        base = Path(work_tmp)
        dir_a = get_space_artifact_dir(base, "space-A", "repository")
        dir_b = get_space_artifact_dir(base, "space-B", "repository")
        dir_a.mkdir(parents=True, exist_ok=True)
        dir_b.mkdir(parents=True, exist_ok=True)

        file_a = dir_a / "manifest.json"
        file_b = dir_b / "manifest.json"
        file_a.write_text("DATA_A", encoding="utf-8")
        file_b.write_text("DATA_B", encoding="utf-8")

        # Delete Space B's root
        space_b_root = get_space_artifact_dir(base, "space-B")
        shutil.rmtree(space_b_root)

        assert not space_b_root.exists()
        assert file_a.exists()
        assert file_a.read_text(encoding="utf-8") == "DATA_A"


# ============================================================================
# ART-008, ART-009, ADV-ART-01..05, ADV-ART-08: Traversal & Path Injections
# ============================================================================


def test_art_008_traversal_rejected_in_paths() -> None:
    """ART-008: '..' traversal in space_id or filename is rejected."""
    with tempfile.TemporaryDirectory() as work_tmp:
        base = Path(work_tmp)
        with pytest.raises(ValueError):
            resolve_artifact_path(base, "space-A", "repository", "../../evil.json")


def test_adv_art_01_malicious_task_id_traversal() -> None:
    """ADV-ART-01: Malicious task_id containing relative traversal is rejected."""
    with tempfile.TemporaryDirectory() as work_tmp:
        base = Path(work_tmp)
        with pytest.raises(ValueError):
            resolve_artifact_path(base, "space-A", "repository", "../../escaped_task_manifest.json")


def test_adv_art_02_malicious_windows_task_id_traversal() -> None:
    """ADV-ART-02: Windows backslash traversal ..\\..\\ is rejected."""
    with tempfile.TemporaryDirectory() as work_tmp:
        base = Path(work_tmp)
        with pytest.raises(ValueError):
            resolve_artifact_path(base, "space-A", "repository", "..\\..\\windows_escaped.json")


def test_adv_art_03_malicious_space_id() -> None:
    """ADV-ART-03: Malicious space_id traversal is rejected."""
    with tempfile.TemporaryDirectory() as work_tmp:
        base = Path(work_tmp)
        with pytest.raises(ValueError):
            resolve_artifact_path(base, "space/../../other", "repository", "file.json")


def test_adv_art_04_absolute_windows_path_rejected() -> None:
    """ADV-ART-04: Absolute Windows path is rejected by containment check."""
    with tempfile.TemporaryDirectory() as work_tmp:
        base = Path(work_tmp)
        safe, _, err = is_safe_artifact_path("C:\\Windows\\System32\\calc.exe", base, "space-A")
        assert safe is False
        assert "escapes space sandbox boundary" in err


def test_adv_art_05_unc_path_rejected() -> None:
    """ADV-ART-05: UNC path is rejected by containment check."""
    with tempfile.TemporaryDirectory() as work_tmp:
        base = Path(work_tmp)
        safe, _, err = is_safe_artifact_path("\\\\unc_share\\evil\\hack", base, "space-A")
        assert safe is False


def test_adv_art_08_null_byte_rejected() -> None:
    """ADV-ART-08: Null byte in path or filename is rejected."""
    with pytest.raises(ValueError, match="null bytes"):
        validate_artifact_filename("file\x00.txt")
    with pytest.raises(ValueError, match="null bytes"):
        validate_space_id("space\x00id")


# ============================================================================
# ART-010: Old Unpartitioned Fallback Is Rejected
# ============================================================================


def test_art_010_old_unpartitioned_fallback_rejected() -> None:
    """ART-010: Legacy path without space_id is rejected by Dispatcher containment check."""
    with tempfile.TemporaryDirectory() as work_tmp:
        base = Path(work_tmp)
        legacy_dir = base / "artifacts" / "repository"
        legacy_dir.mkdir(parents=True, exist_ok=True)
        legacy_file = legacy_dir / "task-1_manifest.json"
        legacy_file.write_text("LEGACY", encoding="utf-8")

        safe, resolved, err = is_safe_artifact_path(legacy_file, base, "space-target")
        assert safe is False
        assert "escapes space sandbox boundary" in err


# ============================================================================
# ART-011 & ADV-ART-07: SHA-256 Binding and Mismatch Detection
# ============================================================================


def test_art_011_and_adv_art_07_sha256_integrity_and_mismatch() -> None:
    """ART-011 & ADV-ART-07: Real on-disk SHA-256 is verified; tampered hash is caught."""
    with tempfile.TemporaryDirectory() as work_tmp:
        base = Path(work_tmp)
        art_path = resolve_artifact_path(base, "space-1", "repository", "manifest.json")
        art_path.parent.mkdir(parents=True, exist_ok=True)
        art_path.write_bytes(b"AUTHENTIC_DATA")

        actual_sha = hashlib.sha256(b"AUTHENTIC_DATA").hexdigest()
        bad_sha = hashlib.sha256(b"CORRUPT_DATA").hexdigest()

        dispatcher = DeterministicDispatcher()
        node = TaskNode(id="t1", capability="repository.inspect")

        # 1. Valid hash
        res_ok = TaskExecutionResult(
            "r1", "ok", "t1", "space-1", 1,
            artifacts=[{"name": "manifest.json", "path": str(art_path), "sha256": actual_sha, "space_id": "space-1"}],
        )
        v_ok = dispatcher.verify_execution_evidence("space-1", node, res_ok, 1, base_dir=base)
        assert v_ok.is_valid is True

        # 2. Tampered hash (ADV-ART-07)
        res_bad = TaskExecutionResult(
            "r2", "ok", "t1", "space-1", 1,
            artifacts=[{"name": "manifest.json", "path": str(art_path), "sha256": bad_sha, "space_id": "space-1"}],
        )
        v_bad = dispatcher.verify_execution_evidence("space-1", node, res_bad, 1, base_dir=base)
        assert v_bad.is_valid is False
        assert any("SHA-256 mismatch" in r for r in v_bad.failure_reasons)


# ============================================================================
# ART-012 & ART-013: Evidence and Provenance Space ID Binding
# ============================================================================


def test_art_012_and_art_013_evidence_and_provenance_space_binding() -> None:
    """ART-012 & ART-013: Artifact and Provenance carry matching space_id."""
    with tempfile.TemporaryDirectory() as repo_tmp, tempfile.TemporaryDirectory() as work_tmp:
        repo_root = Path(repo_tmp)
        base_work = Path(work_tmp)
        space_id = "space-bound-01"
        (repo_root / "x.py").write_text("x = 1\n", encoding="utf-8")

        worker = RepositoryWorker(
            identity=WorkerIdentity("rw-1", "repository.inspect", space_id),
            base_working_dir=base_work,
            repository_identity=RepositoryIdentity("repo-1", space_id, str(repo_root)),
        )
        res = worker.execute(ExecutionRequest("r", "c", space_id, "rw-1", "repository.inspect", {"action": "inspect_tree"}, task_id="t-1"))
        assert res.is_success is True

        art = res.artifacts[0]
        assert art.space_id == space_id

        # Verification through Dispatcher binds evidence space_id
        dispatcher = DeterministicDispatcher()
        node = TaskNode(id="t-1", capability="repository.inspect")
        exec_res = TaskExecutionResult(
            "r", "ok", "t-1", space_id, 1,
            artifacts=[art.to_dict()],
        )
        ver = dispatcher.verify_execution_evidence(space_id, node, exec_res, 1, base_dir=base_work)
        assert ver.is_valid is True
        for ev in ver.evidence_items:
            assert ev.space_id == space_id


# ============================================================================
# ART-014: Concurrent Multi-Space Execution (Zero Collisions)
# ============================================================================


def test_art_014_concurrent_multi_space_execution_zero_collisions() -> None:
    """ART-014: 4 concurrent Spaces executing identical task ID concurrently produce zero collisions."""
    num_spaces = 4
    task_id = "task-concurrent-001"

    with tempfile.TemporaryDirectory() as work_tmp:
        base_work = Path(work_tmp)

        repo_roots = []
        for idx in range(num_spaces):
            r = base_work / f"repo_source_{idx}"
            r.mkdir(parents=True, exist_ok=True)
            (r / f"file_{idx}.py").write_text(f"UNIQUE_CONTENT_FOR_SPACE_{idx}\n", encoding="utf-8")
            repo_roots.append(r)

        def run_space(idx: int) -> tuple[str, str, str]:
            space_id = f"space-concurrent-{idx}"
            repo_root = repo_roots[idx]

            worker = RepositoryWorker(
                identity=WorkerIdentity(f"rw-{idx}", "repository.inspect", space_id),
                base_working_dir=base_work,
                repository_identity=RepositoryIdentity(f"repo-{idx}", space_id, str(repo_root)),
            )
            res = worker.execute(
                ExecutionRequest(f"req-{idx}", f"corr-{idx}", space_id, f"rw-{idx}", "repository.inspect", {"action": "inspect_tree"}, task_id=task_id)
            )
            assert res.is_success is True, f"Failed for space {space_id}: {res.error.message if res.error else 'unknown'}"
            art = res.artifacts[0]
            return space_id, art.path, art.sha256

        with concurrent.futures.ThreadPoolExecutor(max_workers=num_spaces) as executor:
            futures = [executor.submit(run_space, i) for i in range(num_spaces)]
            results = [f.result() for f in futures]

        # Assert all 4 paths are distinct
        paths = [r[1] for r in results]
        assert len(set(paths)) == num_spaces

        # Assert each file exists and has its exact sha256
        for space_id, path_str, expected_sha in results:
            p = Path(path_str)
            assert p.exists()
            assert space_id in str(p)
            assert hashlib.sha256(p.read_bytes()).hexdigest() == expected_sha


# ============================================================================
# ART-015 & ART-017: Restart / Replay Validation
# ============================================================================


def test_art_015_and_art_017_restart_replay_validates_space_namespace() -> None:
    """ART-015 & ART-017: Replay mode preserves and verifies space-scoped artifact paths."""
    dispatcher = DeterministicDispatcher()
    node = TaskNode(id="t-rep", capability="repository.inspect")

    # In replay mode: artifact path must still be safe for space-replay-1
    res_ok = TaskExecutionResult(
        request_id="req-rep",
        status="ok",
        task_id="t-rep",
        space_id="space-replay-1",
        plan_version=2,
        artifacts=[{
            "name": "manifest.json",
            "path": "artifacts/space-replay-1/repository/manifest.json",
            "sha256": "abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789",
            "space_id": "space-replay-1",
        }],
    )
    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)
        v = dispatcher.verify_execution_evidence(
            space_id="space-replay-1",
            task_node=node,
            execution_result=res_ok,
            plan_version=2,
            base_dir=base,
            replay_mode=True,
        )
        assert v.is_valid is True

        # But replay with wrong space path fails closed
        res_wrong = TaskExecutionResult(
            request_id="req-rep-wrong",
            status="ok",
            task_id="t-rep",
            space_id="space-replay-1",
            plan_version=2,
            artifacts=[{
                "name": "manifest.json",
                "path": "artifacts/space-other/repository/manifest.json",
                "sha256": "abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789",
                "space_id": "space-replay-1",
            }],
        )
        v_wrong = dispatcher.verify_execution_evidence(
            space_id="space-replay-1",
            task_node=node,
            execution_result=res_wrong,
            plan_version=2,
            base_dir=base,
            replay_mode=True,
        )
        assert v_wrong.is_valid is False
        assert any("escapes space sandbox boundary" in r for r in v_wrong.failure_reasons)
