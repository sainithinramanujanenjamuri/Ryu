"""Unit tests for SkillRegistry and supply-chain verification."""

import pytest

from skills.model import (
    RiskTier,
    SkillLifecycleState,
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
    return DefaultHmacVerifier(secret_key="test-secret-key")


@pytest.fixture
def registry(verifier):
    return SkillRegistry(verifier=verifier)


def test_register_skill_success(registry, verifier):
    payload = {"manifest": "test_skill", "version": "1.0.0"}
    c_hash = compute_sha256_hash(payload)
    sig = verifier.sign("test-skill", "1.0.0", c_hash, RiskTier.LOW, "tester")

    reg = SkillRegistration(
        skill_id="test-skill",
        version="1.0.0",
        content_hash=c_hash,
        signature=sig,
        capabilities=("file.read",),
        risk_tier=RiskTier.LOW,
        registered_by="tester",
    )

    saved = registry.register_skill(reg, payload=payload)
    assert saved.versioned_id == "test-skill@1.0.0"

    retrieved = registry.get_skill("test-skill", "1.0.0")
    assert retrieved == saved


def test_register_skill_unsigned_or_invalid_sig(registry):
    payload = {"manifest": "test_skill"}
    c_hash = compute_sha256_hash(payload)

    reg = SkillRegistration(
        skill_id="bad-skill",
        version="1.0.0",
        content_hash=c_hash,
        signature="invalid-fake-signature",
        capabilities=(),
        risk_tier=RiskTier.LOW,
        registered_by="tester",
    )

    with pytest.raises(UnsignedArtifactError):
        registry.register_skill(reg, payload=payload)


def test_register_skill_content_hash_mismatch(registry, verifier):
    payload = {"manifest": "actual_payload"}
    wrong_payload = {"manifest": "tampered_payload"}
    wrong_hash = compute_sha256_hash(wrong_payload)
    sig = verifier.sign("tampered-skill", "1.0.0", wrong_hash, RiskTier.LOW, "tester")

    reg = SkillRegistration(
        skill_id="tampered-skill",
        version="1.0.0",
        content_hash=wrong_hash,
        signature=sig,
        capabilities=(),
        risk_tier=RiskTier.LOW,
        registered_by="tester",
    )

    # When actual payload doesn't match declared wrong_hash
    with pytest.raises(ContentHashMismatchError):
        registry.register_skill(reg, payload=payload)


def test_idempotent_duplicate_and_conflict(registry, verifier):
    payload1 = {"v": 1}
    h1 = compute_sha256_hash(payload1)
    sig1 = verifier.sign("dup-skill", "1.0.0", h1, RiskTier.LOW, "tester")

    reg1 = SkillRegistration(
        skill_id="dup-skill",
        version="1.0.0",
        content_hash=h1,
        signature=sig1,
        capabilities=(),
        risk_tier=RiskTier.LOW,
        registered_by="tester",
    )

    # First registration
    registry.register_skill(reg1, payload=payload1)

    # Duplicate registration of identical artifact -> idempotent return
    again = registry.register_skill(reg1, payload=payload1)
    assert again == reg1

    # Conflicting payload swap on same version -> error
    payload2 = {"v": 2}
    h2 = compute_sha256_hash(payload2)
    sig2 = verifier.sign("dup-skill", "1.0.0", h2, RiskTier.LOW, "tester")
    reg2 = SkillRegistration(
        skill_id="dup-skill",
        version="1.0.0",
        content_hash=h2,
        signature=sig2,
        capabilities=(),
        risk_tier=RiskTier.LOW,
        registered_by="tester",
    )
    with pytest.raises(RegistrationConflictError):
        registry.register_skill(reg2, payload=payload2)


def test_version_pinning_rejects_latest(registry, verifier):
    payload = {"v": 1}
    h = compute_sha256_hash(payload)
    sig = verifier.sign("pinned-skill", "2.1.0", h, RiskTier.LOW, "tester")
    reg = SkillRegistration(
        skill_id="pinned-skill",
        version="2.1.0",
        content_hash=h,
        signature=sig,
        capabilities=(),
        risk_tier=RiskTier.LOW,
        registered_by="tester",
    )
    registry.register_skill(reg)

    # Explicit version works
    assert registry.get_skill("pinned-skill", "2.1.0").version == "2.1.0"

    # @latest must be rejected (REG-005)
    with pytest.raises(InvalidVersionPinningError, match="unpinned version"):
        registry.get_skill("pinned-skill", "latest")
    with pytest.raises(InvalidVersionPinningError, match="unpinned version"):
        registry.get_skill("pinned-skill", "@latest")
    with pytest.raises(InvalidVersionPinningError, match="unpinned version"):
        registry.get_skill("pinned-skill", "*")


def test_risk_reclassification_requires_human_approval(registry, verifier):
    payload = {"v": 1}
    h = compute_sha256_hash(payload)
    sig = verifier.sign("risky-skill", "1.0.0", h, RiskTier.HIGH, "tester")
    reg = SkillRegistration(
        skill_id="risky-skill",
        version="1.0.0",
        content_hash=h,
        signature=sig,
        capabilities=("terminal.exec",),
        risk_tier=RiskTier.HIGH,
        registered_by="tester",
    )
    registry.register_skill(reg)

    # Self-service / unauthorized mutation fails (REG-004)
    with pytest.raises(SecurityPolicyViolationError, match="requires a human-approved"):
        registry.reclassify_skill_risk("risky-skill", "1.0.0", RiskTier.LOW)

    # Valid human approval pulse allows reclassification
    approval_pulse = {
        "type": "security.grant.approved",
        "payload": {
            "approver_id": "human_admin_1",
            "decision": "APPROVE",
            "capability": "risky-skill@1.0.0",
        },
    }
    updated = registry.reclassify_skill_risk(
        "risky-skill",
        "1.0.0",
        RiskTier.LOW,
        grant_approved_pulse=approval_pulse,
    )
    assert updated.risk_tier == RiskTier.LOW
    assert updated.metadata["reclassified_by"] == "human_admin_1"


def test_skill_lifecycle_state_and_revocation(registry, verifier):
    payload = {"v": 1}
    h = compute_sha256_hash(payload)
    sig = verifier.sign("lifecycle-skill", "1.0.0", h, RiskTier.LOW, "tester")
    reg = SkillRegistration(
        skill_id="lifecycle-skill",
        version="1.0.0",
        content_hash=h,
        signature=sig,
        capabilities=(),
        risk_tier=RiskTier.LOW,
        registered_by="tester",
    )
    registry.register_skill(reg)

    # Disable
    disabled = registry.set_skill_state("lifecycle-skill", "1.0.0", SkillLifecycleState.DISABLED)
    assert disabled.lifecycle_state == SkillLifecycleState.DISABLED

    # Re-enable
    enabled = registry.set_skill_state("lifecycle-skill", "1.0.0", SkillLifecycleState.ENABLED)
    assert enabled.lifecycle_state == SkillLifecycleState.ENABLED

    # Revoke (terminal)
    revoked = registry.set_skill_state("lifecycle-skill", "1.0.0", SkillLifecycleState.REVOKED)
    assert revoked.lifecycle_state == SkillLifecycleState.REVOKED

    # Cannot un-revoke
    with pytest.raises(SecurityPolicyViolationError, match="permanently revoked"):
        registry.set_skill_state("lifecycle-skill", "1.0.0", SkillLifecycleState.ENABLED)


def test_tool_registration_and_query(registry, verifier):
    payload = {"name": "read_file"}
    h = compute_sha256_hash(payload)
    sig = verifier.sign("mcp.fs.read", "1.0.0", h, RiskTier.LOW, "tester")
    tool = ToolRegistration(
        tool_id="mcp.fs.read",
        version="1.0.0",
        content_hash=h,
        signature=sig,
        capability="file.read",
        risk_tier=RiskTier.LOW,
        registered_by="tester",
        source_type="mcp",
        server_id="fs",
    )
    registry.register_tool(tool, payload=payload)

    retrieved = registry.get_tool("mcp.fs.read", "1.0.0")
    assert retrieved.tool_id == "mcp.fs.read"
    assert retrieved.server_id == "fs"

    # Filter by server
    fs_tools = registry.list_tools(server_id="fs")
    assert len(fs_tools) == 1
    assert fs_tools[0].tool_id == "mcp.fs.read"

    other_tools = registry.list_tools(server_id="other")
    assert len(other_tools) == 0

