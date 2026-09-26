"""
Harness Case: V1-004 — Consolidated Security Regression Battery.

Executes and verifies all 12 mandatory security proofs across the architecture.

spec §4, §10, §16, ROADMAP v1.0 Exit Gate V1-004
"""

from __future__ import annotations

from scripts.v1_run_security_regression import run_security_battery


def test_v1_security_regression_battery() -> None:
    """Verify all 12 mandatory security proofs pass consecutively without bypass."""
    report = run_security_battery()
    assert report["status"] == "PASS", f"Security battery failed: {report}"
    assert report["passed_count"] == 12, f"Expected 12 passed security proofs, got {report['passed_count']}"
    assert report["failed_count"] == 0, f"Expected 0 failed security proofs, got {report['failed_count']}"

