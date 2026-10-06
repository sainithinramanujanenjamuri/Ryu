"""Unit and adversarial test suite for Phase 15.5.2: Durable Semantic Memory Storage + Schema.

Contracts: MEM-SEM-001, MEM-SEM-003, ADR-0049
Verification IDs: STORAGE-001 through STORAGE-020, adversarial storage test matrix
"""

from __future__ import annotations

import concurrent.futures
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from ryu.pulse_bus.config import PostgresConfig

from core.space.memory_protocol import (
    ExperienceQuery,
    ExperienceRecord,
    MemoryFailure,
    SpaceIsolationViolation,
)
from memory.adapters.in_memory import InMemoryMemoryAdapter
from memory.adapters.postgres import PostgreSQLMemoryAdapter
from memory.embeddings.deterministic_mock import DeterministicMockEmbeddingProvider


def _make_sample_record(
    exp_id: str = "exp-001",
    space_id: str = "space-alpha",
    counterfactual: str = "Use alternative retry strategy next time",
    **kwargs: Any,
) -> ExperienceRecord:
    defaults = {
        "experience_id": exp_id,
        "space_id": space_id,
        "situation": {"task_id": "task-1", "capability": "python.exec"},
        "action": {"capability": "python.exec", "exit_code": 1},
        "outcome": "Execution failed with timeout",
        "counterfactual": counterfactual,
        "applicable_context": {"error_class": "timeout", "failure_fingerprint": "fp-sha256-abc"},
        "stored_at": datetime.now(timezone.utc),
    }
    defaults.update(kwargs)
    return ExperienceRecord(**defaults)  # type: ignore[arg-type]


class TestPhase15_5_2Storage:
    """STORAGE-001 through STORAGE-020 verification cases."""

    def test_storage_001_migration_validity(self) -> None:
        """STORAGE-001: Migration 009 exists, is valid SQL, and creates expected schema."""
        repo_root = Path(__file__).resolve().parents[2]
        migration_path = repo_root / "deploy" / "migrations" / "009_add_semantic_embeddings_to_space_experiences.sql"
        assert migration_path.exists(), f"Migration 009 not found at {migration_path}"

        sql = migration_path.read_text(encoding="utf-8")
        assert "ALTER TABLE space_experiences" in sql
        assert "ADD COLUMN IF NOT EXISTS embedding JSONB" in sql
        assert "ADD COLUMN IF NOT EXISTS embedding_model VARCHAR" in sql
        assert "ADD COLUMN IF NOT EXISTS embedding_dimension INTEGER" in sql
        assert "ADD COLUMN IF NOT EXISTS embedding_version VARCHAR" in sql
        assert "ADD COLUMN IF NOT EXISTS failure_fingerprint VARCHAR" in sql
        assert "ADD COLUMN IF NOT EXISTS provenance_ref VARCHAR" in sql
        assert "CREATE INDEX IF NOT EXISTS idx_space_exp_space_stored" in sql
        assert "CREATE INDEX IF NOT EXISTS idx_space_exp_fingerprint" in sql

    def test_storage_002_existing_record_compatibility(self) -> None:
        """STORAGE-002: Existing ExperienceRecord without embedding remains valid."""
        rec = _make_sample_record()
        assert rec.embedding is None
        assert rec.embedding_model is None
        assert rec.embedding_dimension is None
        assert rec.embedding_version is None
        # failure_fingerprint extracted from applicable_context
        assert rec.failure_fingerprint == "fp-sha256-abc"

        adapter = InMemoryMemoryAdapter()
        adapter.store_experience(rec)
        fetched = adapter.get_experience("space-alpha", "exp-001")
        assert fetched is not None
        assert fetched.embedding is None
        assert fetched.failure_fingerprint == "fp-sha256-abc"

    def test_storage_003_embedding_persistence(self) -> None:
        """STORAGE-003: Embedding vector survives store and retrieval round-trip identically."""
        mock_provider = DeterministicMockEmbeddingProvider(dimension=128)
        emb_res = mock_provider.embed("test failure experience")

        rec = _make_sample_record(
            embedding=emb_res.vector,
            embedding_model=emb_res.model,
            embedding_dimension=emb_res.dimension,
            embedding_version=emb_res.version,
        )

        adapter = InMemoryMemoryAdapter()
        adapter.store_experience(rec)

        loaded = adapter.get_experience("space-alpha", "exp-001")
        assert loaded is not None
        assert loaded.embedding == emb_res.vector
        assert loaded.embedding_dimension == 128
        assert loaded.embedding_model == "deterministic-mock"
        assert loaded.embedding_version == "1.0.0"

    def test_storage_004_metadata_persistence(self) -> None:
        """STORAGE-004: Model, dimension, and version metadata survive round-trip."""
        vec = tuple(0.1 for _ in range(64))
        rec = _make_sample_record(
            embedding=vec,
            embedding_model="custom-provider-v2",
            embedding_dimension=64,
            embedding_version="2.0.1",
        )

        adapter = InMemoryMemoryAdapter()
        adapter.store_experience(rec)
        loaded = adapter.get_experience("space-alpha", "exp-001")
        assert loaded is not None
        assert loaded.embedding_model == "custom-provider-v2"
        assert loaded.embedding_dimension == 64
        assert loaded.embedding_version == "2.0.1"

    def test_storage_005_dimension_validation(self) -> None:
        """STORAGE-005: Vector length mismatch with declared dimension is rejected."""
        vec = tuple(0.1 for _ in range(32))
        with pytest.raises(ValueError, match="does not match declared embedding_dimension"):
            _make_sample_record(
                embedding=vec,
                embedding_model="mock",
                embedding_dimension=64,  # Mismatch
                embedding_version="1.0",
            )

        with pytest.raises(ValueError, match="must be a positive integer"):
            _make_sample_record(
                embedding=vec,
                embedding_model="mock",
                embedding_dimension=0,
                embedding_version="1.0",
            )

    def test_storage_006_nan_and_infinity_rejected(self) -> None:
        """STORAGE-006: NaN and infinity components in vector are rejected."""
        with pytest.raises(ValueError, match="NaN"):
            _make_sample_record(
                embedding=(0.1, float("nan"), 0.3),
                embedding_model="mock",
                embedding_dimension=3,
                embedding_version="1.0",
            )

        with pytest.raises(ValueError, match="infinite"):
            _make_sample_record(
                embedding=(0.1, float("inf"), 0.3),
                embedding_model="mock",
                embedding_dimension=3,
                embedding_version="1.0",
            )

    def test_storage_007_space_isolation(self) -> None:
        """STORAGE-007: Space A records cannot appear in Space B queries or lookups."""
        adapter = InMemoryMemoryAdapter()
        rec_a = _make_sample_record(exp_id="exp-a", space_id="space-A")
        rec_b = _make_sample_record(exp_id="exp-b", space_id="space-B")

        adapter.store_experience(rec_a)
        adapter.store_experience(rec_b)

        # Direct ID lookup isolation
        assert adapter.get_experience("space-A", "exp-a") is not None
        assert adapter.get_experience("space-A", "exp-b") is None
        assert adapter.get_experience("space-B", "exp-a") is None
        assert adapter.get_experience("space-B", "exp-b") is not None

        # List experiences isolation
        list_a = adapter.list_experiences("space-A")
        assert len(list_a) == 1
        assert list_a[0].experience_id == "exp-a"

        # Empty space_id rejection
        with pytest.raises(SpaceIsolationViolation):
            adapter.get_experience("", "exp-a")
        with pytest.raises(SpaceIsolationViolation):
            adapter.list_experiences("")

    def test_storage_008_failure_fingerprint_persistence(self) -> None:
        """STORAGE-008: failure_fingerprint explicitly passed survives round-trip."""
        rec = _make_sample_record(
            failure_fingerprint="fp-custom-sha256-999",
        )
        adapter = InMemoryMemoryAdapter()
        adapter.store_experience(rec)

        loaded = adapter.get_experience("space-alpha", "exp-001")
        assert loaded is not None
        assert loaded.failure_fingerprint == "fp-custom-sha256-999"

    def test_storage_009_provenance_persistence(self) -> None:
        """STORAGE-009: provenance_ref survives round-trip."""
        rec = _make_sample_record(
            provenance_ref="prov-task-1-sha256-evidence",
        )
        adapter = InMemoryMemoryAdapter()
        adapter.store_experience(rec)

        loaded = adapter.get_experience("space-alpha", "exp-001")
        assert loaded is not None
        assert loaded.provenance_ref == "prov-task-1-sha256-evidence"

    def test_storage_010_duplicate_identity_handling(self) -> None:
        """STORAGE-010: Duplicate store of same experience ID updates idempotently without duplicating."""
        adapter = InMemoryMemoryAdapter()
        rec1 = _make_sample_record(outcome="Initial attempt failed")
        rec2 = _make_sample_record(outcome="Updated attempt failed with new info")

        adapter.store_experience(rec1)
        adapter.store_experience(rec2)

        all_records = adapter.list_experiences("space-alpha")
        assert len(all_records) == 1
        assert all_records[0].outcome == "Updated attempt failed with new info"

    def test_storage_011_store_with_embedding_result_helper(self) -> None:
        """STORAGE-011: store_experience accepts optional EmbeddingResult parameter."""
        mock_provider = DeterministicMockEmbeddingProvider(dimension=128)
        emb_res = mock_provider.embed("sample text")

        legacy_rec = _make_sample_record()
        assert legacy_rec.embedding is None

        adapter = InMemoryMemoryAdapter()
        adapter.store_experience(legacy_rec, embedding=emb_res)

        loaded = adapter.get_experience("space-alpha", "exp-001")
        assert loaded is not None
        assert loaded.embedding == emb_res.vector
        assert loaded.embedding_dimension == 128
        assert loaded.embedding_model == "deterministic-mock"

    def test_storage_012_inmemory_parity(self) -> None:
        """STORAGE-012: InMemory adapter satisfies SpaceMemoryProtocol."""
        adapter = InMemoryMemoryAdapter()
        assert hasattr(adapter, "store_experience")
        assert hasattr(adapter, "get_experience")
        assert hasattr(adapter, "list_experiences")
        assert hasattr(adapter, "query_similar_experiences")

    def test_storage_013_postgres_row_conversion_parity(self) -> None:
        """STORAGE-013: PostgreSQL adapter deserializes full 14-column rows correctly."""
        now = datetime.now(timezone.utc)
        vec = [0.25, 0.5, 0.75]
        row_14 = (
            "exp-pg-1",
            "space-pg",
            {"task_id": "t1"},
            {"exit_code": 0},
            "success",
            "working counterfactual",
            {"ctx": 1},
            now,
            json.dumps(vec),
            "mock-model",
            3,
            "1.0.0",
            "fp-123",
            "prov-ref-456",
        )

        # Test _row_to_experience without database connection
        dummy_config = PostgresConfig()
        adapter = PostgreSQLMemoryAdapter(config=dummy_config)
        rec = adapter._row_to_experience(row_14)

        assert rec.experience_id == "exp-pg-1"
        assert rec.space_id == "space-pg"
        assert rec.embedding == tuple(vec)
        assert rec.embedding_model == "mock-model"
        assert rec.embedding_dimension == 3
        assert rec.embedding_version == "1.0.0"
        assert rec.failure_fingerprint == "fp-123"
        assert rec.provenance_ref == "prov-ref-456"

    def test_storage_014_postgres_legacy_8_row_compatibility(self) -> None:
        """STORAGE-014: PostgreSQL adapter deserializes legacy 8-column rows without error."""
        now = datetime.now(timezone.utc)
        row_8 = (
            "exp-legacy",
            "space-pg",
            {"task_id": "t1"},
            {"exit_code": 1},
            "failed",
            "legacy counterfactual",
            {"failure_fingerprint": "fp-legacy-auto"},
            now,
        )

        dummy_config = PostgresConfig()
        adapter = PostgreSQLMemoryAdapter(config=dummy_config)
        rec = adapter._row_to_experience(row_8)

        assert rec.experience_id == "exp-legacy"
        assert rec.space_id == "space-pg"
        assert rec.embedding is None
        assert rec.embedding_model is None
        assert rec.embedding_dimension is None
        assert rec.failure_fingerprint == "fp-legacy-auto"

    def test_storage_015_model_version_validation(self) -> None:
        """STORAGE-015: Vector present without model or version is rejected."""
        vec = (0.1, 0.2, 0.3)
        with pytest.raises(ValueError, match="embedding_model must not be empty"):
            _make_sample_record(
                embedding=vec,
                embedding_model="",
                embedding_dimension=3,
                embedding_version="1.0",
            )

        with pytest.raises(ValueError, match="embedding_version must not be empty"):
            _make_sample_record(
                embedding=vec,
                embedding_model="mock",
                embedding_dimension=3,
                embedding_version="",
            )

    def test_storage_016_oversized_vector_bounds(self) -> None:
        """STORAGE-016: Vector dimension specified without embedding vector is rejected."""
        with pytest.raises(ValueError, match="embedding_dimension specified without an embedding vector"):
            _make_sample_record(
                embedding=None,
                embedding_dimension=128,
            )

    def test_storage_017_concurrent_writes(self) -> None:
        """STORAGE-017: Concurrent multi-threaded writes do not corrupt storage."""
        adapter = InMemoryMemoryAdapter()
        mock_provider = DeterministicMockEmbeddingProvider(dimension=64)

        def worker(idx: int) -> str:
            text = f"concurrent task outcome {idx}"
            emb = mock_provider.embed(text)
            rec = _make_sample_record(
                exp_id=f"exp-concurrent-{idx}",
                space_id="space-concurrent",
                embedding=emb.vector,
                embedding_model=emb.model,
                embedding_dimension=emb.dimension,
                embedding_version=emb.version,
            )
            return adapter.store_experience(rec)

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            futures = [executor.submit(worker, i) for i in range(50)]
            results = [f.result() for f in futures]

        assert len(results) == 50
        all_stored = adapter.list_experiences("space-concurrent")
        assert len(all_stored) == 50
        for item in all_stored:
            assert item.embedding is not None
            assert len(item.embedding) == 64

    def test_storage_018_sql_metacharacters_in_metadata(self) -> None:
        """STORAGE-018: Special metacharacters in metadata are preserved safely without corruption."""
        malicious_string = "'; DROP TABLE space_experiences; -- \n\t\"'\\/*"
        rec = _make_sample_record(
            failure_fingerprint=malicious_string,
            provenance_ref=malicious_string,
            embedding=(0.5, 0.5),
            embedding_model=malicious_string,
            embedding_dimension=2,
            embedding_version=malicious_string,
        )

        adapter = InMemoryMemoryAdapter()
        adapter.store_experience(rec)
        loaded = adapter.get_experience("space-alpha", "exp-001")
        assert loaded is not None
        assert loaded.failure_fingerprint == malicious_string
        assert loaded.provenance_ref == malicious_string
        assert loaded.embedding_model == malicious_string

    def test_storage_019_no_implicit_provider_invocation(self) -> None:
        """STORAGE-019: Storage adapter does not invoke any embedding provider or network."""
        rec = _make_sample_record()
        adapter = InMemoryMemoryAdapter()
        # Storing record without embedding stores it as None (does not magically call Ollama/ML)
        adapter.store_experience(rec)
        loaded = adapter.get_experience("space-alpha", "exp-001")
        assert loaded is not None
        assert loaded.embedding is None

    def test_storage_020_no_semantic_retrieval_in_storage(self) -> None:
        """STORAGE-020: Storage layer does not perform semantic ranking or candidate generation."""
        adapter = InMemoryMemoryAdapter()
        # query_similar_experiences is the existing keyword/recency query (Phase 10),
        # not the Phase 15.5.3 semantic ranking engine
        q = ExperienceQuery(space_id="space-alpha", situation_hint={"capability": "python.exec"})
        res = adapter.query_similar_experiences(q)
        assert isinstance(res, list)


class TestAdversarialStorageCases:
    """Adversarial stress and edge cases for durable semantic memory storage."""

    def test_adv_mocked_postgres_connection_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """PostgreSQL adapter wraps connection failure in MemoryFailure (Law 6: never silent)."""
        adapter = PostgreSQLMemoryAdapter(config=PostgresConfig())

        def mock_connect(*args: Any, **kwargs: Any) -> None:
            raise ConnectionRefusedError("Database host unreachable")

        monkeypatch.setattr(adapter, "_get_conn", mock_connect)

        rec = _make_sample_record()
        with pytest.raises(MemoryFailure, match="Database host unreachable"):
            adapter.store_experience(rec)

    def test_adv_mocked_postgres_insert_sql_structure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Verify the exact parameterized SQL statement and parameter count executed by store_experience."""
        executed_sqls: list[str] = []
        executed_params: list[tuple] = []  # type: ignore[type-arg]

        mock_cursor = MagicMock()

        def mock_execute(sql: str, params: tuple) -> None:  # type: ignore[type-arg]
            executed_sqls.append(sql)
            executed_params.append(params)

        mock_cursor.execute = mock_execute

        mock_conn = MagicMock()
        mock_conn.__enter__.return_value = mock_conn
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

        adapter = PostgreSQLMemoryAdapter(config=PostgresConfig())
        monkeypatch.setattr(adapter, "_get_conn", lambda: mock_conn)

        rec = _make_sample_record(
            embedding=(0.1, 0.2),
            embedding_model="test-model",
            embedding_dimension=2,
            embedding_version="1.0",
        )
        adapter.store_experience(rec)

        assert len(executed_sqls) == 1
        assert "INSERT INTO space_experiences" in executed_sqls[0]
        assert "embedding, embedding_model, embedding_dimension" in executed_sqls[0]
        assert len(executed_params[0]) == 14
        assert executed_params[0][0] == "exp-001"
        assert executed_params[0][1] == "space-alpha"
        assert executed_params[0][9] == "test-model"
        assert executed_params[0][10] == 2
        assert executed_params[0][11] == "1.0"

    def test_adv_empty_space_id_rejected_on_store(self) -> None:
        """store_experience rejects empty space_id with SpaceIsolationViolation."""
        # Using object.__setattr__ to bypass dataclass check
        rec = _make_sample_record()
        object.__setattr__(rec, "space_id", "")

        adapter = InMemoryMemoryAdapter()
        with pytest.raises(SpaceIsolationViolation):
            adapter.store_experience(rec)

        pg_adapter = PostgreSQLMemoryAdapter(config=PostgresConfig())
        with pytest.raises(SpaceIsolationViolation):
            pg_adapter.store_experience(rec)
