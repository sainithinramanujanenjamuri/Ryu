# ADR-0042 — Crash Recovery, Durable Execution State, and Deterministic Runtime Reconstruction

**Status:** Accepted  
**Date:** 2026-09-29  
**Phase:** 12.8 — Crash Recovery & Durable Execution State  
**Supersedes:** (none — new decision)  
**See also:** ADR-0041 (Dispatcher, DAG Traversal, Plan Convergence), ADR-0031 (Memory Durability)

---

## Context / Problem

Phase 12.1–12.7 established a fully verified autonomous execution engine. The engine executes Task DAGs,
collects evidence, evaluates goals, and applies convergence proposals through bounded retry and replan
cycles. All plan authority flows through `SpaceKernel.commit_plan_delta()` via CAS.

However, significant execution control state is currently held **only in memory**:

- `ConvergenceEngine._retry_counts: dict[str, int]` — per-task retry budget consumed
- `ConvergenceEngine._replan_counts: dict[str, int]` — per-task replan budget consumed
- `ConvergenceEngine._seen_fingerprints: set[str]` — SHA-256 failure fingerprints for loop detection
- `DeterministicDispatcher._tracked_attempts: dict[str, DispatchAttempt]` — in-flight idempotency registry

When the RYU daemon process crashes or is restarted:

1. All retry counters reset to zero, allowing the system to re-enter the same failure loop as if it had
   never retried — effectively bypassing the retry budget.
2. All failure fingerprints are lost, making previously-detected failure loops undetectable.
3. In-flight task attempts (state = `running`) become **interrupted** with no safe recovery path.
4. Resource leases held by interrupted tasks may remain allocated indefinitely, consuming capacity.
5. Idempotency protection for re-delivered work items is lost, risking duplicate execution.

This creates a **durability gap** between the authoritative PostgreSQL plan state and the in-memory
execution control state. The engine is **partially amnesic** across restarts.

The Phase 13 architecture audit (see `docs/PHASE_13_POST_EXECUTION_ENGINE_ARCHITECTURE_AUDIT.md`)
identified this gap as DEBT-02 (HIGH priority), a prerequisite to connecting experiential memory
(Phase 13 scope) to execution. Execution state must be durable before adaptation feedback can be trusted.

### Constraints from existing architecture

1. **PostgreSQL is the authoritative durable store.** Redis is transient transport only; it MUST NOT
   become a source of truth for execution or recovery state.
2. **SpaceKernel retains sole authority over plan state.** Recovery MUST NOT mutate plan state except
   through the standard `commit_plan_delta()` / `propose_task_transition()` CAS pathway.
3. **SCCA Law 6 (Failures are contained, escalated, never silent).** Interrupted tasks must be
   classified and escalated deterministically — never silently discarded.
4. **Recovery idempotency.** Running recovery twice from the same PostgreSQL state MUST NOT produce
   duplicate effects (no double-retry, no duplicate lease release).
5. **The deterministic core independence boundary (AGENTS.md §7) must not be violated.** `core/`
   modules must not import from `agents/`, `workers/`, `skills/`, `channels/`, or `memory/`.
6. **Unsafe ambiguity escalates.** When recovery cannot safely classify an interrupted task state,
   the task MUST be escalated to human review — never silently resolved.

---

## Decision

### 1. Durable Execution Attempt Records

Introduce a new PostgreSQL table `execution_attempts` to durably record every task dispatch attempt:

```sql
CREATE TABLE execution_attempts (
    attempt_id       VARCHAR(255) PRIMARY KEY,
    idempotency_key  VARCHAR(255) NOT NULL UNIQUE,
    space_id         VARCHAR(255) NOT NULL,
    task_id          VARCHAR(255) NOT NULL,
    plan_version     INTEGER      NOT NULL,
    attempt_number   INTEGER      NOT NULL DEFAULT 1,
    capability       VARCHAR(255) NOT NULL,
    status           VARCHAR(50)  NOT NULL DEFAULT 'dispatched',
    worker_id        VARCHAR(255),
    started_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    completed_at     TIMESTAMPTZ,
    failure_class    VARCHAR(255),
    failure_message  TEXT,
    exit_code        INTEGER,
    artifact_sha256  VARCHAR(128),
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
```

Entries are written **before** worker dispatch (status=`dispatched`) and updated on completion or
failure. Status transitions mirror the task state machine: `dispatched` → `running` → `completed` |
`failed`.

### 2. Durable Convergence State Records

Introduce a new PostgreSQL table `convergence_state` to durably record per-space-per-task bounded
counters and failure fingerprints:

```sql
CREATE TABLE convergence_state (
    space_id          VARCHAR(255) NOT NULL,
    task_id           VARCHAR(255) NOT NULL,
    retry_count       INTEGER      NOT NULL DEFAULT 0,
    replan_count      INTEGER      NOT NULL DEFAULT 0,
    failure_fingerprints JSONB     NOT NULL DEFAULT '[]'::jsonb,
    last_failure_class VARCHAR(255),
    last_failure_at   TIMESTAMPTZ,
    updated_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    PRIMARY KEY (space_id, task_id)
);
```

`ConvergenceEngine` loads this state on first access for a given `(space_id, task_id)` pair and
persists updates **before** emitting the convergence proposal pulse. The in-memory cache is
authoritative only for the lifetime of the current process; on restart, state is re-loaded from
PostgreSQL.

### 3. ExecutionAttemptStore Protocol

Define a new `ExecutionAttemptStore` Protocol in `core/orchestrator/execution_state.py`. Two
implementations:

- `PostgresExecutionAttemptStore` — production durable store (psycopg2)
- `InMemoryExecutionAttemptStore` — test substitute satisfying the same Protocol

The `DeterministicDispatcher` accepts an optional `attempt_store: ExecutionAttemptStore | None`
parameter. When provided, every `record_attempt()` call also persists to the store. On startup,
the dispatcher's `_tracked_attempts` dict is pre-populated from the store.

### 4. ConvergenceStateStore Protocol

Define a new `ConvergenceStateStore` Protocol (also in `core/orchestrator/execution_state.py`).
`ConvergenceEngine` accepts an optional `state_store: ConvergenceStateStore | None` parameter.
Retry counts and fingerprints are loaded from and persisted to the store. When `state_store` is
`None`, behavior reverts to in-memory-only (backward compatible with existing tests).

### 5. StartupRecoveryEngine

Define `StartupRecoveryEngine` in `core/orchestrator/startup_recovery.py`. This class executes
once at daemon startup (before accepting new work) and:

1. **Scans `execution_attempts`** for entries with `status = 'running'` or `status = 'dispatched'`
   that are older than a configurable crash detection window (default: 60 seconds).
2. **Classifies each interrupted attempt** as:
   - `CRASH` — worker vanished with no completion record
   - `STALE_DISPATCHED` — dispatched but never reached running state
   - `AMBIGUOUS` — cannot safely determine state (escalates)
3. **Reconciles resource leases** — calls `ResourceManager.release()` for any stale lease
   associated with the interrupted attempt, using the stored `lease_token`.
4. **Proposes task recovery** via the standard `SpaceKernel.propose_task_transition()` CAS path:
   - For `CRASH` / `STALE_DISPATCHED` → transition task to `failed` with `failure_class =
     "transient.worker_crash"`, then let the normal `ConvergenceEngine` handle retry/replan logic.
   - For `AMBIGUOUS` → transition task to `escalated`.
5. **Updates `execution_attempts`** to mark recovered entries with `status = 'recovered'`.
6. **Emits recovery observability pulses** (`recovery.started`, `recovery.scan_completed`,
   `task.interrupted_detected`, `lease.reconciled`, `recovery.completed`) via the `PulseBus`.

**Recovery is idempotent**: re-running against the same PostgreSQL state produces identical outcomes
because interrupted tasks are only recovered once (status = `recovered` entries are skipped).

### 6. New Pulse Types for Recovery Observability

Six new pulse types are added to `contracts/registry/pulse-types.json`:

| Type | Severity | Description |
|:---|:---|:---|
| `recovery.started` | `info` | Emitted at daemon startup when recovery scan begins |
| `recovery.scan_completed` | `info` | Emitted when recovery scan finishes, with count of interrupted tasks found |
| `task.interrupted_detected` | `warning` | Emitted for each interrupted task found during recovery scan |
| `task.worker_crash` | `error` | Emitted when a task is classified as worker crash |
| `lease.reconciled` | `info` | Emitted when a stale lease is successfully released during recovery |
| `recovery.completed` | `info` | Emitted when full recovery sequence completes |

Total pulse types after this change: **44** (38 existing + 6 new).

### 7. New Contracts: RECOVERY-001 through RECOVERY-007

Seven new contracts govern the crash recovery subsystem:

| ID | Title |
|:---|:---|
| RECOVERY-001 | Execution attempt state is durable across process restart |
| RECOVERY-002 | Retry budget survives restart (no counter reset) |
| RECOVERY-003 | Failure fingerprints survive restart (no loop bypass) |
| RECOVERY-004 | Interrupted tasks are detected and classified at startup |
| RECOVERY-005 | Stale leases are released during startup recovery |
| RECOVERY-006 | Recovery is idempotent (running twice produces no duplicate effects) |
| RECOVERY-007 | Ambiguous recovery states escalate to human review |

### 8. What is explicitly NOT changed

- `SpaceKernel` authority boundaries remain unchanged.
- `AdmissionController` and `ResourceManager` authority boundaries remain unchanged.
- `DeterministicDispatcher.evaluate_plan()` logic is unchanged.
- `ConvergenceEngine` convergence logic is unchanged; only its state persistence is extended.
- `PlanReconciler` is unchanged.
- No connection to Phase 13 `Reflector`, `AdaptationLayer`, or `PromotionPipeline` is made.
- No second dispatcher, second kernel, or second bus is introduced.

---

## Consequences

### Positive

- RYU's autonomous execution engine no longer has a durability gap. A restart does not cause safety
  regressions such as retry budget bypass or failure loop re-entry.
- Startup recovery is deterministic and observable (6 new pulse types provide full audit trail).
- Backward compatibility is maintained: all existing tests pass unchanged because stores default to
  in-memory when no PostgreSQL store is provided.
- Recovery uses the same legitimate `SpaceKernel` CAS transition mechanism as normal execution.
- The integrity of the 355-test baseline is preserved (no tests are weakened or removed).

### Negative / Trade-offs

- Adds two new database tables and a migration script (migration 006).
- Adds startup latency proportional to the number of interrupted tasks (expected: O(tens), not O(thousands)).
- `DeterministicDispatcher` and `ConvergenceEngine` gain optional constructor parameters — callers
  that need durability must provide stores; existing call sites remain unchanged.

### Risks

- PostgreSQL availability at startup is now a soft dependency for recovery. If PostgreSQL is
  unavailable, `StartupRecoveryEngine` logs a warning and proceeds in degraded mode (no recovery);
  it does NOT block startup.
- `convergence_state` table rows are per-(space, task); for very large deployments with many tasks,
  this table will grow. Phase 13 or later may introduce TTL-based pruning. This is deferred.

---

## Verification

Phase 12.8 is verified complete when:

1. `pytest core/ workers/` passes **≥ 355 tests, 0 failures**
2. New crash-recovery unit tests pass (CRASH-01 through CRASH-14)
3. `python scripts/contract_sync.py` reports 44/44 pulse types
4. `python scripts/dep_guard.py` reports 0 forbidden imports
5. All V1 governance gates pass
6. `docs/PHASE_12_8_CRASH_RECOVERY_VERIFICATION.md` contains the evidence report
7. Git tree is clean with conventional commit `feat(phase12): implement Phase 12.8 Crash Recovery and Durable Execution State`

---

*Filed: Phase 12.8 — Crash Recovery & Durable Execution State*  
*Author: RYU AI Engineering*
