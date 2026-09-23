"""Unit tests for InMemoryMemoryAdapter: isolation, queries, and capability tokens.

spec §4 (Space Memory), MEM-001, MEM-003, MEM-005, ADR-0033..0035 — Phase 10
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

import pytest

from core.space.memory_protocol import (
    ExperienceQuery,
    ExperienceRecord,
    KnowledgeEntry,
    PromotionAuthorization,
    SpaceIsolationViolation,
    compute_promotion_signature,
)
from memory.adapters.in_memory import InMemoryMemoryAdapter


def _create_record(
    space_id: str, exp_id: str, cap: str = "general.compute", outcome: str = "success"
) -> ExperienceRecord:
    return ExperienceRecord(
        experience_id=exp_id,
        space_id=space_id,
        situation={"task": f"test-{exp_id}"},
        action={"capability": cap},
        outcome=outcome,
        counterfactual="Better strategy would be alternative capability",
        applicable_context={"tag": "unit-test"},
        stored_at=datetime.now(timezone.utc),
    )


def test_in_memory_store_and_get() -> None:
    adapter = InMemoryMemoryAdapter()
    rec = _create_record("space-1", "exp-1")
    exp_id = adapter.store_experience(rec)
    assert exp_id == "exp-1"

    fetched = adapter.get_experience("space-1", "exp-1")
    assert fetched is not None
    assert fetched.experience_id == "exp-1"
    assert fetched.space_id == "space-1"


def test_in_memory_space_isolation() -> None:
    adapter = InMemoryMemoryAdapter()
    adapter.store_experience(_create_record("space-a", "exp-a1"))
    adapter.store_experience(_create_record("space-b", "exp-b1"))

    # Space A cannot see Space B's record
    assert adapter.get_experience("space-a", "exp-b1") is None
    assert adapter.get_experience("space-b", "exp-a1") is None

    # List experiences strictly scoped
    a_records = adapter.list_experiences("space-a")
    assert len(a_records) == 1
    assert a_records[0].experience_id == "exp-a1"

    b_records = adapter.list_experiences("space-b")
    assert len(b_records) == 1
    assert b_records[0].experience_id == "exp-b1"


def test_in_memory_empty_space_id_raises_violation() -> None:
    adapter = InMemoryMemoryAdapter()
    with pytest.raises(SpaceIsolationViolation):
        adapter.get_experience("", "exp-1")

    with pytest.raises(SpaceIsolationViolation):
        adapter.list_experiences("")


def test_in_memory_query_similar_experiences() -> None:
    adapter = InMemoryMemoryAdapter()
    adapter.store_experience(
        _create_record("space-1", "exp-net", cap="net.http", outcome="404 error")
    )
    adapter.store_experience(
        _create_record("space-1", "exp-fs", cap="fs.read", outcome="ok")
    )
    adapter.store_experience(
        _create_record(
            "space-2", "exp-net-foreign", cap="net.http", outcome="404 error"
        )
    )

    query = ExperienceQuery(
        space_id="space-1",
        situation_hint={"capability": "net.http"},
        limit=5,
    )
    results = adapter.query_similar_experiences(query)
    assert len(results) >= 1
    assert results[0].experience_id == "exp-net"
    # Never returns results from space-2
    assert all(r.space_id == "space-1" for r in results)


def test_in_memory_store_knowledge_enforces_auth_token() -> None:
    key = b"signing-key-test-space-a"
    adapter = InMemoryMemoryAdapter(signing_key=key)

    now_dt = datetime.now(timezone.utc)
    entry = KnowledgeEntry(
        knowledge_id="know-100",
        source_space_id="space-a",
        content={"rule": "always check mirror"},
        promoted_by="approver-1",
        promotion_pulse_id="pulse-promo-1",
        global_version=1,
        promoted_at=now_dt,
    )

    # 1. Direct call without PromotionAuthorization raises PermissionError
    with pytest.raises(PermissionError, match="PromotionAuthorization capability token required"):
        adapter.store_knowledge(entry, "invalid-token-string")  # type: ignore[arg-type]

    # 2. Forged signature raises PermissionError
    forged_auth = PromotionAuthorization(
        promotion_id="promo-1",
        knowledge_id="know-100",
        source_space_id="space-a",
        approver_id="approver-1",
        approval_request_id="req-1",
        signature="bad-signature-hex",
        issued_at=time.time(),
    )
    with pytest.raises(PermissionError, match="signature verification failed"):
        adapter.store_knowledge(entry, forged_auth)

    # 3. Valid authorization succeeds
    now_ts = time.time()
    sig = compute_promotion_signature(
        signing_key=key,
        promotion_id="promo-1",
        knowledge_id="know-100",
        source_space_id="space-a",
        approver_id="approver-1",
        approval_request_id="req-1",
        issued_at=now_ts,
    )
    valid_auth = PromotionAuthorization(
        promotion_id="promo-1",
        knowledge_id="know-100",
        source_space_id="space-a",
        approver_id="approver-1",
        approval_request_id="req-1",
        signature=sig,
        issued_at=now_ts,
    )
    adapter.store_knowledge(entry, valid_auth)
    assert adapter.get_global_knowledge("know-100") is not None

    # 4. Replay raises PermissionError
    with pytest.raises(PermissionError, match="Replayed promotion authorization"):
        adapter.store_knowledge(entry, valid_auth)

