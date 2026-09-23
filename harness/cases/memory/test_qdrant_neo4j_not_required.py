"""Harness Case: Qdrant and Neo4j Extension Boundaries (ADR-0033).

Acceptance Criterion:
InMemory and PostgreSQL are the Phase 10 authoritative implementations.
Qdrant and Neo4j are extension boundaries only. Running them is NOT required for the Phase 10 gate.
Stubs raise typed NotImplementedError identifying the future specification.

ADR-0033 — Phase 10
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from core.space.memory_protocol import (
    ExperienceQuery,
    ExperienceRecord,
    KnowledgeEntry,
    PromotionAuthorization,
)
from memory.adapters.neo4j_stub import Neo4jAdapterStub
from memory.adapters.qdrant_stub import QdrantAdapterStub


def _sample_exp() -> ExperienceRecord:
    return ExperienceRecord(
        experience_id="exp-stub-1",
        space_id="space-stub",
        situation={},
        action={"capability": "general.compute"},
        outcome="ok",
        counterfactual="none",
        applicable_context={},
        stored_at=datetime.now(timezone.utc),
    )


def test_qdrant_stub_raises_not_implemented() -> None:
    stub = QdrantAdapterStub()
    exp = _sample_exp()

    with pytest.raises(NotImplementedError, match="Vector Store"):
        stub.store_experience(exp)

    with pytest.raises(NotImplementedError, match="Vector Store"):
        stub.get_experience("space-stub", "exp-stub-1")

    with pytest.raises(NotImplementedError, match="Vector Store"):
        stub.list_experiences("space-stub")

    with pytest.raises(NotImplementedError, match="Vector Store"):
        stub.query_similar_experiences(ExperienceQuery(space_id="space-stub", situation_hint={}))

    with pytest.raises(NotImplementedError, match="Vector Store"):
        stub.get_global_knowledge("know-1")


def test_neo4j_stub_raises_not_implemented() -> None:
    stub = Neo4jAdapterStub()
    exp = _sample_exp()

    with pytest.raises(NotImplementedError, match="Knowledge Graph"):
        stub.store_experience(exp)

    with pytest.raises(NotImplementedError, match="Knowledge Graph"):
        stub.get_experience("space-stub", "exp-stub-1")

    with pytest.raises(NotImplementedError, match="Knowledge Graph"):
        stub.list_experiences("space-stub")

    with pytest.raises(NotImplementedError, match="Knowledge Graph"):
        stub.query_similar_experiences(ExperienceQuery(space_id="space-stub", situation_hint={}))

    with pytest.raises(NotImplementedError, match="Knowledge Graph"):
        stub.get_global_knowledge("know-1")

