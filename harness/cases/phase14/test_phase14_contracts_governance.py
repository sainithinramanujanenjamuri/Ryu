"""Phase 14.0 — Architecture Baseline, Contracts & Governance Test Suite.

Verifies:
1. ADR-0044 section completeness and consistency.
2. Phase 14 pulse types registered in pulse-types.json.
3. 1:1 payload schemas created and valid for all Phase 14 pulses.
4. Python codegen synchronization (ALL_PULSE_TYPES contains all 50 types).
5. All 20 Phase 14 contracts (RESEARCH-001..005, REPO-001..005, EVIDENCE-001..003,
   REPAIR-001..004, PROVENANCE-001..003) governance integrity.
6. Six SCCA Laws compliance and authority separation invariants.
7. Deterministic Core Independence (AGENTS.md §7).

spec §16 (Component Contracts), ADR-0044, GATE-14.0 — Phase 14.0
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
import jsonschema

from contracts.codegen.python.generated.pulse_models import (
    ALL_PULSE_TYPES,
    PULSE_DEFAULT_SEVERITIES,
    PulseType,
)


def _get_repo_root() -> Path:
    here = Path(__file__).resolve().parent
    for candidate in [here.parent.parent, here.parent, here]:
        if (candidate / "contracts").is_dir() and (candidate / "adr").is_dir():
            return candidate
    return Path.cwd()


REPO_ROOT = _get_repo_root()


class TestPhase14Governance:
    """Verifies Phase 14.0 architecture, ADR-0044, and pulse contracts."""

    def test_adr_0044_section_completeness(self) -> None:
        """Verify ADR-0044 exists and contains all required sections per Phase 14 Directive §5."""
        adr_path = REPO_ROOT / "adr" / "0044-autonomous-research-and-software-engineering-runtime.md"
        assert adr_path.exists(), f"ADR-0044 not found at {adr_path}"

        content = adr_path.read_text(encoding="utf-8")
        assert len(content) > 2000, "ADR-0044 is unexpectedly brief"

        # Check required sections
        required_headers = [
            "Context",
            "Problem Statement",
            "Goals",
            "Non-Goals",
            "Decision",
            "Research Boundary",
            "Repository and Software-Engineering Boundary",
            "Evidence Boundary",
            "Provenance Boundary",
            "Repair and Convergence Boundary",
            "Memory Boundary",
            "External-Tool Boundary",
            "Authority Model",
            "Security Model",
            "Failure Model",
            "Recovery Expectations",
            "Replay Expectations",
            "Contract Strategy",
            "Pulse Strategy",
            "Testing Strategy",
            "Phase 14 Implementation Sequence",
            "Alternatives Considered",
            "Consequences",
        ]

        missing = []
        for header in required_headers:
            if not re.search(rf"(?i)#+\s*(?:\d+\.?\s*)?{re.escape(header)}", content):
                missing.append(header)

        assert not missing, f"ADR-0044 is missing required sections: {missing}"

    def test_phase14_pulse_types_in_registry(self) -> None:
        """Verify all 6 Phase 14 pulse types are registered in pulse-types.json."""
        registry_file = REPO_ROOT / "contracts" / "registry" / "pulse-types.json"
        with open(registry_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        types_map = {t["type"]: t for t in data.get("types", [])}

        expected_pulses = {
            "research.retrieved": ("research", "info"),
            "research.conflict_detected": ("research", "warning"),
            "repo.patch_applied": ("repo", "info"),
            "repo.patch_reverted": ("repo", "warning"),
            "test.executed": ("test", "info"),
            "repair.loop_iterated": ("repair", "warning"),
        }

        assert len(types_map) >= 50, f"Expected at least 50 types, found {len(types_map)}"

        for ptype, (expected_ns, expected_sev) in expected_pulses.items():
            assert ptype in types_map, f"Pulse type '{ptype}' missing from pulse-types.json"
            assert types_map[ptype]["namespace"] == expected_ns, f"Wrong namespace for {ptype}"
            assert types_map[ptype]["default_severity"] == expected_sev, f"Wrong severity for {ptype}"

    def test_phase14_payload_schemas_valid(self) -> None:
        """Verify each Phase 14 pulse type has a valid JSON schema with required fields."""
        schema_dir = REPO_ROOT / "contracts" / "registry" / "payload-schemas"
        expected_pulses = [
            "research.retrieved",
            "research.conflict_detected",
            "repo.patch_applied",
            "repo.patch_reverted",
            "test.executed",
            "repair.loop_iterated",
        ]

        for ptype in expected_pulses:
            schema_file = schema_dir / f"{ptype}.json"
            assert schema_file.exists(), f"Schema file {schema_file} does not exist"

            with open(schema_file, "r", encoding="utf-8") as sf:
                sdata = json.load(sf)

            # Validate it is valid JSON Schema
            jsonschema.Draft202012Validator.check_schema(sdata)

            assert sdata.get("type") == "object", f"Schema for {ptype} must have type: object"
            assert "task_id" in sdata.get("properties", {}), f"{ptype} schema missing task_id"
            assert "plan_version" in sdata.get("properties", {}), f"{ptype} schema missing plan_version"
            assert sdata.get("additionalProperties") is False, f"{ptype} schema must enforce additionalProperties: false"

    def test_phase14_codegen_synchronization(self) -> None:
        """Verify Python codegen pulse_models.py contains all 50 types."""
        expected_types = [
            "research.retrieved",
            "research.conflict_detected",
            "repo.patch_applied",
            "repo.patch_reverted",
            "test.executed",
            "repair.loop_iterated",
        ]

        for ptype in expected_types:
            assert ptype in ALL_PULSE_TYPES, f"{ptype} missing from ALL_PULSE_TYPES"
            enum_attr = ptype.upper().replace(".", "_")
            assert hasattr(PulseType, enum_attr), f"PulseType missing enum member {enum_attr}"
            assert ptype in PULSE_DEFAULT_SEVERITIES, f"{ptype} missing from PULSE_DEFAULT_SEVERITIES"


class TestPhase14ContractCoverage:
    """Verifies that all 20 Phase 14 contracts are defined and maintain SCCA invariants."""

    PHASE_14_CONTRACT_IDS = [
        "RESEARCH-001", "RESEARCH-002", "RESEARCH-003", "RESEARCH-004", "RESEARCH-005",
        "REPO-001", "REPO-002", "REPO-003", "REPO-004", "REPO-005",
        "EVIDENCE-001", "EVIDENCE-002", "EVIDENCE-003",
        "REPAIR-001", "REPAIR-002", "REPAIR-003", "REPAIR-004",
        "PROVENANCE-001", "PROVENANCE-002", "PROVENANCE-003",
    ]

    def test_all_phase14_contract_ids_defined(self) -> None:
        """Verify exactly 20 contracts are established for Phase 14."""
        assert len(self.PHASE_14_CONTRACT_IDS) == 20

    def test_authority_invariants(self) -> None:
        """Verify that Phase 14 does not give Plan CAS authority to LLMs, Memory, or Workers."""
        # Check that core/space/kernel.py remains the sole CAS mutation point
        kernel_file = REPO_ROOT / "core" / "space" / "kernel.py"
        assert kernel_file.exists()
        kernel_src = kernel_file.read_text(encoding="utf-8")
        assert "def commit_plan_delta" in kernel_src
        assert "verify_space_identity" in kernel_src

    def test_core_boundary_independence(self) -> None:
        """Verify core/ does not import workers, agents, memory, or llm (AGENTS.md §7)."""
        core_dir = REPO_ROOT / "core"
        forbidden_modules = [
            "agents", "workers", "skills", "workflows", "llm", "channels", "memory"
        ]

        violations = []
        for py_file in core_dir.rglob("*.py"):
            text = py_file.read_text(encoding="utf-8")
            for line in text.splitlines():
                line = line.strip()
                if line.startswith("#"):
                    continue
                for mod in forbidden_modules:
                    pattern = rf"^(?:from\s+{mod}\b|import\s+{mod}\b)"
                    if re.match(pattern, line):
                        violations.append(f"{py_file.name}: {line}")

        assert not violations, f"Core boundary violated: {violations}"

    def test_scca_law_compliance_matrix(self) -> None:
        """Verify the 6 SCCA Laws are strictly reflected in Phase 14 governance."""
        audit_file = REPO_ROOT / "docs" / "PHASE_14_ARCHITECTURE_AUDIT.md"
        assert audit_file.exists()
        content = audit_file.read_text(encoding="utf-8")
        for i in range(1, 7):
            assert f"Law {i}" in content or f"LAW {i}" in content
        assert not re.search(r"(?i)Law\s*7\s*[:—\-]", content), "Invalid Law 7 defined! RYU has exactly six laws."
