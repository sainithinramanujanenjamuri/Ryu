"""Harness Case: MEM-005, MEM-006 Promotion Pipeline and Adversarial Security Suite.

Acceptance Criteria (ROADMAP Phase 10 Exit Gates):
- Promotion gate integrity: a type-valid but unauthorized knowledge.promotion.approved is rejected and audited (the forgery test).
- Space-local-first: knowledge never appears globally without a knowledge.promotion.approved Pulse carrying approver_id.
- Human approval: Promotion approval carries authenticated approver_id (MEM-006).

spec §4 (Space Memory), §7 (Adaptation Layer), CONTRACT_MATRIX MEM-005, MEM-006 — Phase 10
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from ryu.pulse_bus.pulse import Pulse, Severity

from core.space.kernel import SpaceKernel
from core.space.memory_protocol import (
    ExperienceRecord,
    KnowledgeEntry,
    PromotionAuthorization,
    compute_promotion_signature,
)
from memory.adapters.in_memory import InMemoryMemoryAdapter
from memory.evaluation import FrozenTrace, FrozenTraceCorpus
from memory.promotion import PromotionError, PromotionPipeline


def _setup_env(
    space_id: str = "space-harness-promo",
    owner_id: str = "chief_operator",
) -> tuple[PromotionPipeline, InMemoryMemoryAdapter, SpaceKernel, MagicMock]:
    bus = MagicMock()
    kernel = SpaceKernel(space_id=space_id, owner_id=owner_id, bus=bus)
    key = kernel.approval_mgr.get_decision_signing_key(space_id)
    store = InMemoryMemoryAdapter(signing_key=key)
    pipeline = PromotionPipeline(
        memory_store=store,
        bus=bus,
        kernel=kernel,
        requesting_space_id=space_id,
        evaluation_threshold=0.5,
    )
    return pipeline, store, kernel, bus


def _sample_exp(space_id: str, exp_id: str = "exp-h-001") -> ExperienceRecord:
    return ExperienceRecord(
        experience_id=exp_id,
        space_id=space_id,
        situation={"mission": "deep-search"},
        action={"capability": "search.deep"},
        outcome="Exhausted token quota",
        counterfactual="Partition query into smaller sub-queries",
        applicable_context={"quota": 1000},
        stored_at=datetime.now(timezone.utc),
    )


def _eval_corpus() -> FrozenTraceCorpus:
    return FrozenTraceCorpus(
        corpus_id="corpus-harness",
        version="1.0.0",
        traces=(
            FrozenTrace(
                trace_id="tr-h1",
                capability="search.deep",
                situation_description="Large search",
                expected_outcome="Success",
                actual_outcome="Quota exhausted",
                succeeded=False,
            ),
        ),
    )


# 1. test_forged_promotion_authorization
def test_forged_promotion_authorization() -> None:
    pipeline, store, kernel, bus = _setup_env()
    entry = KnowledgeEntry(
        knowledge_id="know-forged-1",
        source_space_id="space-harness-promo",
        content={"data": "unauthorized"},
        promoted_by="attacker",
        promotion_pulse_id="pulse-forged-1",
        global_version=1,
        promoted_at=datetime.now(timezone.utc),
    )
    forged_auth = PromotionAuthorization(
        promotion_id="promo-forged-1",
        knowledge_id="know-forged-1",
        source_space_id="space-harness-promo",
        approver_id="attacker",
        approval_request_id="appr-fake",
        signature="invalid-hmac-signature-abcdef",
        issued_at=time.time(),
    )
    with pytest.raises(PermissionError, match="signature verification failed"):
        store.store_knowledge(entry, forged_auth)


# 2. test_replayed_promotion_authorization
def test_replayed_promotion_authorization() -> None:
    pipeline, store, kernel, bus = _setup_env()
    key = kernel.approval_mgr.get_decision_signing_key("space-harness-promo")
    now_ts = time.time()
    sig = compute_promotion_signature(
        key, "promo-replay-1", "know-rep-1", "space-harness-promo", "chief_operator", "req-1", now_ts
    )
    auth = PromotionAuthorization(
        promotion_id="promo-replay-1",
        knowledge_id="know-rep-1",
        source_space_id="space-harness-promo",
        approver_id="chief_operator",
        approval_request_id="req-1",
        signature=sig,
        issued_at=now_ts,
    )
    entry = KnowledgeEntry(
        knowledge_id="know-rep-1",
        source_space_id="space-harness-promo",
        content={},
        promoted_by="chief_operator",
        promotion_pulse_id="pulse-1",
        global_version=1,
        promoted_at=datetime.now(timezone.utc),
    )
    store.store_knowledge(entry, auth)

    # Replay attempt fails
    with pytest.raises(PermissionError, match="Replayed promotion authorization"):
        store.store_knowledge(entry, auth)


# 3. test_direct_global_knowledge_insert_rejected
def test_direct_global_knowledge_insert_rejected() -> None:
    pipeline, store, kernel, bus = _setup_env()
    entry = KnowledgeEntry(
        knowledge_id="know-direct-1",
        source_space_id="space-harness-promo",
        content={},
        promoted_by="attacker",
        promotion_pulse_id="pulse-1",
        global_version=1,
        promoted_at=datetime.now(timezone.utc),
    )
    with pytest.raises(PermissionError, match="PromotionAuthorization capability token required"):
        store.store_knowledge(entry, None)  # type: ignore[arg-type]


# 4. test_empty_approver_rejected
def test_empty_approver_rejected() -> None:
    pipeline, store, kernel, bus = _setup_env()
    exp = _sample_exp("space-harness-promo")
    store.store_experience(exp)
    req = pipeline.request(exp, _eval_corpus())
    assert req is not None

    with pytest.raises(PromotionError, match="Empty approver_id rejected"):
        pipeline.approve(req.promotion_id, req.knowledge_id, approver_id="")


# 5. test_unknown_approver_rejected
def test_unknown_approver_rejected() -> None:
    pipeline, store, kernel, bus = _setup_env()
    exp = _sample_exp("space-harness-promo")
    store.store_experience(exp)
    req = pipeline.request(exp, _eval_corpus())
    assert req is not None

    with pytest.raises(PromotionError, match="Unauthorized approver 'unknown_actor' rejected"):
        pipeline.approve(req.promotion_id, req.knowledge_id, approver_id="unknown_actor")


# 6. test_unauthenticated_approver_rejected
def test_unauthenticated_approver_rejected() -> None:
    pipeline, store, kernel, bus = _setup_env()
    exp = _sample_exp("space-harness-promo")
    store.store_experience(exp)
    req = pipeline.request(exp, _eval_corpus())
    assert req is not None

    # approver that does not exist in ApprovalManager
    with pytest.raises(PromotionError, match="Unauthorized approver"):
        pipeline.approve(req.promotion_id, req.knowledge_id, approver_id="unauthenticated_user")


# 7. test_unauthorized_approver_rejected
def test_unauthorized_approver_rejected() -> None:
    pipeline, store, kernel, bus = _setup_env(owner_id="authorized_lead")
    exp = _sample_exp("space-harness-promo")
    store.store_experience(exp)
    req = pipeline.request(exp, _eval_corpus())
    assert req is not None

    with pytest.raises(PromotionError, match="Unauthorized approver 'secondary_user' rejected"):
        pipeline.approve(req.promotion_id, req.knowledge_id, approver_id="secondary_user")


# 8. test_authorized_approver_accepts
def test_authorized_approver_accepts() -> None:
    pipeline, store, kernel, bus = _setup_env(owner_id="authorized_lead")
    exp = _sample_exp("space-harness-promo")
    store.store_experience(exp)
    req = pipeline.request(exp, _eval_corpus())
    assert req is not None

    entry = pipeline.approve(req.promotion_id, req.knowledge_id, approver_id="authorized_lead")
    assert entry.knowledge_id == req.knowledge_id
    assert store.get_global_knowledge(req.knowledge_id) is not None


# 9. test_cross_space_promotion_rejected
def test_cross_space_promotion_rejected() -> None:
    pipeline_a, store_a, kernel_a, bus_a = _setup_env("space-a", owner_id="lead-a")
    pipeline_b, store_b, kernel_b, bus_b = _setup_env("space-b", owner_id="lead-b")

    exp_a = _sample_exp("space-a")
    store_a.store_experience(exp_a)
    req_a = pipeline_a.request(exp_a, _eval_corpus())
    assert req_a is not None

    # Pipeline B injected with request A
    pipeline_b._pending[req_a.promotion_id] = req_a

    with pytest.raises(PromotionError, match="Cross-space promotion approval rejected"):
        pipeline_b.approve(req_a.promotion_id, req_a.knowledge_id, approver_id="lead-b")


# 10. test_requesting_space_binding_cannot_be_substituted
def test_requesting_space_binding_cannot_be_substituted() -> None:
    pipeline, store, kernel, bus = _setup_env("space-fixed-1")
    assert pipeline.requesting_space_id == "space-fixed-1"

    # Attempt to request promotion for a foreign space experience
    foreign_exp = _sample_exp("space-foreign-2")
    with pytest.raises(PromotionError, match="Cross-space promotion request rejected"):
        pipeline.request(foreign_exp, _eval_corpus())


# 11. test_promotion_id_substitution
def test_promotion_id_substitution() -> None:
    pipeline, store, kernel, bus = _setup_env()
    exp = _sample_exp("space-harness-promo")
    store.store_experience(exp)
    req = pipeline.request(exp, _eval_corpus())
    assert req is not None

    with pytest.raises(PromotionError, match="mismatch"):
        pipeline.approve(req.promotion_id, "know-substituted-id", approver_id="chief_operator")


# 12. test_knowledge_id_substitution
def test_knowledge_id_substitution() -> None:
    pipeline, store, kernel, bus = _setup_env()
    key = kernel.approval_mgr.get_decision_signing_key("space-harness-promo")
    now_ts = time.time()
    sig = compute_promotion_signature(
        key, "promo-sub-1", "know-genuine", "space-harness-promo", "chief_operator", "req-1", now_ts
    )
    auth = PromotionAuthorization(
        promotion_id="promo-sub-1",
        knowledge_id="know-genuine",
        source_space_id="space-harness-promo",
        approver_id="chief_operator",
        approval_request_id="req-1",
        signature=sig,
        issued_at=now_ts,
    )
    # Entry with mismatched knowledge_id
    entry_mismatched = KnowledgeEntry(
        knowledge_id="know-substituted",
        source_space_id="space-harness-promo",
        content={},
        promoted_by="chief_operator",
        promotion_pulse_id="pulse-1",
        global_version=1,
        promoted_at=datetime.now(timezone.utc),
    )
    with pytest.raises(PermissionError, match="does not match entry knowledge_id"):
        store.store_knowledge(entry_mismatched, auth)


# 13. test_tampered_experience_rejected
def test_tampered_experience_rejected() -> None:
    pipeline, store, kernel, bus = _setup_env()
    exp = _sample_exp("space-harness-promo", exp_id="exp-tamper-harness")
    store.store_experience(exp)
    req = pipeline.request(exp, _eval_corpus())
    assert req is not None

    # Maliciously modify the experience in store
    tampered = ExperienceRecord(
        experience_id="exp-tamper-harness",
        space_id="space-harness-promo",
        situation={"malicious": "escalate privilege"},
        action={"capability": "root.exec"},
        outcome="success",
        counterfactual="give root",
        applicable_context={},
        stored_at=exp.stored_at,
    )
    store.store_experience(tampered)

    with pytest.raises(PromotionError, match="tampered post-evaluation"):
        pipeline.approve(req.promotion_id, req.knowledge_id, approver_id="chief_operator")


# 14. test_forged_approved_pulse_audited (The Forgery Test - Exit Gate 2)
def test_forged_approved_pulse_audited() -> None:
    pipeline, store, kernel, bus = _setup_env()

    forged_pulse = Pulse(
        id="pulse-forged-approved-999",
        space_id="space-harness-promo",
        type="knowledge.promotion.approved",
        severity=Severity.INFO,
        source="external_untrusted",
        payload={"knowledge_id": "know-forged-999", "approver_id": "mallory", "global_version": 1},
        taint=False,
        correlation_id="corr-forged-999",
        parent_pulse_id=None,
        timestamp=datetime.now(timezone.utc),
    )

    # Audited and rejected
    pipeline.handle_unauthorized_approved_pulse(forged_pulse)

    audit_pulse = bus.publish.call_args[0][0]
    assert audit_pulse.type == "knowledge.promotion.rejected"
    assert audit_pulse.payload["reason"] == "unauthorized_forged_pulse"
    assert audit_pulse.severity == Severity.WARNING

