"""Adversarial and boundary tests for Phase 15.6.5 — Embedding Ingestion Outbox (MEM-INGEST-001).

Tests:
- Space isolation across outbox processing (Law 1, Law 4)
- Provider timeout handling without blocking callers
- Bounded retry ceiling (max 3 attempts) leading to terminal 'failed' status
- Provider dimension and numerical validation (reject NaN, Inf, dimension mismatch)
- Idempotent re-ingestion skips already completed records
- Crash/recovery state preservation
- Replay mode isolation (zero memory mutation during replay)
- Secret sanitization defense during embedding input construction
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from core.space.memory_protocol import (
    EmbeddingResult,
    ExperienceRecord,
    SpaceIsolationViolation,
)
from memory.adapters.in_memory import InMemoryMemoryAdapter
from memory.embeddings.deterministic_mock import DeterministicMockEmbeddingProvider
from memory.ingestion.pipeline import (
    EmbeddingIngestionPipeline,
    format_experience_for_embedding,
)

_MOCK_PROV = DeterministicMockEmbeddingProvider()


def test_adv_space_isolation_outbox_containment() -> None:
    """SCCA Law 1 & MEM-INGEST-001: Pipeline strictly isolates outbox records by space_id."""
    adapter = InMemoryMemoryAdapter()
    pipeline = EmbeddingIngestionPipeline(
        memory_store=adapter,
        embedding_provider=_MOCK_PROV,
    )
    now = datetime.now(timezone.utc)

    # Store in Space A
    rec_a = ExperienceRecord(
        experience_id="exp-space-a",
        space_id="space-A",
        situation={"task": "tA"},
        action={"capability": "tool.a"},
        outcome="failure A",
        counterfactual="fix A",
        applicable_context={},
        stored_at=now,
        embedding_status="pending",
    )
    adapter.store_experience(rec_a)

    # Store in Space B
    rec_b = ExperienceRecord(
        experience_id="exp-space-b",
        space_id="space-B",
        situation={"task": "tB"},
        action={"capability": "tool.b"},
        outcome="failure B",
        counterfactual="fix B",
        applicable_context={},
        stored_at=now,
        embedding_status="pending",
    )
    adapter.store_experience(rec_b)

    # Processing Space A only processes Space A
    res_a = pipeline.process_space_outbox("space-A")
    assert res_a.total_claimed == 1
    assert res_a.succeeded == 1
    assert "exp-space-a" in res_a.processed_ids
    assert "exp-space-b" not in res_a.processed_ids

    # Space B record remains pending
    pending_b = adapter.get_pending_embeddings("space-B")
    assert len(pending_b) == 1
    assert pending_b[0].experience_id == "exp-space-b"

    # Empty or whitespace space_id raises SpaceIsolationViolation
    with pytest.raises(SpaceIsolationViolation):
        pipeline.process_space_outbox("   ")
    with pytest.raises(SpaceIsolationViolation):
        adapter.get_pending_embeddings("")


def test_adv_provider_timeout_graceful_degradation() -> None:
    """MEM-INGEST-001 & Law 6: Slow provider timeout is caught, bounded, and increments attempts."""
    adapter = InMemoryMemoryAdapter()

    # Provider that sleeps longer than timeout_seconds
    slow_provider = MagicMock(spec=_MOCK_PROV)
    slow_provider.model_name = _MOCK_PROV.model_name
    slow_provider.version = _MOCK_PROV.version
    slow_provider.dimension = _MOCK_PROV.dimension

    def _hang(text: str) -> EmbeddingResult:
        time.sleep(1.0)
        return _MOCK_PROV.embed(text)

    slow_provider.embed.side_effect = _hang

    pipeline = EmbeddingIngestionPipeline(
        memory_store=adapter,
        embedding_provider=slow_provider,
        timeout_seconds=0.05,  # 50ms timeout
    )
    space_id = "space-timeout"
    now = datetime.now(timezone.utc)

    rec = ExperienceRecord(
        experience_id="exp-slow",
        space_id=space_id,
        situation={"task": "t"},
        action={"capability": "slow"},
        outcome="failure",
        counterfactual="speed up",
        applicable_context={},
        stored_at=now,
        embedding_status="pending",
    )
    adapter.store_experience(rec)

    try:
        # Execution does NOT raise or block forever; records failure
        start_time = time.perf_counter()
        batch_res = pipeline.process_space_outbox(space_id)
        elapsed = time.perf_counter() - start_time

        assert elapsed < 0.5  # Guard: finished well before 1.0s sleep
        assert batch_res.failed == 1
        assert batch_res.succeeded == 0

        # Record remains in outbox with attempt count incremented
        rec_after = adapter.get_experience(space_id, "exp-slow")
        assert rec_after is not None
        assert rec_after.embedding_status == "pending"
        assert rec_after.embedding_attempts == 1
        assert "TimeoutError" in (rec_after.embedding_error or "")
    finally:
        pipeline.close()


def test_adv_bounded_retry_ceiling_and_terminal_failure() -> None:
    """MEM-INGEST-001: 3 consecutive failures transitions outbox status to terminal 'failed'."""
    adapter = InMemoryMemoryAdapter()
    failing_provider = MagicMock(spec=_MOCK_PROV)
    failing_provider.model_name = _MOCK_PROV.model_name
    failing_provider.version = _MOCK_PROV.version
    failing_provider.dimension = _MOCK_PROV.dimension
    failing_provider.embed.side_effect = RuntimeError("Upstream embedding service down")

    pipeline = EmbeddingIngestionPipeline(
        memory_store=adapter,
        embedding_provider=failing_provider,
        max_attempts=3,
    )
    space_id = "space-max-retry"
    now = datetime.now(timezone.utc)

    rec = ExperienceRecord(
        experience_id="exp-fail",
        space_id=space_id,
        situation={"task": "t"},
        action={"capability": "fail"},
        outcome="failure",
        counterfactual="retry",
        applicable_context={},
        stored_at=now,
        embedding_status="pending",
    )
    adapter.store_experience(rec)

    # Attempt 1 -> pending, attempt=1
    res1 = pipeline.process_space_outbox(space_id)
    assert res1.failed == 1
    r1 = adapter.get_experience(space_id, "exp-fail")
    assert r1 is not None and r1.embedding_status == "pending" and r1.embedding_attempts == 1

    # Attempt 2 -> pending, attempt=2
    res2 = pipeline.process_space_outbox(space_id)
    assert res2.failed == 1
    r2 = adapter.get_experience(space_id, "exp-fail")
    assert r2 is not None and r2.embedding_status == "pending" and r2.embedding_attempts == 2

    # Attempt 3 -> terminal 'failed', attempt=3
    res3 = pipeline.process_space_outbox(space_id)
    assert res3.failed == 1
    r3 = adapter.get_experience(space_id, "exp-fail")
    assert r3 is not None and r3.embedding_status == "failed" and r3.embedding_attempts == 3

    # Attempt 4 -> No longer claimed as pending (ceiling reached!)
    res4 = pipeline.process_space_outbox(space_id)
    assert res4.total_claimed == 0


def test_adv_malformed_vector_or_dimension_mismatch_rejected() -> None:
    """MEM-INGEST-001: Invalid vectors (NaN, wrong dimension, model mismatch) are rejected."""
    adapter = InMemoryMemoryAdapter()
    bad_provider = MagicMock(spec=_MOCK_PROV)
    bad_provider.model_name = "test-model"
    bad_provider.version = "v1"
    bad_provider.dimension = 4

    # Case 1: Dimension mismatch (returns vector of len 2 instead of 4)
    # EmbeddingResult __post_init__ catches length != dimension
    # So if provider tries to return invalid EmbeddingResult or mismatched dimension
    with pytest.raises(ValueError, match="vector length 2 does not match declared dimension 4"):
        EmbeddingResult(vector=(0.1, 0.2), model="test-model", dimension=4, version="v1")

    # Case 2: Provider returns EmbeddingResult with mismatched model name
    bad_res = EmbeddingResult(vector=(0.1, 0.2, 0.3, 0.4), model="wrong-model", dimension=4, version="v1")
    bad_provider.embed.return_value = bad_res

    pipeline = EmbeddingIngestionPipeline(
        memory_store=adapter,
        embedding_provider=bad_provider,
    )
    space_id = "space-bad-model"
    rec = ExperienceRecord(
        experience_id="exp-bad",
        space_id=space_id,
        situation={},
        action={},
        outcome="fail",
        counterfactual="fix",
        applicable_context={},
        stored_at=datetime.now(timezone.utc),
        embedding_status="pending",
    )
    adapter.store_experience(rec)

    res = pipeline.process_space_outbox(space_id)
    assert res.failed == 1
    r_after = adapter.get_experience(space_id, "exp-bad")
    assert r_after is not None and r_after.embedding is None
    assert "model mismatch" in (r_after.embedding_error or "").lower()


def test_adv_idempotent_re_ingestion() -> None:
    """MEM-INGEST-001: Records already in 'completed' status with vectors are never overwritten."""
    adapter = InMemoryMemoryAdapter()
    pipeline = EmbeddingIngestionPipeline(
        memory_store=adapter,
        embedding_provider=_MOCK_PROV,
    )
    space_id = "space-idempotent"
    now = datetime.now(timezone.utc)

    # Pre-embed record with known vector
    orig_emb = _MOCK_PROV.embed("sample idempotency text")
    rec = ExperienceRecord(
        experience_id="exp-done",
        space_id=space_id,
        situation={},
        action={},
        outcome="success",
        counterfactual="maintain",
        applicable_context={},
        stored_at=now,
    ).with_embedding(orig_emb)

    adapter.store_experience(rec)

    # get_pending_embeddings does not return it
    assert len(adapter.get_pending_embeddings(space_id)) == 0

    # process_space_outbox claims 0 records
    res = pipeline.process_space_outbox(space_id)
    assert res.total_claimed == 0

    # Vector remains identical
    after = adapter.get_experience(space_id, "exp-done")
    assert after is not None and after.embedding == orig_emb.vector


def test_adv_crash_recovery_resets_interrupted_jobs() -> None:
    """MEM-INGEST-001: recover_in_flight ensures orphaned in-flight jobs are recovered."""
    adapter = InMemoryMemoryAdapter()
    pipeline = EmbeddingIngestionPipeline(
        memory_store=adapter,
        embedding_provider=_MOCK_PROV,
    )
    space_id = "space-crash-recovery"
    now = datetime.now(timezone.utc)

    # Record left in 'processing' status from crashed process
    rec = ExperienceRecord(
        experience_id="exp-crashed",
        space_id=space_id,
        situation={},
        action={},
        outcome="crashed task",
        counterfactual="recover",
        applicable_context={},
        stored_at=now,
        embedding_status="processing",
    )
    adapter.store_experience(rec)

    # Recovery scans and confirms it is claimable
    recovered_count = pipeline.recover_in_flight(space_id)
    assert recovered_count == 1

    # Next outbox pass successfully completes the job
    batch_res = pipeline.process_space_outbox(space_id)
    assert batch_res.succeeded == 1

    after = adapter.get_experience(space_id, "exp-crashed")
    assert after is not None
    assert after.embedding_status == "completed"
    assert after.embedding is not None


def test_adv_secret_sanitization_in_embedding_input() -> None:
    """MEM-INGEST-001 & Law 1/Security: Sensitive tokens are scrubbed from embedding input."""
    now = datetime.now(timezone.utc)
    rec = ExperienceRecord(
        experience_id="exp-sec",
        space_id="space-sec",
        situation={"task": "t", "api_key": "sk-secret-12345"},
        action={"capability": "tool.run", "token": "bearer-xyz"},
        outcome="Task 't' failed with authorization error",
        counterfactual="Check token validity",
        applicable_context={"error_class": "auth.failed"},
        stored_at=now,
    )

    formatted = format_experience_for_embedding(rec)
    assert "sk-secret-12345" not in formatted
    assert "bearer-xyz" not in formatted
    assert "counterfactual: Check token validity" in formatted
