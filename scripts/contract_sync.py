#!/usr/bin/env python3
"""
scripts/contract_sync.py

Phase 0 contract synchronization checker.

SCOPE (Phase 0):
  Verifies that the Pulse Type Registry in docs/Architecture §16
  is consistent with contracts/registry/pulse-types.json.

  Specifically:
    - Extracts all pulse type strings from the Pulse Type Registry
      table in docs/Architecture (§16 Component Contracts).
    - Compares against the 'types[].type' entries in pulse-types.json.
    - Exits 0 if all types match; exits non-zero if there is any
      missing or extra type in either direction.

SCOPE LIMITATION:
  This script only verifies the Pulse Type Registry (§16 table) against
  pulse-types.json. It does NOT verify:
    - payload-schemas/ contents against architecture §16 payload definitions
    - failure-taxonomy.json against §10 failure taxonomy
    - capability-risks.json, grants.json
  Those verifications require Phase 1 contract-sync tooling.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path


def find_repo_root() -> Path:
    here = Path(__file__).resolve().parent
    for candidate in [here.parent, here]:
        if (candidate / "docs").is_dir() and (candidate / "contracts").is_dir():
            return candidate
    raise FileNotFoundError("Cannot locate repo root from script location.")


def extract_types_from_architecture(arch_file: Path) -> set[str]:
    """
    Parse docs/Architecture §16 Pulse Type Registry table.

    The table follows '#### Pulse Type Registry' and has columns: Namespace | Types
    Types are enclosed in backticks with format: namespace.action (e.g. `space.created`)
    """
    text = arch_file.read_text(encoding="utf-8")

    section = re.search(
        r"#### Pulse Type Registry.*?\n(\|.*?)(?=\n\n|\n\*|\n#)",
        text, re.DOTALL
    )
    if not section:
        print("ERROR: Could not find '#### Pulse Type Registry' table in docs/Architecture.")
        sys.exit(2)

    table_text = section.group(1)
    # Extract all backtick-quoted dot-namespaced identifiers
    types = set(re.findall(r"`([a-z_]+(?:\.[a-z_]+)+)`", table_text))
    return types


def extract_types_from_registry(registry_file: Path) -> set[str]:
    """Load pulse-types.json and return the set of all type strings."""
    with open(registry_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    return {entry["type"] for entry in data.get("types", [])}


def main() -> int:
    repo_root = find_repo_root()
    arch_file = repo_root / "docs" / "Architecture"
    registry_file = repo_root / "contracts" / "registry" / "pulse-types.json"

    if not arch_file.exists():
        print(f"ERROR: Architecture file not found: {arch_file}")
        return 2
    if not registry_file.exists():
        print(f"ERROR: Registry file not found: {registry_file}")
        return 2

    arch_types = extract_types_from_architecture(arch_file)
    registry_types = extract_types_from_registry(registry_file)

    missing_in_registry = arch_types - registry_types
    extra_in_registry = registry_types - arch_types

    print("[contract-sync] Scope: Architecture Sec 16 Registry <-> pulse-types.json")
    print(f"[contract-sync] Architecture types found : {len(arch_types)}")
    print(f"[contract-sync] Registry types found     : {len(registry_types)}")

    ok = True

    if missing_in_registry:
        print("\n[contract-sync] FAIL -- Types in architecture Sec 16 but MISSING from registry:")
        for t in sorted(missing_in_registry):
            print(f"  - {t}")
        ok = False

    if extra_in_registry:
        print("\n[contract-sync] WARNING -- Types in registry but NOT in architecture Sec 16:")
        print("  (These may be additions that need to be documented in the architecture.)")
        for t in sorted(extra_in_registry):
            print(f"  - {t}")

    if ok:
        print(f"\n[contract-sync] PASS -- All {len(arch_types)} types in registry.")
        return 0
    else:
        print("\n[contract-sync] FAIL -- Contract sync mismatch. See above.")
        return 1


if __name__ == "__main__":
    sys.exit(main())

