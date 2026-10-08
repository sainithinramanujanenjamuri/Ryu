"""Adversarial and Edge Case Tests for Phase 15.6.4 — PostgreSQL Runtime Hardening.

Covers:
  - Operations on closed adapter raise MemoryFailure
  - Pool exhaustion handled gracefully (raises MemoryFailure without hanging)
  - Connection leak prevention under unhandled exceptions (guaranteed putconn)
  - Concurrent multi-threaded pool access without race conditions
  - Idempotent close() calls across threads
  - Space isolation pre-check prevents connection acquisition waste
  - External pool putconn exception containment

Contracts:
  - MEM-PG-001 (PostgreSQL Connection Pooling & Candidate Indexing)
  - SCCA Law 1 (Space Isolation Boundary)
  - SCCA Law 6 (Failures Contained and Never Silent)
  - ADR-0050 §4

AGENTS.md §5, §6, §7
"""

from __future__ import annotations

import threading
from unittest.mock import MagicMock

import pytest
from ryu.pulse_bus.config import PostgresConfig

from core.space.memory_protocol import (
    MemoryFailure,
    SemanticExperienceQuery,
    SpaceIsolationViolation,
)
from memory.adapters.postgres import PostgreSQLMemoryAdapter
from memory.embeddings.deterministic_mock import DeterministicMockEmbeddingProvider

_MOCK_PROV = DeterministicMockEmbeddingProvider()


def test_adversarial_operations_on_closed_adapter() -> None:
    """MEM-PG-001: Attempted operations on closed adapter raise MemoryFailure immediately."""
    cfg = PostgresConfig(host="localhost", port=5432, db="ryu_dev", user="ryu", password="p")
    mock_pool = MagicMock()
    adapter = PostgreSQLMemoryAdapter(config=cfg, pool=mock_pool)
    adapter.close()

    assert adapter._closed is True

    # connection acquisition
    with pytest.raises(MemoryFailure, match="closed"):
        with adapter.connection():
            pass

    # get_experience
    with pytest.raises(MemoryFailure, match="closed"):
        adapter.get_experience("space-1", "exp-1")

    # count_experiences
    with pytest.raises(MemoryFailure, match="closed"):
        adapter.count_experiences("space-1")

    # prune_experiences
    with pytest.raises(MemoryFailure, match="closed"):
        adapter.prune_experiences("space-1")


def test_adversarial_pool_exhaustion_raises_memory_failure() -> None:
    """MEM-PG-001: Pool exhaustion raises MemoryFailure without hanging or crashing."""
    cfg = PostgresConfig(host="localhost", port=5432, db="ryu_dev", user="ryu", password="p")
    mock_pool = MagicMock()
    mock_pool.getconn.side_effect = RuntimeError("connection pool exhausted: maxconn reached")

    adapter = PostgreSQLMemoryAdapter(config=cfg, pool=mock_pool)

    with pytest.raises(MemoryFailure, match="connection pool exhausted"):
        with adapter.connection():
            pass


def test_adversarial_connection_leak_prevention_on_unhandled_exception() -> None:
    """MEM-PG-001: Connections are guaranteed to return to pool even if unhandled exceptions occur."""
    cfg = PostgresConfig(host="localhost", port=5432, db="ryu_dev", user="ryu", password="p")
    mock_pool = MagicMock()
    mock_conn = MagicMock()
    mock_pool.getconn.return_value = mock_conn

    adapter = PostgreSQLMemoryAdapter(config=cfg, pool=mock_pool)

    with pytest.raises(ZeroDivisionError):
        with adapter.connection() as conn:
            assert conn == mock_conn
            _ = 1 / 0

    # putconn must be called despite the unhandled ZeroDivisionError
    mock_pool.putconn.assert_called_once_with(mock_conn)


def test_adversarial_concurrent_pool_access() -> None:
    """MEM-PG-001: 10 concurrent threads acquiring connections from pool maintain lock safety."""
    cfg = PostgresConfig(host="localhost", port=5432, db="ryu_dev", user="ryu", password="p")
    mock_pool = MagicMock()
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_conn.__enter__.return_value = mock_conn
    mock_conn.cursor.return_value.__enter__.return_value = mock_cur
    mock_cur.fetchone.return_value = (5,)
    mock_pool.getconn.return_value = mock_conn

    adapter = PostgreSQLMemoryAdapter(config=cfg, pool=mock_pool, max_connections=10)

    errors: list[Exception] = []

    def caller() -> None:
        try:
            for _ in range(5):
                cnt = adapter.count_experiences("space-concurrent")
                assert cnt == 5
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=caller) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(errors) == 0
    assert mock_pool.getconn.call_count == 50
    assert mock_pool.putconn.call_count == 50


def test_adversarial_idempotent_close() -> None:
    """MEM-PG-001: Multiple close() calls across threads execute safely and idempotently."""
    cfg = PostgresConfig(host="localhost", port=5432, db="ryu_dev", user="ryu", password="p")
    mock_pool = MagicMock()
    adapter = PostgreSQLMemoryAdapter(config=cfg, pool=mock_pool)

    threads = [threading.Thread(target=adapter.close) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert adapter._closed is True


def test_adversarial_space_isolation_precheck_prevents_connection_waste() -> None:
    """SCCA Law 1 & MEM-PG-001: Empty space_id is rejected before acquiring connection from pool."""
    cfg = PostgresConfig(host="localhost", port=5432, db="ryu_dev", user="ryu", password="p")
    mock_pool = MagicMock()
    adapter = PostgreSQLMemoryAdapter(config=cfg, pool=mock_pool)

    # get_experience
    with pytest.raises(SpaceIsolationViolation):
        adapter.get_experience("", "exp-1")

    # count_experiences
    with pytest.raises(SpaceIsolationViolation):
        adapter.count_experiences("")

    # prune_experiences
    with pytest.raises(SpaceIsolationViolation):
        adapter.prune_experiences("")

    # retrieve_semantic_experiences
    with pytest.raises(SpaceIsolationViolation):
        query = SemanticExperienceQuery(space_id="   ", query_text="query")
        adapter.retrieve_semantic_experiences(query, embedding_provider=_MOCK_PROV)

    # Zero connections acquired from pool for invalid requests
    assert not mock_pool.getconn.called


def test_adversarial_putconn_internal_error_containment() -> None:
    """MEM-PG-001: Internal putconn error does not mask application exceptions."""
    cfg = PostgresConfig(host="localhost", port=5432, db="ryu_dev", user="ryu", password="p")
    mock_pool = MagicMock()
    mock_conn = MagicMock()
    mock_pool.getconn.return_value = mock_conn
    mock_pool.putconn.side_effect = RuntimeError("Broken pool state")

    adapter = PostgreSQLMemoryAdapter(config=cfg, pool=mock_pool)

    # Context completes without re-raising putconn error
    with adapter.connection() as conn:
        assert conn == mock_conn
