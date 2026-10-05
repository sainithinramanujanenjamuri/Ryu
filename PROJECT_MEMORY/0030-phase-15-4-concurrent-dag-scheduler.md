# Project Memory: 0030 — Phase 15.4 Concurrent DAG Scheduler

**Date:** 2026-10-05  
**Author:** Ryu Autonomous Core Agent  
**Baseline:** Phase 15.3 Verified (`a74a19f`)  
**Status:** COMPLETE (GATE-15.4: PASS WITH EXPLICIT INTEGRATION LIMITATIONS)  
**Governing ADR:** ADR-0048 (Concurrent DAG Scheduler and Bounded Execution Engine)  
**Governing Contracts:** SCHED-001, SCHED-002, SCHED-003, SCHED-004, SCHED-005  
**Governing Architecture:** Space-Centric Cognitive Architecture (SCCA)  

---

## 1. Context & Purpose

The Phase 15 Architecture Audit (`docs/PHASE_15_ARCHITECTURE_AUDIT.md`) identified **Finding F-04 (Concurrent DAG Scheduler — P1)** as a primary execution engine bottleneck:
- Prior to Phase 15.4, `DeterministicDispatcher.execute_task_full_pipeline()` was strictly synchronous and blocking.
- When an execution DAG contained multiple independent ready tasks (such as parallel branches or diamond structures), they could only be executed sequentially in a single thread.
- This introduced artificial latency, underutilized system CPU/GPU resources, and prevented concurrent workload scaling across Spaces.
- At the same time, naive concurrency would violate SCCA invariants: it could bypass SpaceKernel's single-writer Plan CAS authority, race against ResourceManager leases, cause budget overspend races, or introduce non-deterministic execution ordering.

Phase 15.4 eliminates this synchronous execution bottleneck by introducing a bounded, multi-space, deterministic scheduler (`ConcurrentDAGScheduler`) that preserves all SCCA authority boundaries, CAS determinism, and budget guarantees.

---

## 2. What Changed

1. **Architectural Decision Record & Contract Matrix:**
   - Authored `adr/0048-concurrent-dag-scheduler.md` establishing the design invariants, rebase model, and authority boundaries.
   - Added Section 30H to `docs/CONTRACT_MATRIX.md` specifying canonical contracts `SCHED-001` through `SCHED-005`.
   - Classified `SCHED-006` through `SCHED-016` as inherited acceptance criteria rather than additional contracts.
   - Updated `harness/spec_map.yaml` with traceability mappings to automated test suites.

2. **Durable Duplicate-Dispatch & Race-Safe Idempotency (`core/orchestrator/execution_state.py`, `core/orchestrator/scheduler.py`):**
   - Added `claim_attempt(record) -> bool` to `ExecutionAttemptStore` protocol and `InMemoryExecutionAttemptStore`.
   - `ConcurrentDAGScheduler._dispatch_candidate()` atomically claims an attempt in `ExecutionAttemptStore` prior to spawning worker threads. Losing callers observe existing claims, discard from active sets, and spawn zero workers.

3. **Atomic Budget Pre-Reservation (`core/capabilities/admission.py`):**
   - Added thread-safe budget reservation structures under reentrant lock: `reserve_budget()`, `release_reservation()`, `reconcile_reservation()`, `get_reserved_budget()`.
   - `check_admission()` evaluates available budget minus existing uncommitted reservations before admitting a request, preventing parallel tasks from overspending Space limits.
   - Conformed `space.budget.exceeded` payload strictly to schema.

4. **Plan CAS Optimistic Rebase & Rollback Hardening (`core/space/kernel.py`, `core/orchestrator/dispatch_model.py`):**
   - Added bounded optimistic rebase loop (`max_rebases: int = 0`) with contention backoff sleep to `SpaceKernel.propose_task_transition()`. Contending tasks dynamically reload the latest `TaskGraph` state and rebase if the transition remains valid.
   - Configured `DeterministicDispatcher` to use `max_rebases=3` for pipeline state transitions.
   - Updated `DeterministicDispatcher.execute_task_full_pipeline()` to support `expected_plan_version=None`, allowing concurrent worker threads to dynamically fetch the current plan version on demand.
   - Added `is_plan_converged()` and `is_plan_succeeded()` helper methods to `DeterministicDispatcher`.
   - Ensured `execute_task_full_pipeline()` reconciles or releases capability budget reservations in a `try...finally` block.

5. **Concurrent DAG Scheduler Engine (`core/orchestrator/scheduler.py`):**
   - Implemented `SchedulerConfig` dataclass enforcing bounded worker pool limits ($1 \le N \le 64$), per-space concurrency limits, ready queue capacity bounds, and positive timeouts.
   - Implemented `TaskCandidate` with deterministic sort order `(-priority, topological_depth, task_id)`.
   - Implemented `ConcurrentDAGScheduler`:
     - Bounded `ThreadPoolExecutor`.
     - Multi-Space fair sharing (Round-Robin distribution across registered spaces).
     - Idempotency and duplicate dispatch deduplication tracking.
     - Topological DAG depth traversal.
     - Event-driven wakeup and drain primitives (`step()`, `drain()`, `run_until_converged()`, `start()`, `stop()`).
   - Exported symbols from `core/orchestrator/__init__.py`.

6. **Dedicated Test Suite (`core/orchestrator/tests/test_concurrent_dag_scheduler.py`):**
   - Implemented 19 comprehensive unit, integration, and adversarial tests verifying bounded worker limits, per-space limits, diamond DAG parallel execution, CAS rebase resilience, fair sharing, budget pre-reservation, failure containment, durable attempt claim race safety, and graceful shutdown.

---

## 3. What Was Verified

1. **Dedicated Scheduler Test Suite:**
   - `core/orchestrator/tests/test_concurrent_dag_scheduler.py`: **19 passed, 0 failed, 0 skipped** (100% PASS in 2.66s).
   - All 5 canonical contracts (`SCHED-001` through `SCHED-005`), acceptance criteria (`SCHED-006` through `SCHED-016`), and adversarial test cases (`ADV-SCHED-01` through `ADV-SCHED-14`) verified.

2. **Core, Workers, and Harness Regression Suite:**
   - Ran `pytest core workers harness/cases -q --tb=no`:
     - **1143 passed, 0 failed, 7 skipped**.
     - Zero regressions across existing kernels, dispatch models, crash recovery, workers, or pulse bus.
     - Skipped tests are solely environment-bounded (offline Docker or non-elevated symlinks).

3. **Core Boundary Independence Audit:**
   - Ran `python scripts/dep_guard.py`:
     - **[PASS]**: Zero forbidden imports in `core/`.

4. **Contract Synchronization & Governance Audits:**
   - Ran `python scripts/contract_sync.py`: **PASS**.
   - Ran `python scripts/v1_audit_governance.py`: **V1-005 STATUS: PASS** (ADRs 0001..0048, Pulse Registry, Payload Schemas 1:1, Contract Matrix integrity all clean).
   - Ran `python scripts/v1_audit_spec_coverage.py`: **V1-001 STATUS: PASS** (Zero orphaned criteria, zero missing tests).

5. **Static Type Checking & Linting:**
   - Ran `mypy core/orchestrator/scheduler.py core/orchestrator/execution_state.py core/orchestrator/dispatch_model.py core/capabilities/admission.py core/space/kernel.py`:
     - **Success: no issues found in 5 source files**.
   - Ruff: UNVERIFIED — ruff is not installed in the execution environment; lint verification could not be independently executed.

6. **Environment Limitations & Working Tree:**
   - Hermetic/mock PostgreSQL verification passed; live PostgreSQL integration remains environment-limited due to local Docker service unavailability.
   - Working tree: Changes staged and ready for commit; no remote push performed.

---

## 4. What Remains / Next Steps

1. **Phase 15.5 — Finding F-05 (Semantic Memory & Experience Retrieval — P1):**
   - Architectural audit and implementation of semantic vector storage, experience clustering, and cross-session knowledge retrieval.
2. **Phase 15.6 — Finding F-06 (Durable Convergence State & Repair Memory — P1):**
   - Persisting convergence attempts, repair loops, and failure fingerprints to PostgreSQL.
3. **Phase 15.7 — Finding F-07 (Agent Hierarchy & Dynamic Delegation — P2):**
   - Sub-space goal delegation and multi-agent coordination.
