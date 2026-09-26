"""
Harness Case: V1-006 — Deterministic Replay Equivalence Verification.

Verifies deterministic replay equivalence:
Exact artifact SHA-256 byte identity + structural/causal equivalence.

spec §4, §16, ROADMAP v1.0 Exit Gate V1-006
"""

from __future__ import annotations

from scripts.v1_verify_replay import verify_replay_equivalence


def test_v1_deterministic_replay_equivalence() -> None:
    """Verify exact artifact byte identity and structural causal replay equivalence."""
    report = verify_replay_equivalence()
    assert report["status"] == "PASS", f"Replay equivalence verification failed: {report}"
    v = report["verifications"]
    assert v["artifact_exact_byte_identity"]["passed"] is True
    assert v["pulse_causation_equivalence"]["passed"] is True
    assert v["plan_state_equivalence"]["passed"] is True
    assert v["memory_experience_equivalence"]["passed"] is True

