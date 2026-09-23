"""Unit tests for PromotionPipeline: Human gate authentication, Space binding, and forgery resistance.

spec §4 (Space Memory), §7 (Adaptation Layer), MEM-005, MEM-006, ADR-0035 — Phase 10
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from core.space.kernel import SpaceKernel
from core.space.memory_protocol import ExperienceRecord
from memory.adapters.in_memory import InMemoryMemoryAdapter
from memory.evaluation import FrozenTrace, FrozenTraceCorpus
from memory.promotion import PromotionError, PromotionPipeline


def _setup_pipeline(
    space_id: str = "space-p1",
    owner_id: str = "human_operator",
    threshold: float = 0.5,
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
        evaluation_threshold=threshold,
    )
    return pipeline, store, kernel, bus


def _create_exp(space_id: str, exp_id: str = "exp-promo-1", cap: str = "net.http") -> ExperienceRecord:
    return ExperienceRecord(
        experience_id=exp_id,
        space_id=space_id,
        situation={"url": "https://service.internal"},
        action={"capability": cap},
        outcome="Connection timeout",
        counterfactual="Switch to secondary cluster gateway",
        applicable_context={"retries": 3},
        stored_at=datetime.now(timezone.utc),
    )


def _matching_corpus() -> FrozenTraceCorpus:
    return FrozenTraceCorpus(
        corpus_id="corpus-p",
        version="1.0.0",
        traces=(
            FrozenTrace(
                trace_id="tr-p1",
                capability="net.http",
                situation_description="Connect",
                expected_outcome="200 OK",
                actual_outcome="timeout",
                succeeded=False,
            ),
        ),
    )


def test_promotion_request_happy_path() -> None:
    pipeline, store, kernel, bus = _setup_pipeline("space-1")
    exp = _create_exp("space-1")
    store.store_experience(exp)

    req = pipeline.request(exp, _matching_corpus())
    assert req is not None
    assert req.source_space_id == "space-1"
    assert req.experience_id == "exp-promo-1"

    # Pulse published: knowledge.promotion.requested
    pulse_types = [c.args[0].type for c in bus.publish.call_args_list]
    assert "knowledge.promotion.requested" in pulse_types


def test_promotion_request_below_threshold_returns_none() -> None:
    pipeline, store, kernel, bus = _setup_pipeline("space-1", threshold=0.99)
    exp = _create_exp("space-1", cap="unknown.capability")
    store.store_experience(exp)

    req = pipeline.request(exp, _matching_corpus())
    assert req is None
    # No pulse emitted
    pulse_types = [c.args[0].type for c in bus.publish.call_args_list]
    assert "knowledge.promotion.requested" not in pulse_types


def test_promotion_request_cross_space_rejected() -> None:
    pipeline, store, kernel, bus = _setup_pipeline("space-1")
    # Experience belongs to space-2, but pipeline is space-1
    exp_foreign = _create_exp("space-2")
    with pytest.raises(PromotionError, match="Cross-space promotion request rejected"):
        pipeline.request(exp_foreign, _matching_corpus())


def test_promotion_approve_happy_path() -> None:
    pipeline, store, kernel, bus = _setup_pipeline("space-1", owner_id="operator-alice")
    exp = _create_exp("space-1")
    store.store_experience(exp)

    req = pipeline.request(exp, _matching_corpus())
    assert req is not None

    entry = pipeline.approve(
        promotion_id=req.promotion_id,
        knowledge_id=req.knowledge_id,
        approver_id="operator-alice",
    )

    assert entry.knowledge_id == req.knowledge_id
    assert entry.promoted_by == "operator-alice"

    # Global knowledge now exists
    glob = store.get_global_knowledge(req.knowledge_id)
    assert glob is not None
    assert glob.promoted_by == "operator-alice"

    # knowledge.promotion.approved Pulse was published
    pulse_types = [c.args[0].type for c in bus.publish.call_args_list]
    assert "knowledge.promotion.approved" in pulse_types


def test_promotion_approve_rejects_empty_approver() -> None:
    pipeline, store, kernel, bus = _setup_pipeline("space-1")
    exp = _create_exp("space-1")
    store.store_experience(exp)
    req = pipeline.request(exp, _matching_corpus())
    assert req is not None

    with pytest.raises(PromotionError, match="Empty approver_id rejected"):
        pipeline.approve(req.promotion_id, req.knowledge_id, approver_id="")


def test_promotion_approve_rejects_unknown_or_unauthorized_approver() -> None:
    pipeline, store, kernel, bus = _setup_pipeline("space-1", owner_id="authorized-bob")
    exp = _create_exp("space-1")
    store.store_experience(exp)
    req = pipeline.request(exp, _matching_corpus())
    assert req is not None

    # Mallory is not the space's designated approver
    with pytest.raises(PromotionError, match="Unauthorized approver 'mallory' rejected"):
        pipeline.approve(req.promotion_id, req.knowledge_id, approver_id="mallory")

    # Global knowledge unchanged
    assert store.get_global_knowledge(req.knowledge_id) is None


def test_promotion_approve_rejects_replayed_approval() -> None:
    pipeline, store, kernel, bus = _setup_pipeline("space-1", owner_id="operator-1")
    exp = _create_exp("space-1")
    store.store_experience(exp)
    req = pipeline.request(exp, _matching_corpus())
    assert req is not None

    pipeline.approve(req.promotion_id, req.knowledge_id, approver_id="operator-1")

    # Second approval attempt on the same promotion_id raises PromotionError
    with pytest.raises(PromotionError, match="Replayed promotion approval rejected"):
        pipeline.approve(req.promotion_id, req.knowledge_id, approver_id="operator-1")


def test_promotion_approve_rejects_mismatched_knowledge_id() -> None:
    pipeline, store, kernel, bus = _setup_pipeline("space-1", owner_id="operator-1")
    exp = _create_exp("space-1")
    store.store_experience(exp)
    req = pipeline.request(exp, _matching_corpus())
    assert req is not None

    with pytest.raises(PromotionError, match="mismatch"):
        pipeline.approve(req.promotion_id, "wrong-knowledge-id", approver_id="operator-1")


def test_promotion_approve_rejects_cross_space() -> None:
    pipeline_a, store_a, kernel_a, bus_a = _setup_pipeline("space-a", owner_id="admin-a")
    pipeline_b, store_b, kernel_b, bus_b = _setup_pipeline("space-b", owner_id="admin-b")

    exp_a = _create_exp("space-a")
    store_a.store_experience(exp_a)

    req_a = pipeline_a.request(exp_a, _matching_corpus())
    assert req_a is not None

    # Inject req_a into pipeline_b._pending maliciously
    pipeline_b._pending[req_a.promotion_id] = req_a

    # Pipeline B must reject because req.source_space_id ('space-a') != pipeline_b._requesting_space_id ('space-b')
    with pytest.raises(PromotionError, match="Cross-space promotion approval rejected"):
        pipeline_b.approve(req_a.promotion_id, req_a.knowledge_id, approver_id="admin-b")


def test_promotion_approve_rejects_tampered_experience() -> None:
    pipeline, store, kernel, bus = _setup_pipeline("space-1", owner_id="operator-1")
    exp = _create_exp("space-1", exp_id="exp-tamper")
    store.store_experience(exp)
    req = pipeline.request(exp, _matching_corpus())
    assert req is not None

    # Tamper with the experience record in storage before approve() is called
    tampered_exp = ExperienceRecord(
        experience_id="exp-tamper",
        space_id="space-1",
        situation={"malicious": "injected payload"},
        action={"capability": "terminal.exec"},
        outcome="tampered",
        counterfactual="tampered counterfactual",
        applicable_context={},
        stored_at=exp.stored_at,
    )
    store.store_experience(tampered_exp)

    # Approve must detect hash mismatch and reject
    with pytest.raises(PromotionError, match="tampered post-evaluation"):
        pipeline.approve(req.promotion_id, req.knowledge_id, approver_id="operator-1")


def test_promotion_reject_transitions_state_and_emits_pulse() -> None:
    pipeline, store, kernel, bus = _setup_pipeline("space-1", owner_id="operator-1")
    exp = _create_exp("space-1")
    store.store_experience(exp)
    req = pipeline.request(exp, _matching_corpus())
    assert req is not None

    pipeline.reject(req.promotion_id, req.knowledge_id, approver_id="operator-1", reason="Low confidence")

    assert req.promotion_id not in pipeline._pending
    assert req.promotion_id in pipeline._used_ids

    # Pulse published: knowledge.promotion.rejected
    pulse_types = [c.args[0].type for c in bus.publish.call_args_list]
    assert "knowledge.promotion.rejected" in pulse_types


def test_handle_unauthorized_approved_pulse_emits_audit() -> None:
    pipeline, store, kernel, bus = _setup_pipeline("space-1")

    forged_pulse = MagicMock()
    forged_pulse.id = "pulse-forged-999"
    forged_pulse.space_id = "space-1"
    forged_pulse.correlation_id = "corr-forged"
    forged_pulse.payload = {"knowledge_id": "know-forged", "approver_id": "mallory"}

    pipeline.handle_unauthorized_approved_pulse(forged_pulse)

    # Verifies that an audit rejection pulse was published
    published_pulse = bus.publish.call_args[0][0]
    assert published_pulse.type == "knowledge.promotion.rejected"
    assert published_pulse.payload["reason"] == "unauthorized_forged_pulse"
    assert published_pulse.source == "promotion_pipeline_audit"

