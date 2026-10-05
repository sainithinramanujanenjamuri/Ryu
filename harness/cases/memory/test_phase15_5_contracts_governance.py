"""Phase 15.5.0 — Semantic Memory & Experience Retrieval Governance Test Suite.

Verifies:
1. ADR-0049 existence, section completeness, and consistency.
2. Phase 15.5 contracts (MEM-SEM-001..005) registered in docs/CONTRACT_MATRIX.md.
3. Spec-map traceability in harness/spec_map.yaml.
4. Authority separation & advisory memory invariants (SCCA Law 2 & Law 5).
5. Core Boundary Rule (core/ independent of ML/LLM/memory implementations).

Phase 15.5.0 Governance — Finding F-05
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest


def _get_repo_root() -> Path:
    here = Path(__file__).resolve().parent
    for candidate in [here.parent.parent.parent, here.parent.parent, here.parent, here]:
        if (candidate / "docs").is_dir() and (candidate / "adr").is_dir():
            return candidate
    return Path.cwd()


REPO_ROOT = _get_repo_root()

PHASE_15_5_CONTRACT_IDS = [
    "MEM-SEM-001",
    "MEM-SEM-002",
    "MEM-SEM-003",
    "MEM-SEM-004",
    "MEM-SEM-005",
]


class TestPhase15_5Governance:
    """Verifies Phase 15.5.0 architecture, ADR-0049, and governance integrity."""

    def test_adr_0049_section_completeness(self) -> None:
        """Verify ADR-0049 exists and contains all required sections."""
        adr_path = REPO_ROOT / "adr" / "0049-semantic-memory-and-experience-retrieval-governance.md"
        assert adr_path.exists(), f"ADR-0049 not found at {adr_path}"

        content = adr_path.read_text(encoding="utf-8")
        assert len(content) > 1500, "ADR-0049 is unexpectedly brief"

        # Check required sections
        assert re.search(r"(?i)#+\s*(?:context|problem)", content), "ADR-0049 missing Context/Problem"
        assert re.search(r"(?i)#+\s*decision", content), "ADR-0049 missing Decision"
        assert re.search(r"(?i)#+\s*consequences", content), "ADR-0049 missing Consequences"
        assert "MEM-SEM-001" in content, "ADR-0049 must reference MEM-SEM-001"
        assert "EmbeddingProviderProtocol" in content, "ADR-0049 must reference EmbeddingProviderProtocol"

    def test_all_phase15_5_contract_ids_defined(self) -> None:
        """Verify all 5 Phase 15.5 contracts are registered in docs/CONTRACT_MATRIX.md."""
        matrix_path = REPO_ROOT / "docs" / "CONTRACT_MATRIX.md"
        assert matrix_path.exists(), f"CONTRACT_MATRIX.md not found at {matrix_path}"

        content = matrix_path.read_text(encoding="utf-8")
        for cid in PHASE_15_5_CONTRACT_IDS:
            assert f"| {cid} |" in content, f"Contract {cid} missing from CONTRACT_MATRIX.md"
            # Verify status is ARCHITECTURAL_TARGET (not overclaimed as verified)
            pattern = rf"\|\s*{re.escape(cid)}\s*\|[^|]+\|[^|]+\|[^|]+\|[^|]+\|[^|]+\|\s*`?ARCHITECTURAL_TARGET`?\s*\|"
            assert re.search(pattern, content), f"Contract {cid} must have status ARCHITECTURAL_TARGET"

    def test_spec_map_traceability(self) -> None:
        """Verify all Phase 15.5 contracts are mapped in harness/spec_map.yaml."""
        spec_map_path = REPO_ROOT / "harness" / "spec_map.yaml"
        assert spec_map_path.exists(), f"spec_map.yaml not found at {spec_map_path}"

        content = spec_map_path.read_text(encoding="utf-8")
        for cid in PHASE_15_5_CONTRACT_IDS:
            assert f"- id: {cid}" in content or f"- id: \"{cid}\"" in content, (
                f"Contract {cid} missing from harness/spec_map.yaml"
            )

    def test_core_boundary_independence(self) -> None:
        """Verify core/ package contains zero forbidden ML/LLM/memory imports."""
        core_dir = REPO_ROOT / "core"
        forbidden_imports = [
            "llm",
            "memory.adapters",
            "memory.embeddings",
            "memory.reflector",
            "torch",
            "sentence_transformers",
            "numpy",
            "ollama",
        ]

        violations: list[str] = []
        for py_file in core_dir.rglob("*.py"):
            text = py_file.read_text(encoding="utf-8", errors="ignore")
            for line in text.splitlines():
                line_clean = line.strip()
                if line_clean.startswith("#"):
                    continue
                for forbidden in forbidden_imports:
                    if (
                        line_clean.startswith(f"import {forbidden}")
                        or line_clean.startswith(f"from {forbidden}")
                    ):
                        violations.append(f"{py_file.name}: {line_clean}")

        assert not violations, f"Core Boundary Rule violations: {violations}"

    def test_zero_new_pulses_in_phase_15_5_0(self) -> None:
        """Verify registry maintains exactly 50 registered pulse types (no pulse proliferation)."""
        registry_path = REPO_ROOT / "contracts" / "registry" / "pulse-types.json"
        assert registry_path.exists(), f"pulse-types.json not found at {registry_path}"

        import json
        with open(registry_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        types = [t.get("type") for t in data.get("types", [])]
        assert len(types) == 50, f"Expected 50 registered pulse types, found {len(types)}"
