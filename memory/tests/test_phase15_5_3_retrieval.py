"""Unit test suite for Phase 15.5.3: Semantic Retrieval & Deterministic Ranking.

Contracts: MEM-SEM-001, MEM-SEM-002, MEM-SEM-003, ADR-0049
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock

import pytest
from ryu.pulse_bus.config import PostgresConfig

from core.space.memory_protocol import (
    EmbeddingResult,
    ExperienceRecord,
    SemanticExperienceQuery,
    SpaceIsolationViolation,
    compute_cosine_similarity,
)
from memory.adapters.in_memory import InMemoryMemoryAdapter
from memory.adapters.postgres import PostgreSQLMemoryAdapter
from memory.embeddings.deterministic_mock import DeterministicMockEmbeddingProvider
from memory.retrieval.ranker import (
    deterministic_rank_candidates,
    is_embedding_compatible,
)


def _make_record(
    exp_id: str,
    space_id: str = "space-alpha",
    embedding: tuple[float, ...] | None = None,
    model: str = "mock-hash-128",
    dimension: int = 128,
    version: str = "1.0.0",
    failure_fingerprint: str | None = None,
    capability: str = "python.exec",
    error_class: str | None = None,
    stored_at: datetime | None = None,
) -> ExperienceRecord:
    app_context: dict[str, Any] = {}
    if error_class:
        app_context["error_class"] = error_class
    if failure_fingerprint:
        app_context["failure_fingerprint"] = failure_fingerprint

    return ExperienceRecord(
        experience_id=exp_id,
        space_id=space_id,
        situation={"task_id": "task-test", "capability": capability},
        action={"capability": capability, "exit_code": 1},
        outcome="failure",
        counterfactual=f"Alternative guidance for {exp_id}",
        applicable_context=app_context,
        stored_at=stored_at or datetime(2026, 10, 5, 12, 0, 0, tzinfo=timezone.utc),
        embedding=embedding,
        embedding_model=model if embedding is not None else None,
        embedding_dimension=dimension if embedding is not None else None,
        embedding_version=version if embedding is not None else None,
        failure_fingerprint=failure_fingerprint,
        provenance_ref=f"prov-{exp_id}",
    )


class TestCosineSimilarityArithmetic:
    """Verifies normalized vector similarity arithmetic and edge cases."""

    def test_identical_vectors_similarity(self) -> None:
        v1 = (0.6, 0.8)
        v2 = (0.6, 0.8)
        sim = compute_cosine_similarity(v1, v2)
        assert pytest.approx(sim, 1e-6) == 1.0

    def test_orthogonal_vectors_similarity(self) -> None:
        v1 = (1.0, 0.0)
        v2 = (0.0, 1.0)
        sim = compute_cosine_similarity(v1, v2)
        assert pytest.approx(sim, 1e-6) == 0.0

    def test_opposite_vectors_similarity(self) -> None:
        v1 = (1.0, 0.0)
        v2 = (-1.0, 0.0)
        sim = compute_cosine_similarity(v1, v2)
        assert pytest.approx(sim, 1e-6) == -1.0

    def test_unnormalized_vectors_normalized_correctly(self) -> None:
        v1 = (3.0, 4.0)
        v2 = (6.0, 8.0)
        sim = compute_cosine_similarity(v1, v2)
        assert pytest.approx(sim, 1e-6) == 1.0

    def test_dimension_mismatch_raises(self) -> None:
        v1 = (1.0, 2.0)
        v2 = (1.0, 2.0, 3.0)
        with pytest.raises(ValueError, match="dimension mismatch"):
            compute_cosine_similarity(v1, v2)

    def test_empty_vectors_raises(self) -> None:
        with pytest.raises(ValueError, match="must not be empty"):
            compute_cosine_similarity((), ())

    def test_zero_vector_returns_zero(self) -> None:
        v1 = (0.0, 0.0)
        v2 = (1.0, 1.0)
        assert compute_cosine_similarity(v1, v2) == 0.0

    def test_nan_vector_component_raises(self) -> None:
        v1 = (1.0, float("nan"))
        v2 = (1.0, 2.0)
        with pytest.raises(ValueError, match="must not be NaN"):
            compute_cosine_similarity(v1, v2)

    def test_inf_vector_component_raises(self) -> None:
        v1 = (1.0, float("inf"))
        v2 = (1.0, 2.0)
        with pytest.raises(ValueError, match="must not be infinite"):
            compute_cosine_similarity(v1, v2)


class TestCandidateValidationAndRanking:
    """Verifies compatibility filtering, deterministic ranking, and tie-breaking."""

    def test_compatibility_validation(self) -> None:
        query_emb = EmbeddingResult(vector=(1.0, 0.0), model="m1", dimension=2, version="v1")

        rec_valid = _make_record("r1", embedding=(1.0, 0.0), model="m1", dimension=2, version="v1")
        assert is_embedding_compatible(rec_valid, query_emb) is True

        rec_no_emb = _make_record("r2", embedding=None)
        assert is_embedding_compatible(rec_no_emb, query_emb) is False

        rec_diff_model = _make_record("r3", embedding=(1.0, 0.0), model="m2", dimension=2, version="v1")
        assert is_embedding_compatible(rec_diff_model, query_emb) is False

        rec_diff_version = _make_record("r4", embedding=(1.0, 0.0), model="m1", dimension=2, version="v2")
        assert is_embedding_compatible(rec_diff_version, query_emb) is False

        rec_diff_dim = _make_record("r5", embedding=(1.0, 0.0, 0.0), model="m1", dimension=3, version="v1")
        assert is_embedding_compatible(rec_diff_dim, query_emb) is False

    def test_primary_score_ordering(self) -> None:
        query_emb = EmbeddingResult(vector=(1.0, 0.0), model="m1", dimension=2, version="v1")
        r_high = _make_record("r-high", embedding=(0.9, 0.1), model="m1", dimension=2, version="v1")
        r_low = _make_record("r-low", embedding=(0.3, 0.7), model="m1", dimension=2, version="v1")

        results = deterministic_rank_candidates([r_low, r_high], query_emb, top_k=5)
        assert len(results) == 2
        assert results[0].experience_id == "r-high"
        assert results[0].rank == 1
        assert results[1].experience_id == "r-low"
        assert results[1].rank == 2

    def test_canonical_tie_breaking_stored_at(self) -> None:
        """Identical rounded scores tie-break by stored_at DESC."""
        query_emb = EmbeddingResult(vector=(1.0, 0.0), model="m1", dimension=2, version="v1")
        t_older = datetime(2026, 10, 1, 10, 0, 0, tzinfo=timezone.utc)
        t_newer = datetime(2026, 10, 5, 10, 0, 0, tzinfo=timezone.utc)

        r_older = _make_record("r-older", embedding=(1.0, 0.0), model="m1", dimension=2, version="v1", stored_at=t_older)
        r_newer = _make_record("r-newer", embedding=(1.0, 0.0), model="m1", dimension=2, version="v1", stored_at=t_newer)

        results = deterministic_rank_candidates([r_older, r_newer], query_emb, top_k=5)
        assert results[0].experience_id == "r-newer"
        assert results[1].experience_id == "r-older"

    def test_canonical_tie_breaking_experience_id(self) -> None:
        """Identical rounded scores and stored_at tie-break by experience_id ASC."""
        query_emb = EmbeddingResult(vector=(1.0, 0.0), model="m1", dimension=2, version="v1")
        t = datetime(2026, 10, 5, 10, 0, 0, tzinfo=timezone.utc)

        r_z = _make_record("exp-zzz", embedding=(1.0, 0.0), model="m1", dimension=2, version="v1", stored_at=t)
        r_a = _make_record("exp-aaa", embedding=(1.0, 0.0), model="m1", dimension=2, version="v1", stored_at=t)

        results = deterministic_rank_candidates([r_z, r_a], query_emb, top_k=5)
        assert results[0].experience_id == "exp-aaa"
        assert results[1].experience_id == "exp-zzz"

    def test_threshold_filtering(self) -> None:
        query_emb = EmbeddingResult(vector=(1.0, 0.0), model="m1", dimension=2, version="v1")
        r_pass = _make_record("r-pass", embedding=(0.9, 0.1), model="m1", dimension=2, version="v1")
        r_fail = _make_record("r-fail", embedding=(0.1, 0.9), model="m1", dimension=2, version="v1")

        results = deterministic_rank_candidates([r_pass, r_fail], query_emb, top_k=5, min_similarity=0.8)
        assert len(results) == 1
        assert results[0].experience_id == "r-pass"

    def test_top_k_bounds(self) -> None:
        query_emb = EmbeddingResult(vector=(1.0, 0.0), model="m1", dimension=2, version="v1")
        records = [
            _make_record(f"r-{i}", embedding=(1.0, 0.0), model="m1", dimension=2, version="v1")
            for i in range(10)
        ]

        res_k3 = deterministic_rank_candidates(records, query_emb, top_k=3)
        assert len(res_k3) == 3

        res_k5 = deterministic_rank_candidates(records, query_emb, top_k=5)
        assert len(res_k5) == 5

        # Clamped to 5 even if top_k parameter > 5 is passed to ranker directly
        res_k10 = deterministic_rank_candidates(records, query_emb, top_k=10)
        assert len(res_k10) == 5


class TestInMemorySemanticRetrievalPipeline:
    """Verifies end-to-end multi-prong candidate generation and ranking in InMemory adapter."""

    def test_in_memory_multi_prong_candidate_bounds(self) -> None:
        adapter = InMemoryMemoryAdapter()
        space_id = "space-multi"
        provider = DeterministicMockEmbeddingProvider()

        # Seed 70 records in space-multi
        for i in range(70):
            fp = "fp-match" if i < 15 else f"fp-{i}"
            cap = "python.eval" if i % 2 == 0 else "terminal.exec"
            err = "transient.timeout" if i % 3 == 0 else "syntax.error"
            rec = _make_record(
                exp_id=f"exp-{i:03d}",
                space_id=space_id,
                failure_fingerprint=fp,
                capability=cap,
                error_class=err,
            )
            emb = provider.embed(f"experience text {i}")
            adapter.store_experience(rec, embedding=emb)

        query = SemanticExperienceQuery(
            space_id=space_id,
            query_text="experience text 0",
            failure_fingerprint="fp-match",
            situation_hint={"capability": "python.eval", "error_class": "transient.timeout"},
            top_k=5,
        )

        # Internal candidate selection must clamp to C <= 50
        candidates = adapter._get_semantic_candidates(query)
        assert len(candidates) <= 50
        assert len(candidates) > 0

        # Prong A priority: exact fingerprint matches present
        fp_candidates = [c for c in candidates if c.failure_fingerprint == "fp-match"]
        assert len(fp_candidates) <= 10
        assert len(fp_candidates) > 0

        # Full retrieval execution
        scored = adapter.retrieve_semantic_experiences(query, embedding_provider=provider)
        assert len(scored) <= 5
        assert len(scored) > 0
        assert scored[0].rank == 1
        assert 0.0 <= scored[0].similarity_score <= 1.0

    def test_space_isolation_in_memory(self) -> None:
        adapter = InMemoryMemoryAdapter()
        provider = DeterministicMockEmbeddingProvider()

        # Store record in Space A
        rec_a = _make_record("exp-a", space_id="space-A", failure_fingerprint="fp-leak")
        adapter.store_experience(rec_a, embedding=provider.embed("guidance A"))

        # Query from Space B
        query_b = SemanticExperienceQuery(
            space_id="space-B",
            query_text="guidance A",
            failure_fingerprint="fp-leak",
            top_k=5,
        )
        results = adapter.retrieve_semantic_experiences(query_b, embedding_provider=provider)
        assert len(results) == 0

    def test_empty_space_id_rejected(self) -> None:
        with pytest.raises(SpaceIsolationViolation):
            SemanticExperienceQuery(space_id="")

    def test_invalid_top_k_rejected(self) -> None:
        with pytest.raises(ValueError, match="top_k must be between 1 and 5"):
            SemanticExperienceQuery(space_id="space-test", top_k=6)
        with pytest.raises(ValueError, match="top_k must be between 1 and 5"):
            SemanticExperienceQuery(space_id="space-test", top_k=0)

    def test_query_embedding_or_provider_required(self) -> None:
        adapter = InMemoryMemoryAdapter()
        q_no_emb_no_prov = SemanticExperienceQuery(space_id="space-test", query_text="")
        with pytest.raises(ValueError, match="must provide query_embedding or non-empty query_text"):
            adapter.retrieve_semantic_experiences(q_no_emb_no_prov, embedding_provider=None)


class TestHardenedListExperiencesPagination:
    """Verifies bounded pagination in list_experiences under contract MEM-SEM-001."""

    def test_in_memory_list_experiences_default_bound(self) -> None:
        adapter = InMemoryMemoryAdapter()
        space_id = "space-page"
        for i in range(120):
            adapter.store_experience(_make_record(f"e-{i:03d}", space_id=space_id))

        # Default limit 50 enforced
        res = adapter.list_experiences(space_id)
        assert len(res) == 50

        # Maximum ceiling 100 enforced even if 200 requested
        res_large = adapter.list_experiences(space_id, limit=200)
        assert len(res_large) == 100

        # Keysite pagination before_stored_at
        middle_time = res[25].stored_at
        res_paginated = adapter.list_experiences(space_id, limit=50, before_stored_at=middle_time)
        for r in res_paginated:
            assert r.stored_at < middle_time


class TestPostgreSQLSemanticRetrievalStructure:
    """Verifies SQL query structure and parameterization in PostgreSQL adapter."""

    def test_mock_postgres_retrieval_flow(self) -> None:
        config = PostgresConfig(host="localhost", port=5432, db="ryu", user="r", password="p")
        adapter = PostgreSQLMemoryAdapter(config=config)
        provider = DeterministicMockEmbeddingProvider()

        # Mock database connection and cursor
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.__enter__.return_value = mock_conn
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur
        adapter._get_conn = MagicMock(return_value=mock_conn)  # type: ignore

        # Return mock row
        now = datetime(2026, 10, 5, 12, 0, 0, tzinfo=timezone.utc)
        emb = provider.embed("mock sql experience")
        mock_row = (
            "exp-sql-001", "space-sql",
            {"task": "t1"}, {"capability": "python.exec"},
            "failure", "Try different approach",
            {"error_class": "Timeout"}, now,
            list(emb.vector), emb.model, emb.dimension, emb.version,
            "fp-sql", "prov-sql",
        )
        mock_cur.fetchall.return_value = [mock_row]

        query = SemanticExperienceQuery(
            space_id="space-sql",
            query_embedding=emb,
            failure_fingerprint="fp-sql",
            situation_hint={"capability": "python.exec"},
            top_k=3,
        )

        results = adapter.retrieve_semantic_experiences(query)
        assert len(results) == 1
        assert results[0].experience_id == "exp-sql-001"
        assert pytest.approx(results[0].similarity_score, 1e-4) == 1.0
        assert results[0].rank == 1
        assert results[0].exact_fingerprint_match is True

        # Verify SQL queries used parameterized placeholders
        for call_args in mock_cur.execute.call_args_list:
            sql, params = call_args[0]
            assert "%s" in sql
            assert "space-sql" in params
