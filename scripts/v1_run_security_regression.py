#!/usr/bin/env python3
"""
scripts/v1_run_security_regression.py

V1-004 — Consolidated Security Regression Battery.

Executes all 12 mandatory security proofs across the architecture:
1. Space Isolation (Space boundary + Space Memory isolation)
2. Capability Boundary (Admission Control)
3. Budget Enforcement (Strict financial accounting)
4. Secret Sanitization (Zero secret leakage)
5. Taint Propagation (Causal propagation)
6. Taint Clearance (Forward-only + non-mutating)
7. Prompt Injection Defense (Canary protection)
8. Grant Forgery Detection (Cryptographic HMAC validation)
9. Unsigned Artifact Rejection (Registry integrity)
10. Version Pinning (Supply chain immutability)
11. Human Approver Authenticity (token-hmac-v1 verification)
12. MDM Capability Allow-Lists (Restricted node enforcement)

Output:
- build/v1_evidence/reports/v1_security_regression_report.json
"""

from __future__ import annotations

import json
import os
import subprocess
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


SECURITY_TEST_BATTERY = [
    {
        "id": "SEC-01",
        "name": "Space Isolation",
        "target": "harness/cases/space/test_space_isolation.py",
        "description": "Cross-space pulse and resource access rejected",
    },
    {
        "id": "SEC-02",
        "name": "Capability Boundary",
        "target": "harness/cases/kernel/test_kernel_admission.py",
        "description": "Direct unrequested capability execution blocked",
    },
    {
        "id": "SEC-03",
        "name": "Budget Enforcement",
        "target": "core/capabilities/tests/test_admission.py",
        "description": "Zero calls admitted when budget is exhausted",
    },
    {
        "id": "SEC-04",
        "name": "Secret Sanitization",
        "target": "workers/tests/test_secret_sanitization.py",
        "description": "Secrets stripped before reaching persistence or pulse logs",
    },
    {
        "id": "SEC-05",
        "name": "Taint Propagation",
        "target": "harness/cases/pulse_bus/test_taint_chain.py",
        "description": "Untrusted external input propagates taint down causal chain",
    },
    {
        "id": "SEC-06",
        "name": "Taint Clearance Forward-Only",
        "target": "core/pulse_bus/tests/test_taint_module.py",
        "description": "Clearance applies forward-only without mutating historical pulses",
    },
    {
        "id": "SEC-07",
        "name": "Prompt Injection Defense",
        "target": "harness/cases/mcp/test_mcp_security.py",
        "description": "Canary token blocks indirect injection through MCP tools",
    },
    {
        "id": "SEC-08",
        "name": "Grant Forgery Detection",
        "target": "harness/cases/node/test_node_security_adversarial.py",
        "description": "Forged device grants rejected at node hardware boundary",
    },
    {
        "id": "SEC-09",
        "name": "Unsigned Artifact Rejection",
        "target": "harness/cases/skills/test_skill_governance.py",
        "description": "Unsigned or corrupted tools rejected by supply chain registry",
    },
    {
        "id": "SEC-10",
        "name": "Version Pinning & Immutability",
        "target": "harness/cases/skills/test_skill_supply_chain.py",
        "description": "Artifact version mutation rejected; pinned identity preserved",
    },
    {
        "id": "SEC-11",
        "name": "Human Approver Authenticity",
        "target": "channels/tests/test_approver_auth.py",
        "description": "token-hmac-v1 wire protocol validates authentic approver signature",
    },
    {
        "id": "SEC-12",
        "name": "MDM Capability Allow-Lists",
        "target": "harness/cases/node/test_restricted_node_tier.py",
        "description": "Restricted nodes enforce device-local allow-lists even with signed grants",
    },
]


def run_security_battery() -> dict[str, Any]:
    repo_root = find_repo_root()
    python_bin = sys.executable

    results = []
    all_passed = True

    env = os.environ.copy()
    env["PYTHONPATH"] = str(repo_root)
    # Ensure integration tests are enabled if services are running
    env["RYU_INTEGRATION_TESTS"] = "1"
    env["RYU_PG_HOST"] = "127.0.0.1"
    env["RYU_REDIS_HOST"] = "127.0.0.1"

    for test in SECURITY_TEST_BATTERY:
        target_path = repo_root / test["target"]
        if not target_path.exists():
            results.append({
                "id": test["id"],
                "name": test["name"],
                "target": test["target"],
                "passed": False,
                "error": f"Target test file {target_path} not found",
            })
            all_passed = False
            continue

        cmd = [python_bin, "-m", "pytest", str(target_path), "-q"]
        res = subprocess.run(
            cmd,
            cwd=str(repo_root),
            env=env,
            capture_output=True,
            text=True,
        )

        passed = (res.returncode == 0)
        if not passed:
            all_passed = False

        results.append({
            "id": test["id"],
            "name": test["name"],
            "target": test["target"],
            "description": test["description"],
            "passed": passed,
            "stdout": res.stdout[-500:] if len(res.stdout) > 500 else res.stdout,
            "stderr": res.stderr[-500:] if len(res.stderr) > 500 else res.stderr,
        })

    report = {
        "criterion": "V1-004",
        "title": "Consolidated Security Regression Battery",
        "status": "PASS" if all_passed else "FAIL",
        "battery_size": len(SECURITY_TEST_BATTERY),
        "passed_count": sum(1 for r in results if r["passed"]),
        "failed_count": sum(1 for r in results if not r["passed"]),
        "tests": results,
    }

    out_dir = repo_root / "build" / "v1_evidence" / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "v1_security_regression_report.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )

    return report


def main() -> int:
    print("============================================================")
    print("RYU AI — V1-004 Consolidated Security Regression Battery")
    print("============================================================")
    report = run_security_battery()
    for t in report["tests"]:
        status_str = "[PASS]" if t["passed"] else "[FAIL]"
        print(f"  {status_str} {t['id']}: {t['name']}")
        if not t["passed"]:
            print(f"         Error: {t.get('error') or t.get('stderr') or t.get('stdout')}")
    print("------------------------------------------------------------")
    print(f"V1-004 STATUS: {report['status']} ({report['passed_count']}/{report['battery_size']} passed)")
    print("============================================================")
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())

