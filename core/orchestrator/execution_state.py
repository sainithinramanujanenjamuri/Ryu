"""Durable execution attempt and convergence state storage protocols.

Phase 12.8 — Crash Recovery & Durable Execution State
ADR-0042: Crash Recovery, Durable Execution State, and Deterministic Runtime Reconstruction

Defines:
  - ExecutionAttemptRecord  — immutable data contract for a persisted dispatch attempt.
  - ConvergenceStateRecord  — immutable data contract for persisted convergence counters.
  - ExecutionAttemptStore   — Protocol: two implementations (Postgres, InMemory).
  - ConvergenceStateStore   — Protocol: two implementations (Postgres, InMemory).

Authority rules:
  - This module is strictly part of core/. It must NOT import from agents/, workers/,
    skills/, channels/, memory/, or llm/. (AGENTS.md §7)
  - The stores are persistence proxies only. They do NOT own plan authority, lease
    authority, or admission authority. All such authority remains in SpaceKernel,
    ResourceManager, and AdmissionController respectively.
  - PostgreSQL remains the authoritative durable store. Redis MUST NOT be used here.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol, runtime_checkable


# ── Data contracts ────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ExecutionAttemptRecord:
    """Immutable record of one task dispatch attempt (RECOVERY-001).

    Written BEFORE worker dispatch and updated on completion or failure.
    The startup recovery engine scans for records with status in
    ('dispatched', 'running') to detect interrupted attempts.
    """

    attempt_id: str
    idempotency_key: str
    space_id: str
    task_id: str
    plan_version: int
    attempt_number: int
    capability: str
    status: str  # 'dispatched' | 'running' | 'completed' | 'failed' | 'recovered'
    worker_id: str | None = None
    lease_token: str | None = None
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: datetime | None = None
    failure_class: str | None = None
    failure_message: str | None = None
    exit_code: int | None = None
    artifact_sha256: str | None = None


@dataclass(frozen=True)
class ConvergenceStateRecord:
    """Immutable snapshot of persisted convergence counters for one (space, task) pair (RECOVERY-002, RECOVERY-003).

    Retry/replan budgets and failure fingerprints survive restart by
    loading this record at ConvergenceEngine startup for each task.
    """

    space_id: str
    task_id: str
    retry_count: int = 0
    replan_count: int = 0
    failure_fingerprints: tuple[str, ...] = field(default_factory=tuple)
    last_failure_class: str | None = None
    last_failure_at: datetime | None = None
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


# ── Protocols ─────────────────────────────────────────────────────────────────

@runtime_checkable
class ExecutionAttemptStore(Protocol):
    """Durable store protocol for execution attempt records (RECOVERY-001).

    Two implementations:
      - InMemoryExecutionAttemptStore  — for unit tests, no external services.
      - PostgresExecutionAttemptStore  — for production (not imported here; in memory/).
    """

    def save_attempt(self, record: ExecutionAttemptRecord) -> None:
        """Persist a new attempt record. Idempotent: second call with same idempotency_key updates in place."""
        ...

    def update_attempt_status(
        self,
        idempotency_key: str,
        status: str,
        *,
        completed_at: datetime | None = None,
        failure_class: str | None = None,
        failure_message: str | None = None,
        exit_code: int | None = None,
        artifact_sha256: str | None = None,
    ) -> None:
        """Update the status (and optional outcome fields) of an existing attempt."""
        ...

    def get_attempt(self, idempotency_key: str) -> ExecutionAttemptRecord | None:
        """Retrieve an attempt by idempotency key. Returns None if not found."""
        ...

    def get_attempts_for_task(self, space_id: str, task_id: str) -> list[ExecutionAttemptRecord]:
        """Return all attempts for a given (space_id, task_id) pair, ordered by started_at."""
        ...

    def get_interrupted_attempts(
        self,
        crash_detection_window_seconds: float = 60.0,
    ) -> list[ExecutionAttemptRecord]:
        """Return attempts stuck in 'dispatched' or 'running' beyond the crash detection window.

        These are candidates for startup recovery classification.
        """
        ...

    def mark_recovered(self, idempotency_key: str) -> None:
        """Mark an interrupted attempt as 'recovered' to prevent duplicate recovery on next startup."""
        ...


@runtime_checkable
class ConvergenceStateStore(Protocol):
    """Durable store protocol for convergence counters and fingerprints (RECOVERY-002, RECOVERY-003).

    Two implementations:
      - InMemoryConvergenceStateStore  — for unit tests, no external services.
      - PostgresConvergenceStateStore  — for production (not imported here; in memory/).
    """

    def load_state(self, space_id: str, task_id: str) -> ConvergenceStateRecord:
        """Load the convergence state for a (space, task). Returns default zero-state if not found."""
        ...

    def save_state(self, record: ConvergenceStateRecord) -> None:
        """Persist the convergence state for (space, task). Upserts if record already exists."""
        ...

    def increment_retry(self, space_id: str, task_id: str, failure_class: str | None = None) -> int:
        """Atomically increment retry_count and return the new value.

        Also records last_failure_class and last_failure_at.
        """
        ...

    def increment_replan(self, space_id: str, task_id: str) -> int:
        """Atomically increment replan_count and return the new value."""
        ...

    def add_fingerprint(self, space_id: str, task_id: str, fingerprint: str) -> None:
        """Append a failure fingerprint to the set for (space, task)."""
        ...

    def get_fingerprints(self, space_id: str, task_id: str) -> frozenset[str]:
        """Return all failure fingerprints for (space, task)."""
        ...


# ── In-Memory Implementations ─────────────────────────────────────────────────

class InMemoryExecutionAttemptStore:
    """Thread-safe in-memory ExecutionAttemptStore for unit tests (RECOVERY-001).

    Does NOT require PostgreSQL. Satisfies the ExecutionAttemptStore Protocol.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # keyed by idempotency_key
        self._records: dict[str, ExecutionAttemptRecord] = {}

    def save_attempt(self, record: ExecutionAttemptRecord) -> None:
        with self._lock:
            if record.idempotency_key not in self._records:
                self._records[record.idempotency_key] = record
            # If already exists: idempotent — do not overwrite (first writer wins for new records)

    def update_attempt_status(
        self,
        idempotency_key: str,
        status: str,
        *,
        completed_at: datetime | None = None,
        failure_class: str | None = None,
        failure_message: str | None = None,
        exit_code: int | None = None,
        artifact_sha256: str | None = None,
    ) -> None:
        with self._lock:
            existing = self._records.get(idempotency_key)
            if existing is None:
                return  # Not found; safe no-op (idempotent update is safe to miss)
            from dataclasses import replace as dc_replace
            updated = dc_replace(
                existing,
                status=status,
                completed_at=completed_at if completed_at is not None else existing.completed_at,
                failure_class=failure_class if failure_class is not None else existing.failure_class,
                failure_message=failure_message if failure_message is not None else existing.failure_message,
                exit_code=exit_code if exit_code is not None else existing.exit_code,
                artifact_sha256=artifact_sha256 if artifact_sha256 is not None else existing.artifact_sha256,
            )
            self._records[idempotency_key] = updated

    def get_attempt(self, idempotency_key: str) -> ExecutionAttemptRecord | None:
        with self._lock:
            return self._records.get(idempotency_key)

    def get_attempts_for_task(self, space_id: str, task_id: str) -> list[ExecutionAttemptRecord]:
        with self._lock:
            return sorted(
                [r for r in self._records.values() if r.space_id == space_id and r.task_id == task_id],
                key=lambda r: r.started_at,
            )

    def get_interrupted_attempts(
        self,
        crash_detection_window_seconds: float = 60.0,
    ) -> list[ExecutionAttemptRecord]:
        cutoff = datetime.now(timezone.utc).timestamp() - crash_detection_window_seconds
        with self._lock:
            return [
                r for r in self._records.values()
                if r.status in ("dispatched", "running")
                and r.started_at.timestamp() < cutoff
            ]

    def mark_recovered(self, idempotency_key: str) -> None:
        self.update_attempt_status(idempotency_key, "recovered")

    def all_records(self) -> list[ExecutionAttemptRecord]:
        """Test helper: return all stored records."""
        with self._lock:
            return list(self._records.values())


class InMemoryConvergenceStateStore:
    """Thread-safe in-memory ConvergenceStateStore for unit tests (RECOVERY-002, RECOVERY-003).

    Does NOT require PostgreSQL. Satisfies the ConvergenceStateStore Protocol.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # keyed by (space_id, task_id)
        self._states: dict[tuple[str, str], ConvergenceStateRecord] = {}
        self._fingerprints: dict[tuple[str, str], list[str]] = {}

    def _key(self, space_id: str, task_id: str) -> tuple[str, str]:
        return (space_id, task_id)

    def load_state(self, space_id: str, task_id: str) -> ConvergenceStateRecord:
        with self._lock:
            return self._states.get(
                self._key(space_id, task_id),
                ConvergenceStateRecord(space_id=space_id, task_id=task_id),
            )

    def save_state(self, record: ConvergenceStateRecord) -> None:
        with self._lock:
            self._states[self._key(record.space_id, record.task_id)] = record
            # Sync fingerprints list from record
            self._fingerprints[self._key(record.space_id, record.task_id)] = list(
                record.failure_fingerprints
            )

    def increment_retry(self, space_id: str, task_id: str, failure_class: str | None = None) -> int:
        with self._lock:
            key = self._key(space_id, task_id)
            existing = self._states.get(key, ConvergenceStateRecord(space_id=space_id, task_id=task_id))
            now = datetime.now(timezone.utc)
            fps = self._fingerprints.get(key, [])
            updated = ConvergenceStateRecord(
                space_id=space_id,
                task_id=task_id,
                retry_count=existing.retry_count + 1,
                replan_count=existing.replan_count,
                failure_fingerprints=tuple(fps),
                last_failure_class=failure_class or existing.last_failure_class,
                last_failure_at=now,
                updated_at=now,
            )
            self._states[key] = updated
            return updated.retry_count

    def increment_replan(self, space_id: str, task_id: str) -> int:
        with self._lock:
            key = self._key(space_id, task_id)
            existing = self._states.get(key, ConvergenceStateRecord(space_id=space_id, task_id=task_id))
            now = datetime.now(timezone.utc)
            fps = self._fingerprints.get(key, [])
            updated = ConvergenceStateRecord(
                space_id=space_id,
                task_id=task_id,
                retry_count=existing.retry_count,
                replan_count=existing.replan_count + 1,
                failure_fingerprints=tuple(fps),
                last_failure_class=existing.last_failure_class,
                last_failure_at=existing.last_failure_at,
                updated_at=now,
            )
            self._states[key] = updated
            return updated.replan_count

    def add_fingerprint(self, space_id: str, task_id: str, fingerprint: str) -> None:
        with self._lock:
            key = self._key(space_id, task_id)
            fps = self._fingerprints.setdefault(key, [])
            if fingerprint not in fps:
                fps.append(fingerprint)
            # Update stored record to keep fingerprints in sync
            existing = self._states.get(key, ConvergenceStateRecord(space_id=space_id, task_id=task_id))
            now = datetime.now(timezone.utc)
            updated = ConvergenceStateRecord(
                space_id=space_id,
                task_id=task_id,
                retry_count=existing.retry_count,
                replan_count=existing.replan_count,
                failure_fingerprints=tuple(fps),
                last_failure_class=existing.last_failure_class,
                last_failure_at=existing.last_failure_at,
                updated_at=now,
            )
            self._states[key] = updated

    def get_fingerprints(self, space_id: str, task_id: str) -> frozenset[str]:
        with self._lock:
            return frozenset(self._fingerprints.get(self._key(space_id, task_id), []))

    def all_states(self) -> list[ConvergenceStateRecord]:
        """Test helper: return all stored records."""
        with self._lock:
            return list(self._states.values())
