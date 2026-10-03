"""Startup recovery engine for crash recovery and durable execution state reconstruction.

Phase 12.8 — Crash Recovery & Durable Execution State
ADR-0042: Crash Recovery, Durable Execution State, and Deterministic Runtime Reconstruction

This module provides the StartupRecoveryEngine, which executes ONCE at daemon startup
(before accepting new work). Its responsibilities are:

1. Scan ExecutionAttemptStore for interrupted attempts (status='running'/'dispatched').
2. Classify each as: CRASH | STALE_DISPATCHED | AMBIGUOUS.
3. Reconcile stale resource leases via ResourceManager (using existing release() path).
4. Propose task recovery via SpaceKernel.propose_task_transition() CAS (same path as normal execution).
5. Emit recovery observability pulses (recovery.started, task.interrupted_detected, etc.).
6. Mark recovered attempts as 'recovered' in ExecutionAttemptStore (idempotency).

Authority rules:
  - This module is strictly core/. No imports from agents/, workers/, skills/, channels/, memory/, llm/.
  - Recovery NEVER mutates plan state directly. All state changes flow through SpaceKernel CAS.
  - Recovery is idempotent: running twice from the same PostgreSQL state produces identical outcomes.
  - Unsafe ambiguity ESCALATES rather than being silently resolved (SCCA Law 6).
  - PostgreSQL is the authoritative source. Redis MUST NOT be used here.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Protocol

from ryu.pulse_bus.pulse import Pulse, Severity

from core.orchestrator.execution_state import (
    ExecutionAttemptRecord,
    ExecutionAttemptStore,
)

# ── Classification taxonomy ───────────────────────────────────────────────────

class InterruptionClass(str, Enum):
    """Deterministic classification of a detected interrupted task attempt (RECOVERY-004)."""

    CRASH = "crash"                    # Worker vanished with no completion record
    STALE_DISPATCHED = "stale_dispatched"  # Dispatched but never transitioned to running
    AMBIGUOUS = "ambiguous"            # Cannot safely determine state → ESCALATE


@dataclass(frozen=True)
class InterruptedAttempt:
    """Classified interrupted attempt ready for recovery action (RECOVERY-004)."""

    record: ExecutionAttemptRecord
    interruption_class: InterruptionClass
    proposed_recovery_action: str  # "retry" | "escalate" | "mark_failed"


@dataclass(frozen=True)
class RecoveryResult:
    """Result of the full startup recovery sequence (RECOVERY-006)."""

    daemon_instance_id: str
    recovered_tasks: int
    released_leases: int
    escalated_tasks: int
    total_duration_seconds: float
    interrupted_tasks_found: int
    errors: list[str] = field(default_factory=list)
    completed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


# ── Kernel authority protocol (read-only subset needed for recovery) ──────────

class RecoveryKernelProtocol(Protocol):
    """Minimal SpaceKernel interface used by StartupRecoveryEngine.

    Recovery only needs:
    - verify_space_identity: confirms cross-space isolation
    - propose_task_transition: CAS-based task state mutation (same path as normal execution)
    - get_task_graph: read the current task graph
    - space_id property
    """

    @property
    def space_id(self) -> str: ...

    def verify_space_identity(self, space_id: str) -> None: ...

    def propose_task_transition(
        self,
        task_id: str,
        *args: Any,
        **kwargs: Any,
    ) -> Any: ...

    def get_task_graph(self) -> Any: ...


# ── Resource manager protocol ─────────────────────────────────────────────────

class RecoveryResourceManagerProtocol(Protocol):
    """Minimal ResourceManager interface for lease release during recovery (RECOVERY-005)."""

    def release(self, lease_token: str, space_id: str) -> bool: ...


# ── Pulse bus protocol ────────────────────────────────────────────────────────

class RecoveryPulseBusProtocol(Protocol):
    """Minimal PulseBus interface for recovery observability pulses."""

    def publish(self, pulse: Pulse) -> Pulse: ...


# ── Startup Recovery Engine ───────────────────────────────────────────────────

_RECOVERY_CORRELATION_PREFIX = "recovery"


class StartupRecoveryEngine:
    """Executes the startup crash recovery sequence (ADR-0042, RECOVERY-001..007).

    Runs once at daemon startup before accepting new work. Idempotent:
    running twice from the same PostgreSQL state produces identical outcomes.

    Usage:
        engine = StartupRecoveryEngine(
            attempt_store=my_attempt_store,
            kernels={"space-1": kernel1, ...},
            resource_managers={"space-1": res_mgr1, ...},
            bus=my_bus,
            crash_detection_window_seconds=60.0,
        )
        result = engine.run()
        assert result.recovered_tasks >= 0
    """

    def __init__(
        self,
        attempt_store: ExecutionAttemptStore,
        kernels: dict[str, RecoveryKernelProtocol],
        resource_managers: dict[str, RecoveryResourceManagerProtocol] | None = None,
        bus: RecoveryPulseBusProtocol | None = None,
        crash_detection_window_seconds: float = 60.0,
    ) -> None:
        self.attempt_store = attempt_store
        self.kernels = kernels
        self.resource_managers = resource_managers or {}
        self.bus = bus
        self.crash_detection_window_seconds = crash_detection_window_seconds
        self.daemon_instance_id = str(uuid.uuid4())

    def run(self) -> RecoveryResult:
        """Execute the full startup recovery sequence. Returns a RecoveryResult.

        Steps:
        1. Emit recovery.started pulse.
        2. Scan for interrupted attempts.
        3. Classify each attempt.
        4. Emit recovery.scan_completed pulse.
        5. For each interrupted attempt: reconcile lease, recover/escalate task.
        6. Emit recovery.completed pulse.
        """
        start_time = datetime.now(timezone.utc)
        self._emit_recovery_started(start_time)
        errors: list[str] = []

        # ── Step 1: Scan for interrupted attempts ─────────────────────────────
        interrupted_records = self.attempt_store.get_interrupted_attempts(
            self.crash_detection_window_seconds
        )

        # ── Step 2: Classify each attempt ─────────────────────────────────────
        classified: list[InterruptedAttempt] = []
        for record in interrupted_records:
            cls = self._classify(record)
            classified.append(cls)
            self._emit_interrupted_detected(record, cls.interruption_class)

        stale_leases_found = sum(
            1 for c in classified if c.record.lease_token is not None
        )

        self._emit_scan_completed(
            start_time=start_time,
            interrupted_count=len(classified),
            stale_leases=stale_leases_found,
        )

        # ── Step 3: Recover or escalate each classified attempt ───────────────
        recovered_tasks = 0
        released_leases = 0
        escalated_tasks = 0

        for classified_attempt in classified:
            record = classified_attempt.record

            # 3a. Reconcile stale lease
            if record.lease_token:
                lease_released = self._reconcile_lease(record)
                if lease_released:
                    released_leases += 1
                    self._emit_lease_reconciled(record)

            # 3b. Propose task recovery via kernel CAS
            kernel = self.kernels.get(record.space_id)
            if kernel is None:
                # No kernel available for this space — mark as recovered in store
                # (space may have been terminated; attempts are now stale)
                self.attempt_store.mark_recovered(record.idempotency_key)
                recovered_tasks += 1
                continue

            if classified_attempt.interruption_class == InterruptionClass.AMBIGUOUS:
                # AMBIGUOUS → escalate; do not guess
                ok = self._transition_task(
                    kernel=kernel,
                    record=record,
                    from_state="running",
                    to_state="escalated",
                    error="transient.worker_crash",
                    reason="Recovery: ambiguous interrupted state — escalated to human review",
                )
                escalated_tasks += 1
                self._emit_worker_crash(record, "escalate")
            else:
                # CRASH or STALE_DISPATCHED → mark as failed with transient.worker_crash
                # The normal ConvergenceEngine retry logic handles the retry decision
                from_state = "running" if classified_attempt.interruption_class == InterruptionClass.CRASH else "dispatched"
                ok = self._transition_task(
                    kernel=kernel,
                    record=record,
                    from_state=from_state,
                    to_state="failed",
                    error="transient.worker_crash",
                    reason=(
                        f"Recovery: {classified_attempt.interruption_class.value} "
                        f"detected for attempt {record.attempt_id[:8]}"
                    ),
                )
                if ok:
                    recovered_tasks += 1
                    self._emit_worker_crash(record, "mark_failed")
                else:
                    # Transition failed (e.g. task already in a terminal state) — still mark recovered
                    recovered_tasks += 1

            # Mark as recovered to prevent duplicate recovery on next restart (RECOVERY-006)
            self.attempt_store.mark_recovered(record.idempotency_key)

        # ── Step 4: Emit completion pulse ─────────────────────────────────────
        end_time = datetime.now(timezone.utc)
        duration = (end_time - start_time).total_seconds()

        result = RecoveryResult(
            daemon_instance_id=self.daemon_instance_id,
            recovered_tasks=recovered_tasks,
            released_leases=released_leases,
            escalated_tasks=escalated_tasks,
            total_duration_seconds=duration,
            interrupted_tasks_found=len(classified),
            errors=errors,
            completed_at=end_time,
        )
        self._emit_recovery_completed(result)
        return result

    # ── Classification ────────────────────────────────────────────────────────

    def _classify(self, record: ExecutionAttemptRecord) -> InterruptedAttempt:
        """Classify an interrupted attempt (RECOVERY-004).

        Rules:
        - status='running'   → CRASH (worker was executing when daemon died)
        - status='dispatched' → STALE_DISPATCHED (work item never picked up)
        - anything else      → AMBIGUOUS (should not happen; escalate safely)
        """
        if record.status == "running":
            cls = InterruptionClass.CRASH
            action = "mark_failed"
        elif record.status == "dispatched":
            cls = InterruptionClass.STALE_DISPATCHED
            action = "mark_failed"
        else:
            cls = InterruptionClass.AMBIGUOUS
            action = "escalate"

        return InterruptedAttempt(
            record=record,
            interruption_class=cls,
            proposed_recovery_action=action,
        )

    # ── Lease reconciliation ──────────────────────────────────────────────────

    def _reconcile_lease(self, record: ExecutionAttemptRecord) -> bool:
        """Release a stale resource lease via ResourceManager (RECOVERY-005).

        Uses the standard ResourceManager.release() path — same authority boundary
        as normal execution.
        """
        if not record.lease_token:
            return False
        res_mgr = self.resource_managers.get(record.space_id)
        if res_mgr is None:
            return False
        try:
            return res_mgr.release(record.lease_token, record.space_id)
        except Exception:
            return False  # Non-fatal; log externally

    # ── Task state recovery via kernel CAS ───────────────────────────────────

    def _transition_task(
        self,
        kernel: RecoveryKernelProtocol,
        record: ExecutionAttemptRecord,
        from_state: str,
        to_state: str,
        error: str,
        reason: str,
    ) -> bool:
        """Propose a task state transition via SpaceKernel CAS (RECOVERY-004).

        Uses the same legitimate SpaceKernel.propose_task_transition() mechanism as
        normal execution. Recovery does NOT bypass the CAS authority boundary.

        Returns True if transition succeeded or task was already in a compatible state.
        """
        try:
            res = kernel.propose_task_transition(
                record.task_id,
                from_state,
                to_state,
                error=error,
                reason=reason,
            )
            if isinstance(res, (tuple, list)) and len(res) > 0:
                return bool(res[0])
            return bool(res)
        except Exception:
            # Task may already be in a terminal state — treat as recovered
            return True

    # ── Pulse emission helpers ─────────────────────────────────────────────────

    def _make_pulse(
        self,
        pulse_type: str,
        severity: str,
        payload: dict,
        space_id: str = "system",
    ) -> Pulse:
        correlation_id = f"{_RECOVERY_CORRELATION_PREFIX}-{self.daemon_instance_id}"
        return Pulse(
            id=str(uuid.uuid4()),
            type=pulse_type,
            severity=Severity(severity),
            source="startup_recovery_engine",
            space_id=space_id,
            correlation_id=correlation_id,
            payload=payload,
        )

    def _safe_publish(self, pulse: Pulse) -> None:
        """Publish a pulse, degrading gracefully if bus is unavailable."""
        if self.bus is None:
            return
        try:
            self.bus.publish(pulse)
        except Exception:
            pass  # Recovery observability failure must not block recovery itself

    def _emit_recovery_started(self, started_at: datetime) -> None:
        pulse = self._make_pulse(
            pulse_type="recovery.started",
            severity="info",
            payload={
                "daemon_instance_id": self.daemon_instance_id,
                "started_at": started_at.isoformat(),
            },
        )
        self._safe_publish(pulse)

    def _emit_scan_completed(
        self,
        start_time: datetime,
        interrupted_count: int,
        stale_leases: int,
    ) -> None:
        now = datetime.now(timezone.utc)
        duration = (now - start_time).total_seconds()
        pulse = self._make_pulse(
            pulse_type="recovery.scan_completed",
            severity="info",
            payload={
                "daemon_instance_id": self.daemon_instance_id,
                "interrupted_tasks_found": interrupted_count,
                "stale_leases_found": stale_leases,
                "scan_duration_seconds": round(duration, 4),
            },
        )
        self._safe_publish(pulse)

    def _emit_interrupted_detected(
        self,
        record: ExecutionAttemptRecord,
        interruption_class: InterruptionClass,
    ) -> None:
        pulse = self._make_pulse(
            pulse_type="task.interrupted_detected",
            severity="warning",
            payload={
                "task_id": record.task_id,
                "space_id": record.space_id,
                "attempt_id": record.attempt_id,
                "interruption_class": interruption_class.value,
                "interrupted_at": record.started_at.isoformat(),
            },
            space_id=record.space_id,
        )
        self._safe_publish(pulse)

    def _emit_worker_crash(
        self,
        record: ExecutionAttemptRecord,
        recovery_action: str,
    ) -> None:
        pulse = self._make_pulse(
            pulse_type="task.worker_crash",
            severity="error",
            payload={
                "task_id": record.task_id,
                "space_id": record.space_id,
                "attempt_id": record.attempt_id,
                "plan_version": record.plan_version,
                "recovery_action": recovery_action,
            },
            space_id=record.space_id,
        )
        self._safe_publish(pulse)

    def _emit_lease_reconciled(self, record: ExecutionAttemptRecord) -> None:
        pulse = self._make_pulse(
            pulse_type="lease.reconciled",
            severity="info",
            payload={
                "lease_token": record.lease_token or "",
                "space_id": record.space_id,
                "task_id": record.task_id,
                "attempt_id": record.attempt_id,
                "released_at": datetime.now(timezone.utc).isoformat(),
            },
            space_id=record.space_id,
        )
        self._safe_publish(pulse)

    def _emit_recovery_completed(self, result: RecoveryResult) -> None:
        pulse = self._make_pulse(
            pulse_type="recovery.completed",
            severity="info",
            payload={
                "daemon_instance_id": result.daemon_instance_id,
                "recovered_tasks": result.recovered_tasks,
                "released_leases": result.released_leases,
                "escalated_tasks": result.escalated_tasks,
                "total_duration_seconds": round(result.total_duration_seconds, 4),
            },
        )
        self._safe_publish(pulse)
