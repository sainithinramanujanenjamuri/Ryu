"""Unit tests for DevicePolicyEngine and RestrictedNodePolicy.

CONTRACT_MATRIX NODE-012, ADR-0039 — Phase 11
INVARIANT:
MDM_ALLOW != Authentication.
MDM is an additional local policy constraint.
"""

from __future__ import annotations

from node.policy.engine import DevicePolicyEngine
from node.policy.models import NodeTrustTier, RestrictedNodePolicy


def test_full_trust_tier_bypasses_policy() -> None:
    # Full trust node permits any capability without MDM constraint
    permitted, reason = DevicePolicyEngine.evaluate(
        trust_tier=NodeTrustTier.FULL_TRUST,
        policy=None,
        capability="gpu.cuda",
    )
    assert permitted is True
    assert reason is None


def test_restricted_tier_without_policy_fails_closed() -> None:
    # Restricted tier without configured policy denies all
    permitted, reason = DevicePolicyEngine.evaluate(
        trust_tier=NodeTrustTier.RESTRICTED,
        policy=None,
        capability="compute.cpu",
    )
    assert permitted is False
    assert "fail-closed" in str(reason)


def test_restricted_tier_allow_and_deny_lists() -> None:
    policy = RestrictedNodePolicy(
        policy_id="corp-mdm-001",
        allowed_capabilities={"compute.cpu", "storage.workspace", "terminal.read_only"},
        denied_capabilities={"gpu.cuda", "network.raw"},
    )

    # Allowed capability
    p1, r1 = DevicePolicyEngine.evaluate(
        trust_tier=NodeTrustTier.RESTRICTED,
        policy=policy,
        capability="compute.cpu",
    )
    assert p1 is True
    assert r1 is None

    # Denied capability (explicitly in denied)
    p2, r2 = DevicePolicyEngine.evaluate(
        trust_tier=NodeTrustTier.RESTRICTED,
        policy=policy,
        capability="gpu.cuda",
    )
    assert p2 is False
    assert "explicitly denied" in str(r2)

    # Unlisted capability (not in allow-list)
    p3, r3 = DevicePolicyEngine.evaluate(
        trust_tier=NodeTrustTier.RESTRICTED,
        policy=policy,
        capability="terminal.admin",
    )
    assert p3 is False
    assert "not in allow-list" in str(r3)


def test_restricted_tier_storage_path_constraints() -> None:
    policy = RestrictedNodePolicy(
        policy_id="corp-mdm-storage",
        allowed_capabilities={"storage.workspace"},
        allowed_storage_paths=["/tmp/allowed_scratch", "C:/Workspace/Allowed"],
    )

    # Path inside allowed prefix
    p_ok, r_ok = DevicePolicyEngine.evaluate(
        trust_tier=NodeTrustTier.RESTRICTED,
        policy=policy,
        capability="storage.workspace",
        parameters={"path": "/tmp/allowed_scratch/subfolder/file.txt"},
    )
    assert p_ok is True
    assert r_ok is None

    # Path outside allowed prefix
    p_bad, r_bad = DevicePolicyEngine.evaluate(
        trust_tier=NodeTrustTier.RESTRICTED,
        policy=policy,
        capability="storage.workspace",
        parameters={"path": "/etc/passwd"},
    )
    assert p_bad is False
    assert "not within MDM allowed paths" in str(r_bad)


def test_policy_serialization_roundtrip() -> None:
    policy = RestrictedNodePolicy(
        policy_id="roundtrip-pol",
        allowed_capabilities={"cap.a", "cap.b"},
        denied_capabilities={"cap.evil"},
        allowed_storage_paths=["/safe/path"],
        allow_terminal_exec=True,
    )
    data = policy.to_dict()
    reconstructed = RestrictedNodePolicy.from_dict(data)
    assert reconstructed.policy_id == policy.policy_id
    assert reconstructed.allowed_capabilities == policy.allowed_capabilities
    assert reconstructed.denied_capabilities == policy.denied_capabilities
    assert reconstructed.allowed_storage_paths == policy.allowed_storage_paths
    assert reconstructed.allow_terminal_exec is True

