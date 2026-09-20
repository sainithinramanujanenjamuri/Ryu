"""Device-local append-only audit log with SHA-256 hash chaining.

Space-Centric Cognitive Architecture (SCCA) — Phase 7
spec §11 (Node Runtime), CONTRACT_MATRIX NODE-008
ADR-0017, ADR-0019
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from node.contract import AuditCorruptionError, AuditRecord

GENESIS_HASH = "0000000000000000000000000000000000000000000000000000000000000000"


class DeviceAuditLog:
    """Device-local append-only audit logger with tamper-evident cryptographic hash chaining.

    Each record includes:
    - Sequence number (strictly monotonically increasing)
    - ISO timestamp
    - Node ID and Space ID
    - Event type (e.g. DEVICE_BOUND, DEVICE_RELEASED, GRANT_REVOKED)
    - Grant ID, Device ID, Operation ID
    - Execution result string
    - Previous record hash (genesis hash for first entry)
    - Record hash = SHA256(seq | ts | node | space | type |
                           grant | device | op | result | prev_hash)
    """

    def __init__(self, log_path: str | Path | None = None) -> None:
        self.log_path = Path(log_path) if log_path else None
        self._records: list[AuditRecord] = []
        self._last_seq = 0
        self._last_hash = GENESIS_HASH

        if self.log_path and self.log_path.exists():
            self._load_existing()

    @property
    def last_seq(self) -> int:
        return self._last_seq

    @property
    def last_hash(self) -> str:
        return self._last_hash

    @property
    def records(self) -> list[AuditRecord]:
        return list(self._records)

    def _load_existing(self) -> None:
        """Load and verify existing records from file."""
        if not self.log_path or not self.log_path.exists():
            return

        with open(self.log_path, "r", encoding="utf-8") as f:
            for line_no, line in enumerate(f, start=1):
                clean_line = line.strip()
                if not clean_line:
                    continue
                try:
                    data = json.loads(clean_line)
                    record = AuditRecord.from_dict(data)
                except Exception as exc:
                    raise AuditCorruptionError(
                        f"Malformed audit record at line {line_no}: {exc}"
                    ) from exc

                # Verify chain
                if record.seq != self._last_seq + 1:
                    raise AuditCorruptionError(
                        f"Audit sequence gap at line {line_no}: "
                        f"expected {self._last_seq + 1}, got {record.seq}"
                    )
                if record.prev_hash != self._last_hash:
                    raise AuditCorruptionError(
                        f"Audit hash chain mismatch at line {line_no}: "
                        f"expected prev_hash {self._last_hash}, got {record.prev_hash}"
                    )
                computed = record.compute_hash()
                if record.record_hash != computed:
                    raise AuditCorruptionError(
                        f"Audit record hash mismatch at line {line_no}: "
                        f"expected {computed}, got {record.record_hash}"
                    )

                self._records.append(record)
                self._last_seq = record.seq
                self._last_hash = record.record_hash

    def append(
        self,
        node_id: str,
        space_id: str,
        event_type: str,
        grant_id: str,
        device_id: str,
        operation_id: str,
        result: str,
        timestamp: str | None = None,
    ) -> AuditRecord:
        """Append a new tamper-evident audit record to the log."""
        if not timestamp:
            timestamp = datetime.now(timezone.utc).isoformat()

        seq = self._last_seq + 1
        prev_hash = self._last_hash

        record = AuditRecord(
            seq=seq,
            timestamp=timestamp,
            node_id=node_id,
            space_id=space_id,
            event_type=event_type,
            grant_id=grant_id,
            device_id=device_id,
            operation_id=operation_id,
            result=result,
            prev_hash=prev_hash,
            record_hash="",
        )
        record.record_hash = record.compute_hash()

        self._records.append(record)
        self._last_seq = seq
        self._last_hash = record.record_hash

        # Write to file if persistent path is configured
        if self.log_path:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record.to_dict()) + "\n")

        return record

    def verify_chain(self) -> tuple[bool, int, str | None]:
        """Verify cryptographic integrity of the entire audit hash chain.

        Returns:
            (is_valid, record_count, error_message_if_any)
        """
        # If backed by file, verify file on disk to catch external tampering
        if self.log_path and self.log_path.exists():
            expected_prev = GENESIS_HASH
            expected_seq = 1
            count = 0
            with open(self.log_path, "r", encoding="utf-8") as f:
                for line_no, line in enumerate(f, start=1):
                    clean_line = line.strip()
                    if not clean_line:
                        continue
                    try:
                        data = json.loads(clean_line)
                        rec = AuditRecord.from_dict(data)
                    except Exception as exc:
                        return False, count, f"Line {line_no} corrupt JSON: {exc}"

                    if rec.seq != expected_seq:
                        return (
                            False,
                            count,
                            f"Line {line_no} sequence mismatch: "
                            f"expected {expected_seq}, got {rec.seq}",
                        )
                    if rec.prev_hash != expected_prev:
                        return (
                            False,
                            count,
                            f"Line {line_no} prev_hash mismatch: "
                            f"expected {expected_prev}, got {rec.prev_hash}",
                        )
                    computed = rec.compute_hash()
                    if rec.record_hash != computed:
                        return (
                            False,
                            count,
                            f"Line {line_no} hash mismatch: "
                            f"expected {computed}, got {rec.record_hash}",
                        )

                    expected_prev = rec.record_hash
                    expected_seq += 1
                    count += 1
            return True, count, None

        # Verify in-memory records
        expected_prev = GENESIS_HASH
        for i, rec in enumerate(self._records):
            expected_seq = i + 1
            if rec.seq != expected_seq:
                return (
                    False,
                    i,
                    f"Record {i} sequence mismatch: expected {expected_seq}, got {rec.seq}",
                )
            if rec.prev_hash != expected_prev:
                return (
                    False,
                    i,
                    f"Record {i} prev_hash mismatch: expected {expected_prev}, got {rec.prev_hash}",
                )
            computed = rec.compute_hash()
            if rec.record_hash != computed:
                return (
                    False,
                    i,
                    f"Record {i} hash mismatch: expected {computed}, got {rec.record_hash}",
                )
            expected_prev = rec.record_hash

        return True, len(self._records), None
