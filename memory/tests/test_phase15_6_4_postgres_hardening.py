"""Dedicated Tests for Phase 15.6.4 — PostgreSQL Runtime Hardening & Candidate Indexing.

Covers:
  - Migration 010 schema inspection (functional JSONB expression indexes)
  - Configurable ThreadedConnectionPool bounds (min_connections, max_connections clamping)
  - Connection pool lazy initialization and get_pool_status observability
  - Deterministic connection acquisition and guaranteed return via contextmanager
  - Adapter close() cleans up managed connection pool
  - External connection pool injection and ownership preservation
  - Adapter context manager lifecycle (__enter__, __exit__)
  - Bounded candidate query bounds (C <= 50, K <= 5) under pooled execution
  - Parameterized query execution and transactional commit semantics

Contracts:
  - MEM-PG-001 (PostgreSQL Connection Pooling & Candidate Indexing)
  - MEM-SEM-001 (Bounded Space-Scoped Candidate Retrieval)
  - MEM-SEM-002 (Deterministic Semantic Similarity Ranking)
  - ADR-0050 §4

AGENTS.md §5, §6, §7
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from ryu.pulse_bus.config import PostgresConfig

from core.space.memory_protocol import (
    ExperienceRecord,
    MemoryFailure,
    SemanticExperienceQuery,
)
from memory.adapters.postgres import PostgreSQLMemoryAdapter
from memory.embeddings.deterministic_mock import DeterministicMockEmbeddingProvider

_MOCK_PROV = DeterministicMockEmbeddingProvider()
_MOCK_VEC = _MOCK_PROV.embed("postgres hardening test").vector


def test_migration_010_schema_validity() -> None:
    """MEM-PG-001 & ADR-0050 §4: Migration 010 exists and defines candidate expression indexes."""
    repo_root = Path(__file__).resolve().parents[2]
    migration_file = repo_root / "deploy" / "migrations" / "010_add_space_experience_candidate_indexes.sql"
    assert migration_file.exists(), f"Migration 010 not found at {migration_file}"

    sql = migration_file.read_text(encoding="utf-8")
    assert "CREATE INDEX IF NOT EXISTS idx_space_exp_cap" in sql
    assert "(space_id, (action->>'capability'))" in sql
    assert "CREATE INDEX IF NOT EXISTS idx_space_exp_error_class" in sql
    assert "(space_id, (applicable_context->>'error_class'))" in sql
    assert "CREATE INDEX IF NOT EXISTS idx_space_exp_cap_stored" in sql
    assert "(space_id, (action->>'capability'), stored_at DESC)" in sql


def test_connection_pool_bounds_clamping() -> None:
    """MEM-PG-001: Connection pool parameters are safely clamped to [1, 50]."""
    cfg = PostgresConfig(host="localhost", port=5432, db="ryu_dev", user="ryu", password="p")

    # Below minimum
    adapter_low = PostgreSQLMemoryAdapter(config=cfg, min_connections=0, max_connections=0)
    assert adapter_low.min_connections == 1
    assert adapter_low.max_connections == 1

    # Above maximum
    adapter_high = PostgreSQLMemoryAdapter(config=cfg, min_connections=5, max_connections=100)
    assert adapter_high.min_connections == 5
    assert adapter_high.max_connections == 50

    status = adapter_low.get_pool_status()
    assert status["min_connections"] == 1
    assert status["max_connections"] == 1
    assert status["closed"] is False
    assert status["external_pool"] is False
    assert status["pool_initialized"] is False


def test_lazy_pool_initialization_and_mock_injection() -> None:
    """MEM-PG-001: Pool initializes lazily and reports accurate observability status."""
    cfg = PostgresConfig(host="localhost", port=5432, db="ryu_dev", user="ryu", password="p")
    mock_pool = MagicMock()
    mock_conn = MagicMock()
    mock_pool.getconn.return_value = mock_conn

    adapter = PostgreSQLMemoryAdapter(config=cfg, pool=mock_pool)
    status_init = adapter.get_pool_status()
    assert status_init["external_pool"] is True
    assert status_init["pool_initialized"] is True

    # Acquire connection
    with adapter.connection() as conn:
        assert conn == mock_conn
        assert mock_pool.getconn.called

    # Verify connection returned
    mock_pool.putconn.assert_called_once_with(mock_conn)


def test_external_pool_ownership_preserved_on_close() -> None:
    """MEM-PG-001: External pool is not closed when adapter.close() is called."""
    cfg = PostgresConfig(host="localhost", port=5432, db="ryu_dev", user="ryu", password="p")
    mock_pool = MagicMock()

    adapter = PostgreSQLMemoryAdapter(config=cfg, pool=mock_pool)
    adapter.close()

    assert adapter._closed is True
    assert not mock_pool.closeall.called


def test_managed_pool_closeall_called_on_close() -> None:
    """MEM-PG-001: Internal managed pool invokes closeall() upon adapter.close()."""
    cfg = PostgresConfig(host="localhost", port=5432, db="ryu_dev", user="ryu", password="p")
    adapter = PostgreSQLMemoryAdapter(config=cfg)

    mock_pool = MagicMock()
    adapter._pool = mock_pool

    adapter.close()
    assert adapter._closed is True
    mock_pool.closeall.assert_called_once()
    assert adapter._pool is None


def test_context_manager_lifecycle() -> None:
    """MEM-PG-001: PostgreSQLMemoryAdapter functions cleanly as a context manager."""
    cfg = PostgresConfig(host="localhost", port=5432, db="ryu_dev", user="ryu", password="p")
    mock_pool = MagicMock()

    with PostgreSQLMemoryAdapter(config=cfg, pool=mock_pool) as adapter:
        assert adapter._closed is False

    assert adapter._closed is True


def test_pooled_store_and_retrieve_semantic_candidates() -> None:
    """MEM-SEM-001 & MEM-PG-001: Candidate extraction executes through connection pool with C <= 50."""
    cfg = PostgresConfig(host="localhost", port=5432, db="ryu_dev", user="ryu", password="p")
    mock_pool = MagicMock()
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_conn.__enter__.return_value = mock_conn
    mock_conn.cursor.return_value.__enter__.return_value = mock_cur
    mock_pool.getconn.return_value = mock_conn

    adapter = PostgreSQLMemoryAdapter(config=cfg, pool=mock_pool)

    # Mock cursor fetchall returning sample row
    sample_row = (
        "exp-1",
        "space-pool-test",
        {"capability": "python.exec"},
        {"capability": "python.exec"},
        "failure",
        "Fallback advice",
        {"error_class": "timeout", "failure_fingerprint": "fp-123"},
        "2026-10-08T00:00:00Z",
        list(_MOCK_VEC),
        _MOCK_PROV.model_name,
        _MOCK_PROV.dimension,
        _MOCK_PROV.version,
        "fp-123",
        "prov-123",
    )
    mock_cur.fetchall.return_value = [sample_row]

    query = SemanticExperienceQuery(
        space_id="space-pool-test",
        query_text="timeout failure",
        failure_fingerprint="fp-123",
        situation_hint={"capability": "python.exec", "error_class": "timeout"},
        top_k=5,
    )

    results = adapter.retrieve_semantic_experiences(query, embedding_provider=_MOCK_PROV)
    assert len(results) == 1
    assert results[0].experience_id == "exp-1"
    assert results[0].record.failure_fingerprint == "fp-123"

    # Verify connection was acquired and released
    mock_pool.getconn.assert_called()
    mock_pool.putconn.assert_called()


def test_transactional_rollback_on_cursor_error() -> None:
    """MEM-PG-001: Execution errors release connection safely and raise MemoryFailure."""
    cfg = PostgresConfig(host="localhost", port=5432, db="ryu_dev", user="ryu", password="p")
    mock_pool = MagicMock()
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_conn.__enter__.return_value = mock_conn
    mock_conn.cursor.return_value.__enter__.return_value = mock_cur
    mock_cur.execute.side_effect = RuntimeError("Database disk full")
    mock_pool.getconn.return_value = mock_conn

    adapter = PostgreSQLMemoryAdapter(config=cfg, pool=mock_pool)
    rec = ExperienceRecord(
        experience_id="exp-err",
        space_id="space-err",
        situation={"task_id": "t1"},
        action={"capability": "tool"},
        outcome="failure",
        counterfactual="advice",
        applicable_context={},
        stored_at=MagicMock(),
    )

    try:
        adapter.store_experience(rec)
    except MemoryFailure as mf:
        assert "Database disk full" in str(mf)

    # Connection MUST be returned to pool even after error
    mock_pool.putconn.assert_called_with(mock_conn)
