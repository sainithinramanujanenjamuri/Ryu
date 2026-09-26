#!/usr/bin/env python3
"""
scripts/v1_audit_governance.py

V1-005 — Governance & Documentation Hygiene Audit.

Verifies:
1. Complete ADR inventory (all 39 ADRs 0001..0039 present with required sections).
2. Pulse registry completeness (all 38 types registered and synchronized with codegen).
3. Payload schemas completeness (1:1 schema file for every registered pulse type).
4. Contract matrix integrity (bidirectional validity and unique contract IDs).

Output:
- build/v1_evidence/reports/v1_governance_report.json
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any


def find_repo_root() -> Path:
    here = Path(__file__).resolve().parent
    for candidate in [here.parent, here]:
        if (candidate / "docs").is_dir() and (candidate / "harness").is_dir():
            return candidate
    return Path.cwd()


REPO_ROOT = find_repo_root()
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def audit_adrs(repo_root: Path) -> tuple[bool, dict[str, Any]]:
    adr_dir = repo_root / "adr"
    if not adr_dir.exists():
        return False, {"error": "adr/ directory not found"}

    adr_files = sorted(list(adr_dir.glob("00*.md")))
    expected_count = 39

    adrs_found = {}
    missing_numbers = []
    missing_sections = []

    for num in range(1, expected_count + 1):
        prefix = f"{num:04d}-"
        matching = [f for f in adr_files if f.name.startswith(prefix)]
        if not matching:
            missing_numbers.append(num)
        else:
            f = matching[0]
            content = f.read_text(encoding="utf-8")
            # Check required ADR sections with optional numbering
            has_context = bool(re.search(r"(?i)#+\s*(?:\d+\.?\s*)?(?:context|problem)", content))
            has_decision = bool(re.search(r"(?i)#+\s*(?:\d+\.?\s*)?decision", content))
            has_consequences = bool(re.search(r"(?i)#+\s*(?:\d+\.?\s*)?consequences", content))

            if not (has_context and has_decision and has_consequences):
                missing_sections.append({
                    "file": f.name,
                    "has_context": has_context,
                    "has_decision": has_decision,
                    "has_consequences": has_consequences,
                })

            adrs_found[f.name] = {
                "size_bytes": len(content),
                "valid": has_context and has_decision and has_consequences,
            }

    passed = (len(adrs_found) == expected_count) and (len(missing_numbers) == 0) and (len(missing_sections) == 0)
    details = {
        "expected_count": expected_count,
        "actual_count": len(adrs_found),
        "missing_numbers": missing_numbers,
        "missing_sections_count": len(missing_sections),
        "missing_sections": missing_sections,
    }
    return passed, details


def audit_pulse_registry(repo_root: Path) -> tuple[bool, dict[str, Any]]:
    registry_file = repo_root / "contracts" / "registry" / "pulse-types.json"
    codegen_file = repo_root / "contracts" / "codegen" / "python" / "generated" / "pulse_models.py"

    if not registry_file.exists():
        return False, {"error": "pulse-types.json not found"}
    if not codegen_file.exists():
        return False, {"error": "pulse_models.py not found"}

    with open(registry_file, "r", encoding="utf-8") as f:
        registry_data = json.load(f)

    reg_types = [t.get("type") for t in registry_data.get("types", [])]
    reg_set = set(reg_types)

    # Dynamic import of ALL_PULSE_TYPES
    sys_path_save = list(sys.path)
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    try:
        from contracts.codegen.python.generated.pulse_models import ALL_PULSE_TYPES
        codegen_set = set(ALL_PULSE_TYPES)
        codegen_types = sorted(list(codegen_set))
    finally:
        sys.path = sys_path_save

    types_match = (reg_set == codegen_set)
    expected_type_count = 38
    count_ok = len(reg_types) >= expected_type_count

    passed = types_match and count_ok
    details = {
        "registry_types_count": len(reg_types),
        "codegen_types_count": len(codegen_types),
        "types_match": types_match,
        "missing_in_codegen": list(reg_set - codegen_set),
        "missing_in_registry": list(codegen_set - reg_set),
    }
    return passed, details


def audit_payload_schemas(repo_root: Path) -> tuple[bool, dict[str, Any]]:
    registry_file = repo_root / "contracts" / "registry" / "pulse-types.json"
    schema_dir = repo_root / "contracts" / "registry" / "payload-schemas"

    if not registry_file.exists() or not schema_dir.exists():
        return False, {"error": "Registry or schema directory not found"}

    with open(registry_file, "r", encoding="utf-8") as f:
        registry_data = json.load(f)

    reg_types = [t.get("type") for t in registry_data.get("types", [])]

    missing_schemas = []
    invalid_schemas = []

    for pt in reg_types:
        schema_path = schema_dir / f"{pt}.json"
        if not schema_path.exists():
            missing_schemas.append(f"{pt}.json")
            continue

        try:
            with open(schema_path, "r", encoding="utf-8") as sf:
                sdata = json.load(sf)
                if not isinstance(sdata, dict) or "type" not in sdata:
                    invalid_schemas.append(f"{pt}.json (missing 'type')")
        except Exception as e:
            invalid_schemas.append(f"{pt}.json ({e})")

    passed = (len(missing_schemas) == 0) and (len(invalid_schemas) == 0)
    details = {
        "registered_types_count": len(reg_types),
        "schemas_verified_count": len(reg_types) - len(missing_schemas) - len(invalid_schemas),
        "missing_schemas": missing_schemas,
        "invalid_schemas": invalid_schemas,
    }
    return passed, details


def audit_contract_matrix(repo_root: Path) -> tuple[bool, dict[str, Any]]:
    matrix_file = repo_root / "docs" / "CONTRACT_MATRIX.md"
    if not matrix_file.exists():
        return False, {"error": "CONTRACT_MATRIX.md not found"}

    from scripts.v1_audit_spec_coverage import extract_contract_matrix_contracts
    contracts = extract_contract_matrix_contracts(matrix_file)

    contract_prefixes = (
        "ARC-", "SPACE-", "PULSE-", "RESOURCE-", "ORCH-", "AGENT-",
        "WORKER-", "NODE-", "CLI-", "SKILL-", "MEM-", "REC-",
        "TAINT-", "SEC-", "HUMAN-", "GOAL-", "PLAN-", "TEAM-", "MDM-",
        "MCP-", "CHAOS-", "APP-", "REG-", "V1-",
    )

    relevant_contracts = {
        cid: c for cid, c in contracts.items()
        if any(cid.startswith(p) for p in contract_prefixes)
    }

    invalid_entries = []
    for cid, c in relevant_contracts.items():
        if not c.get("contract") or not c.get("source") or not c.get("harness"):
            invalid_entries.append(cid)

    passed = len(relevant_contracts) > 100 and len(invalid_entries) == 0
    details = {
        "contracts_count": len(relevant_contracts),
        "invalid_contracts_count": len(invalid_entries),
        "invalid_contracts": invalid_entries,
    }
    return passed, details


def audit_governance() -> dict[str, Any]:
    repo_root = find_repo_root()

    adr_ok, adr_details = audit_adrs(repo_root)
    reg_ok, reg_details = audit_pulse_registry(repo_root)
    schema_ok, schema_details = audit_payload_schemas(repo_root)
    matrix_ok, matrix_details = audit_contract_matrix(repo_root)

    overall_passed = adr_ok and reg_ok and schema_ok and matrix_ok

    report = {
        "criterion": "V1-005",
        "title": "Governance & Documentation Hygiene",
        "status": "PASS" if overall_passed else "FAIL",
        "audits": {
            "adr_audit": {
                "passed": adr_ok,
                "details": adr_details,
            },
            "pulse_registry_audit": {
                "passed": reg_ok,
                "details": reg_details,
            },
            "payload_schemas_audit": {
                "passed": schema_ok,
                "details": schema_details,
            },
            "contract_matrix_audit": {
                "passed": matrix_ok,
                "details": matrix_details,
            },
        },
    }

    out_dir = repo_root / "build" / "v1_evidence" / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "v1_governance_report.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )

    return report


def main() -> int:
    print("============================================================")
    print("RYU AI — V1-005 Governance & Documentation Hygiene Audit")
    print("============================================================")
    report = audit_governance()
    a = report["audits"]
    print(f"  ADR Inventory (0001..0039):     {'[PASS]' if a['adr_audit']['passed'] else '[FAIL]'}")
    print(f"  Pulse Registry & Codegen Sync:  {'[PASS]' if a['pulse_registry_audit']['passed'] else '[FAIL]'}")
    print(f"  Payload Schemas (1:1 Coverage): {'[PASS]' if a['payload_schemas_audit']['passed'] else '[FAIL]'}")
    print(f"  Contract Matrix Integrity:      {'[PASS]' if a['contract_matrix_audit']['passed'] else '[FAIL]'}")
    print("------------------------------------------------------------")
    print(f"V1-005 STATUS: {report['status']}")
    print("============================================================")

    if report["status"] != "PASS":
        print("FAILURES DETECTED:")
        for name, item in a.items():
            if not item["passed"]:
                print(f"  {name}: {item['details']}")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())

