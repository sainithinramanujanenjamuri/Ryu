"""Unit tests for SpaceMemory models and cryptographic authorization tokens.

spec §4 (Space Memory), MEM-002, ADR-0034, ADR-0035 — Phase 10
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from core.space.memory_protocol import (
    ExperienceRecord,
    KnowledgeEntry,
    PromotionAuthorization,
    compute_promotion_signature,
    verify_promotion_authorization,
)


def test_experience_record_creation_valid() -> None:
    now = datetime.now(timezone.utc)
    rec = ExperienceRecord(
        experience_id="exp-123",
        space_id="space-a",
        situation={"task": "download file"},
        action={"capability": "net.http_get"},
        outcome="Failed with 404 Not Found",
        counterfactual="Verify the URL endpoint before requesting or use mirror",
        applicable_context={"domain": "downloads"},
        stored_at=now,
    )
    assert rec.experience_id == "exp-123"
    assert rec.space_id == "space-a"
    assert rec.counterfactual.startswith("Verify")


def test_experience_record_missing_counterfactual_raises_value_error() -> None:
    now = datetime.now(timezone.utc)
    with pytest.raises(ValueError, match="counterfactual must not be empty"):
        ExperienceRecord(
            experience_id="exp-124",
            space_id="space-a",
            situation={"task": "download file"},
            action={"capability": "net.http_get"},
            outcome="Failed",
            counterfactual="",  # Empty string rejected
            applicable_context={},
            stored_at=now,
        )


def test_experience_record_whitespace_counterfactual_raises_value_error() -> None:
    now = datetime.now(timezone.utc)
    with pytest.raises(ValueError, match="counterfactual must not be empty"):
        ExperienceRecord(
            experience_id="exp-125",
            space_id="space-a",
            situation={},
            action={},
            outcome="Failed",
            counterfactual="   ",
            applicable_context={},
            stored_at=now,
        )


def test_experience_record_missing_space_id_raises_value_error() -> None:
    now = datetime.now(timezone.utc)
    with pytest.raises(ValueError, match="space_id must not be empty"):
        ExperienceRecord(
            experience_id="exp-126",
            space_id="",
            situation={},
            action={},
            outcome="Success",
            counterfactual="Action was optimal",
            applicable_context={},
            stored_at=now,
        )


def test_knowledge_entry_validation() -> None:
    now = datetime.now(timezone.utc)
    entry = KnowledgeEntry(
        knowledge_id="know-001",
        source_space_id="space-a",
        content={"lesson": "Use mirrors"},
        promoted_by="operator-1",
        promotion_pulse_id="pulse-promo-001",
        global_version=1,
        promoted_at=now,
    )
    assert entry.knowledge_id == "know-001"
    assert entry.promoted_by == "operator-1"

    with pytest.raises(ValueError, match="promoted_by must not be empty"):
        KnowledgeEntry(
            knowledge_id="know-002",
            source_space_id="space-a",
            content={},
            promoted_by="",
            promotion_pulse_id="pulse-1",
            global_version=1,
            promoted_at=now,
        )


def test_promotion_signature_and_verification() -> None:
    signing_key = b"test-kernel-key-12345"
    now_ts = 1720000000.0

    sig = compute_promotion_signature(
        signing_key=signing_key,
        promotion_id="promo-1",
        knowledge_id="know-1",
        source_space_id="space-a",
        approver_id="human-approver",
        approval_request_id="appr-req-1",
        issued_at=now_ts,
    )

    auth = PromotionAuthorization(
        promotion_id="promo-1",
        knowledge_id="know-1",
        source_space_id="space-a",
        approver_id="human-approver",
        approval_request_id="appr-req-1",
        signature=sig,
        issued_at=now_ts,
    )

    # Valid signature passes
    assert verify_promotion_authorization(auth, signing_key) is True

    # Tampered key fails
    assert verify_promotion_authorization(auth, b"wrong-key") is False

    # Tampered promotion_id fails
    auth_tampered = PromotionAuthorization(
        promotion_id="promo-2-tampered",
        knowledge_id="know-1",
        source_space_id="space-a",
        approver_id="human-approver",
        approval_request_id="appr-req-1",
        signature=sig,
        issued_at=now_ts,
    )
    assert verify_promotion_authorization(auth_tampered, signing_key) is False

