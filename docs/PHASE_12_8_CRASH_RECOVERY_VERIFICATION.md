# RYU AI — Phase 12.8 Crash Recovery & Durable Execution State Verification Report

**Milestone:** Phase 12.8 — Crash Recovery & Durable Execution State  
**Architectural Baseline:** Phase 12.7 (`224c6c0`) + Phase 13 Audit (`3d3b2a4`)  
**Status:** `GATE_VERIFIED`  
**Governing Architecture:** Space-Centric Cognitive Architecture (SCCA)  
**Governing ADR:** [ADR-0042](../adr/0042-crash-recovery-durable-execution-state-and-deterministic-runtime-reconstruction.md)  
**Date:** 2026-09-29  

---

## 1. Executive Summary

Phase 12.8 resolves the critical **durability gap** (DEBT-02 identified in the Phase 13 architecture audit) in the RYU AI autonomous execution engine. Previously, execution control state—specifically per-task retry counters, replan budgets, SHA-256 failure fingerprints, and in-flight attempt tracking—was held only in process memory. A daemon restart or process crash could allow a failing task to reset its retry budget, bypass failure-loop detection, or leave in-flight tasks and allocated resource leases orphaned.

Phase 12.8 hardens the execution engine by:
1. Making task attempt execution state durably persisted before worker invocation (`ExecutionAttemptStore`, `execution_attempts` table).
2. Making retry/replan budgets and failure fingerprints survive process crashes and restarts (`ConvergenceStateStore`, `convergence_state` table).
3. Implementing a deterministic startup recovery scanner (`StartupRecoveryEngine`) that detects interrupted attempts, classifies them into deterministic failure taxonomies (`CRASH`, `STALE_DISPATCHED`, `AMBIGUOUS`), releases stale leases, and initiates recovery via legitimate `SpaceKernel` CAS transitions.
4. Preserving strict SCCA authority boundaries: zero cognitive imports in `core/` (AGENTS.md §7), zero direct plan mutation by recovery components, and mandatory escalation for ambiguous states (SCCA Law 6).
5. Registering 6 new typed pulses (`recovery.*`, `task.interrupted_detected`, `task.worker_crash`, `lease.reconciled`) and 7 machine-verifiable contracts (`RECOVERY-001` through `RECOVERY-007`).

---

## 2. Contracts Established & Verified

| Contract ID | Title | Invariant Verified | Evidence |
|:---|:---|:---|:---|
| **RECOVERY-001** | Execution Attempt Durability | Task dispatch attempts are durably recorded to store before dispatch; idempotency keys survive restart; duplicate attempts deduplicated. | `test_phase12_8_crash_recovery.py` (CRASH-04, CRASH-05, CRASH-12) |
| **RECOVERY-002** | Durable Retry & Replan Budgets | `ConvergenceEngine` retry/replan counters persist across process restarts; cannot bypass MAX_RETRY_BUDGET (3) or MAX_REPLAN_BUDGET (3). | `test_phase12_8_crash_recovery.py` (CRASH-01, CRASH-02, CRASH-11, CRASH-13) |
| **RECOVERY-003** | Durable Failure Fingerprint Registry | Failure fingerprints persist across restarts; repeated failures trigger immediate `ESCALATE` to prevent infinite replan loops. | `test_phase12_8_crash_recovery.py` (CRASH-03, CRASH-13) |
| **RECOVERY-004** | Interrupted Task Detection & CAS Recovery | `StartupRecoveryEngine` scans and classifies interrupted attempts (`CRASH`, `STALE_DISPATCHED`); transitions state via standard `SpaceKernel` CAS. | `test_phase12_8_crash_recovery.py` (CRASH-06, CRASH-08) |
| **RECOVERY-005** | Stale Lease Reconciliation | Stale leases held by crashed workers are released back to `ResourceManager` during startup scan; resources never permanently leaked. | `test_phase12_8_crash_recovery.py` (CRASH-07) |
| **RECOVERY-006** | Startup Recovery Idempotency & Observability | Running recovery repeatedly produces identical outcomes; recovery lifecycle published via 6 typed recovery pulses. | `test_phase12_8_crash_recovery.py` (CRASH-09, SEC-RECOVERY-03) |
| **RECOVERY-007** | Ambiguity Escalation & Cross-Space Isolation | Unclassifiable/ambiguous states escalate to human review; recovery strictly respects Space boundaries and never crosses kernels. | `test_phase12_8_crash_recovery.py` (CRASH-10, CRASH-14, SEC-RECOVERY-01, SEC-RECOVERY-02) |

---

## 3. Component Architecture & Implementation Details

### 3.1 Durable Storage Protocols (`core/orchestrator/execution_state.py`)
- **`ExecutionAttemptRecord`**: Immutable frozen dataclass capturing attempt ID, idempotency key, space ID, task ID, plan version, attempt number, capability, status (`dispatched`, `running`, `completed`, `failed`, `recovered`), worker ID, lease token, timestamps, failure class, and exit code.
- **`ConvergenceStateRecord`**: Immutable frozen dataclass capturing space ID, task ID, retry count, replan count, failure fingerprints tuple, last failure class, and timestamps.
- **`ExecutionAttemptStore`**: Abstract Protocol defining durable operations (`save_attempt`, `update_attempt_status`, `get_attempt`, `get_attempts_for_task`, `get_interrupted_attempts`, `mark_recovered`).
- **`ConvergenceStateStore`**: Abstract Protocol defining operations (`load_state`, `save_state`, `increment_retry`, `increment_replan`, `add_fingerprint`, `get_fingerprints`).
- **`InMemoryExecutionAttemptStore` & `InMemoryConvergenceStateStore`**: Thread-safe in-memory implementations providing hermetic unit testing without requiring live PostgreSQL services.

### 3.2 Dispatcher & Convergence Durability (`core/orchestrator/dispatch_model.py`)
- **`DeterministicDispatcher`**: Accepts optional `attempt_store`. Pre-populates tracked attempts on startup from store. Persists every dispatch attempt prior to worker invocation. Updates attempt status to `completed` or `failed` with outcome metrics.
- **`ConvergenceEngine`**: Accepts optional `state_store`. Dynamically loads retry/replan counters and failure fingerprints from durable store upon task evaluation. Increments and persists updated counters upon failure before generating convergence proposals.

### 3.3 Startup Recovery Engine (`core/orchestrator/startup_recovery.py`)
- **`StartupRecoveryEngine`**: Single-pass startup scanner that:
  1. Emits `recovery.started` pulse with daemon instance ID.
  2. Scans store for attempts in `dispatched` or `running` state exceeding the configurable crash window (default: 60s).
  3. Classifies each attempt deterministically (`CRASH`, `STALE_DISPATCHED`, `AMBIGUOUS`).
  4. Emits `task.interrupted_detected` warning pulse per candidate.
  5. Emits `recovery.scan_completed` summary pulse.
  6. Reconciles stale leases via `ResourceManager.release()`, emitting `lease.reconciled`.
  7. Proposes task state transition to `failed` (`transient.worker_crash`) or `escalated` strictly via `SpaceKernel.propose_task_transition()`.
  8. Marks attempts as `recovered` in store to guarantee recovery idempotency.
  9. Emits `recovery.completed` pulse.

### 3.4 PostgreSQL Migration 006 (`deploy/migrations/006_create_execution_attempts_and_convergence_state.sql`)
- Creates `execution_attempts` table with indexes on `space_id`, `task_id`, `status`, `idempotency_key`, and a partial index on `(status, started_at)` for fast interrupted task retrieval.
- Creates `convergence_state` table with composite primary key `(space_id, task_id)` and JSONB storage for failure fingerprints.

### 3.5 Pulse Registry & Schema Governance
- Total pulse types expanded from 38 to 44.
- 6 new pulse types:
  - `recovery.started` (info)
  - `recovery.scan_completed` (info)
  - `task.interrupted_detected` (warning)
  - `task.worker_crash` (error)
  - `lease.reconciled` (info)
  - `recovery.completed` (info)
- 1:1 JSON schemas created in `contracts/registry/payload-schemas/`.
- Code generation updated via `generate_pulse_models.py` (44 types).
- Synchronized with `docs/Architecture` §16.

---

## 4. Test Evidence & Suite Results

### 4.1 Crash Recovery Test Matrix (32 Tests — All PASS)
Located at `core/orchestrator/tests/test_phase12_8_crash_recovery.py`:

| Test ID | Test Name | Purpose / Assertion | Result |
|:---|:---|:---|:---|
| **CRASH-01** | `test_retry_count_persisted_and_reloaded` | Retry budget is reloaded from store across engine restart | PASS |
| **CRASH-01** | `test_retry_count_zero_for_new_task` | Unseen tasks initialize retry count to 0 | PASS |
| **CRASH-02** | `test_replan_count_persisted_and_reloaded` | Replan budget persists and reloads across restart | PASS |
| **CRASH-03** | `test_fingerprints_survive_restart` | SHA-256 failure fingerprints survive restart; loop guard remains active | PASS |
| **CRASH-04** | `test_record_attempt_writes_to_store` | In-flight dispatch attempt written to store before execution | PASS |
| **CRASH-04** | `test_duplicate_attempt_is_idempotent` | Duplicate dispatch attempt rejected by idempotency check | PASS |
| **CRASH-05** | `test_update_attempt_status_to_completed` | Status and exit code updated to completed on success | PASS |
| **CRASH-05** | `test_update_attempt_status_to_failed` | Status, failure class, and error message updated on failure | PASS |
| **CRASH-06** | `test_running_task_classified_as_crash` | Attempt stuck in running classified as CRASH | PASS |
| **CRASH-06** | `test_dispatched_task_classified_as_stale` | Attempt stuck in dispatched classified as STALE_DISPATCHED | PASS |
| **CRASH-06** | `test_recent_task_not_interrupted` | Attempt within active time window is NOT flagged as interrupted | PASS |
| **CRASH-06** | `test_completed_task_not_interrupted` | Completed attempt is NOT flagged as interrupted | PASS |
| **CRASH-07** | `test_stale_lease_released_via_resource_manager` | Stale lease token released via `ResourceManager.release()` | PASS |
| **CRASH-07** | `test_no_lease_no_release_call` | Non-leased attempt does not trigger lease release | PASS |
| **CRASH-08** | `test_crash_task_transitioned_to_failed_via_kernel` | Task transitioned to failed with `transient.worker_crash` via kernel CAS | PASS |
| **CRASH-08** | `test_stale_dispatched_transitioned_from_dispatched` | Stale dispatched task transitioned from dispatched state | PASS |
| **CRASH-09** | `test_second_recovery_run_is_no_op` | Second recovery run against same store produces 0 duplicates | PASS |
| **CRASH-10** | `test_unknown_status_classified_as_ambiguous` | Unrecognized state classified as AMBIGUOUS | PASS |
| **CRASH-10** | `test_ambiguous_escalates_via_kernel` | Ambiguous interrupted state transitions to escalated (Law 6) | PASS |
| **CRASH-11** | `test_exhausted_retry_budget_still_escalates_after_restart` | Task with 3 prior retries cannot retry again after daemon restart | PASS |
| **CRASH-12** | `test_save_and_retrieve` | `ExecutionAttemptStore` protocol save and read operations | PASS |
| **CRASH-12** | `test_get_nonexistent_returns_none` | Non-existent attempt returns None | PASS |
| **CRASH-12** | `test_mark_recovered_updates_status` | Attempt marked as recovered cannot be re-recovered | PASS |
| **CRASH-12** | `test_get_interrupted_uses_window` | Time window properly isolates stale from active attempts | PASS |
| **CRASH-13** | `test_load_default_returns_zero_state` | Uninitialized convergence record defaults to 0 counts | PASS |
| **CRASH-13** | `test_increment_and_reload` | Atomic increment operations update durable convergence record | PASS |
| **CRASH-13** | `test_fingerprint_deduplication` | Duplicate fingerprints within a task are deduplicated | PASS |
| **CRASH-13** | `test_cross_task_isolation` | Counters and fingerprints for space-A/task-1 isolated from space-B | PASS |
| **CRASH-14** | `test_no_kernel_marks_recovered_without_error` | Missing kernel for terminated space degrades gracefully | PASS |
| **SEC-01** | `test_cross_space_kernel_not_used` | Kernel for space A cannot be used to recover task in space B | PASS |
| **SEC-02** | `test_direct_store_increment_respected_by_engine` | External store tampering cannot bypass convergence budget | PASS |
| **SEC-03** | `test_convergence_state_only_from_store_not_pulse` | Convergence state loaded strictly from store, immune to pulse forgery | PASS |

### 4.2 Full Regression Test Suite Results
- **Core + Workers Tests**: **387 passed, 0 failed, 0 errors** (was 355 passed; +32 new tests)
- **Execution Time**: ~48.2s
- **Broken Tests**: 0
- **Weakened Assertions**: 0

### 4.3 Governance & Verification Gate Results

| Gate | Script | Result | Key Metrics |
|:---|:---|:---|:---|
| **V1-001** | `scripts/v1_audit_spec_coverage.py` | **PASS** | 141 criteria, 199 contracts, 162 mappings, 0 orphans, 0 stale |
| **V1-002** | `scripts/v1_verify_core_independence.py` | **PASS** | AST check clean, runtime blocker clean, zero-LLM loop verified |
| **V1-004** | `scripts/v1_run_security_regression.py` | **PASS** | 12/12 security test batteries pass |
| **V1-005** | `scripts/v1_audit_governance.py` | **PASS** | 42 ADRs (0001..0042) valid, 44 pulse types in registry & codegen, 44/44 schemas |
| **Core Boundary** | `scripts/dep_guard.py` | **PASS** | 0 forbidden imports in `core/` |
| **Contract Sync** | `scripts/contract_sync.py` | **PASS** | 44/44 pulse types match Architecture §16 table |

---

## 5. Scope Boundary (What Was NOT Done)

In strict adherence to the Phase 12.8 directive:
1. **No Phase 13 Adaptation**: Did not connect `Reflector`, `AdaptationLayer`, or `PromotionPipeline` to `DeterministicDispatcher` or `ConvergenceEngine`. (Reserved for Phase 13).
2. **No Second Architecture**: Did not introduce a secondary dispatcher, secondary pulse bus, or bypass `SpaceKernel` CAS authority.
3. **No Direct Plan Mutations**: Recovery operations flow strictly through `SpaceKernel.propose_task_transition()`.
4. **No LLM Integration in Core**: Core remains 100% deterministic and free of LLM dependencies.

---

## 6. Conclusion & Master Gate Decision

Phase 12.8 has successfully closed the execution durability and crash recovery gap. All 7 contracts (`RECOVERY-001` through `RECOVERY-007`) are implemented, traceable in `docs/CONTRACT_MATRIX.md`, and backed by 32 dedicated unit tests and 387 total passing tests across the core and worker suites.

**Phase 12.8 Gate Decision:** **PASS (`GATE_VERIFIED`)**
