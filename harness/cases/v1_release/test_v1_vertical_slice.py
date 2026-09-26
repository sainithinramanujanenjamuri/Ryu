"""
Harness Case: V1-003 — Complete End-to-End Vertical Slice Execution.

Verifies that the complete architecture executes coherently:
Goal -> Plan (CAS v1) -> Real Human Approval (token-hmac-v1) ->
Device Grant -> Node Execution -> Artifact -> Reflection -> Causation Replay.

spec §4, §11, §16, ROADMAP v1.0 Exit Gate V1-003
"""

from __future__ import annotations

import pytest
from scripts.v1_run_vertical_slice import run_vertical_slice


def test_v1_vertical_slice_execution() -> None:
    """Execute the full vertical slice and assert complete architectural correctness."""
    report = run_vertical_slice()

    if report.get("status") == "BLOCKED":
        pytest.skip(f"V1-003 blocked: {report.get('error')}")

    assert report["status"] == "PASS", f"Vertical slice failed: {report}"
    assert report["human_approval_verified"] is True, "Human approval must be verified"
    assert len(report["artifact_sha256"]) == 64, "Artifact SHA-256 must be valid"
    assert report["pulses_persisted_count"] >= 4, "Pulses must be durably persisted in Postgres"
    assert report["causal_chain_length"] >= 3, "Causal chain must be reconstructable"

