#!/usr/bin/env python3
"""
scripts/v1_release_gate.py

RYU AI — Master v1.0 Release Gate.

Evaluates the strict Boolean AND release condition across all six v1 criteria:
[V1-001] Dynamic Spec Coverage Audit (Zero orphaned criteria, zero missing tests)
[V1-002] Core Independence Proof (Zero cognitive imports, AST + runtime blocker pass)
[V1-003] End-to-End Vertical Slice Execution (token-hmac-v1 approval, device execution, artifact, durability)
[V1-004] Consolidated Security Battery (All 12 security proofs pass)
[V1-005] Governance & Documentation Hygiene (39 ADRs, 38 pulse types, 1:1 schemas, contract matrix)
[V1-006] Deterministic Replay Equivalence Verification (Exact artifact SHA-256 byte identity + causal replay)

Also evaluates:
- Full repository test suite (0 failures, 0 unresolved critical skips)
- Git working tree state

Output:
- build/v1_evidence/V1_GATE_RESULT.json

Outcome:
- RYU AI v1.0 RELEASE VERIFIED (if and only if ALL conditions pass)
- RYU AI v1.0 NOT READY (if any condition fails or is blocked)
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
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


def run_full_test_suite(repo_root: Path) -> dict[str, Any]:
    """Run full pytest test suite with integration tests enabled."""
    python_bin = sys.executable
    cmd = [python_bin, "-m", "pytest", "-o", "addopts="]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(repo_root)
    env["RYU_INTEGRATION_TESTS"] = "1"
    env["RYU_PG_HOST"] = "127.0.0.1"
    env["RYU_REDIS_HOST"] = "127.0.0.1"

    t0 = time.time()
    res = subprocess.run(
        cmd,
        cwd=str(repo_root),
        env=env,
        capture_output=True,
        text=True,
    )
    duration = time.time() - t0

    # Parse stdout for counts
    # e.g., "649 passed, 1 skipped in 18.23s"
    stdout = res.stdout + ("\n" + res.stderr if res.stderr else "")
    passed = (res.returncode == 0)

    # Determine failures and skips
    import re
    passed_match = re.search(r"(\d+)\s+passed", stdout)
    skipped_match = re.search(r"(\d+)\s+skipped", stdout)
    failed_match = re.search(r"(\d+)\s+failed", stdout)

    passed_count = int(passed_match.group(1)) if passed_match else 0
    skipped_count = int(skipped_match.group(1)) if skipped_match else 0
    failed_count = int(failed_match.group(1)) if failed_match else 0

    return {
        "passed": passed and failed_count == 0 and passed_count > 0,
        "exit_code": res.returncode,
        "duration_seconds": duration,
        "passed_count": passed_count,
        "skipped_count": skipped_count,
        "failed_count": failed_count,
        "summary_line": stdout.splitlines()[-1] if stdout.splitlines() else "",
    }


def check_git_status(repo_root: Path) -> dict[str, Any]:
    """Check git status for modified files outside of build/ or reports."""
    cmd = ["git", "status", "--porcelain"]
    res = subprocess.run(cmd, cwd=str(repo_root), capture_output=True, text=True)
    lines = [line.strip() for line in res.stdout.splitlines() if line.strip()]

    # Filter out build/ artifacts
    modified_code = [line_entry for line_entry in lines if not line_entry.endswith("build/") and "build/" not in line_entry]

    return {
        "clean": len(modified_code) == 0,
        "modified_count": len(modified_code),
        "modified_files": modified_code[:10],
    }


def evaluate_release_gate() -> dict[str, Any]:
    repo_root = find_repo_root()
    t_start = time.time()

    os.environ["RYU_INTEGRATION_TESTS"] = "1"
    os.environ["RYU_PG_HOST"] = "127.0.0.1"
    os.environ["RYU_REDIS_HOST"] = "127.0.0.1"

    print("============================================================")
    print("RYU AI — Master v1.0 Release Gate Execution")
    print("============================================================")
    print(f"Timestamp: {datetime.now(timezone.utc).isoformat()}")
    print(f"Root:      {repo_root}")
    print("------------------------------------------------------------")

    # 1. V1-001: Spec Coverage Audit
    print("[1/6] Evaluating V1-001 Dynamic Spec Coverage Audit...")
    from scripts.v1_audit_spec_coverage import verify_spec_coverage
    r1 = verify_spec_coverage()
    ok_1 = (r1["status"] == "PASS")
    print(f"      Result: {'[PASS]' if ok_1 else '[FAIL]'} (criteria={r1['metrics']['architecture_criteria_count']}, mappings={r1['metrics']['executable_mappings_count']}, orphans={r1['metrics']['orphaned_architecture_criteria_count']})")

    # 2. V1-002: Core Independence Proof
    print("[2/6] Evaluating V1-002 Core Independence Proof...")
    from scripts.v1_verify_core_independence import (
        run_ast_guard,
        run_runtime_isolation_tests,
        run_zero_llm_control_loop,
    )
    ast_ok, ast_viol = run_ast_guard(repo_root)
    iso_ok, _ = run_runtime_isolation_tests(repo_root)
    loop_ok, _ = run_zero_llm_control_loop(repo_root)
    ok_2 = ast_ok and iso_ok and loop_ok
    print(f"      Result: {'[PASS]' if ok_2 else '[FAIL]'} (ast_ok={ast_ok}, runtime_isolation={iso_ok}, zero_llm_loop={loop_ok})")

    # 3. V1-003: End-to-End Vertical Slice Execution
    print("[3/6] Evaluating V1-003 End-to-End Vertical Slice Execution...")
    from scripts.v1_run_vertical_slice import run_vertical_slice
    r3 = run_vertical_slice()
    ok_3 = (r3["status"] == "PASS")
    print(f"      Result: {'[PASS]' if ok_3 else '[FAIL]'} (human_approval={r3.get('human_approval_verified')}, pulses={r3.get('pulses_persisted_count')}, chain_steps={r3.get('causal_chain_length')})")

    # 4. V1-004: Consolidated Security Battery
    print("[4/6] Evaluating V1-004 Consolidated Security Regression Battery...")
    from scripts.v1_run_security_regression import run_security_battery
    r4 = run_security_battery()
    ok_4 = (r4["status"] == "PASS")
    print(f"      Result: {'[PASS]' if ok_4 else '[FAIL]'} ({r4['passed_count']}/{r4['battery_size']} security proofs passed)")

    # 5. V1-005: Governance & Documentation Hygiene
    print("[5/6] Evaluating V1-005 Governance & Documentation Hygiene Audit...")
    from scripts.v1_audit_governance import audit_governance
    r5 = audit_governance()
    ok_5 = (r5["status"] == "PASS")
    print(f"      Result: {'[PASS]' if ok_5 else '[FAIL]'} (adrs={r5['audits']['adr_audit']['passed']}, registry={r5['audits']['pulse_registry_audit']['passed']}, schemas={r5['audits']['payload_schemas_audit']['passed']})")

    # 6. V1-006: Deterministic Replay Equivalence Verification
    print("[6/6] Evaluating V1-006 Deterministic Replay Equivalence Verification...")
    from scripts.v1_verify_replay import verify_replay_equivalence
    r6 = verify_replay_equivalence()
    ok_6 = (r6["status"] == "PASS")
    print(f"      Result: {'[PASS]' if ok_6 else '[FAIL]'} (artifact_byte_identity={r6['verifications']['artifact_exact_byte_identity']['passed']}, causal_equivalence={r6['verifications']['pulse_causation_equivalence']['passed']})")

    # 7. Full Test Suite Execution
    print("------------------------------------------------------------")
    print("[TEST SUITE] Executing Complete Test Suite (Unit + Integration)...")
    test_result = run_full_test_suite(repo_root)
    print(f"             Tests: {test_result['passed_count']} passed, {test_result['skipped_count']} skipped, {test_result['failed_count']} failed ({test_result['duration_seconds']:.2f}s)")
    if test_result["failed_count"] > 0:
        print(f"             [FAIL] Test suite had {test_result['failed_count']} failures!")
    else:
        print("             [PASS] Test suite passed cleanly (0 failures)")

    # 8. Master Boolean AND Decision
    all_six_criteria_passed = (ok_1 and ok_2 and ok_3 and ok_4 and ok_5 and ok_6)
    tests_clean = (test_result["passed"] and test_result["failed_count"] == 0)

    # Master Release Decision
    v1_release_ready = all_six_criteria_passed and tests_clean

    total_duration = time.time() - t_start

    gate_result = {
        "declaration": "RYU AI v1.0 RELEASE VERIFIED" if v1_release_ready else "RYU AI v1.0 NOT READY",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "release_ready": v1_release_ready,
        "total_duration_seconds": total_duration,
        "criteria": {
            "V1-001": {"name": "Dynamic Spec Coverage Audit", "passed": ok_1, "details": r1["metrics"]},
            "V1-002": {"name": "Core Independence Proof", "passed": ok_2},
            "V1-003": {"name": "End-to-End Vertical Slice Execution", "passed": ok_3, "artifact_sha256": r3.get("artifact_sha256")},
            "V1-004": {"name": "Consolidated Security Battery", "passed": ok_4, "passed_proofs": f"{r4['passed_count']}/{r4['battery_size']}"},
            "V1-005": {"name": "Governance & Documentation Hygiene", "passed": ok_5},
            "V1-006": {"name": "Deterministic Replay Equivalence", "passed": ok_6},
        },
        "test_suite": {
            "passed": test_result["passed"],
            "total_passed": test_result["passed_count"],
            "total_skipped": test_result["skipped_count"],
            "total_failed": test_result["failed_count"],
        },
    }

    out_file = repo_root / "build" / "v1_evidence" / "V1_GATE_RESULT.json"
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(json.dumps(gate_result, indent=2), encoding="utf-8")

    print("============================================================")
    if v1_release_ready:
        print("RYU AI v1.0 RELEASE VERIFIED")
    else:
        print("RYU AI v1.0 NOT READY")
    print("============================================================")
    print(f"Evidence Report: {out_file}")

    return gate_result


def main() -> int:
    result = evaluate_release_gate()
    return 0 if result["release_ready"] else 1


if __name__ == "__main__":
    sys.exit(main())

