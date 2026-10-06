"""Phase 15.5.3 — Deterministic Semantic Ranking & Candidate Validation Engine (MEM-SEM-002, ADR-0049).

Invariants:
- Canonical deterministic ranking: (round(score, 4), stored_at DESC, experience_id ASC).
- Finite, bounded, non-NaN/Inf vector arithmetic.
- Strict compatibility verification between query and candidate vectors.
- Top-K bounded: 1 <= K <= 5.
"""

from __future__ import annotations

import math
from datetime import timezone

from core.space.memory_protocol import (
    EmbeddingProviderProtocol,
    EmbeddingResult,
    ExperienceRecord,
    ScoredExperienceRecord,
    SemanticExperienceQuery,
    compute_cosine_similarity,
    normalize_embedding_input,
)


def resolve_query_embedding(
    query: SemanticExperienceQuery,
    provider: EmbeddingProviderProtocol | None = None,
) -> EmbeddingResult:
    """Resolve and validate the query embedding for semantic retrieval.

    If query.query_embedding is supplied, validates that all vector components are finite numbers.
    If query.query_embedding is None, normalizes query.query_text and invokes provider.embed().

    Raises:
        ValueError: If neither a valid query_embedding nor query_text with provider is supplied,
                    or if the vector contains NaN or infinite components.
    """
    if query.query_embedding is not None:
        emb = query.query_embedding
        for i, val in enumerate(emb.vector):
            if not isinstance(val, (int, float)):
                raise TypeError(f"query vector component at index {i} must be float, got {type(val).__name__}")
            if math.isnan(val):
                raise ValueError(f"query vector component at index {i} must not be NaN")
            if math.isinf(val):
                raise ValueError(f"query vector component at index {i} must not be infinite")
        return emb

    if query.query_text and query.query_text.strip() and provider is not None:
        normalized_text = normalize_embedding_input(query.query_text)
        return provider.embed(normalized_text)

    raise ValueError(
        "Semantic retrieval query must provide query_embedding or non-empty query_text with an embedding_provider"
    )


def is_embedding_compatible(
    candidate: ExperienceRecord,
    query_emb: EmbeddingResult,
) -> bool:
    """Verify whether a candidate ExperienceRecord's embedding is compatible with the query vector.

    Validates:
    - candidate has a non-null embedding
    - model match: candidate.embedding_model == query_emb.model
    - version match: candidate.embedding_version == query_emb.version
    - dimension match: candidate.embedding_dimension == query_emb.dimension == len(candidate.embedding)
    - finite numeric components: no NaN, no Inf
    """
    if candidate.embedding is None:
        return False
    if candidate.embedding_model != query_emb.model:
        return False
    if candidate.embedding_version != query_emb.version:
        return False
    if candidate.embedding_dimension != query_emb.dimension:
        return False
    if len(candidate.embedding) != len(query_emb.vector):
        return False

    for val in candidate.embedding:
        if not isinstance(val, (int, float)):
            return False
        if math.isnan(val) or math.isinf(val):
            return False

    return True


def deterministic_rank_candidates(
    candidates: list[ExperienceRecord],
    query_emb: EmbeddingResult,
    top_k: int = 5,
    min_similarity: float = 0.0,
    target_fingerprint: str | None = None,
) -> list[ScoredExperienceRecord]:
    """Score, filter, and deterministically rank candidate experiences (MEM-SEM-002, ADR-0049).

    Algorithm:
    1. Filter candidates to strictly compatible embeddings (model, version, dimension, finite).
    2. Compute cosine similarity for each compatible candidate.
    3. Filter candidates below min_similarity threshold.
    4. Deterministic tie-breaking sort key:
       (round(score, 4), stored_at DESC, experience_id ASC)
    5. Slice to top_k (strictly clamped to 1 <= top_k <= 5).
    6. Construct immutable ScoredExperienceRecord instances with assigned ranks (1..K).
    """
    clamped_k = min(max(1, top_k), 5)

    scored_candidates: list[tuple[float, ExperienceRecord]] = []
    for cand in candidates:
        if not is_embedding_compatible(cand, query_emb):
            continue

        assert cand.embedding is not None  # guaranteed by is_embedding_compatible
        score = compute_cosine_similarity(query_emb.vector, cand.embedding)
        if score < min_similarity:
            continue

        scored_candidates.append((score, cand))

    def _canonical_sort_key(item: tuple[float, ExperienceRecord]) -> tuple[float, float, str]:
        score, rec = item
        stored_at = (
            rec.stored_at
            if rec.stored_at.tzinfo is not None
            else rec.stored_at.replace(tzinfo=timezone.utc)
        )
        return (-round(score, 4), -stored_at.timestamp(), rec.experience_id)

    scored_candidates.sort(key=_canonical_sort_key)
    top_slice = scored_candidates[:clamped_k]

    results: list[ScoredExperienceRecord] = []
    for idx, (score, rec) in enumerate(top_slice):
        rank = idx + 1
        is_exact_fp = bool(
            target_fingerprint
            and rec.failure_fingerprint
            and rec.failure_fingerprint == target_fingerprint
        )
        results.append(
            ScoredExperienceRecord(
                record=rec,
                similarity_score=score,
                rank=rank,
                exact_fingerprint_match=is_exact_fp,
            )
        )

    return results
