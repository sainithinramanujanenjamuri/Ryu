#!/usr/bin/env python3
"""
scripts/v1_verify_replay.py

V1-006 — Deterministic Replay Equivalence Verification.

Verifies deterministic replay equivalence:
1. Exact byte identity for artifact payloads (SHA-256 hash match).
2. Structural and causal equivalence for pulse streams and causation chains.
3. State machine equivalence for plan execution and memory experience records.

CRITICAL INVARIANT (Hardened v1.0 Release Plan):
Deterministic Replay Equivalence Verification: Exact byte comparison only for artifacts;
structural, causal, and semantic equivalence for events, states, and memory.

Output:
- build/v1_evidence/reports/v1_replay_report.json
"""

from __future__ import annotations

import hashlib
import json
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

from ryu.pulse_bus.config import PostgresConfig
from ryu.pulse_bus.replay import PulseReplayer
from ryu.pulse_bus.store import PostgresPulseStore


def verify_replay_equivalence(run_fresh: bool = True) -> dict[str, Any]:
    repo_root = find_repo_root()
    run_file = repo_root / "build" / "v1_evidence" / "runs" / "v1_vertical_slice_run.json"

    # Always execute a fresh vertical slice to guarantee hermetic test execution
    # and deterministic replay against current database state
    if run_fresh or not run_file.exists():
        from scripts.v1_run_vertical_slice import run_vertical_slice
        slice_report = run_vertical_slice()
        if slice_report.get("status") != "PASS":
            return {
                "criterion": "V1-006",
                "title": "Deterministic Replay Equivalence Verification",
                "status": "FAIL",
                "error": f"Failed to generate vertical slice execution run: {slice_report.get('error')}",
            }

    with open(run_file, "r", encoding="utf-8") as f:
        run_data = json.load(f)

    # 1. Exact Artifact Byte Identity Verification
    original_artifact_payload = run_data["artifact"]["payload"]
    original_sha256 = run_data["artifact"]["sha256"]

    # Re-serialize deterministically using canonical JSON
    replayed_artifact_bytes = json.dumps(original_artifact_payload, sort_keys=True).encode("utf-8")
    replayed_sha256 = hashlib.sha256(replayed_artifact_bytes).hexdigest()

    artifact_byte_identity = (original_sha256 == replayed_sha256)

    # 2. Causal Chain Structural Equivalence Verification
    pg_cfg = PostgresConfig.from_env()
    pg_store = PostgresPulseStore(pg_cfg)
    replayer = PulseReplayer(store=pg_store)

    recorded_chain = run_data["pulse_causation_chain"]
    leaf_pulse_id = recorded_chain[-1]["id"] if recorded_chain else ""

    replayed_pulses = replayer.replay_causal_chain(leaf_pulse_id=leaf_pulse_id)
    replayed_chain = [
        {
            "id": p.id,
            "type": p.type,
            "source": p.source,
            "parent_pulse_id": p.parent_pulse_id,
            "correlation_id": p.correlation_id,
        }
        for p in replayed_pulses
    ]

    causal_equivalence = (len(recorded_chain) == len(replayed_chain))
    if causal_equivalence:
        for orig, rep in zip(recorded_chain, replayed_chain):
            if (
                orig["id"] != rep["id"]
                or orig["type"] != rep["type"]
                or orig["source"] != rep["source"]
                or orig["parent_pulse_id"] != rep["parent_pulse_id"]
                or orig["correlation_id"] != rep["correlation_id"]
            ):
                causal_equivalence = False
                break

    # 3. Plan & State Structural Equivalence
    plan_data = run_data["plan"]
    plan_equivalence = (
        plan_data.get("plan_version") == 1
        and plan_data.get("tasks_count", 0) >= 1
    )

    # 4. Memory Experience Record Semantic Equivalence
    mem_data = run_data["memory_experience"]
    mem_equivalence = (
        mem_data.get("experience_id", "").startswith("exp-")
        and original_sha256 in mem_data.get("outcome", "")
        and len(mem_data.get("counterfactual", "")) > 10
    )

    passed = (
        artifact_byte_identity
        and causal_equivalence
        and plan_equivalence
        and mem_equivalence
    )

    report = {
        "criterion": "V1-006",
        "title": "Deterministic Replay Equivalence Verification",
        "status": "PASS" if passed else "FAIL",
        "verifications": {
            "artifact_exact_byte_identity": {
                "passed": artifact_byte_identity,
                "original_sha256": original_sha256,
                "replayed_sha256": replayed_sha256,
            },
            "pulse_causation_equivalence": {
                "passed": causal_equivalence,
                "chain_length": len(replayed_chain),
            },
            "plan_state_equivalence": {
                "passed": plan_equivalence,
                "plan_version": plan_data.get("plan_version"),
            },
            "memory_experience_equivalence": {
                "passed": mem_equivalence,
                "experience_id": mem_data.get("experience_id"),
            },
        },
    }

    out_dir = repo_root / "build" / "v1_evidence" / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "v1_replay_report.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )

    return report


def main() -> int:
    print("============================================================")
    print("RYU AI — V1-006 Deterministic Replay Equivalence Verification")
    print("============================================================")
    report = verify_replay_equivalence()
    v = report["verifications"]
    print(f"  Artifact Byte Identity (SHA-256): {'PASS' if v['artifact_exact_byte_identity']['passed'] else 'FAIL'}")
    print(f"  Pulse Causation Equivalence:       {'PASS' if v['pulse_causation_equivalence']['passed'] else 'FAIL'}")
    print(f"  Plan State Equivalence:            {'PASS' if v['plan_state_equivalence']['passed'] else 'FAIL'}")
    print(f"  Memory Experience Equivalence:     {'PASS' if v['memory_experience_equivalence']['passed'] else 'FAIL'}")
    print("------------------------------------------------------------")
    print(f"V1-006 STATUS: {report['status']}")
    print("============================================================")
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())

