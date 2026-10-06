"""Phase 15.5.3 — Semantic Retrieval & Deterministic Ranking Engine (MEM-SEM-001, MEM-SEM-002, ADR-0049)."""

from __future__ import annotations

from memory.retrieval.ranker import (
    deterministic_rank_candidates,
    is_embedding_compatible,
    resolve_query_embedding,
)

__all__ = [
    "deterministic_rank_candidates",
    "is_embedding_compatible",
    "resolve_query_embedding",
]

