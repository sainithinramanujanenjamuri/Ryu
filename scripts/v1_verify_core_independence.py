#!/usr/bin/env python3
"""
scripts/v1_verify_core_independence.py

V1-002 — Core Independence Proof.

Proves that the deterministic core (core/) executes completely independently
of higher-level cognitive layers, LLMs, external providers, and agents.

Architectural Law:
- Dependency direction is strictly one-way (core/ MUST NOT import agents/, workers/,
  skills/, workflows/, llm/, channels/, memory/, or external LLM SDKs).

Three-Tier Verification:
1. Static AST dependency guard across all files under core/
2. Runtime isolation execution via custom sys.meta_path ImportBlocker
   blocking ("agents", "workers", "skills", "workflows", "llm", "channels", "memory")
   while executing the full core/ test battery.
3. Zero-LLM deterministic control loop execution proof.

Output: build/v1_evidence/reports/v1_core_independence_report.json
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from pathlib import Path

FORBIDDEN_MODULES = (
    "agents",
    "workers",
    "skills",
    "workflows",
    "llm",
    "channels",
    "memory",
    "click",
    "typer",
    "openai",
    "anthropic",
    "ollama",
    "transformers",
)


def find_repo_root() -> Path:
    here = Path(__file__).resolve().parent
    for candidate in [here.parent, here]:
        if (candidate / "core").is_dir():
            return candidate
    return Path.cwd()


REPO_ROOT = find_repo_root()
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def run_ast_guard(repo_root: Path) -> tuple[bool, list[str]]:
    """Run AST guard across all .py files in core/."""
    core_dir = repo_root / "core"
    violations: list[str] = []

    for py_file in core_dir.rglob("*.py"):
        # Skip pycache and tests if necessary, but check all source
        if "__pycache__" in str(py_file):
            continue
        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        except SyntaxError as e:
            violations.append(f"Syntax error in {py_file}: {e}")
            continue

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root_mod = alias.name.split(".")[0]
                    if root_mod in FORBIDDEN_MODULES:
                        violations.append(
                            f"{py_file.relative_to(repo_root)}:{node.lineno} forbidden import '{alias.name}'"
                        )
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    root_mod = node.module.split(".")[0]
                    if root_mod in FORBIDDEN_MODULES:
                        violations.append(
                            f"{py_file.relative_to(repo_root)}:{node.lineno} forbidden from-import '{node.module}'"
                        )

    return len(violations) == 0, violations


def run_runtime_isolation_tests(repo_root: Path) -> tuple[bool, str]:
    """
    Run pytest on core/ inside a subprocess with an import hook that raises
    ImportError if any forbidden module is imported at runtime.
    """
    runner_code = """
import sys
import importlib.abc

FORBIDDEN = {
    "agents", "workers", "skills", "workflows",
    "llm", "channels", "memory", "openai", "anthropic", "ollama"
}

class CoreIsolationBlocker(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        root = fullname.split(".")[0]
        if root in FORBIDDEN:
            raise ImportError(f"Architectural Boundary Violation: core is isolated from {fullname}")
        return None

sys.meta_path.insert(0, CoreIsolationBlocker())

import pytest
exit_code = pytest.main(["core", "-v", "--tb=short"])
sys.exit(exit_code)
"""

    python_bin = sys.executable
    cmd = [python_bin, "-c", runner_code]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(repo_root)

    res = subprocess.run(
        cmd,
        cwd=str(repo_root),
        env=env,
        capture_output=True,
        text=True,
    )

    passed = res.returncode == 0
    output = res.stdout + ("\n" + res.stderr if res.stderr else "")
    return passed, output


def run_zero_llm_control_loop(repo_root: Path) -> tuple[bool, str]:
    """Execute the zero-LLM deterministic control loop test."""
    test_target = "harness/cases/orchestrator/test_orchestrator_future.py::test_orchestrator_full_deterministic_loop"
    target_path = repo_root / "harness" / "cases" / "orchestrator" / "test_orchestrator_future.py"

    if not target_path.exists():
        return False, f"Target test file {target_path} not found"

    python_bin = sys.executable
    cmd = [python_bin, "-m", "pytest", test_target, "-v"]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(repo_root)

    res = subprocess.run(
        cmd,
        cwd=str(repo_root),
        env=env,
        capture_output=True,
        text=True,
    )

    passed = res.returncode == 0
    output = res.stdout + ("\n" + res.stderr if res.stderr else "")
    return passed, output


def main() -> int:
    repo_root = find_repo_root()
    print("============================================================")
    print("RYU AI — V1-002 Core Independence Proof")
    print("============================================================")

    # 1. AST Guard
    print("[1/3] Running AST Dependency Guard on core/...")
    ast_ok, ast_violations = run_ast_guard(repo_root)
    if ast_ok:
        print("  [PASS] AST Dependency Guard (0 forbidden imports)")
    else:
        print(f"  [FAIL] AST Dependency Guard ({len(ast_violations)} violations)")
        for v in ast_violations[:5]:
            print(f"    - {v}")

    # 2. Runtime Isolation
    print("[2/3] Running Core Tests with Runtime Import Blocker...")
    runtime_ok, runtime_log = run_runtime_isolation_tests(repo_root)
    if runtime_ok:
        print("  [PASS] Runtime Isolation Tests (all core tests pass with cognitive layers blocked)")
    else:
        print("  [FAIL] Runtime Isolation Tests")
        print(runtime_log[-1000:] if len(runtime_log) > 1000 else runtime_log)

    # 3. Zero-LLM Control Loop
    print("[3/3] Running Zero-LLM Deterministic Control Loop...")
    loop_ok, loop_log = run_zero_llm_control_loop(repo_root)
    if loop_ok:
        print("  [PASS] Zero-LLM Control Loop")
    else:
        print("  [FAIL] Zero-LLM Control Loop")
        print(loop_log[-1000:] if len(loop_log) > 1000 else loop_log)

    overall_passed = ast_ok and runtime_ok and loop_ok

    report = {
        "criterion": "V1-002",
        "title": "Core Independence Proof",
        "status": "PASS" if overall_passed else "FAIL",
        "ast_guard": {
            "passed": ast_ok,
            "violations_count": len(ast_violations),
            "violations": ast_violations,
        },
        "runtime_isolation": {
            "passed": runtime_ok,
            "blocked_modules": list(FORBIDDEN_MODULES),
        },
        "zero_llm_control_loop": {
            "passed": loop_ok,
        },
    }

    out_dir = repo_root / "build" / "v1_evidence" / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    report_file = out_dir / "v1_core_independence_report.json"
    report_file.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print("------------------------------------------------------------")
    print(f"V1-002 STATUS: {report['status']}")
    print("============================================================")

    return 0 if overall_passed else 1


if __name__ == "__main__":
    sys.exit(main())
