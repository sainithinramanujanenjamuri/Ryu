"""Harness specification tests for Signed Skill & Tool Supply Chain.

Contracts verified:
- REG-001: Explicit version identity
- REG-002: Content hash binding (SHA-256)
- REG-003: Signature verification (reject unsigned/invalid)
- REG-004: Immutable risk tier bound to hash; reclassification requires human approval
- REG-005: Version pinning (@latest rejected)
- SKILL-001: Skill registration and signature verification
- SKILL-002: Unsigned artifact rejection
- SKILL-003: Version pinning enforcement

spec §9, §16, docs/CONTRACT_MATRIX.md REG-001..REG-005, ROADMAP Phase 9, ADR-0027
"""

import pytest

from skills.model import (
    RiskTier,
    SkillRegistration,
    ToolRegistration,
    compute_sha256_hash,
)
from skills.registry import (
    ContentHashMismatchError,
    DefaultHmacVerifier,
    InvalidVersionPinningError,
    RegistrationConflictError,
    SecurityPolicyViolationError,
    SkillRegistry,
    UnsignedArtifactError,
)


@pytest.fixture
def verifier():
    return DefaultHmacVerifier(secret_key="harness-signing-key")


@pytest.fixture
def registry(verifier):
    return SkillRegistry(verifier=verifier)


# ---------------------------------------------------------------------------
# REG-001 & SKILL-001: Version Identity & Successful Registration
# ---------------------------------------------------------------------------

def test_reg_001_and_skill_001_version_identity(registry, verifier):
    """REG-001, SKILL-001: Skill has explicit SemVer version and verifies signature."""
    payload = {"source": "print('hello')"}
    content_hash = compute_sha256_hash(payload)
    sig = verifier.sign("py-runner", "1.0.0", content_hash, RiskTier.LOW, "admin")

    skill = SkillRegistration(
        skill_id="py-runner",
        version="1.0.0",
        content_hash=content_hash,
        signature=sig,
        capabilities=("python.eval_sandboxed",),
        risk_tier=RiskTier.LOW,
        registered_by="admin",
    )

    registered = registry.register_skill(skill, payload=payload)
    assert registered.versioned_id == "py-runner@1.0.0"
    assert registry.get_skill("py-runner", "1.0.0") == registered


# ---------------------------------------------------------------------------
# REG-002: Content Hash Integrity
# ---------------------------------------------------------------------------

def test_reg_002_content_hash_integrity(registry, verifier):
    """REG-002: Payload content hash mismatch is rejected synchronously."""
    actual_payload = {"code": "actual_code"}
    declared_payload = {"code": "modified_tampered_code"}
    tampered_hash = compute_sha256_hash(declared_payload)
    sig = verifier.sign("tampered-tool", "1.0.0", tampered_hash, RiskTier.LOW, "admin")

    skill = SkillRegistration(
        skill_id="tampered-tool",
        version="1.0.0",
        content_hash=tampered_hash,
        signature=sig,
        capabilities=(),
        risk_tier=RiskTier.LOW,
        registered_by="admin",
    )

    with pytest.raises(ContentHashMismatchError):
        registry.register_skill(skill, payload=actual_payload)


# ---------------------------------------------------------------------------
# REG-003 & SKILL-002: Signature Verification
# ---------------------------------------------------------------------------

def test_reg_003_and_skill_002_unsigned_artifact_rejection(registry):
    """REG-003, SKILL-002: Unsigned or invalid signature is rejected."""
    payload = {"source": "pass"}
    content_hash = compute_sha256_hash(payload)

    skill = SkillRegistration(
        skill_id="unsigned-skill",
        version="1.0.0",
        content_hash=content_hash,
        signature="invalid_signature_xyz",
        capabilities=(),
        risk_tier=RiskTier.LOW,
        registered_by="unknown",
    )

    with pytest.raises(UnsignedArtifactError):
        registry.register_skill(skill, payload=payload)


# ---------------------------------------------------------------------------
# REG-004: Risk Tier Immutability
# ---------------------------------------------------------------------------

def test_reg_004_risk_tier_immutability(registry, verifier):
    """REG-004: Mutating risk tier without human approval pulse is rejected."""
    payload = {"exec": "cmd"}
    content_hash = compute_sha256_hash(payload)
    sig = verifier.sign("high-risk-tool", "1.0.0", content_hash, RiskTier.HIGH, "admin")

    skill = SkillRegistration(
        skill_id="high-risk-tool",
        version="1.0.0",
        content_hash=content_hash,
        signature=sig,
        capabilities=("terminal.exec",),
        risk_tier=RiskTier.HIGH,
        registered_by="admin",
    )
    registry.register_skill(skill, payload=payload)

    # Self-service mutation attempt fails
    with pytest.raises(SecurityPolicyViolationError, match="security.grant.approved"):
        registry.reclassify_skill_risk("high-risk-tool", "1.0.0", RiskTier.LOW)

    # Reclassification with authenticated human approval succeeds
    approved_pulse = {
        "type": "security.grant.approved",
        "payload": {
            "approver_id": "human_operator_1",
            "decision": "APPROVE",
        },
    }
    reclassified = registry.reclassify_skill_risk(
        "high-risk-tool",
        "1.0.0",
        RiskTier.LOW,
        grant_approved_pulse=approved_pulse,
    )
    assert reclassified.risk_tier == RiskTier.LOW
    assert reclassified.metadata["reclassified_by"] == "human_operator_1"


# ---------------------------------------------------------------------------
# REG-005 & SKILL-003: Version Pinning Enforcement
# ---------------------------------------------------------------------------

def test_reg_005_and_skill_003_version_pinning_rejects_latest(registry, verifier):
    """REG-005, SKILL-003: Querying or resolving @latest is rejected."""
    payload = {"code": "pass"}
    content_hash = compute_sha256_hash(payload)
    sig = verifier.sign("pinned-app", "3.4.1", content_hash, RiskTier.LOW, "admin")

    skill = SkillRegistration(
        skill_id="pinned-app",
        version="3.4.1",
        content_hash=content_hash,
        signature=sig,
        capabilities=(),
        risk_tier=RiskTier.LOW,
        registered_by="admin",
    )
    registry.register_skill(skill, payload=payload)

    # Exact SemVer resolves
    assert registry.get_skill("pinned-app", "3.4.1").version == "3.4.1"

    # All unpinned variants are rejected
    for unpinned in ["latest", "@latest", "*", "LATEST"]:
        with pytest.raises(InvalidVersionPinningError):
            registry.get_skill("pinned-app", unpinned)

