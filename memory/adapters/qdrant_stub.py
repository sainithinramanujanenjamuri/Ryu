"""Qdrant vector store adapter — extension boundary only.

InMemory and PostgreSQL are the Phase 10 authoritative implementations.
Qdrant is an extension boundary for future vector-search capability.
Running a Qdrant instance is NOT required for the Phase 10 gate.
This stub verifies the architecture can accommodate Qdrant without interface changes (ADR-0033).

spec §4 (Vector Store), §15 (Storage Layer) — Phase 11+
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from core.space.memory_protocol import (
    CompactionResult,
    EmbeddingProviderProtocol,
    EmbeddingResult,
    ExperienceQuery,
    ExperienceRecord,
    KnowledgeEntry,
    PromotionAuthorization,
    RetentionPolicy,
    ScoredExperienceRecord,
    SemanticExperienceQuery,
    SpaceMemoryProtocol,
)


class QdrantAdapterStub(SpaceMemoryProtocol):
    """Extension stub for future Qdrant vector database integration."""

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs

    def store_experience(
        self, record: ExperienceRecord, embedding: EmbeddingResult | None = None
    ) -> str:
        raise NotImplementedError("QdrantAdapter: spec §4 (Vector Store) — Phase 11+")

    def get_experience(
        self, space_id: str, experience_id: str
    ) -> ExperienceRecord | None:
        raise NotImplementedError("QdrantAdapter: spec §4 (Vector Store) — Phase 11+")

    def list_experiences(
        self, space_id: str, limit: int = 50, before_stored_at: datetime | None = None
    ) -> list[ExperienceRecord]:
        raise NotImplementedError("QdrantAdapter: spec §4 (Vector Store) — Phase 11+")

    def query_similar_experiences(
        self, query: ExperienceQuery
    ) -> list[ExperienceRecord]:
        raise NotImplementedError("QdrantAdapter: spec §4 (Vector Store) — Phase 11+")

    def retrieve_semantic_experiences(
        self,
        query: SemanticExperienceQuery,
        embedding_provider: EmbeddingProviderProtocol | None = None,
    ) -> list[ScoredExperienceRecord]:
        raise NotImplementedError("QdrantAdapter: spec §4 (Vector Store) — Phase 11+")

    def count_experiences(self, space_id: str) -> int:
        raise NotImplementedError("QdrantAdapter: spec §4 (Vector Store) — Phase 11+")

    def prune_experiences(
        self, space_id: str, policy: RetentionPolicy | None = None
    ) -> CompactionResult:
        raise NotImplementedError("QdrantAdapter: spec §4 (Vector Store) — Phase 11+")

    def store_knowledge(
        self, entry: KnowledgeEntry, auth: PromotionAuthorization
    ) -> None:
        raise NotImplementedError("QdrantAdapter: spec §4 (Vector Store) — Phase 11+")

    def get_global_knowledge(self, knowledge_id: str) -> KnowledgeEntry | None:
        raise NotImplementedError("QdrantAdapter: spec §4 (Vector Store) — Phase 11+")

