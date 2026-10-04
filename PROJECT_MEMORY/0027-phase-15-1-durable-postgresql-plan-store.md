# Project Memory: 0027 — Phase 15.1 Durable PostgreSQL Plan Store & Cold-Boot Reconstruction

**Date:** 2026-10-03  
**Author:** Ryu Autonomous Core Agent  
**Baseline:** Phase 14.8 Hardened (`7d4198a`)  
**Status:** COMPLETE (GATE-15.1: PASS)  
**Governing ADR:** ADR-0045 (Durable PostgreSQL Plan Store and Cold-Boot Reconstruction)  
**Governing Architecture:** Space-Centric Cognitive Architecture (SCCA)  

---

## 1. Context & Purpose

The Phase 15 Architecture Audit (`docs/PHASE_15_ARCHITECTURE_AUDIT.md`) established that the primary runtime bottleneck following Phase 14.8 was **Durable State & Space-Safe Concurrent Execution**, specifically **Finding F-01 (Recovery Cliff)**.

In Phase 12.8, durable execution tracking was introduced via PostgreSQL (`execution_attempts` and `convergence_state` tables), but `PlanStore` remained an in-memory dictionary. Upon process restart or crash:
1. `StartupRecoveryEngine` queried PostgreSQL for interrupted execution attempts (`running`).
2. `SpaceKernel` initialized with an empty in-memory plan graph (v1 with 0 tasks).
3. Attempting to mark interrupted tasks as `failed` raised errors because the `task_id` did not exist in the in-memory graph.
4. Threading locks provided no cross-process concurrency safety.

Phase 15.1 addresses ONLY Finding F-01 by establishing PostgreSQL as the authoritative production plan store (`PostgresPlanStore`) with deterministic cold-boot reconstruction.

---

## 2. What Changed

1. **Database Schema & Migrations (`deploy/migrations/007_create_plans_and_plan_history_tables.sql`):**
   - Created authoritative `plans` table (`space_id` PK, `plan_version`, `graph_json`, `status`, `created_at`, `updated_at`).
   - Created immutable append-only `plan_history` table (`history_id` BIGSERIAL PK, `space_id`, `plan_version`, `graph_json`, `delta_json`, `recorded_at`, unique constraint on `(space_id, plan_version)`).
   - Applied migration to active PostgreSQL cluster.

2. **Deterministic TaskGraph Serialization (`core/plans/serialization.py`):**
   - Implemented `task_graph_to_dict`, `task_graph_from_dict`, `serialize_task_graph_json`, `deserialize_task_graph_json`.
   - Node dictionaries sorted alphabetically by ID.
   - Strict validation of all node fields (`id`, `capability`, `state`, `dependencies`, `params`, `optional`, `retry_count`, `error`).
   - Strict DAG verification (dependency referential integrity + cycle detection).
   - Complete prohibition of `pickle`; fail-closed deserialization with `DeserializationError`.

3. **Authoritative PostgreSQL Plan Store (`core/plans/postgres_plan_store.py`):**
   - Implemented `PostgresPlanStore` using psycopg2 with parameterized SQL.
   - Enforced single-writer atomic CAS via `SELECT ... FOR UPDATE` row-level locks.
   - Guaranteed atomic rollback on delta application or serialization failures.
   - Maintained append-only monotonic `plan_history`.
   - Provided `list_active_spaces()`, `load_all_plans()`, and `restore_graph()`.
   - Strictly space-scoped queries (`WHERE space_id = %s`); uninitialized space access raises `KeyError`.

4. **Plan Store Interface & Protocol Hardening (`core/plans/plan_store.py`):**
   - Defined `@runtime_checkable` `PlanStoreProtocol`.
   - Added `list_active_spaces()`, `load_all_plans()`, and `restore_graph()` to `PlanStore`.
   - Aliased `InMemoryPlanStore = PlanStore` for unit testing without database dependencies.
   - Exported symbols in `core/plans/__init__.py`.

5. **Kernel & Recovery Engine Integration:**
   - `core/space/kernel.py`: Accepted `plan_store: PlanStoreProtocol | None = None`. Auto-loads reconstructed TaskGraphs on initialization. Restores checkpoints via `plan_store.restore_graph()`. Fixed `propose_task_transition` to accept keyword and positional arguments seamlessly.
   - `core/orchestrator/startup_recovery.py`: Handled 2-tuple and 3-tuple returns from `kernel.propose_task_transition` without masking errors.

6. **Contract Matrix & Spec Mapping:**
   - Registered contracts `PLAN-DURABLE-001` through `PLAN-DURABLE-010` in `docs/CONTRACT_MATRIX.md`.
   - Mapped all 10 contracts in `harness/spec_map.yaml` pointing to executable test cases in `core/plans/tests/test_postgres_plan_store.py`.

7. **Architectural Decision Record (ADR-0045):**
   - Created `adr/0045-durable-postgresql-plan-store-and-cold-boot-reconstruction.md`.

---

## 3. Verification Evidence

- **Serialization Unit Tests (`core/plans/tests/test_serialization.py`):**
  - 7/7 PASSED (roundtrip fidelity, cycle rejection, missing dependency rejection, invalid state rejection, sorted JSON determinism).
- **Postgres Plan Store Integration Battery (`core/plans/tests/test_postgres_plan_store.py`):**
  - 10/10 PASSED against live PostgreSQL:
    - `test_plan_durable_001_atomic_cas_single_winner`: Atomic single-winner CAS.
    - `test_plan_durable_002_schema_validation_and_integrity`: Schema validation & integrity.
    - `test_plan_durable_003_immutable_plan_history`: Append-only immutable history.
    - `test_plan_durable_004_atomic_rollback_on_failure`: Atomic transaction rollback.
    - `test_plan_durable_005_strict_space_isolation`: Strict space-scoped isolation.
    - `test_plan_durable_006_multi_space_cold_boot_reconstruction`: Multi-space cold boot.
    - `test_plan_durable_007_startup_recovery_cold_boot_integration`: Recovery cliff eliminated.
    - `test_plan_durable_008_corrupted_graph_fails_closed`: Corrupted graph fails closed.
    - `test_plan_durable_009_missing_space_fails_closed`: Missing space fails closed.
    - `test_plan_durable_010_concurrent_multi_process_cas`: Multi-process concurrent CAS contention.
- **Existing Plan & Kernel Regressions:**
  - `core/plans/tests/test_plan_engine.py`: 7/7 PASSED.
  - `core/orchestrator/tests/test_phase12_8_crash_recovery.py`: 32/32 PASSED.
  - `core/orchestrator/tests/test_phase12_*.py`: 57/57 PASSED.
  - Executed Plan and Orchestrator regression suites: 187/187 PASSED without failures.
- **System Audits & Governance:**
  - `scripts/dep_guard.py`: 0 violations (Core Boundary strictly preserved).
  - `scripts/contract_sync.py`: PASS.
  - `scripts/v1_audit_spec_coverage.py`: PASS (192/192 mappings, 0 orphaned criteria).
  - `scripts/v1_audit_governance.py`: PASS (ADR inventory 0001..0045, registry, schemas, matrix).
  - `ruff check core/plans`: 0 lint errors.
  - `mypy core/plans`: 0 type errors across 10 source files.

---

## 4. Architectural Invariants Preserved

1. **Production Authority Invariant:** PostgreSQL is the authoritative production store for Plans. Memory is an ephemeral cache.
2. **SCCA Law 1 (Everything happens inside a Space):** All operations and tables are strictly space-isolated (`space_id`).
3. **SCCA Law 6 (Failures are contained, escalated, never silent):** Database outages and malformed graphs fail closed immediately.
4. **Core Independence Rule (AGENTS.md §7):** `core/` has zero dependencies on higher layers.

---

## 5. What Was NOT Implemented (Deferred Scope)

In strict adherence to Phase 15.1 scope limits, Phase 15.1 addresses ONLY Finding F-01. All other findings from `docs/PHASE_15_ARCHITECTURE_AUDIT.md` remain deferred:
- F-02 (P0): Unpartitioned Artifact Namespace (Phase 15.2)
- F-03 (P1): Unbounded Pulse Retrieval (Phase 15.3)
- F-04 (P1): Sequential-Only Task Dispatch (Phase 16)
- F-05 (P1): Recency-Bounded Experience Retrieval (Phase 16+)
- F-06 (P1): In-Memory Convergence State (Phase 15.4)
- F-07 (P2): Disconnected Agent Hierarchy (Phase 16+)
- F-08 (P2): Undocumented Pulse Types in Architecture Sec 16 (Phase 15.5)
- F-09 through F-13 (P2/P3): Non-blocking test isolation, MCP scoping, stub cleanup, and environment alignment items
