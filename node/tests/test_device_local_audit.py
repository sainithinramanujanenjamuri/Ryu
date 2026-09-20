"""Unit tests for DeviceAuditLog: SHA-256 hash chaining, tamper detection, and verification.

CONTRACT_MATRIX NODE-008
ADR-0017, ADR-0019
"""

from __future__ import annotations

import json

import pytest

from node.audit import GENESIS_HASH, DeviceAuditLog
from node.contract import AuditCorruptionError


def test_audit_log_append_and_hash_chain(tmp_path) -> None:
    log_file = tmp_path / "audit.log.jsonl"
    audit = DeviceAuditLog(log_path=log_file)

    rec1 = audit.append(
        node_id="node-01",
        space_id="space-alpha",
        event_type="DEVICE_BOUND",
        grant_id="grant-01",
        device_id="gpu-0",
        operation_id="bind-01",
        result="SUCCESS",
        timestamp="2026-09-20T12:00:00Z",
    )

    assert rec1.seq == 1
    assert rec1.prev_hash == GENESIS_HASH
    assert rec1.record_hash == rec1.compute_hash()

    rec2 = audit.append(
        node_id="node-01",
        space_id="space-alpha",
        event_type="DEVICE_RELEASED",
        grant_id="grant-01",
        device_id="gpu-0",
        operation_id="bind-01",
        result="SUCCESS",
        timestamp="2026-09-20T12:05:00Z",
    )

    assert rec2.seq == 2
    assert rec2.prev_hash == rec1.record_hash
    assert rec2.record_hash == rec2.compute_hash()

    # Verify whole chain
    valid, count, err = audit.verify_chain()
    assert valid is True
    assert count == 2
    assert err is None


def test_audit_tamper_detection(tmp_path) -> None:
    log_file = tmp_path / "audit.log.jsonl"
    audit = DeviceAuditLog(log_path=log_file)

    audit.append(
        "node-01", "space-alpha", "DEVICE_BOUND", "grant-01", "gpu-0", "bind-01", "SUCCESS"
    )
    audit.append(
        "node-01", "space-alpha", "DEVICE_RELEASED", "grant-01", "gpu-0", "bind-01", "SUCCESS"
    )

    # Tamper with file contents on disk
    lines = log_file.read_text(encoding="utf-8").strip().split("\n")
    rec1_data = json.loads(lines[0])
    rec1_data["result"] = "TAMPERED_RESULT"
    lines[0] = json.dumps(rec1_data)
    log_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # Verify chain on tampered file must detect corruption
    audit_tampered = DeviceAuditLog.__new__(DeviceAuditLog)
    audit_tampered.log_path = log_file
    audit_tampered._records = []

    valid, count, err = audit_tampered.verify_chain()
    assert valid is False
    assert err is not None and "hash mismatch" in err


def test_reopen_corrupt_log_raises_audit_corruption_error(tmp_path) -> None:
    log_file = tmp_path / "audit.log.jsonl"
    audit = DeviceAuditLog(log_path=log_file)
    audit.append(
        "node-01", "space-alpha", "DEVICE_BOUND", "grant-01", "gpu-0", "bind-01", "SUCCESS"
    )

    # Tamper record
    lines = log_file.read_text(encoding="utf-8").strip().split("\n")
    rec_data = json.loads(lines[0])
    rec_data["space_id"] = "space-hacked"
    log_file.write_text(json.dumps(rec_data) + "\n", encoding="utf-8")

    # Opening existing corrupt log must immediately raise AuditCorruptionError
    with pytest.raises(AuditCorruptionError, match="Audit record hash mismatch"):
        DeviceAuditLog(log_path=log_file)
