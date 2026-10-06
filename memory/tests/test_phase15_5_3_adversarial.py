"""Phase 15.5.3 Adversarial Test Suite: Semantic Retrieval & Deterministic Ranking.

Contracts: MEM-SEM-001, MEM-SEM-002, MEM-SEM-003, ADR-0049
Adversarial Verification Suite: SEM-RET-ADV-01 through SEM-RET-ADV-20
"""

from __future__ import annotations

import concurrent.futures
from datetime import datetime, timezone
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
    resolve_query_embedding,
)


def _make_experience(
    exp_id: str,
    space_id: str = "space-adversarial",
    embedding: list[float] | tuple[float, ...] | None = None,
    stored_at: datetime | None = None,
    failure_fingerprint: str = "",
    capability: str = "python.exec",
    error_class: str = "Timeout",
    task_id: str = "task-adv",
    embedding_model: str = "deterministic-mock",
    embedding_version: str = "1.0.0",
) -> ExperienceRecord:
    now = stored_at or datetime.now(timezone.utc)
    emb_tuple = tuple(embedding) if embedding is not None else None
    return ExperienceRecord(
        experience_id=exp_id,
        space_id=space_id,
        situation={"task_id": task_id, "capability": capability},
        action={"capability": capability, "params": {}},
        outcome="Execution failed with timeout",
        counterfactual="Retry with bounded exponential backoff",
        applicable_context={
            "error_class": error_class,
            "failure_fingerprint": failure_fingerprint,
        },
        stored_at=now,
        embedding=emb_tuple,
        embedding_model=embedding_model if emb_tuple is not None else None,
        embedding_dimension=len(emb_tuple) if emb_tuple is not None else None,
        embedding_version=embedding_version if emb_tuple is not None else None,
        failure_fingerprint=failure_fingerprint or None,
    )


class TestPhase15_5_3Adversarial:
    """Covers SEM-RET-ADV-01 through SEM-RET-ADV-20."""

    def test_adv_01_cross_space_isolation(self) -> None:
        """SEM-RET-ADV-01: Cross-Space Retrieval Attempt.
        Space A cannot retrieve Space B experiences under any query even with identical vector.
        """
        adapter = InMemoryMemoryAdapter()
        provider = DeterministicMockEmbeddingProvider()
        emb = provider.embed("same failure context")

        # Store in space-A and space-B
        exp_a = _make_experience("exp-a", space_id="space-A", embedding=emb.vector)
        exp_b = _make_experience("exp-b", space_id="space-B", embedding=emb.vector)
        adapter.store_experience(exp_a)
        adapter.store_experience(exp_b)

        query = SemanticExperienceQuery(
            space_id="space-A",
            query_text="same failure context",
            top_k=5,
            min_similarity=0.0,
        )
        results = adapter.retrieve_semantic_experiences(query, embedding_provider=provider)
        assert len(results) == 1
        assert results[0].experience_id == "exp-a"
        assert results[0].space_id == "space-A"

        # Explicit verification that space-B never leaked into space-A
        assert all(r.space_id == "space-A" for r in results)

    def test_adv_02_empty_or_whitespace_space_id(self) -> None:
        """SEM-RET-ADV-02: Forged / Empty / Whitespace Space ID.
        Queries with space_id='' or '   ' must be rejected with SpaceIsolationViolation.
        """
        with pytest.raises(SpaceIsolationViolation):
            SemanticExperienceQuery(space_id="")

        with pytest.raises(SpaceIsolationViolation):
            SemanticExperienceQuery(space_id="   ")

    def test_adv_03_invalid_top_k_bounds(self) -> None:
        """SEM-RET-ADV-03: Invalid top_k bounds.
        top_k < 1 or top_k > 5 must raise ValueError to protect memory and context limits.
        """
        for invalid_k in [0, -1, -10, 6, 7, 50, 100]:
            with pytest.raises(ValueError, match=r"top_k must be between 1 and 5"):
                SemanticExperienceQuery(space_id="space-test", top_k=invalid_k)

    def test_adv_04_invalid_min_similarity_nan_inf(self) -> None:
        """SEM-RET-ADV-04: Invalid min_similarity bounds.
        NaN or Inf must raise ValueError.
        """
        with pytest.raises(ValueError, match="min_similarity must be a finite float"):
            SemanticExperienceQuery(space_id="space-test", min_similarity=float("nan"))

        with pytest.raises(ValueError, match="min_similarity must be a finite float"):
            SemanticExperienceQuery(space_id="space-test", min_similarity=float("inf"))

        with pytest.raises(ValueError, match="min_similarity must be a finite float"):
            SemanticExperienceQuery(space_id="space-test", min_similarity=float("-inf"))

    def test_adv_05_vector_dimension_mismatch(self) -> None:
        """SEM-RET-ADV-05: Vector Dimension Mismatch.
        Candidate vector with dimension 128 against query dimension 384 is flagged incompatible.
        """
        provider = DeterministicMockEmbeddingProvider()
        q_emb = provider.embed("sample query")  # dim 384

        rec = _make_experience("exp-mismatch-dim", embedding=[0.1] * 64)
        assert is_embedding_compatible(rec, q_emb) is False

        # In ranking, it must be excluded
        results = deterministic_rank_candidates([rec], q_emb, top_k=5)
        assert len(results) == 0

    def test_adv_06_model_name_mismatch(self) -> None:
        """SEM-RET-ADV-06: Model Name Mismatch.
        Candidate stored with different model name is flagged incompatible.
        """
        provider = DeterministicMockEmbeddingProvider()
        q_emb = provider.embed("sample query")

        rec = _make_experience(
            "exp-mismatch-model",
            embedding=q_emb.vector,
            embedding_model="incompatible-other-model",
        )
        assert is_embedding_compatible(rec, q_emb) is False

        results = deterministic_rank_candidates([rec], q_emb, top_k=5)
        assert len(results) == 0

    def test_adv_07_model_version_mismatch(self) -> None:
        """SEM-RET-ADV-07: Model Version Mismatch.
        Candidate stored with different model version is flagged incompatible.
        """
        provider = DeterministicMockEmbeddingProvider()
        q_emb = provider.embed("sample query")

        rec = _make_experience(
            "exp-mismatch-ver",
            embedding=q_emb.vector,
            embedding_version="99.0.0",
        )
        assert is_embedding_compatible(rec, q_emb) is False

        results = deterministic_rank_candidates([rec], q_emb, top_k=5)
        assert len(results) == 0

    def test_adv_08_query_vector_non_finite_rejection(self) -> None:
        """SEM-RET-ADV-08: Query Vector Non-Finite Component Rejection.
        Query embedding containing NaN or Inf is rejected by resolve_query_embedding.
        """
        # Synthesize bypassed EmbeddingResult instances
        fake_nan_emb = object.__new__(EmbeddingResult)
        object.__setattr__(fake_nan_emb, "vector", (1.0, float("nan"), 0.5))
        object.__setattr__(fake_nan_emb, "model", "test-model")
        object.__setattr__(fake_nan_emb, "dimension", 3)
        object.__setattr__(fake_nan_emb, "version", "1.0.0")

        bad_query_nan = SemanticExperienceQuery(
            space_id="space-test",
            query_embedding=fake_nan_emb,
        )
        with pytest.raises(ValueError, match="query vector component at index 1 must not be NaN"):
            resolve_query_embedding(bad_query_nan, None)

        fake_inf_emb = object.__new__(EmbeddingResult)
        object.__setattr__(fake_inf_emb, "vector", (1.0, float("inf"), 0.5))
        object.__setattr__(fake_inf_emb, "model", "test-model")
        object.__setattr__(fake_inf_emb, "dimension", 3)
        object.__setattr__(fake_inf_emb, "version", "1.0.0")

        bad_query_inf = SemanticExperienceQuery(
            space_id="space-test",
            query_embedding=fake_inf_emb,
        )
        with pytest.raises(ValueError, match="query vector component at index 1 must not be infinite"):
            resolve_query_embedding(bad_query_inf, None)

    def test_adv_09_candidate_non_finite_vector_rejection(self) -> None:
        """SEM-RET-ADV-09: Candidate Non-Finite Vector Rejection.
        Candidate record containing NaN or Inf in its embedding is safely deemed incompatible.
        """
        provider = DeterministicMockEmbeddingProvider()
        q_emb = provider.embed("sample query")

        rec_nan = _make_experience("exp-nan", embedding=[0.1] * 384)
        bad_nan_tuple = list(rec_nan.embedding or ())
        bad_nan_tuple[10] = float("nan")
        object.__setattr__(rec_nan, "embedding", tuple(bad_nan_tuple))

        rec_inf = _make_experience("exp-inf", embedding=[0.1] * 384)
        bad_inf_tuple = list(rec_inf.embedding or ())
        bad_inf_tuple[10] = float("inf")
        object.__setattr__(rec_inf, "embedding", tuple(bad_inf_tuple))

        assert is_embedding_compatible(rec_nan, q_emb) is False
        assert is_embedding_compatible(rec_inf, q_emb) is False

        results = deterministic_rank_candidates([rec_nan, rec_inf], q_emb, top_k=5)
        assert len(results) == 0

    def test_adv_10_candidate_null_or_empty_embedding(self) -> None:
        """SEM-RET-ADV-10: Candidate Null or Empty Embedding.
        Candidates with None embedding are safely skipped without crash.
        """
        provider = DeterministicMockEmbeddingProvider()
        q_emb = provider.embed("sample query")

        rec_none = _make_experience("exp-none", embedding=None)
        assert is_embedding_compatible(rec_none, q_emb) is False

        results = deterministic_rank_candidates([rec_none], q_emb, top_k=5)
        assert len(results) == 0

    def test_adv_11_deterministic_tie_breaking(self) -> None:
        """SEM-RET-ADV-11: Deterministic Tie Collision.
        Multiple records with identical cosine similarity and identical timestamp
        must be sorted deterministically by experience_id ASC across 100 runs.
        """
        provider = DeterministicMockEmbeddingProvider()
        q_emb = provider.embed("tie query")
        fixed_time = datetime(2026, 10, 6, 12, 0, 0, tzinfo=timezone.utc)

        # 8 records with identical vector and identical timestamp
        candidates = [
            _make_experience(f"exp-{i:03d}", embedding=q_emb.vector, stored_at=fixed_time)
            for i in [7, 2, 9, 1, 5, 3, 8, 4]
        ]

        expected_ids = ["exp-001", "exp-002", "exp-003", "exp-004", "exp-005"]
        for _ in range(100):
            ranked = deterministic_rank_candidates(candidates, q_emb, top_k=5)
            actual_ids = [r.experience_id for r in ranked]
            assert actual_ids == expected_ids
            assert [r.rank for r in ranked] == [1, 2, 3, 4, 5]

    def test_adv_12_score_rounding_quantization(self) -> None:
        """SEM-RET-ADV-12: Floating-Point Clamping & Rounding.
        Scores within the 4-decimal quantization window (e.g. 0.85001 and 0.85004)
        quantize to 0.8500, so tie-breaker orders by stored_at DESC, then experience_id ASC.
        """
        provider = DeterministicMockEmbeddingProvider()
        q_emb = provider.embed("quantization test")

        t1 = datetime(2026, 10, 6, 10, 0, 0, tzinfo=timezone.utc)
        t2 = datetime(2026, 10, 6, 11, 0, 0, tzinfo=timezone.utc)

        vec = list(q_emb.vector)

        # Tiny perturbation at index 0 that causes difference only beyond 4th decimal place
        vec_a = list(vec)
        vec_a[0] += 0.0000001
        vec_b = list(vec)
        vec_b[0] += 0.0000002

        rec_old = _make_experience("exp-old", embedding=vec_b, stored_at=t1)
        rec_new = _make_experience("exp-new", embedding=vec_a, stored_at=t2)

        # Both round to 1.0000. Since t2 > t1, rec_new (newer) wins over rec_old
        ranked = deterministic_rank_candidates([rec_old, rec_new], q_emb, top_k=2)
        assert [r.experience_id for r in ranked] == ["exp-new", "exp-old"]
        assert round(ranked[0].similarity_score, 4) == 1.0

    def test_adv_13_candidate_bound_c_max_50(self) -> None:
        """SEM-RET-ADV-13: Candidate Bound Enforcement (C <= 50).
        When a space has >100 matching experiences, candidate selection strictly caps at 50.
        """
        adapter = InMemoryMemoryAdapter()
        provider = DeterministicMockEmbeddingProvider()
        emb = provider.embed("candidate test")

        # Ingest 15 experiences with matching failure_fingerprint (Prong A gives 10)
        for i in range(15):
            exp = _make_experience(
                f"exp-fp-{i:03d}",
                space_id="space-large",
                embedding=emb.vector,
                failure_fingerprint="fp-common",
                capability="other.cap",
                error_class="OtherError",
            )
            adapter.store_experience(exp)

        # Ingest 30 experiences with matching capability & error_class (Prong B gives 25)
        for i in range(30):
            exp = _make_experience(
                f"exp-ctx-{i:03d}",
                space_id="space-large",
                embedding=emb.vector,
                failure_fingerprint=f"fp-diff-{i}",
                capability="python.exec",
                error_class="Timeout",
            )
            adapter.store_experience(exp)

        # Ingest 30 generic experiences (Prong C recency gives 20)
        for i in range(30):
            exp = _make_experience(
                f"exp-rec-{i:03d}",
                space_id="space-large",
                embedding=emb.vector,
                failure_fingerprint="",
                capability="generic.cap",
                error_class="",
            )
            adapter.store_experience(exp)

        query = SemanticExperienceQuery(
            space_id="space-large",
            query_text="candidate test",
            failure_fingerprint="fp-common",
            situation_hint={"capability": "python.exec", "error_class": "Timeout"},
            top_k=5,
        )
        candidates = adapter._get_semantic_candidates(query)  # type: ignore[attr-defined]
        assert len(candidates) <= 50
        assert len(candidates) == 50

    def test_adv_14_top_k_ceiling_k_max_5(self) -> None:
        """SEM-RET-ADV-14: Top-K Ceiling Enforcement (K <= 5).
        Even if 50 candidates are perfectly similar, ranking never returns more than top_k <= 5.
        """
        provider = DeterministicMockEmbeddingProvider()
        q_emb = provider.embed("ceiling test")

        candidates = [
            _make_experience(f"exp-{i:02d}", embedding=q_emb.vector)
            for i in range(50)
        ]

        ranked = deterministic_rank_candidates(candidates, q_emb, top_k=5)
        assert len(ranked) == 5
        assert [r.rank for r in ranked] == [1, 2, 3, 4, 5]

        # If caller specifies top_k=3, returns 3
        ranked_3 = deterministic_rank_candidates(candidates, q_emb, top_k=3)
        assert len(ranked_3) == 3

    def test_adv_15_threshold_filtering(self) -> None:
        """SEM-RET-ADV-15: Threshold Filtering.
        Candidates below min_similarity are filtered out.
        """
        provider = DeterministicMockEmbeddingProvider()
        q_emb = provider.embed("threshold query")

        # Opposite direction vector has negative similarity
        neg_vec = [-v for v in q_emb.vector]
        rec_high = _make_experience("exp-high", embedding=q_emb.vector)
        rec_low = _make_experience("exp-low", embedding=neg_vec)

        ranked = deterministic_rank_candidates(
            [rec_high, rec_low],
            q_emb,
            top_k=5,
            min_similarity=0.5,
        )
        assert len(ranked) == 1
        assert ranked[0].experience_id == "exp-high"

    def test_adv_16_list_experiences_unbounded_pagination_protection(self) -> None:
        """SEM-RET-ADV-16: Unbounded list_experiences Pagination Protection.
        Default limit is 50, maximum limit is clamped to 100.
        """
        adapter = InMemoryMemoryAdapter()
        base_time = datetime(2026, 10, 6, 12, 0, 0, tzinfo=timezone.utc)

        for i in range(150):
            adapter.store_experience(
                _make_experience(f"exp-{i:03d}", space_id="space-page", stored_at=base_time)
            )

        # Default limit
        page_default = adapter.list_experiences("space-page")
        assert len(page_default) == 50

        # Excessive requested limit clamped to 100
        page_excessive = adapter.list_experiences("space-page", limit=5000)
        assert len(page_excessive) == 100

        # Cursor pagination
        oldest_in_page = page_default[-1].stored_at
        page_next = adapter.list_experiences(
            "space-page", limit=50, before_stored_at=oldest_in_page
        )
        assert len(page_next) <= 50

    def test_adv_17_exact_fingerprint_flagging_without_numerical_hacking(self) -> None:
        """SEM-RET-ADV-17: Exact Fingerprint Match Flagging Without Numerical Hacking.
        Matching failure fingerprint marks exact_fingerprint_match=True,
        but similarity_score is strictly cosine similarity bounded in [-1.0, 1.0].
        No arbitrary numerical bonus (e.g. +100) is injected.
        """
        provider = DeterministicMockEmbeddingProvider()
        q_emb = provider.embed("fingerprint query")

        rec = _make_experience(
            "exp-fp",
            embedding=q_emb.vector,
            failure_fingerprint="fp-target-123",
        )

        ranked = deterministic_rank_candidates(
            [rec],
            q_emb,
            top_k=5,
            target_fingerprint="fp-target-123",
        )
        assert len(ranked) == 1
        assert ranked[0].exact_fingerprint_match is True
        assert -1.0 <= ranked[0].similarity_score <= 1.0
        assert ranked[0].similarity_score == 1.0

    def test_adv_18_zero_magnitude_vector_handling(self) -> None:
        """SEM-RET-ADV-18: Zero-Magnitude Vector / Degenerate Cosine Similarity.
        All-zero vectors return 0.0 cosine similarity safely without ZeroDivisionError.
        """
        zero_vec = [0.0] * 10
        normal_vec = [1.0] * 10

        sim_zero_a = compute_cosine_similarity(zero_vec, normal_vec)
        assert sim_zero_a == 0.0

        sim_zero_b = compute_cosine_similarity(normal_vec, zero_vec)
        assert sim_zero_b == 0.0

        sim_both_zero = compute_cosine_similarity(zero_vec, zero_vec)
        assert sim_both_zero == 0.0

    def test_adv_19_concurrent_retrieval_thread_safety(self) -> None:
        """SEM-RET-ADV-19: Concurrent Retrieval Thread Safety.
        20 threads simultaneously writing experiences and retrieving semantic experiences
        complete without race conditions, deadlocks, or state corruption.
        """
        adapter = InMemoryMemoryAdapter()
        provider = DeterministicMockEmbeddingProvider()
        emb = provider.embed("thread safety experience")

        for i in range(10):
            adapter.store_experience(
                _make_experience(f"exp-init-{i}", space_id="space-concurrent", embedding=emb.vector)
            )

        errors: list[Exception] = []

        def worker_task(thread_id: int) -> None:
            try:
                rec = _make_experience(
                    f"exp-th-{thread_id}",
                    space_id="space-concurrent",
                    embedding=emb.vector,
                )
                adapter.store_experience(rec)

                query = SemanticExperienceQuery(
                    space_id="space-concurrent",
                    query_text="thread safety experience",
                    top_k=5,
                    min_similarity=0.0,
                )
                results = adapter.retrieve_semantic_experiences(query, embedding_provider=provider)
                assert len(results) >= 1
                assert len(results) <= 5
            except Exception as e:
                errors.append(e)

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(worker_task, i) for i in range(20)]
            concurrent.futures.wait(futures)

        assert len(errors) == 0, f"Thread errors encountered: {errors}"

    def test_adv_20_parameterized_sql_injection_defense(self) -> None:
        """SEM-RET-ADV-20: Parameterized SQL Injection Defense.
        SQL injection strings in space_id, task_id, capability, or error_class
        are strictly passed as parameters and never concatenated directly.
        """
        config = PostgresConfig(host="localhost", port=5432, db="ryu", user="r", password="p")
        adapter = PostgreSQLMemoryAdapter(config=config)
        provider = DeterministicMockEmbeddingProvider()

        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.__enter__.return_value = mock_conn
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur
        adapter._get_conn = MagicMock(return_value=mock_conn)  # type: ignore[assignment]

        mock_cur.fetchall.return_value = []

        injection_payload = "'; DROP TABLE space_experiences; --"
        query = SemanticExperienceQuery(
            space_id=f"space-test{injection_payload}",
            query_text="injection test",
            failure_fingerprint=f"fp{injection_payload}",
            situation_hint={
                "capability": f"cap{injection_payload}",
                "error_class": f"err{injection_payload}",
            },
            top_k=5,
        )

        results = adapter.retrieve_semantic_experiences(query, embedding_provider=provider)
        assert results == []

        for call_item in mock_cur.execute.call_args_list:
            sql_text, params = call_item[0]
            assert "DROP TABLE" not in sql_text
            assert any(injection_payload in str(p) for p in params)
