#!/usr/bin/env python3
"""
scripts/dep_guard.py

AST-based dependency boundary guard.

RULE (AGENTS.md §4, ROADMAP Operating Principle 4):
  core/ MUST NOT import agents/, workers/, skills/, or workflows/.

HOW IT WORKS:
  - Recursively finds all .py files under core/.
  - Parses each file with Python's ast module.
  - Inspects all import and from-import statements.
  - Fails with a non-zero exit if any file under core/ imports
    from agents, workers, skills, or workflows.

This is a structural enforcement check; it tests the actual import graph,
not just filenames or string patterns.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

FORBIDDEN_PREFIXES = (
    "agents",
    "workers",
    "skills",
    "workflows",
    "llm",
    "openai",
    "anthropic",
    "ollama",
    "transformers",
)

BOUNDARY_DESCRIPTION = (
    "core/ MUST NOT import agents/, workers/, skills/, workflows/, llm/, or concrete LLM SDKs"
)


def find_repo_root() -> Path:
    here = Path(__file__).resolve().parent
    for candidate in [here.parent, here]:
        if (candidate / "core").is_dir():
            return candidate
    raise FileNotFoundError("Cannot locate repo root from script location.")


def extract_imports(source: str, filename: str) -> list[str]:
    """
    Parse Python source and return a list of all top-level import roots.
    E.g., 'from agents.base import X' -> 'agents'
          'import workers.python' -> 'workers'
    """
    try:
        tree = ast.parse(source, filename=filename)
    except SyntaxError as exc:
        print(f"  WARN: Could not parse {filename}: {exc}")
        return []

    roots: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.append(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                roots.append(node.module.split(".")[0])
    return roots


def check_core(repo_root: Path) -> list[tuple[str, str]]:
    """
    Scan all Python files under core/.
    Returns list of (filepath, forbidden_import) pairs.
    """
    violations: list[tuple[str, str]] = []
    core_dir = repo_root / "core"

    for py_file in core_dir.rglob("*.py"):
        source = py_file.read_text(encoding="utf-8")
        imports = extract_imports(source, str(py_file))
        for imp in imports:
            if imp in FORBIDDEN_PREFIXES:
                violations.append((str(py_file.relative_to(repo_root)), imp))

    return violations


def main() -> int:
    repo_root = find_repo_root()
    print(f"[dep-guard] Rule: {BOUNDARY_DESCRIPTION}")
    print(f"[dep-guard] Scanning: {repo_root / 'core'}")

    violations = check_core(repo_root)

    if violations:
        print(f"\n[dep-guard] FAIL -- {len(violations)} violation(s) found:")
        for filepath, imp in violations:
            print(f"  {filepath}  imports  '{imp}'")
        print("\n[dep-guard] The dependency direction must never point backward.")
        return 1
    else:
        print("[dep-guard] PASS -- No forbidden imports found in core/")
        return 0


if __name__ == "__main__":
    sys.exit(main())
