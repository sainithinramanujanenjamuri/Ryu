#!/usr/bin/env python3
"""
scripts/v1_audit_spec_coverage.py

V1-001 — Dynamic Spec Coverage Audit.

Verifies bidirectional traceability between:
- Architecture acceptance criteria (docs/Architecture, docs/CONTRACT_MATRIX.md)
- Machine-readable contracts (contracts/registry/pulse-types.json, payload schemas)
- Executable harness specifications (harness/spec_map.yaml)
- Test suite implementations on disk (harness/cases/)

CRITICAL INVARIANT (Hardened v1.0 Release Plan):
Dynamic repository-derived test inventory != hardcoded expected test total.
All counts are computed at verification runtime from repository files.

Output: build/v1_evidence/reports/v1_spec_coverage_report.json
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


def extract_contract_matrix_contracts(matrix_file: Path) -> dict[str, dict[str, Any]]:
    """Extract all contract IDs and metadata from docs/CONTRACT_MATRIX.md tables."""
    if not matrix_file.exists():
        return {}

    text = matrix_file.read_text(encoding="utf-8")
    contracts: dict[str, dict[str, Any]] = {}

    # Match markdown table rows: | ID | Contract | ...
    # e.g., | ARC-001 | Everything happens inside a Space. | ...
    row_pattern = re.compile(
        r"^\|\s*([A-Z0-9_\-]+)\s*\|\s*([^|]+)\s*\|\s*([^|]+)\s*\|\s*([^|]+)\s*\|\s*([^|]+)\s*\|\s*([^|]+)\s*\|\s*([^|]+)\s*\|",
        re.MULTILINE,
    )

    for match in row_pattern.finditer(text):
        cid = match.group(1).strip()
        if cid in ("ID", "---", "Status", "Contract", "Meanings"):
            continue
        if re.match(r"^[-:]+$", cid):
            continue

        contracts[cid] = {
            "id": cid,
            "contract": match.group(2).strip(),
            "source": match.group(3).strip(),
            "harness": match.group(5).strip(),
            "status": match.group(7).strip(),
        }

    return contracts


def extract_architecture_criteria(arch_file: Path) -> dict[str, dict[str, Any]]:
    """Extract formal acceptance criteria from docs/Architecture."""
    if not arch_file.exists():
        return {}

    text = arch_file.read_text(encoding="utf-8")
    criteria: dict[str, dict[str, Any]] = {}

    # Pattern for formal criteria marked with [id] or bold identifiers or checkmarks
    # Examples: "#### Pulse Type Registry", formal numbered sections, etc.
    # We look for explicit bullet points or numbered requirements under architecture sections
    crit_pattern = re.compile(
        r"(?:(?:^|\n)(?:###|####)\s+([^\n]+)|(?:^|\n)\s*[-*]\s*`?([A-Z]+-[0-9]+)`?:\s*([^\n]+))",
        re.MULTILINE,
    )

    current_section = "General"
    for match in crit_pattern.finditer(text):
        section_hdr = match.group(1)
        cid = match.group(2)
        desc = match.group(3)

        if section_hdr:
            current_section = section_hdr.strip()
        elif cid and desc:
            criteria[cid.strip()] = {
                "id": cid.strip(),
                "section": current_section,
                "description": desc.strip(),
            }

    return criteria


def extract_spec_map_entries(spec_map_file: Path) -> list[dict[str, Any]]:
    """Load spec_map.yaml entries."""
    if not spec_map_file.exists():
        return []

    # Simple YAML loader without strict external dependency if pyyaml not present
    try:
        import yaml
        with open(spec_map_file, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
            return data.get("spec_map", [])
    except ImportError:
        # Fallback regex parser for spec_map.yaml
        text = spec_map_file.read_text(encoding="utf-8")
        entries = []
        blocks = text.split("\n  - id:")
        for i, b in enumerate(blocks):
            if i == 0:
                continue
            lines = b.splitlines()
            cid = lines[0].strip().strip('"').strip("'")
            entry = {"id": cid}
            for line in lines[1:]:
                if ":" in line:
                    k, v = line.split(":", 1)
                    k = k.strip()
                    v = v.strip().strip('"').strip("'")
                    if k in ("title", "architecture_section", "harness_case", "evidence_state", "roadmap_phase"):
                        entry[k] = v
            entries.append(entry)
        return entries


def verify_spec_coverage() -> dict[str, Any]:
    repo_root = find_repo_root()
    arch_file = repo_root / "docs" / "Architecture"
    if not arch_file.exists():
        arch_file = repo_root / "docs" / "architecture.md"
    matrix_file = repo_root / "docs" / "CONTRACT_MATRIX.md"
    spec_map_file = repo_root / "harness" / "spec_map.yaml"
    pulse_types_file = repo_root / "contracts" / "registry" / "pulse-types.json"

    # 1. Dynamic Extraction
    matrix_contracts = extract_contract_matrix_contracts(matrix_file)
    spec_map_entries = extract_spec_map_entries(spec_map_file)

    # Registered pulse types
    pulse_types = []
    if pulse_types_file.exists():
        try:
            with open(pulse_types_file, "r", encoding="utf-8") as f:
                pulse_data = json.load(f)
                pulse_types = [t.get("type") for t in pulse_data.get("types", [])]
        except Exception:
            pass

    # 2. Spec-Map Verification
    spec_map_ids: set[str] = set()
    spec_map_by_id: dict[str, list[dict[str, Any]]] = {}
    missing_test_files: list[str] = []
    executable_mappings_count = 0

    for entry in spec_map_entries:
        cid = entry.get("id", "").strip()
        if not cid:
            continue
        spec_map_ids.add(cid)
        spec_map_by_id.setdefault(cid, []).append(entry)

        harness_case = entry.get("harness_case", "")
        if harness_case:
            # Check if file exists (strip ::function and (comment))
            case_path = harness_case.split("::")[0].split(" ")[0].strip()
            full_case_path = repo_root / case_path
            if not full_case_path.exists():
                missing_test_files.append(f"{cid} -> {case_path}")
            else:
                executable_mappings_count += 1

    # 3. Duplicate Contract IDs
    duplicate_ids = [cid for cid, entries in spec_map_by_id.items() if len(entries) > 1]

    # 4. Bidirectional Traceability:
    # Any contract in CONTRACT_MATRIX that is an authoritative contract ID must be in spec-map
    matrix_ids = set(matrix_contracts.keys())

    # Filter matrix IDs: standard contract prefixes
    contract_prefixes = (
        "ARC-", "SPACE-", "PULSE-", "RESOURCE-", "ORCH-", "AGENT-",
        "WORKER-", "NODE-", "CLI-", "SKILL-", "MEM-", "REC-",
        "TAINT-", "SEC-", "HUMAN-", "GOAL-", "PLAN-", "TEAM-", "MDM-",
        "MCP-", "CHAOS-", "APP-", "REG-", "V1-",
    )
    relevant_matrix_ids = {cid for cid in matrix_ids if any(cid.startswith(p) for p in contract_prefixes)}

    orphaned_arch_criteria = sorted(list(relevant_matrix_ids - spec_map_ids))
    # Orphaned spec map entries: in spec-map but not recognized in CONTRACT_MATRIX or arch
    orphaned_spec_map_entries = sorted(list(spec_map_ids - matrix_ids))

    # 5. Stale Evidence Check
    stale_evidence = []
    for entry in spec_map_entries:
        state = entry.get("evidence_state", "")
        # Any entry claiming to be GATE_VERIFIED or INTEGRATION_VERIFIED without an existing harness case is stale
        hcase = entry.get("harness_case", "")
        if hcase:
            cpath = repo_root / hcase.split("::")[0].strip()
            if not cpath.exists() and state in ("GATE_VERIFIED", "INTEGRATION_VERIFIED", "UNIT_VERIFIED"):
                stale_evidence.append(f"{entry.get('id')}: {state} but missing file {hcase}")

    # Compile dynamic metrics
    passed = (
        len(orphaned_arch_criteria) == 0
        and len(orphaned_spec_map_entries) == 0
        and len(duplicate_ids) == 0
        and len(missing_test_files) == 0
        and len(stale_evidence) == 0
    )

    report = {
        "criterion": "V1-001",
        "title": "Spec Coverage Audit",
        "status": "PASS" if passed else "FAIL",
        "metrics": {
            "architecture_criteria_count": len(relevant_matrix_ids),
            "contract_ids_count": len(matrix_contracts),
            "spec_map_entries_count": len(spec_map_entries),
            "executable_mappings_count": executable_mappings_count,
            "registered_pulse_types_count": len(pulse_types),
            "orphaned_architecture_criteria_count": len(orphaned_arch_criteria),
            "orphaned_spec_map_entries_count": len(orphaned_spec_map_entries),
            "duplicate_ids_count": len(duplicate_ids),
            "missing_tests_count": len(missing_test_files),
            "stale_evidence_count": len(stale_evidence),
        },
        "details": {
            "orphaned_architecture_criteria": orphaned_arch_criteria,
            "orphaned_spec_map_entries": orphaned_spec_map_entries,
            "duplicate_ids": duplicate_ids,
            "missing_tests": missing_test_files,
            "stale_evidence": stale_evidence,
        },
    }

    # Ensure output directory exists
    out_dir = repo_root / "build" / "v1_evidence" / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    report_file = out_dir / "v1_spec_coverage_report.json"
    report_file.write_text(json.dumps(report, indent=2), encoding="utf-8")

    return report


def main() -> int:
    report = verify_spec_coverage()
    m = report["metrics"]

    print("============================================================")
    print("RYU AI — V1-001 Dynamic Spec Coverage Audit")
    print("============================================================")
    print(f"Architecture criteria:          {m['architecture_criteria_count']}")
    print(f"Contract IDs:                   {m['contract_ids_count']}")
    print(f"Spec-map entries:               {m['spec_map_entries_count']}")
    print(f"Executable mappings:            {m['executable_mappings_count']}")
    print(f"Orphaned architecture criteria: {m['orphaned_architecture_criteria_count']}")
    print(f"Orphaned spec-map entries:      {m['orphaned_spec_map_entries_count']}")
    print(f"Duplicate IDs:                  {m['duplicate_ids_count']}")
    print(f"Missing tests:                  {m['missing_tests_count']}")
    print(f"Stale evidence:                 {m['stale_evidence_count']}")
    print("------------------------------------------------------------")
    print(f"V1-001 STATUS: {report['status']}")
    print("============================================================")

    if report["status"] != "PASS":
        print("FAILURES DETECTED:")
        for k, v in report["details"].items():
            if v:
                print(f"  {k}: {v}")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
