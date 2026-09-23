"""Qdrant vector store adapter — extension boundary only.

InMemory and PostgreSQL are the Phase 10 authoritative implementations.
Qdrant is an extension boundary for future vector-search capability.
Running a Qdrant instance is NOT required for the Phase 10 gate.
This stub verifies the architecture can accommodate Qdrant without interface changes (ADR-0033).

spec §4 (Vector Store), §15 (Storage Layer) — Phase 11+
"""

from __future__ import annotations

from typing import Any

from core.space.memory_protocol import (
    ExperienceQuery,
    ExperienceRecord,
    KnowledgeEntry,
    PromotionAuthorization,
    SpaceMemoryProtocol,
)


class QdrantAdapterStub(SpaceMemoryProtocol):
    """Extension stub for future Qdrant vector database integration."""

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs

    def store_experience(self, record: ExperienceRecord) -> str:
        raise NotImplementedError("QdrantAdapter: spec §4 (Vector Store) — Phase 11+")

    def get_experience(
        self, space_id: str, experience_id: str
    ) -> ExperienceRecord | None:
        raise NotImplementedError("QdrantAdapter: spec §4 (Vector Store) — Phase 11+")

    def list_experiences(self, space_id: str) -> list[ExperienceRecord]:
        raise NotImplementedError("QdrantAdapter: spec §4 (Vector Store) — Phase 11+")

    def query_similar_experiences(
        self, query: ExperienceQuery
    ) -> list[ExperienceRecord]:
        raise NotImplementedError("QdrantAdapter: spec §4 (Vector Store) — Phase 11+")

    def store_knowledge(
        self, entry: KnowledgeEntry, auth: PromotionAuthorization
    ) -> None:
        raise NotImplementedError("QdrantAdapter: spec §4 (Vector Store) — Phase 11+")

    def get_global_knowledge(self, knowledge_id: str) -> KnowledgeEntry | None:
        raise NotImplementedError("QdrantAdapter: spec §4 (Vector Store) — Phase 11+")

