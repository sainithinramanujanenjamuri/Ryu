"""Harness Case: Dependency Direction (AGENTS.md §4, ADR-0033).

Acceptance Criterion:
The dependency direction is strictly one-way. core/ MUST NOT import memory/.
Enforced via AST inspection across all Python source files in core/.

AGENTS.md §4, ADR-0033 — Phase 10
"""

from __future__ import annotations

import ast
from pathlib import Path


def test_core_does_not_import_memory() -> None:
    """AGENTS.md §4: core/ must have zero imports from memory/ package."""
    repo_root = Path(__file__).resolve().parent.parent.parent.parent
    core_dir = repo_root / "core"

    assert core_dir.is_dir(), f"Cannot locate core directory at {core_dir}"

    violations: list[str] = []

    for py_file in core_dir.rglob("*.py"):
        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        except SyntaxError:
            continue

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root_pkg = alias.name.split(".")[0]
                    if root_pkg == "memory":
                        violations.append(
                            f"{py_file.relative_to(repo_root)}:{node.lineno} -> import {alias.name}"
                        )
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    root_pkg = node.module.split(".")[0]
                    if root_pkg == "memory":
                        violations.append(
                            f"{py_file.relative_to(repo_root)}:{node.lineno} -> from {node.module} import ..."
                        )

    assert not violations, (
        f"AGENTS.md §4 boundary violated: core/ imports from memory/:\n"
        + "\n".join(violations)
    )

