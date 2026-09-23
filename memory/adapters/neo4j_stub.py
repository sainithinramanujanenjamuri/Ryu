"""Neo4j graph store adapter — extension boundary only.

InMemory and PostgreSQL are the Phase 10 authoritative implementations.
Neo4j is an extension boundary for future knowledge-graph capability.
Running a Neo4j instance is NOT required for the Phase 10 gate.
This stub verifies the architecture can accommodate Neo4j without interface changes (ADR-0033).

spec §4 (Knowledge Graph), §15 (Storage Layer) — Phase 11+
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


class Neo4jAdapterStub(SpaceMemoryProtocol):
    """Extension stub for future Neo4j graph database integration."""

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs

    def store_experience(self, record: ExperienceRecord) -> str:
        raise NotImplementedError("Neo4jAdapter: spec §4 (Knowledge Graph) — Phase 11+")

    def get_experience(
        self, space_id: str, experience_id: str
    ) -> ExperienceRecord | None:
        raise NotImplementedError("Neo4jAdapter: spec §4 (Knowledge Graph) — Phase 11+")

    def list_experiences(self, space_id: str) -> list[ExperienceRecord]:
        raise NotImplementedError("Neo4jAdapter: spec §4 (Knowledge Graph) — Phase 11+")

    def query_similar_experiences(
        self, query: ExperienceQuery
    ) -> list[ExperienceRecord]:
        raise NotImplementedError("Neo4jAdapter: spec §4 (Knowledge Graph) — Phase 11+")

    def store_knowledge(
        self, entry: KnowledgeEntry, auth: PromotionAuthorization
    ) -> None:
        raise NotImplementedError("Neo4jAdapter: spec §4 (Knowledge Graph) — Phase 11+")

    def get_global_knowledge(self, knowledge_id: str) -> KnowledgeEntry | None:
        raise NotImplementedError("Neo4jAdapter: spec §4 (Knowledge Graph) — Phase 11+")

