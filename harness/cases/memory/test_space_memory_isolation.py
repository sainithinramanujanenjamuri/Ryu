"""Harness Case: MEM-001, ARC-004 Space-local memory and Law 4 boundary.

Acceptance Criterion:
Knowledge begins in the owning Space (Law 4). A Space caller cannot retrieve or query
another Space's experiences. Knowledge never appears globally without an approved promotion.

spec §4 (Space Memory), SCCA Law 4, CONTRACT_MATRIX MEM-001, ARC-004 — Phase 10
"""

from __future__ import annotations

from datetime import datetime, timezone

from core.space.memory_protocol import ExperienceQuery, ExperienceRecord
from memory.adapters.in_memory import InMemoryMemoryAdapter


def _make_exp(space_id: str, exp_id: str) -> ExperienceRecord:
    return ExperienceRecord(
        experience_id=exp_id,
        space_id=space_id,
        situation={"scenario": f"iso-{space_id}"},
        action={"capability": "general.compute"},
        outcome="Completed",
        counterfactual="No changes needed",
        applicable_context={"space": space_id},
        stored_at=datetime.now(timezone.utc),
    )


def test_space_a_cannot_read_space_b_experience() -> None:
    """MEM-001: Space A cannot access Space B's experiences."""
    store = InMemoryMemoryAdapter()
    store.store_experience(_make_exp("space-alpha", "exp-alpha-1"))
    store.store_experience(_make_exp("space-beta", "exp-beta-1"))

    # Direct get across space boundary returns None
    assert store.get_experience("space-alpha", "exp-beta-1") is None
    assert store.get_experience("space-beta", "exp-alpha-1") is None

    # List strictly isolated
    alpha_list = store.list_experiences("space-alpha")
    assert len(alpha_list) == 1
    assert alpha_list[0].experience_id == "exp-alpha-1"

    beta_list = store.list_experiences("space-beta")
    assert len(beta_list) == 1
    assert beta_list[0].experience_id == "exp-beta-1"


def test_cross_space_query_isolation() -> None:
    """MEM-001: Querying similar experiences never leaks records from other Spaces."""
    store = InMemoryMemoryAdapter()
    store.store_experience(_make_exp("space-red", "exp-red-1"))
    store.store_experience(_make_exp("space-blue", "exp-blue-1"))

    query_red = ExperienceQuery(
        space_id="space-red",
        situation_hint={"scenario": "iso"},
        limit=10,
    )
    red_results = store.query_similar_experiences(query_red)
    assert len(red_results) == 1
    assert red_results[0].experience_id == "exp-red-1"
    assert all(r.space_id == "space-red" for r in red_results)


def test_global_knowledge_requires_promotion() -> None:
    """ARC-004: Stored experiences never appear globally without authorized promotion."""
    store = InMemoryMemoryAdapter()
    store.store_experience(_make_exp("space-local", "exp-local-1"))

    # Global knowledge is empty by default; local experience does not leak to global scope
    assert store.get_global_knowledge("know-exp-local-1") is None
    assert store.get_global_knowledge("exp-local-1") is None

