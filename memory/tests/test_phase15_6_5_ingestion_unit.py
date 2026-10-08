"""Unit tests for Phase 15.6.5 — Durable Experience Embedding Ingestion & Outbox (MEM-INGEST-001).

Tests:
- Migration 011 SQL schema definition
- ExperienceRecord lifecycle status transitions
- Deterministic text formatting for embeddings
- InMemoryMemoryAdapter outbox operations
- Pipeline processing and outbox draining
- Reflector auto_embed integration closing the semantic loop
- PostgreSQLMemoryAdapter outbox SQL execution
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from ryu.pulse_bus.config import PostgresConfig

from core.orchestrator.adapter import Adapter
from core.space.memory_protocol import (
    ExperienceRecord,
)
from memory.adapters.in_memory import InMemoryMemoryAdapter
from memory.adapters.postgres import PostgreSQLMemoryAdapter
from memory.embeddings.deterministic_mock import DeterministicMockEmbeddingProvider
from memory.ingestion.pipeline import (
    EmbeddingIngestionPipeline,
    format_experience_for_embedding,
)
from memory.reflector import Reflector

_MOCK_PROV = DeterministicMockEmbeddingProvider()


def test_migration_011_schema_syntax() -> None:
    """MEM-INGEST-001: Migration 011 exists and specifies required outbox columns and partial index."""
    migration_path = Path("deploy/migrations/011_add_experience_embedding_outbox.sql")
    assert migration_path.exists(), "Migration 011 file must exist"

    content = migration_path.read_text(encoding="utf-8")
    assert "ALTER TABLE space_experiences" in content
    assert "embedding_status" in content
    assert "embedding_attempts" in content
    assert "embedding_error" in content
    assert "embedding_updated_at" in content
    assert "CREATE INDEX IF NOT EXISTS idx_space_exp_embedding_outbox" in content
    assert "WHERE embedding_status IN ('pending', 'processing')" in content


def test_experience_record_embedding_status_lifecycle() -> None:
    """MEM-INGEST-001: ExperienceRecord validates embedding_status and embedding_attempts."""
    now = datetime.now(timezone.utc)

    # Default status is 'completed'
    rec1 = ExperienceRecord(
        experience_id="exp-1",
        space_id="space-1",
        situation={"task": "t1"},
        action={"capability": "python.exec"},
        outcome="failure",
        counterfactual="use retry",
        applicable_context={},
        stored_at=now,
    )
    assert rec1.embedding_status == "completed"
    assert rec1.embedding_attempts == 0
    assert rec1.embedding_error is None

    # with_embedding_status transitions
    rec_pending = rec1.with_embedding_status("pending", attempts=1, error="temporary timeout")
    assert rec_pending.embedding_status == "pending"
    assert rec_pending.embedding_attempts == 1
    assert rec_pending.embedding_error == "temporary timeout"

    # with_embedding transitions back to completed
    res = _MOCK_PROV.embed("sample text")
    rec_done = rec_pending.with_embedding(res)
    assert rec_done.embedding_status == "completed"
    assert rec_done.embedding_error is None
    assert rec_done.embedding == res.vector

    # Invalid status raises ValueError
    with pytest.raises(ValueError, match="embedding_status must be one of"):
        rec1.with_embedding_status("invalid_status")

    # Negative attempts raises ValueError
    with pytest.raises(ValueError, match="embedding_attempts must be non-negative"):
        rec1.with_embedding_status("pending", attempts=-1)


def test_format_experience_for_embedding_determinism() -> None:
    """MEM-INGEST-001: format_experience_for_embedding produces deterministic normalized text."""
    now = datetime.now(timezone.utc)
    rec = ExperienceRecord(
        experience_id="exp-fmt",
        space_id="space-fmt",
        situation={"task": "t1", "capability": "python.exec"},
        action={"capability": "python.exec"},
        outcome="Task 't1' failed: socket timeout error",
        counterfactual="Verify network connectivity and retry",
        applicable_context={"error_class": "transient.timeout", "failure_fingerprint": "fp-socket"},
        stored_at=now,
    )

    text1 = format_experience_for_embedding(rec)
    text2 = format_experience_for_embedding(rec)

    assert text1 == text2
    assert "outcome: Task 't1' failed: socket timeout error" in text1
    assert "counterfactual: Verify network connectivity and retry" in text1
    assert "capability: python.exec" in text1
    assert "error_class: transient.timeout" in text1
    assert "fingerprint: fp-socket" in text1


def test_in_memory_adapter_outbox_operations() -> None:
    """MEM-INGEST-001: InMemoryMemoryAdapter implements get_pending_embeddings and updates atomically."""
    adapter = InMemoryMemoryAdapter()
    space_id = "space-outbox-test"
    now = datetime.now(timezone.utc)

    # Store record without embedding -> auto marks as pending
    rec = ExperienceRecord(
        experience_id="exp-pending-1",
        space_id=space_id,
        situation={"task": "t1"},
        action={"capability": "tool.run"},
        outcome="failure",
        counterfactual="fallback",
        applicable_context={},
        stored_at=now,
    )
    adapter.store_experience(rec)

    pending = adapter.get_pending_embeddings(space_id)
    assert len(pending) == 1
    assert pending[0].experience_id == "exp-pending-1"
    assert pending[0].embedding_status == "pending"

    # Update embedding
    res = _MOCK_PROV.embed("fallback")
    adapter.update_experience_embedding(space_id, "exp-pending-1", res)

    # Should no longer be pending
    pending_after = adapter.get_pending_embeddings(space_id)
    assert len(pending_after) == 0

    stored = adapter.get_experience(space_id, "exp-pending-1")
    assert stored is not None
    assert stored.embedding_status == "completed"
    assert stored.embedding is not None


def test_pipeline_single_record_ingestion_success() -> None:
    """MEM-INGEST-001: EmbeddingIngestionPipeline processes pending record and enriches memory."""
    adapter = InMemoryMemoryAdapter()
    pipeline = EmbeddingIngestionPipeline(
        memory_store=adapter,
        embedding_provider=_MOCK_PROV,
    )
    space_id = "space-pipe-test"
    now = datetime.now(timezone.utc)

    rec = ExperienceRecord(
        experience_id="exp-p1",
        space_id=space_id,
        situation={"task": "t1"},
        action={"capability": "calc"},
        outcome="failed calculation",
        counterfactual="verify inputs",
        applicable_context={},
        stored_at=now,
        embedding_status="pending",
    )
    adapter.store_experience(rec)

    res = pipeline.process_space_outbox(space_id)
    assert res.total_claimed == 1
    assert res.succeeded == 1
    assert res.failed == 0
    assert "exp-p1" in res.processed_ids

    enriched = adapter.get_experience(space_id, "exp-p1")
    assert enriched is not None
    assert enriched.embedding is not None
    assert enriched.embedding_status == "completed"
    assert enriched.embedding_model == _MOCK_PROV.model_name
    assert enriched.embedding_dimension == _MOCK_PROV.dimension


def test_pipeline_batch_processing_and_drain() -> None:
    """MEM-INGEST-001: drain_space_outbox drains multiple pending experiences across batches."""
    adapter = InMemoryMemoryAdapter()
    pipeline = EmbeddingIngestionPipeline(
        memory_store=adapter,
        embedding_provider=_MOCK_PROV,
        max_batch_size=2,
    )
    space_id = "space-drain-test"
    now = datetime.now(timezone.utc)

    for i in range(5):
        rec = ExperienceRecord(
            experience_id=f"exp-drain-{i}",
            space_id=space_id,
            situation={"task": f"t{i}"},
            action={"capability": "tool.test"},
            outcome=f"failure {i}",
            counterfactual=f"avoid {i}",
            applicable_context={},
            stored_at=now,
            embedding_status="pending",
        )
        adapter.store_experience(rec)

    drain_res = pipeline.drain_space_outbox(space_id, max_iterations=5)
    assert drain_res.total_claimed == 5
    assert drain_res.succeeded == 5
    assert drain_res.failed == 0
    assert len(adapter.get_pending_embeddings(space_id)) == 0


def test_reflector_auto_embed_closes_gap() -> None:
    """MEM-INGEST-001 & F05-AUDIT-01: Reflector with auto_embed=True immediately attaches embedding."""
    adapter_mock = MagicMock(spec=Adapter)
    adapter_mock.space_id = "space-closed-loop"

    store = InMemoryMemoryAdapter()
    pipeline = EmbeddingIngestionPipeline(
        memory_store=store,
        embedding_provider=_MOCK_PROV,
    )
    reflector = Reflector(
        adapter=adapter_mock,
        memory_store=store,
        ingestion_pipeline=pipeline,
        auto_embed=True,
    )

    record = reflector.reflect(
        situation={"task": "task-loop"},
        action={"capability": "code.run"},
        outcome="Task 'task-loop' failed with MemoryError",
        counterfactual="Increase RAM allocation",
        applicable_context={"error_class": "resource.oom"},
    )

    # Verified: Record returned has valid embedding attached immediately!
    assert record.embedding is not None
    assert record.embedding_status == "completed"
    assert record.embedding_model == _MOCK_PROV.model_name
    assert record.embedding_dimension == _MOCK_PROV.dimension


def test_postgres_adapter_outbox_methods_mocked() -> None:
    """MEM-INGEST-001: PostgreSQLMemoryAdapter executes outbox SQL statements using connection pool."""
    cfg = PostgresConfig(host="localhost", port=5432, db="ryu_dev", user="ryu", password="p")
    mock_pool = MagicMock()
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_conn.__enter__.return_value = mock_conn
    mock_conn.cursor.return_value.__enter__.return_value = mock_cur
    mock_pool.getconn.return_value = mock_conn

    adapter = PostgreSQLMemoryAdapter(config=cfg, pool=mock_pool)

    # 1. get_pending_embeddings
    sample_row = (
        "exp-pg-1",
        "space-pg-test",
        {"task": "t1"},
        {"capability": "net.get"},
        "failure",
        "retry request",
        {"error_class": "timeout"},
        datetime.now(timezone.utc),
        None,
        None,
        None,
        None,
        None,
        None,
        "pending",
        0,
        None,
    )
    mock_cur.fetchall.return_value = [sample_row]

    pending = adapter.get_pending_embeddings("space-pg-test", limit=5)
    assert len(pending) == 1
    assert pending[0].experience_id == "exp-pg-1"
    assert pending[0].embedding_status == "pending"

    # 2. update_experience_embedding
    emb = _MOCK_PROV.embed("sample text")
    adapter.update_experience_embedding("space-pg-test", "exp-pg-1", emb)
    assert mock_cur.execute.called

    # 3. mark_embedding_failed
    adapter.mark_embedding_failed("space-pg-test", "exp-pg-1", "Connection timed out", attempts=1, terminal=False)
    assert mock_cur.execute.called
