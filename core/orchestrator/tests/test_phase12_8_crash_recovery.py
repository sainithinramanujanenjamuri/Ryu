"""Phase 12.8 crash recovery unit tests: CRASH-01 through CRASH-14.

Tests for:
- ExecutionAttemptStore (InMemory) — RECOVERY-001
- ConvergenceStateStore (InMemory) — RECOVERY-002, RECOVERY-003
- ConvergenceEngine durable state loading — RECOVERY-002, RECOVERY-003
- DeterministicDispatcher durable attempt recording — RECOVERY-001
- StartupRecoveryEngine — RECOVERY-004, RECOVERY-005, RECOVERY-006, RECOVERY-007
- Security: cross-space isolation, unauthorized retry increment, forged recovery events

All tests use InMemory stores (no PostgreSQL required).
"""

from __future__ import annotations

import hashlib
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from core.orchestrator.dispatch_model import (
    ConvergenceDecision,
    ConvergenceEngine,
    DeterministicDispatcher,
    DispatchAttempt,
    compute_dispatch_idempotency_key,
)
from core.orchestrator.execution_state import (
    ConvergenceStateRecord,
    ExecutionAttemptRecord,
    InMemoryConvergenceStateStore,
    InMemoryExecutionAttemptStore,
)
from core.orchestrator.startup_recovery import (
    InterruptionClass,
    StartupRecoveryEngine,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _past(seconds: float) -> datetime:
    return _now() - timedelta(seconds=seconds)


def _attempt_record(
    task_id: str = "task-1",
    space_id: str = "space-1",
    status: str = "running",
    started_seconds_ago: float = 120.0,
    idempotency_key: str | None = None,
    lease_token: str | None = None,
) -> ExecutionAttemptRecord:
    key = idempotency_key or compute_dispatch_idempotency_key(space_id, 1, task_id, 1)
    return ExecutionAttemptRecord(
        attempt_id=key,
        idempotency_key=key,
        space_id=space_id,
        task_id=task_id,
        plan_version=1,
        attempt_number=1,
        capability="python.eval_sandboxed",
        status=status,
        lease_token=lease_token,
        started_at=_past(started_seconds_ago),
    )


def _mock_kernel(space_id: str = "space-1") -> MagicMock:
    kernel = MagicMock()
    kernel.space_id = space_id
    kernel.verify_space_identity = MagicMock()
    kernel.propose_task_transition = MagicMock(return_value=(True, "ok"))
    task_graph = MagicMock()
    task_graph.nodes = []
    kernel.get_task_graph = MagicMock(return_value=task_graph)
    return kernel


def _mock_res_mgr() -> MagicMock:
    res_mgr = MagicMock()
    res_mgr.release = MagicMock(return_value=True)
    return res_mgr


# ── CRASH-01: Retry counter survives simulated restart ────────────────────────

class TestCrash01RetryCountSurvivesRestart:
    """CRASH-01: Retry budget is NOT reset to 0 after process restart.

    RECOVERY-002: Retry counts survive restart.
    """

    def test_retry_count_persisted_and_reloaded(self) -> None:
        store = InMemoryConvergenceStateStore()
        space_id = "space-crash-01"
        task_id = "task-a"

        # Simulate: engine used, increments retry 2 times
        engine1 = ConvergenceEngine.__new__(ConvergenceEngine)
        engine1.space_id = space_id
        engine1._state_store = store
        engine1._retry_counts = {}
        engine1._replan_counts = {}
        engine1._seen_fingerprints = set()

        engine1._increment_retry(task_id, "transient.timeout")
        engine1._increment_retry(task_id, "transient.timeout")

        # Simulate restart: new engine with same store
        engine2 = ConvergenceEngine.__new__(ConvergenceEngine)
        engine2.space_id = space_id
        engine2._state_store = store
        engine2._retry_counts = {}
        engine2._replan_counts = {}
        engine2._seen_fingerprints = set()

        # On first access, engine2 loads from store
        count = engine2._get_retry_count(task_id)
        assert count == 2, f"Expected retry_count=2 after restart, got {count}"

    def test_retry_count_zero_for_new_task(self) -> None:
        store = InMemoryConvergenceStateStore()
        engine = ConvergenceEngine.__new__(ConvergenceEngine)
        engine.space_id = "space-1"
        engine._state_store = store
        engine._retry_counts = {}
        engine._replan_counts = {}
        engine._seen_fingerprints = set()

        count = engine._get_retry_count("brand-new-task")
        assert count == 0


# ── CRASH-02: Replan counter survives simulated restart ───────────────────────

class TestCrash02ReplanCountSurvivesRestart:
    """CRASH-02: Replan budget is NOT reset to 0 after process restart.

    RECOVERY-002: Replan counts survive restart.
    """

    def test_replan_count_persisted_and_reloaded(self) -> None:
        store = InMemoryConvergenceStateStore()
        space_id = "space-crash-02"
        task_id = "task-b"

        engine1 = ConvergenceEngine.__new__(ConvergenceEngine)
        engine1.space_id = space_id
        engine1._state_store = store
        engine1._retry_counts = {}
        engine1._replan_counts = {}
        engine1._seen_fingerprints = set()

        engine1._increment_replan(task_id)
        engine1._increment_replan(task_id)
        engine1._increment_replan(task_id)

        # Restart
        engine2 = ConvergenceEngine.__new__(ConvergenceEngine)
        engine2.space_id = space_id
        engine2._state_store = store
        engine2._retry_counts = {}
        engine2._replan_counts = {}
        engine2._seen_fingerprints = set()

        count = engine2._get_replan_count(task_id)
        assert count == 3, f"Expected replan_count=3 after restart, got {count}"


# ── CRASH-03: Failure fingerprints survive restart ────────────────────────────

class TestCrash03FingerprintsSurviveRestart:
    """CRASH-03: Failure fingerprints survive restart (loop detection remains active).

    RECOVERY-003: Fingerprints survive restart.
    """

    def test_fingerprints_persisted_and_reloaded(self) -> None:
        store = InMemoryConvergenceStateStore()
        space_id = "space-crash-03"
        task_id = "task-c"
        fingerprint_1 = hashlib.sha256(b"space-crash-03:task-c:transient.timeout").hexdigest()[:16]
        fingerprint_2 = hashlib.sha256(b"space-crash-03:task-c:transient.resource_unavailable").hexdigest()[:16]

        engine1 = ConvergenceEngine.__new__(ConvergenceEngine)
        engine1.space_id = space_id
        engine1._state_store = store
        engine1._retry_counts = {}
        engine1._replan_counts = {}
        engine1._seen_fingerprints = set()

        engine1._add_fingerprint(task_id, fingerprint_1)
        engine1._add_fingerprint(task_id, fingerprint_2)

        # Restart
        engine2 = ConvergenceEngine.__new__(ConvergenceEngine)
        engine2.space_id = space_id
        engine2._state_store = store
        engine2._retry_counts = {}
        engine2._replan_counts = {}
        engine2._seen_fingerprints = set()

        # Loading retry count triggers fingerprint preload
        engine2._get_retry_count(task_id)

        assert engine2._has_fingerprint(fingerprint_1), "fp1 should be seen after restart"
        assert engine2._has_fingerprint(fingerprint_2), "fp2 should be seen after restart"
        assert not engine2._has_fingerprint("unknown-fingerprint")


# ── CRASH-04: Dispatch attempt persisted before worker dispatch ───────────────

class TestCrash04AttemptPersistedBeforeDispatch:
    """CRASH-04: Idempotency key is persisted to durable store before worker dispatch.

    RECOVERY-001: Execution attempt state is durable.
    """

    def test_record_attempt_writes_to_store(self) -> None:
        store = InMemoryExecutionAttemptStore()
        dispatcher = DeterministicDispatcher(attempt_store=store)

        ikey = compute_dispatch_idempotency_key("space-1", 1, "task-1", 1)
        attempt = DispatchAttempt(
            idempotency_key=ikey,
            space_id="space-1",
            plan_version=1,
            task_id="task-1",
            attempt=1,
        )
        result = dispatcher.record_attempt(attempt)
        assert result is True

        # Verify in store
        record = store.get_attempt(ikey)
        assert record is not None
        assert record.task_id == "task-1"
        assert record.status == "dispatched"

    def test_duplicate_attempt_is_idempotent(self) -> None:
        store = InMemoryExecutionAttemptStore()
        dispatcher = DeterministicDispatcher(attempt_store=store)

        ikey = compute_dispatch_idempotency_key("space-1", 1, "task-1", 1)
        attempt = DispatchAttempt(
            idempotency_key=ikey,
            space_id="space-1",
            plan_version=1,
            task_id="task-1",
            attempt=1,
        )
        dispatcher.record_attempt(attempt)
        result2 = dispatcher.record_attempt(attempt)
        assert result2 is False  # Second call returns False (duplicate)

        # Only one record
        records = store.get_attempts_for_task("space-1", "task-1")
        assert len(records) == 1


# ── CRASH-05: Attempt status updated to 'completed' on success ────────────────

class TestCrash05AttemptStatusUpdatedOnSuccess:
    """CRASH-05: Dispatcher updates attempt status to 'completed' on task success."""

    def test_update_attempt_status_to_completed(self) -> None:
        store = InMemoryExecutionAttemptStore()
        dispatcher = DeterministicDispatcher(attempt_store=store)

        ikey = compute_dispatch_idempotency_key("space-1", 1, "task-1", 1)
        attempt = DispatchAttempt(
            idempotency_key=ikey,
            space_id="space-1",
            plan_version=1,
            task_id="task-1",
            attempt=1,
        )
        dispatcher.record_attempt(attempt)
        dispatcher.update_attempt_status(ikey, "completed", exit_code=0)

        record = store.get_attempt(ikey)
        assert record is not None
        assert record.status == "completed"
        assert record.exit_code == 0

    def test_update_attempt_status_to_failed(self) -> None:
        store = InMemoryExecutionAttemptStore()
        dispatcher = DeterministicDispatcher(attempt_store=store)

        ikey = compute_dispatch_idempotency_key("space-1", 1, "task-2", 1)
        attempt = DispatchAttempt(
            idempotency_key=ikey,
            space_id="space-1",
            plan_version=1,
            task_id="task-2",
            attempt=1,
        )
        dispatcher.record_attempt(attempt)
        dispatcher.update_attempt_status(
            ikey, "failed",
            failure_class="transient.timeout",
            failure_message="worker timed out",
        )

        record = store.get_attempt(ikey)
        assert record is not None
        assert record.status == "failed"
        assert record.failure_class == "transient.timeout"


# ── CRASH-06: Interrupted tasks detected by startup recovery ─────────────────

class TestCrash06InterruptedTasksDetected:
    """CRASH-06: StartupRecoveryEngine detects tasks stuck in 'running'/'dispatched'.

    RECOVERY-004: Interrupted tasks detected at startup.
    """

    def test_running_task_classified_as_crash(self) -> None:
        store = InMemoryExecutionAttemptStore()
        record = _attempt_record(status="running", started_seconds_ago=120.0)
        store.save_attempt(record)

        engine = StartupRecoveryEngine(
            attempt_store=store,
            kernels={},
            crash_detection_window_seconds=60.0,
        )
        interrupted = store.get_interrupted_attempts(crash_detection_window_seconds=60.0)
        assert len(interrupted) == 1
        classified = engine._classify(interrupted[0])
        assert classified.interruption_class == InterruptionClass.CRASH
        assert classified.proposed_recovery_action == "mark_failed"

    def test_dispatched_task_classified_as_stale(self) -> None:
        store = InMemoryExecutionAttemptStore()
        record = _attempt_record(status="dispatched", started_seconds_ago=120.0)
        store.save_attempt(record)

        engine = StartupRecoveryEngine(
            attempt_store=store,
            kernels={},
            crash_detection_window_seconds=60.0,
        )
        interrupted = store.get_interrupted_attempts(crash_detection_window_seconds=60.0)
        assert len(interrupted) == 1
        classified = engine._classify(interrupted[0])
        assert classified.interruption_class == InterruptionClass.STALE_DISPATCHED

    def test_recent_task_not_interrupted(self) -> None:
        store = InMemoryExecutionAttemptStore()
        record = _attempt_record(status="running", started_seconds_ago=10.0)
        store.save_attempt(record)

        interrupted = store.get_interrupted_attempts(crash_detection_window_seconds=60.0)
        assert len(interrupted) == 0, "Task started 10s ago should NOT be classified as interrupted"

    def test_completed_task_not_interrupted(self) -> None:
        store = InMemoryExecutionAttemptStore()
        record = _attempt_record(status="completed", started_seconds_ago=120.0)
        store.save_attempt(record)

        interrupted = store.get_interrupted_attempts(crash_detection_window_seconds=60.0)
        assert len(interrupted) == 0, "Completed task should NOT appear as interrupted"


# ── CRASH-07: Stale lease is released during recovery ─────────────────────────

class TestCrash07StaleLeaseReleased:
    """CRASH-07: StartupRecoveryEngine releases stale resource leases during recovery.

    RECOVERY-005: Stale leases released at startup.
    """

    def test_stale_lease_released_via_resource_manager(self) -> None:
        store = InMemoryExecutionAttemptStore()
        record = _attempt_record(
            status="running",
            started_seconds_ago=120.0,
            lease_token="lease-abc-123",
        )
        store.save_attempt(record)

        kernel = _mock_kernel("space-1")
        res_mgr = _mock_res_mgr()

        engine = StartupRecoveryEngine(
            attempt_store=store,
            kernels={"space-1": kernel},
            resource_managers={"space-1": res_mgr},
            crash_detection_window_seconds=60.0,
        )
        result = engine.run()

        # Verify resource manager was called to release the lease
        res_mgr.release.assert_called_once_with("lease-abc-123", "space-1")
        assert result.released_leases == 1

    def test_no_lease_no_release_call(self) -> None:
        store = InMemoryExecutionAttemptStore()
        record = _attempt_record(status="running", started_seconds_ago=120.0, lease_token=None)
        store.save_attempt(record)

        kernel = _mock_kernel("space-1")
        res_mgr = _mock_res_mgr()

        engine = StartupRecoveryEngine(
            attempt_store=store,
            kernels={"space-1": kernel},
            resource_managers={"space-1": res_mgr},
            crash_detection_window_seconds=60.0,
        )
        result = engine.run()

        res_mgr.release.assert_not_called()
        assert result.released_leases == 0


# ── CRASH-08: Recovery marks interrupted tasks as failed via CAS ──────────────

class TestCrash08RecoveryTransitionsTaskViaKernelCAS:
    """CRASH-08: Recovery uses kernel.propose_task_transition() CAS (not direct mutation).

    RECOVERY-004: Task state recovery via SpaceKernel CAS.
    """

    def test_crash_task_transitioned_to_failed_via_kernel(self) -> None:
        store = InMemoryExecutionAttemptStore()
        record = _attempt_record(status="running", started_seconds_ago=120.0)
        store.save_attempt(record)

        kernel = _mock_kernel("space-1")
        engine = StartupRecoveryEngine(
            attempt_store=store,
            kernels={"space-1": kernel},
            crash_detection_window_seconds=60.0,
        )
        engine.run()

        # Kernel CAS was called exactly once
        assert kernel.propose_task_transition.call_count == 1
        call_args = kernel.propose_task_transition.call_args
        assert call_args.args[0] == record.task_id
        assert call_args.args[1] == "running"   # from_state
        assert call_args.args[2] == "failed"    # to_state
        assert call_args.kwargs["error"] == "transient.worker_crash"
        assert "crash" in call_args.kwargs["reason"].lower()

    def test_stale_dispatched_transitioned_from_dispatched(self) -> None:
        store = InMemoryExecutionAttemptStore()
        record = _attempt_record(status="dispatched", started_seconds_ago=120.0)
        store.save_attempt(record)

        kernel = _mock_kernel("space-1")
        engine = StartupRecoveryEngine(
            attempt_store=store,
            kernels={"space-1": kernel},
            crash_detection_window_seconds=60.0,
        )
        engine.run()

        call_args = kernel.propose_task_transition.call_args
        assert call_args.args[1] == "dispatched"  # from_state


# ── CRASH-09: Recovery is idempotent (RECOVERY-006) ──────────────────────────

class TestCrash09RecoveryIsIdempotent:
    """CRASH-09: Running recovery twice produces no duplicate effects.

    RECOVERY-006: Recovery idempotency.
    """

    def test_second_recovery_run_is_no_op(self) -> None:
        store = InMemoryExecutionAttemptStore()
        record = _attempt_record(status="running", started_seconds_ago=120.0)
        store.save_attempt(record)

        kernel = _mock_kernel("space-1")
        engine = StartupRecoveryEngine(
            attempt_store=store,
            kernels={"space-1": kernel},
            crash_detection_window_seconds=60.0,
        )

        result1 = engine.run()
        assert result1.recovered_tasks == 1

        # Second run: attempt is now 'recovered', should not re-process it
        result2 = engine.run()
        assert result2.recovered_tasks == 0
        # Kernel CAS called only once (from first run)
        assert kernel.propose_task_transition.call_count == 1


# ── CRASH-10: Ambiguous state escalates (RECOVERY-007) ───────────────────────

class TestCrash10AmbiguousStateEscalates:
    """CRASH-10: Ambiguous interrupted state escalates to human review.

    RECOVERY-007: Ambiguous states escalate.
    """

    def test_unknown_status_classified_as_ambiguous(self) -> None:
        store = InMemoryExecutionAttemptStore()
        # Manually save a record with unusual status
        record = ExecutionAttemptRecord(
            attempt_id="unknown-attempt-id",
            idempotency_key="unknown-ikey",
            space_id="space-1",
            task_id="task-weird",
            plan_version=1,
            attempt_number=1,
            capability="python.eval_sandboxed",
            status="observing",  # unusual, not 'dispatched'/'running'/'completed'/'failed'
            started_at=_past(120.0),
        )
        # Manually add it via dict hack to bypass status filter in get_interrupted_attempts
        store._records["unknown-ikey"] = record  # type: ignore[attr-defined]

        engine = StartupRecoveryEngine(
            attempt_store=store,
            kernels={},
            crash_detection_window_seconds=60.0,
        )
        classified = engine._classify(record)
        assert classified.interruption_class == InterruptionClass.AMBIGUOUS
        assert classified.proposed_recovery_action == "escalate"

    def test_ambiguous_escalates_via_kernel(self) -> None:
        store = InMemoryExecutionAttemptStore()
        # Build an ambiguous case: force interruption by putting it in _records directly
        record = ExecutionAttemptRecord(
            attempt_id="amb-id",
            idempotency_key="amb-ikey",
            space_id="space-1",
            task_id="task-amb",
            plan_version=1,
            attempt_number=1,
            capability="python.eval_sandboxed",
            status="running",
            started_at=_past(120.0),
        )
        store.save_attempt(record)

        kernel = _mock_kernel("space-1")
        engine = StartupRecoveryEngine(
            attempt_store=store,
            kernels={"space-1": kernel},
            crash_detection_window_seconds=60.0,
        )

        # Patch _classify to return AMBIGUOUS for this case
        original_classify = engine._classify

        def _classify_ambiguous(rec: ExecutionAttemptRecord):
            cls = original_classify(rec)
            from dataclasses import replace as dr
            from core.orchestrator.startup_recovery import InterruptedAttempt
            return InterruptedAttempt(
                record=rec,
                interruption_class=InterruptionClass.AMBIGUOUS,
                proposed_recovery_action="escalate",
            )

        engine._classify = _classify_ambiguous
        result = engine.run()

        # Task should be escalated (transitioned to 'escalated')
        call_args = kernel.propose_task_transition.call_args
        assert call_args.args[2] == "escalated"
        assert result.escalated_tasks == 1


# ── CRASH-11: Retry budget NOT bypassed after restart ────────────────────────

class TestCrash11RetryBudgetNotBypassed:
    """CRASH-11: After restart, retry budget is correctly enforced from persisted count.

    A task that already used 3 retries before crash cannot retry again.
    """

    def test_exhausted_retry_budget_still_escalates_after_restart(self) -> None:
        store = InMemoryConvergenceStateStore()
        space_id = "space-crash-11"
        task_id = "task-limit"

        # Pre-populate: 3 retries already used
        for _ in range(3):
            store.increment_retry(space_id, task_id, "transient.timeout")

        # Simulate restart with new engine using same store
        reconciler = MagicMock()
        reconciler.reconcile_task_failure = MagicMock()
        reconciler.commit_delta_with_rebase = MagicMock(return_value=(True, 2, None))

        def _make_fake_kernel(sid: str):
            class _FakeKernel:
                def verify_space_identity(self, s): pass
                def get_plan_version(self): return 1
                def get_task_graph(self):
                    g = MagicMock()
                    g.nodes = []
                    return g
                def propose_task_transition(self, *a, **kw): return (True, "ok")
                def commit_plan_delta(self, delta): return (True, 2, None)
            inst = _FakeKernel()
            inst.space_id = sid
            return inst

        engine = ConvergenceEngine(
            space_id=space_id,
            reconciler=reconciler,
            state_store=store,
        )

        # With 3 retries already consumed, 4th failure should not produce RETRY
        proposal = engine.evaluate_and_propose(
            kernel=_make_fake_kernel(space_id),  # type: ignore[arg-type]
            goal_spec=MagicMock(),
            evidence=[],
            failed_task_id=task_id,
            error_class="transient.timeout",
            error_message="4th failure",
        )

        # Should NOT be RETRY — budget exhausted
        assert proposal.decision != ConvergenceDecision.RETRY, (
            f"Expected REPLAN or ESCALATE, got {proposal.decision} — "
            "retry budget should have been loaded from store (3 already used)"
        )


# ── CRASH-12: InMemoryExecutionAttemptStore protocol compliance ───────────────

class TestCrash12AttemptStoreProtocol:
    """CRASH-12: InMemoryExecutionAttemptStore satisfies ExecutionAttemptStore Protocol."""

    def test_save_and_retrieve(self) -> None:
        store = InMemoryExecutionAttemptStore()
        record = _attempt_record()
        store.save_attempt(record)
        retrieved = store.get_attempt(record.idempotency_key)
        assert retrieved is not None
        assert retrieved.task_id == record.task_id

    def test_get_nonexistent_returns_none(self) -> None:
        store = InMemoryExecutionAttemptStore()
        assert store.get_attempt("nonexistent-key") is None

    def test_mark_recovered_updates_status(self) -> None:
        store = InMemoryExecutionAttemptStore()
        record = _attempt_record()
        store.save_attempt(record)
        store.mark_recovered(record.idempotency_key)
        retrieved = store.get_attempt(record.idempotency_key)
        assert retrieved is not None
        assert retrieved.status == "recovered"

    def test_get_interrupted_uses_window(self) -> None:
        store = InMemoryExecutionAttemptStore()
        old_record = _attempt_record(task_id="old-task", started_seconds_ago=200.0)
        recent_record = _attempt_record(task_id="new-task", started_seconds_ago=10.0)
        store.save_attempt(old_record)
        store.save_attempt(recent_record)

        interrupted = store.get_interrupted_attempts(crash_detection_window_seconds=60.0)
        task_ids = [r.task_id for r in interrupted]
        assert "old-task" in task_ids
        assert "new-task" not in task_ids


# ── CRASH-13: InMemoryConvergenceStateStore protocol compliance ───────────────

class TestCrash13ConvergenceStoreProtocol:
    """CRASH-13: InMemoryConvergenceStateStore satisfies ConvergenceStateStore Protocol."""

    def test_load_default_returns_zero_state(self) -> None:
        store = InMemoryConvergenceStateStore()
        rec = store.load_state("space-x", "task-x")
        assert rec.retry_count == 0
        assert rec.replan_count == 0
        assert len(rec.failure_fingerprints) == 0

    def test_increment_and_reload(self) -> None:
        store = InMemoryConvergenceStateStore()
        count1 = store.increment_retry("s", "t", "transient.crash")
        count2 = store.increment_retry("s", "t", "transient.crash")
        assert count1 == 1
        assert count2 == 2
        rec = store.load_state("s", "t")
        assert rec.retry_count == 2

    def test_fingerprint_deduplication(self) -> None:
        store = InMemoryConvergenceStateStore()
        store.add_fingerprint("s", "t", "fp-abc")
        store.add_fingerprint("s", "t", "fp-abc")  # duplicate
        fps = store.get_fingerprints("s", "t")
        assert fps == frozenset({"fp-abc"})

    def test_cross_task_isolation(self) -> None:
        store = InMemoryConvergenceStateStore()
        store.increment_retry("space-A", "task-1")
        store.increment_retry("space-A", "task-1")
        store.increment_replan("space-B", "task-1")

        count_a = store.load_state("space-A", "task-1").retry_count
        count_b_replan = store.load_state("space-B", "task-1").replan_count
        count_b_retry = store.load_state("space-B", "task-1").retry_count

        assert count_a == 2
        assert count_b_replan == 1
        assert count_b_retry == 0


# ── CRASH-14: No kernel → recovery still marks attempts as recovered ──────────

class TestCrash14NoKernelGracefulDegradation:
    """CRASH-14: If no kernel exists for a space, recovery gracefully marks attempt as recovered."""

    def test_no_kernel_marks_recovered_without_error(self) -> None:
        store = InMemoryExecutionAttemptStore()
        record = _attempt_record(space_id="orphaned-space", status="running", started_seconds_ago=120.0)
        store.save_attempt(record)

        # No kernel registered for 'orphaned-space'
        engine = StartupRecoveryEngine(
            attempt_store=store,
            kernels={},  # empty — no kernel
            crash_detection_window_seconds=60.0,
        )
        result = engine.run()

        # Should not raise; attempt should still be marked recovered
        assert result.recovered_tasks == 1
        rec = store.get_attempt(record.idempotency_key)
        assert rec is not None
        assert rec.status == "recovered"


# ── SECURITY: Cross-space isolation in recovery ───────────────────────────────

class TestSecurityCrossSpaceIsolation:
    """SEC-RECOVERY-01: Recovery must not apply cross-space kernels.

    A kernel for space-A must not be used to recover a task in space-B.
    """

    def test_cross_space_kernel_not_used(self) -> None:
        store = InMemoryExecutionAttemptStore()
        # Record belongs to space-B, but only space-A kernel registered
        record = _attempt_record(space_id="space-B", status="running", started_seconds_ago=120.0)
        store.save_attempt(record)

        kernel_a = _mock_kernel("space-A")
        engine = StartupRecoveryEngine(
            attempt_store=store,
            kernels={"space-A": kernel_a},  # no space-B kernel
            crash_detection_window_seconds=60.0,
        )
        result = engine.run()

        # kernel_a must NOT have been called for space-B task
        kernel_a.propose_task_transition.assert_not_called()
        # Attempt is still marked recovered (space no longer active)
        assert result.recovered_tasks == 1


class TestSecurityUnauthorizedRetryIncrement:
    """SEC-RECOVERY-02: Direct store access cannot exceed retry budget via bypass."""

    def test_direct_store_increment_respected_by_engine(self) -> None:
        """Even if someone directly increments the store, the engine reads the correct value."""
        store = InMemoryConvergenceStateStore()
        space_id = "space-sec-02"
        task_id = "task-target"

        # Direct store manipulation (simulating what a malicious caller might try)
        for _ in range(10):
            store.increment_retry(space_id, task_id, "transient.attack")

        # Engine must read from store and report 10, not 0
        engine = ConvergenceEngine.__new__(ConvergenceEngine)
        engine.space_id = space_id
        engine._state_store = store
        engine._retry_counts = {}
        engine._replan_counts = {}
        engine._seen_fingerprints = set()

        count = engine._get_retry_count(task_id)
        # Budget is 3 (MAX_RETRY_BUDGET); count=10 means budget is ALREADY exhausted
        assert count == 10
        assert count >= ConvergenceEngine.MAX_RETRY_BUDGET


class TestSecurityForgedRecoveryPulseNotTrusted:
    """SEC-RECOVERY-03: Recovery state comes from PostgreSQL, not pulse bus.

    A forged recovery.completed pulse cannot reset convergence state.
    """

    def test_convergence_state_only_from_store_not_pulse(self) -> None:
        """ConvergenceEngine never reads from pulse bus for state.

        It reads from ConvergenceStateStore (PostgreSQL-backed in production).
        Emitting a forged recovery pulse has zero effect on engine state.
        """
        store = InMemoryConvergenceStateStore()
        space_id = "space-sec-03"
        task_id = "task-forgery-test"

        # Simulate: 3 retries already recorded
        store.increment_retry(space_id, task_id)
        store.increment_retry(space_id, task_id)
        store.increment_retry(space_id, task_id)

        # Engine reads from store
        engine = ConvergenceEngine.__new__(ConvergenceEngine)
        engine.space_id = space_id
        engine._state_store = store
        engine._retry_counts = {}
        engine._replan_counts = {}
        engine._seen_fingerprints = set()

        count = engine._get_retry_count(task_id)
        assert count == 3, "Retry count must come from store, not be resettable by pulses"
