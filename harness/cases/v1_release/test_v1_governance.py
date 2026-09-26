"""
Harness Case: V1-005 — Governance & Documentation Hygiene Audit.

Verifies ADR inventory (0001..0039), pulse registry/codegen synchronization,
payload schemas 1:1 coverage, and contract matrix integrity.

spec §4, §16, ROADMAP v1.0 Exit Gate V1-005
"""

from __future__ import annotations

from scripts.v1_audit_governance import audit_governance


def test_v1_governance_and_documentation_hygiene() -> None:
    """Verify complete governance hygiene across ADRs, registry, schemas, and contract matrix."""
    report = audit_governance()
    assert report["status"] == "PASS", f"Governance audit failed: {report}"
    a = report["audits"]
    assert a["adr_audit"]["passed"] is True, "ADR audit must pass"
    assert a["pulse_registry_audit"]["passed"] is True, "Pulse registry audit must pass"
    assert a["payload_schemas_audit"]["passed"] is True, "Payload schemas audit must pass"
    assert a["contract_matrix_audit"]["passed"] is True, "Contract matrix audit must pass"

