"""Unit tests for Skill and Tool models and supply-chain hashing."""


import pytest

from skills.model import (
    RiskTier,
    SkillLifecycleState,
    SkillRegistration,
    ToolRegistration,
    compute_sha256_hash,
)


def test_compute_sha256_hash():
    # Deterministic dictionary hashing
    d1 = {"b": 2, "a": 1}
    d2 = {"a": 1, "b": 2}
    h1 = compute_sha256_hash(d1)
    h2 = compute_sha256_hash(d2)
    assert h1 == h2
    assert len(h1) == 64

    # String and bytes hashing
    s_hash = compute_sha256_hash("hello ryu")
    b_hash = compute_sha256_hash(b"hello ryu")
    assert s_hash == b_hash


def test_skill_registration_valid():
    content = {"code": "def run(): pass"}
    c_hash = compute_sha256_hash(content)

    skill = SkillRegistration(
        skill_id="code-analyzer",
        version="1.0.0",
        content_hash=c_hash,
        signature="dummy-sig-123",
        capabilities=("file.read", "python.eval_sandboxed"),
        risk_tier=RiskTier.LOW,
        registered_by="admin",
        description="Analyzes code quality",
    )

    assert skill.versioned_id == "code-analyzer@1.0.0"
    assert skill.lifecycle_state == SkillLifecycleState.REGISTERED
    assert skill.risk_tier == RiskTier.LOW

    d = skill.to_dict()
    assert d["skill_id"] == "code-analyzer"
    assert d["version"] == "1.0.0"
    assert d["capabilities"] == ["file.read", "python.eval_sandboxed"]


def test_skill_registration_invalid_semver():
    c_hash = compute_sha256_hash("data")
    with pytest.raises(ValueError, match="Invalid SemVer string"):
        SkillRegistration(
            skill_id="test-skill",
            version="v1.0",  # invalid semver (leading 'v')
            content_hash=c_hash,
            signature="sig",
            capabilities=("file.read",),
            risk_tier=RiskTier.LOW,
            registered_by="admin",
        )

    with pytest.raises(ValueError, match="Invalid SemVer string"):
        SkillRegistration(
            skill_id="test-skill",
            version="latest",  # not a semver
            content_hash=c_hash,
            signature="sig",
            capabilities=("file.read",),
            risk_tier=RiskTier.LOW,
            registered_by="admin",
        )


def test_skill_registration_invalid_hash():
    with pytest.raises(ValueError, match="Invalid content_hash format"):
        SkillRegistration(
            skill_id="test-skill",
            version="1.0.0",
            content_hash="not-a-64-char-hash",
            signature="sig",
            capabilities=(),
            risk_tier=RiskTier.LOW,
            registered_by="admin",
        )


def test_tool_registration_valid():
    content = {"tool": "read_file"}
    c_hash = compute_sha256_hash(content)

    tool = ToolRegistration(
        tool_id="mcp.fs.read_file",
        version="0.2.1",
        content_hash=c_hash,
        signature="tool-sig",
        capability="file.read",
        risk_tier=RiskTier.LOW,
        registered_by="mcp-system",
        source_type="mcp",
        server_id="fs",
    )

    assert tool.versioned_id == "mcp.fs.read_file@0.2.1"
    assert tool.capability == "file.read"
    assert tool.source_type == "mcp"
    assert tool.server_id == "fs"

