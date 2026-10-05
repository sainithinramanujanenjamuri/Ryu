# RYU AI FRAMEWORK — PHASE 15.4 ARCHITECTURE AUDIT
## Finding F-04: Concurrent DAG Scheduler
**Priority:** P1  
**Authoritative Source:** `docs/PHASE_15_ARCHITECTURE_AUDIT.md`  
**Current Baseline Commit:** `a74a19f` (`docs(phase15.3): refine PostgreSQL traversal and memory claims to evidence-bounded wording`)  
**Mode:** ARCHITECTURE AUDIT ONLY — NO IMPLEMENTATION  

---

## 1. Executive Verdict

### **READY WITH CONDITIONS**

The RYU AI Framework possesses all essential architectural prerequisites, durability primitives, and authority boundaries required to implement a concurrent DAG scheduler. However, proceeding directly to implementation without establishing explicit bounding invariants, optimistic Compare-And-Swap (CAS) rebase protocols, and multi-space fair queueing would introduce critical failure modes (CAS storm contention, lease leakage, and budget race conditions).

#### Why Implementation is Ready:
1. **Durable PlanStore & CAS Atomicity (Phase 15.1, ADR-0045):** PostgreSQL `plans` and `plan_history` tables enforce single-writer transactional CAS (`SELECT ... FOR UPDATE` row locks per Space), snapshotting immutable historical plan versions and rejecting stale mutations via `plan.version.superseded`.
2. **Space-Partitioned Artifact Isolation (Phase 15.2, ADR-0046):** Capability-generated artifacts are strictly isolated under `<base_working_dir>/artifacts/<space_id>/<worker_namespace>/<filename>`, with task-prefixed filenames eliminating cross-task and cross-space collisions.
3. **Bounded State Retrieval (Phase 15.3, ADR-0047):** Keyset-paginated pulse retrieval bounds memory overhead during historical and causation event scanning.
4. **Thread-Safe Resource Leasing (Phase 3/12, ADR-0006):** `ResourceManager` enforces `threading.RLock()`, fractional capacity allocation, idempotent lease tokens, and automatic lease sweep.
5. **Execution Attempt Tracking (Phase 12.8, ADR-0042):** Migration 006 provides the `execution_attempts` schema with unique idempotency keys, tracking in-flight attempts (`dispatched`, `running`) and supporting multi-attempt recovery.

#### Governing Conditions for Implementation:
1. **Condition 1 (Strict F-04 / F-06 Scope Separation):** F-04 addresses *in-flight concurrent execution of independent DAG nodes*. It must NOT implement durable convergence state persistence (replan/repair counters in PostgreSQL across restarts), which is strictly deferred to Finding F-06.
2. **Condition 2 (Optimistic CAS Rebase Protocol):** The scheduler must handle version supersession during task lifecycle transitions by rebasing unconflicted task transitions up to a bounded ceiling (`max_rebases = 3`), rather than failing tasks on the first CAS conflict.
3. **Condition 3 (Lease Rollback Protection):** If a CAS conflict cannot be resolved after lease acquisition, acquired hardware leases must immediately be released via `ResourceManager.release()` to prevent hardware resource starvation.
4. **Condition 4 (Atomic Budget Pre-Reservation):** `AdmissionController` currently evaluates `remaining_budget <= 0` but only records spend post-execution. Under concurrent admission, multiple tasks can pass admission simultaneously and cause budget overruns under `hard_stop`. F-04 must introduce atomic budget pre-reservation at admission time.
5. **Condition 5 (Core Boundary Rule & Subordination):** The scheduler coordinator must reside in `core/orchestrator/`, must NOT import from `workers/` or higher layers, and must subordinate strictly to `SpaceKernel` authority.
6. **Condition 6 (Environment Partitioning):** Live PostgreSQL and Redis integration verification requires active services; in offline environments (e.g. Docker Desktop stopped), test suites must partition cleanly into unit/mock execution and explicit integration skips without weakening assertions.

---

## 2. Baseline

- **Repository:** `d:\RYU`
- **Git Branch:** `main`
- **Commit Hash:** `a74a19f` (`docs(phase15.3): refine PostgreSQL traversal and memory claims to evidence-bounded wording`)
- **Working Tree:** Clean (0 uncommitted changes, 0 untracked files).
- **Runtime Versions:**
  - Python: 3.12+ (tested with virtual environment `d:\RYU\.env`, Python 3.11.9 / 3.12 compatibility)
  - Rust: 1.75+ (`node_runtime/Cargo.toml`)
  - PostgreSQL: 16 (docker-compose `deploy/docker-compose.yml`)
  - Redis: 7 (docker-compose `deploy/docker-compose.yml`)
- **Test Baseline:** 350+ passing automated tests across `core/`, `workers/`, `harness/`, and `contracts/` (including 124 passing unit/harness tests in Phase 15.3).
- **Governance Baseline:**
  - ADRs: ADR-0001 through ADR-0047 contiguous and monotonically numbered (`scripts/v1_audit_governance.py` PASS).
  - Contracts: 240 contract IDs indexed in `docs/CONTRACT_MATRIX.md`; 38 architecture pulse types, 50 registry types (`scripts/contract_sync.py` PASS).
  - Spec Coverage: 177 architecture criteria mapped across 198 spec-map entries (`scripts/v1_audit_spec_coverage.py` PASS).
  - Core Boundary Rule: 0 forbidden imports in `core/` (`scripts/dep_guard.py` PASS).

---

## 3. Finding F-04: The Problem Statement

### Authoritative Definition (`docs/PHASE_15_ARCHITECTURE_AUDIT.md`)
> **Finding F-04 — Concurrent DAG Scheduler (P1):**  
> `DeterministicDispatcher.execute_task_full_pipeline()` executes one task synchronously and blocks the orchestration loop while worker processes execute (`invoker.invoke(task_req)`). Even though `TaskGraph.get_ready_tasks()` can identify multiple concurrently ready tasks across independent branches (e.g. $A \to B$ and $A \to C$), they are dispatched sequentially in a blocking single-threaded loop.

### Root Cause Analysis
1. **Synchronous Execution Pipeline:** In `core/orchestrator/dispatch_model.py` (lines 1350–1526, 2381–2448), `execute_task_full_pipeline()` synchronously invokes `invoker.invoke(task_req)`. The caller blocks until the worker process finishes, logs artifacts, and returns an exit code.
2. **Missing Concurrency Coordinator:** There is no background dispatcher thread, thread pool, or asynchronous loop in `core/orchestrator/`. Existing tests (e.g., `test_phase12_integrated_execution.py`) simulate DAG progression by manually looping through tasks one by one.
3. **Plan Version CAS Contention:** Every task transition (`READY` $\to$ `ADMISSION_PENDING` $\to$ `ADMITTED` $\to$ `LEASE_PENDING` $\to$ `LEASED` $\to$ `DISPATCHED` $\to$ `RUNNING` $\to$ `OBSERVING` $\to$ `EVALUATING` $\to$ `COMPLETED`) commits a `PlanDelta` to `SpaceKernel.commit_plan_delta()`, which increments `plan_version`. If two tasks run concurrently from the same base version, one CAS succeeds ($V \to V+1$) and the second fails with `cas_failed`. Currently, `dispatch_task` aborts on `cas_failed` instead of rebasing and retrying.
4. **Unbounded Concurrency & Starvation Risk:** If multiple tasks become `READY` simultaneously (e.g., a fan-out of 100 tasks), there is currently no bounded queue or worker pool to prevent resource starvation, CPU saturation, or uncoordinated database writes.

---

## 4. Current Execution Trace

The current execution flow proceeds through the following hierarchy:

```text
Human Goal
    │
    ▼
Channel Daemon (http://127.0.0.1:8420)
    │
    ▼
GoalSpec (core/orchestrator/goal_analyzer.py)
    │
    ▼
Space (core/space/kernel.py)
    │
    ▼
Plan / PlanStore (core/plans/postgres_plan_store.py)
    │
    ▼
TaskGraph (core/plans/task_graph.py)
    │
    ▼
Dispatcher (core/orchestrator/dispatch_model.py)
    │
    ▼
Admission Control (core/capabilities/admission.py)
    │
    ▼
ResourceManager (core/resources/manager.py)
    │
    ▼
LeaseManager (core/resources/lease.py)
    │
    ▼
RuntimeWorkerInvoker (workers/invoker.py)
    │
    ▼
Worker Process (workers/python/worker.py, shell/worker.py, etc.)
    │
    ▼
Evidence (VerifiedExecutionEvidence in core/orchestrator/dispatch_model.py)
    │
    ▼
Task Completion (TaskCompletionResult in core/orchestrator/dispatch_model.py)
    │
    ▼
Dependency Unblocking (DeterministicDispatcher.unblock_dependencies())
    │
    ▼
Goal Evaluation (DeterministicGoalEvaluator in core/orchestrator/dispatch_model.py)
    │
    ▼
Convergence (ConvergenceEngine in core/orchestrator/dispatch_model.py)
```

### Exact Code Path Citations
- **Pipeline Entry:** `DeterministicDispatcher.execute_task_full_pipeline()` (`core/orchestrator/dispatch_model.py` lines 2381–2448).
- **Admission & Lease Coordination:** `DeterministicDispatcher.coordinate_admission_and_lease()` (`core/orchestrator/dispatch_model.py` lines 1035–1145).
- **Task Dispatch & Blocking Invocation:** `DeterministicDispatcher.dispatch_task()` (`core/orchestrator/dispatch_model.py` lines 1146–1526).
  - Synchronous worker execution occurs at line 1400: `exec_res = invoker.invoke(task_req)`.
  - The calling thread blocks here until the worker process exits.
- **Evidence Verification:** `DeterministicDispatcher.verify_execution_evidence()` (`core/orchestrator/dispatch_model.py` lines 1774–1882).
- **Dependency Unblocking:** `DeterministicDispatcher.unblock_dependencies()` (`core/orchestrator/dispatch_model.py` lines 1955–2035).
  - Traverses `graph.nodes`, checks parents, and issues a `PlanDelta` to transition downstream tasks to `READY`.

### Current Dispatcher Characteristics
| Question | Answer | Evidence / Code Reference |
|:---|:---:|:---|
| Executes one task synchronously? | **YES** | `dispatch_model.py:1400` blocks on `invoker.invoke(task_req)` |
| Blocks the orchestration loop? | **YES** | No background task or thread pool exists |
| Waits for worker completion before next dispatch? | **YES** | Execution is strictly serial per call |
| Permits multiple READY tasks in graph? | **YES** | `task_graph.py:207` `get_ready_tasks()` returns all unblocked tasks |
| Permits independent branches to run concurrently? | **NO** | Callers must invoke `execute_task_full_pipeline()` sequentially |
| Can dispatch while another task is RUNNING? | **NO** | Single-threaded execution model |
| Can represent multiple active attempts? | **YES** | `execution_attempts` DB table supports concurrent rows |
| Safely reconciles multiple task completions? | **PARTIAL** | `unblock_dependencies()` has CAS retry, but task transition itself does not rebase |

---

## 5. Current Concurrency Model

| Capability | Current State | Evidence | Risk |
|:---|:---:|:---|:---|
| **Task-Level Concurrency** | **NO** | `dispatch_model.py:2381-2448` executes serially | High execution latency on embarrassingly parallel DAGs |
| **Branch Concurrency** | **NO** | `test_phase12_integrated_execution.py:240-310` tests branches by calling pipeline sequentially | Parallel branches serialized unnecessarily |
| **Space-Level Concurrency** | **PARTIAL** | Separate `SpaceKernel` instances operate on distinct `space_id` keys in PostgreSQL | No coordinator exists to balance worker allocations across Spaces |
| **Worker-Level Concurrency** | **PARTIAL** | Multiple worker objects can be instantiated, workers run sandboxed processes | Worker invocation blocks the caller thread synchronously |
| **Resource-Level Concurrency** | **YES** | `ResourceManager` (`core/resources/manager.py:52,91-170`) uses `threading.RLock()`, fractional capacity, and queueing | None; resource manager is already fully concurrency-safe |
| **Node-Level Concurrency** | **PARTIAL** | `node_runtime` (Rust) has multi-threaded runtime, but orchestrator invokes node worker synchronously | Hardware capacity underutilized |
| **Global Concurrency** | **NO** | Global dispatch is serialized by synchronous caller loop | Throughput capped at single-task speed |

---

## 6. Task State Machine

The complete SCCA task lifecycle defines 17 discrete states (`core/plans/task_graph.py` lines 39–62).

```text
                  ┌──────────────┐
                  │   PENDING    │
                  └──────┬───────┘
                         │ (All upstream deps COMPLETED)
                         ▼
                  ┌──────────────┐
                  │    READY     │
                  └──────┬───────┘
                         │ (Request capability)
                         ▼
             ┌────────────────────────┐
             │   ADMISSION_PENDING    │
             └──────┬──────────┬──────┘
  (Admitted) │                 │ (Policy/Budget denied)
             ▼                 ▼
      ┌─────────────┐   ┌─────────────┐
      │   ADMITTED  │   │   BLOCKED   │
      └──────┬──────┘   └─────────────┘
             │ (Request resource lease)
             ▼
      ┌─────────────┐
      │LEASE_PENDING│◀────────────────┐
      └──────┬──────┘                 │ (Contested queue)
   (Leased)  │                        │
             ▼                        │
      ┌─────────────┐                 │
      │   LEASED    │─────────────────┘
      └──────┬──────┘
             │ (Prepare execution attempt)
             ▼
      ┌─────────────┐
      │ DISPATCHED  │
      └──────┬──────┘
             │ (Worker process spawned)
             ▼
      ┌─────────────┐
      │   RUNNING   │
      └──────┬──────┘
             │ (Worker process exit)
             ▼
      ┌─────────────┐
      │  OBSERVING  │
      └──────┬──────┘
             │ (Evidence verification: SHA-256, exit code)
             ▼
      ┌─────────────┐
      │ EVALUATING  │
      └──────┬──────┘
             │
     ┌───────┴───────────────────────┐
     │ (Verified exit_code == 0)     │ (Execution failed / Timeout)
     ▼                               ▼
┌───────────┐                 ┌─────────────┐
│ COMPLETED │                 │   FAILED    │ (or TIMED_OUT / CANCELLED)
└───────────┘                 └──────┬──────┘
                                     │ (Convergence evaluation)
                                     ▼
                              ┌─────────────┐
                              │RETRY_PENDING│ (or ESCALATED)
                              └─────────────┘
```

### Transition Matrix & Concurrency Safety

| Current State | Event | Next State | Authority | Persistence | CAS Required? | Concurrent-Safe? | Existing Test Evidence |
|:---|:---|:---|:---|:---|:---:|:---:|:---|
| `PENDING` | Dependencies satisfied | `READY` | SpaceKernel | PlanStore | YES | YES | `test_phase12_integrated_execution.py:240` |
| `READY` | Admission requested | `ADMISSION_PENDING` | SpaceKernel | PlanStore | YES | **RACE** | `test_phase12_worker_invocation.py:85` |
| `ADMISSION_PENDING` | Capability admitted | `ADMITTED` | AdmissionController | PlanStore | YES | **RACE** | `test_phase12_worker_invocation.py:92` |
| `ADMISSION_PENDING` | Capability denied | `BLOCKED` | AdmissionController | PlanStore | YES | YES | `test_phase12_integrated_execution.py:320` |
| `ADMITTED` | Lease requested | `LEASE_PENDING` | SpaceKernel | PlanStore | YES | **RACE** | `test_phase12_worker_invocation.py:110` |
| `LEASE_PENDING` | Lease granted | `LEASED` | ResourceManager | PlanStore | YES | **RACE** | `test_phase12_worker_invocation.py:115` |
| `LEASE_PENDING` | Lease contested | `LEASE_PENDING` | ResourceManager | In-Memory / DB | NO | YES | `core/resources/tests/` |
| `LEASE_PENDING` | Lease denied | `FAILED` | ResourceManager | PlanStore | YES | YES | `core/orchestrator/tests/` |
| `LEASED` | Attempt recorded | `DISPATCHED` | Dispatcher | AttemptStore | YES | YES | `core/orchestrator/execution_state.py` |
| `DISPATCHED` | Worker started | `RUNNING` | Invoker / Worker | PlanStore | YES | **RACE** | `test_phase12_integrated_execution.py:120` |
| `RUNNING` | Process exited | `OBSERVING` | Dispatcher | PlanStore | YES | **RACE** | `test_phase12_end_to_end_observation.py:90` |
| `OBSERVING` | Artifacts verified | `EVALUATING` | Dispatcher | PlanStore | YES | **RACE** | `test_phase12_end_to_end_observation.py:150` |
| `EVALUATING` | Evidence verified | `COMPLETED` | Dispatcher | PlanStore | YES | **RACE** | `test_phase12_integrated_execution.py:145` |
| `EVALUATING` | Verification failed | `FAILED` | Dispatcher | PlanStore | YES | **RACE** | `test_phase12_integrated_execution.py:165` |
| `RUNNING` | Timeout expired | `TIMED_OUT` | Scheduler | PlanStore | YES | **RACE** | Unit tests |
| `FAILED` | Retry admitted | `RETRY_PENDING` | ConvergenceEngine| PlanStore | YES | YES | `test_phase12_integrated_execution.py:180` |
| Any | Replan / Human Stop | `CANCELLED` | SpaceKernel | PlanStore | YES | YES | Plan CAS tests |

### Vulnerability Analysis of State Transitions
- **The "RACE" Tag:** Under concurrent execution, any transition requiring CAS (`SpaceKernel.propose_task_transition`) contends on `plan_version`. Because tasks currently execute up to 8 transitions, $N$ concurrent tasks will generate $8N$ CAS attempts. Without optimistic rebase, $(N-1)$ tasks will fail their CAS operations and abort.
- **Lease Leak on Failed CAS:** If task transitions `LEASE_PENDING` $\to$ `LEASED`, acquires a hardware lease, but fails the CAS commit due to a concurrent task updating the plan, the lease must be rolled back immediately. Line 985 in `dispatch_model.py` already includes rollback logic, but currently aborts the task rather than retrying the CAS.

---

## 7. Resource and Admission Concurrency Audit

### ResourceManager Audit (`core/resources/manager.py`)
- **Synchronization:** Uses `threading.RLock()`. All operations (`acquire`, `release`, `_sweep_expirations_locked`) execute under the lock.
- **Capacity Tracking:** Correctly tracks fractional units (`units <= resource.total_capacity`).
- **Contention Handling:** When capacity is insufficient, enqueues request in `ResourceQueue`, returns `granted=False` with `queue_position`, and publishes `resource.conflict`.
- **Queue Drain:** When a lease is released (`release()`), `ResourceManager` drains the queue and grants pending leases in order of queue discipline (`FIFO`, `PRIORITY`, or `FAIR_SHARE`).
- **Verdict:** `ResourceManager` is fully concurrency-safe. No changes to resource leasing logic are required.

### Admission Control Audit (`core/capabilities/admission.py`)
- **Synchronization:** Uses `threading.Lock()` for budget queries.
- **Budget Race Vulnerability (CRITICAL):**
  - In `check_admission()` (line 161), the controller checks:
    ```python
    if remaining <= 0:
        return CapabilityResponse(status="denied", error="budget_exhausted")
    ```
  - However, budget is **NOT** deducted at admission time! Spend is recorded only at task completion via `record_spend()`!
  - **The Race:** If remaining budget is \$10.00, and two tasks requiring \$10.00 arrive simultaneously, both pass `check_admission()`, both execute, and both record \$10.00 spend, driving the Space budget to -\$10.00 under `hard_stop` policy.
  - **Required Architectural Fix:** `AdmissionController` must support atomic budget *pre-reservation* at admission time, with reconciliation/reversion upon completion or failure.

### Human Approval Gate Concurrency
- Human approvals are validated under lock and marked consumed (`setattr(approval, "status", "consumed")`).
- Approval tokens specify `plan_version`. If a concurrent task increments `plan_version`, `approval_plan_version_mismatch` is raised. The scheduler must ensure gated tasks execute with strict version coordination.

---

## 8. PlanStore and CAS Semantics Audit

### PostgreSQL PlanStore (`core/plans/postgres_plan_store.py`)
- **Row-Level Lock:** `commit_delta()` opens a transaction and acquires a row lock:
  ```sql
  SELECT plan_version, graph_json, last_winning_delta
  FROM plans
  WHERE space_id = %s
  FOR UPDATE;
  ```
- **Atomicity:** Updates `plans` table and inserts an immutable snapshot into `plan_history` within the same transaction.
- **Conflict Detection:** If `delta.base_version != current_ver`, the commit is rejected, returning `(False, current_ver, winning_delta_id)`.
- **Space Isolation:** All queries are parameterized strictly by `space_id`. Cross-space contention is impossible.

### The Atomicity Boundary
- The database enforces *single-writer serialization per Space*.
- This ensures graph state cannot be corrupted by concurrent writers.
- However, because the entire `TaskGraph` is serialized as JSONB in a single row, **all task state transitions in a Space contend on the same row lock**.
- Therefore, concurrent scheduling in RYU must be *optimistic*: tasks prepare transitions locally and commit via bounded CAS retry (`rebase_and_propose_transition`).

---

## 9. Execution Attempt Durability Audit

### Execution Attempts Table (`deploy/migrations/006_create_execution_attempts_and_convergence_state.sql`)
- Primary Key: `attempt_id` (`VARCHAR(255)`).
- Unique Constraint: `idempotency_key` (`VARCHAR(255) UNIQUE`).
- Indexes:
  - `idx_exec_attempts_space` on `space_id`
  - `idx_exec_attempts_task` on `task_id`
  - `idx_exec_attempts_status` on `status`
  - `idx_exec_attempts_interrupted` on `(status, started_at) WHERE status IN ('dispatched', 'running')`
- **Concurrency Capability:** Because rows are keyed by unique `idempotency_key` and identified by `(space_id, task_id, attempt_number)`, multiple tasks can record active attempts in parallel without database contention.
- **Verdict:** Fully supports concurrent dispatch tracking.

---

## 10. Worker Concurrency Audit

| Worker Class | Execution Mode | Process/Thread Safe? | Shared State Risk | Serialization Required? |
|:---|:---|:---:|:---|:---:|
| `PythonWorker` | Sandboxed Subprocess (`python -c ...`) | **YES** | None (Isolated temp dir) | **NO** |
| `ShellWorker` | Subprocess (`subprocess.Popen`) | **YES** | None (Isolated environment) | **NO** |
| `FileWorker` | In-process filesystem operations | **YES** | Target file path contention | If same file |
| `NodeWorker` | Loopback HTTP / IPC to Node runtime | **YES** | Device lease contention | Handled by Lease |
| `ResearchWorker` | HTTP queries / Vector retrieval | **YES** | Read-only state | **NO** |
| `TestRunnerWorker` | Subprocess (`pytest`) | **YES** | Test working tree | **NO** |
| `BrowserWorker` | Playwright subprocess | **PARTIAL** | Port / Profile contention | Unique profile required |
| `SubagentWorker` | Antigravity Subagent Invocation | **YES** | Conversation ID isolation | **NO** |
| `RepositoryWorker` | Git CLI / Working tree operations | **NO** | **Git index / `.git` locks** | **YES (or Worktrees)** |

### Critical Finding on `RepositoryWorker`
`RepositoryWorker` operates on the shared git repository directory. If two concurrent tasks attempt `git checkout`, `git add`, or `git commit` simultaneously on the same working tree, git index locks (`.git/index.lock`) will fail one of the tasks.  
*Mitigation:* Git repository tasks must either acquire exclusive repository leases or operate in isolated git worktrees (`git worktree add`).

---

## 11. Artifact Isolation Audit

Phase 15.2 established space-safe artifact paths:
```text
<base_working_dir>/artifacts/<space_id>/<worker_namespace>/<filename>
```
### Intra-Space Task Isolation
- Workers format artifact filenames using task identifiers:
  - Repository worker: `{task_id}_patch.diff`, `{task_id}_inspect.json`
  - Test runner worker: `{task_id}_test_report.json`
  - Research worker: `{task_id}_findings.json`
- Because `task_id` is unique across nodes in a `TaskGraph`, concurrent tasks within the same Space write to distinct files.
- Atomic file writes (writing to `.tmp` and renaming via `os.replace`) prevent partial reads.
- **Verdict:** Artifact isolation is fully concurrency-safe.

---

## 12. Pulse Concurrency Audit

### Pulse Bus & Storage (`core/pulse_bus/`)
- **Sequence Ordering:** PostgreSQL `pulses` table uses `BIGSERIAL` for the `position` column. Sequence allocation in PostgreSQL is atomic, lock-free, and concurrency-safe.
- **Redis Streams:** `XADD` operations append pulses to Redis streams (`ryu:pulses:{space_id}`). Redis single-threaded command processing guarantees linear stream entry ordering.
- **Causation Chains:** Every pulse carries:
  - `correlation_id`: `corr-{space_id}-{task_id}`
  - `parent_pulse_id`: Rooted in the initiating task or dispatch pulse.
- **Concurrency Verdict:** Concurrent pulse emission from multiple worker dispatch threads produces interleaved, monotonic pulse streams without cross-talk or corruption.

---

## 13. Convergence Interaction Audit (F-04 vs F-06 Boundary)

### Scope Separation
- **Finding F-04 (This Audit):** Concurrent task scheduling, multi-task dispatch, and thread-safe failure handling.
- **Finding F-06 (Deferred):** Durable Convergence State (persisting `convergence_state` across daemon reboots, global convergence locks).

### Concurrency Hazards in Convergence
If Task B and Task C fail simultaneously:
1. Both tasks invoke `ConvergenceEngine.evaluate_and_propose()`.
2. Both evaluate failure fingerprints and retry budgets.
3. If both propose a replan (`ConvergenceDecision.REPLAN`), two competing `PlanDelta` proposals are submitted to `SpaceKernel`.
4. **Defense:** The first replan delta wins PlanStore CAS; the second replan delta fails CAS. The `ConvergenceEngine` must process failure evaluations sequentially per Space to prevent duplicate replans and double-consumption of retry budgets.

---

## 14. Crash Recovery Audit Under Concurrency

| Scenario | Durable State at Crash | Recovery Action on Startup | Duplicate Risk | Orphan Lease Risk |
|:---|:---|:---|:---:|:---:|
| **1. Task A running, Task B ready** | Task A: `execution_attempts` status='running'; Task B: `graph` state='ready' | Task A marked 'recovered', re-queued to READY; Task B dispatched normally | Idempotent | Task A lease swept and released |
| **2. Task A completed, state uncommitted** | Worker finished, but PlanStore CAS did not run; `attempt` status='running' | Classified as `STALE_DISPATCHED` or `CRASH`; re-executed or verified by artifact | Handled by idempotency key | Lease expires, cleaned by sweep |
| **3. Tasks A & B running simultaneously** | Both in `execution_attempts` status='running' | `StartupRecoveryEngine` recovers both independently | Idempotent | Both leases released via `ResourceManager.release()` |
| **4. Task A completes, Task B crashes** | Task A in `plan_history` as COMPLETED; Task B in `attempts` as RUNNING | Task A kept completed; Task B recovered to READY | None | Task B lease released |
| **5. Worker process dies** | `execution_attempts` status='running', heartbeat/process dead | Scheduler detects SIGCHLD/exitcode != 0, triggers failure transition | None | Immediate release in `finally` |
| **6. Dispatcher daemon dies** | Multiple tasks in `running`; leases active in PostgreSQL | `StartupRecoveryEngine` runs on cold boot; sweeps leases; resets tasks | None | `002_leases` swept by expiry |
| **7. Node disappears** | Leased resources on dead node | `ResourceManager` marks node unreachable; sweeps leases | None | Leases revoked |
| **8. Lease expires mid-execution** | Worker running past `duration_seconds` | `ResourceManager` revokes lease; task execution rejected on return | Low | Swept by `_sweep_expirations_locked` |
| **9. Pulse published, state commit fails** | Pulse exists in Redis/PG; PlanStore unmutated | PlanStore is authoritative; pulse marked orphaned in trace | None | Replay follows PlanStore |
| **10. State commits, pulse publish fails** | PlanStore updated to COMPLETED; pulse not in Redis | Startup pulse reconciler (`reconcile_unpublished()`) republishes | At-least-once delivery | None |

---

## 15. Replay Determinism Audit

### Control Decisions vs. Wall-Clock Ordering
- **Architectural Invariant:** Deterministic replay means *replaying the exact sequence of committed plan decisions and state transitions*, NOT reproducing non-deterministic physical thread scheduling or microsecond completion timestamps.
- **Deterministic Scheduling Order:** When multiple tasks are simultaneously `READY`, the scheduler must order them deterministically:
  $$\text{Sort key} = (\text{priority DESC}, \text{topological\_depth ASC}, \text{task\_id ASC})$$
- Replaying from committed `plan_history` reconstructs the exact graph evolution regardless of which worker thread completed first.

---

## 16. Cancellation and Timeout Audit

### Cancellation Races
- A user or governance policy may cancel a Space while tasks are `RUNNING`.
- **Handling:**
  1. Space cancellation sets Space state to `CANCELLED` via CAS.
  2. Running workers receive `SIGTERM` (and `SIGKILL` after grace period).
  3. Any worker output arriving after cancellation encounters `TaskState.CANCELLED` in PlanStore; the result is discarded and leases released immediately.

### Timeout Races
- If a task execution exceeds `timeout_seconds`:
  1. The scheduler times out the worker and initiates a transition to `TIMED_OUT`.
  2. If the worker process exits successfully right as the timeout fires:
     - CAS determines the winner: whichever transition commits first (`TIMED_OUT` vs `OBSERVING`) becomes canonical.
     - The loser's update is safely rejected.

---

## 17. Backpressure, Fairness, and Multi-Space Audit

### Bounded Ready Queue
- A DAG may contain thousands of nodes (e.g. 10,000 tasks).
- Storing 10,000 tasks in memory is prohibited.
- The scheduler must maintain a **bounded active queue** (e.g. `max_active_tasks = 100` per Space), querying unblocked tasks incrementally from `TaskGraph`.

### Multi-Space Fairness (Preventing Starvation)
- If Space A has 500 `READY` tasks and Space B has 2 `READY` tasks:
  - Global FIFO would starve Space B until Space A finishes.
  - **Required Policy:** **Deficit Round-Robin (DRR)** or **Fair-Share Worker Allocation**.
  - Each active Space receives a maximum concurrency quota (e.g., up to $\lfloor \text{max\_workers} / \text{active\_spaces} \rfloor$).

---

## 18. Security / Adversarial Concurrency Matrix

| Threat ID | Threat Description | Existing Defense | Missing Defense | Authority | Durable State | Required Contract | Required Test | Severity |
|:---|:---|:---|:---|:---|:---|:---:|:---|:---:|
| **ADV-SCHED-01** | Duplicate dispatch of same task | In-memory `tracked_attempts` | Durable idempotency check before worker spawn | Dispatcher | `execution_attempts` | `SCHED-001` | `test_duplicate_dispatch_blocked` | P0 |
| **ADV-SCHED-02** | Double lease acquisition on same capacity | `ResourceManager._lock` | None (already protected) | ResourceManager | `leases` | `RESOURCE-002` | `test_concurrent_lease_contention` | P1 |
| **ADV-SCHED-03** | Stale task state execution | Expected plan version check | Bounded rebase loop | SpaceKernel | `plans` | `SCHED-002` | `test_stale_task_state_rebase` | P1 |
| **ADV-SCHED-04** | Concurrent CAS storm across workers | PlanStore row lock (`FOR UPDATE`) | Exponential backoff on rebase | PlanStore | `plans` | `PLAN-DURABLE-002` | `test_concurrent_cas_storm` | P1 |
| **ADV-SCHED-05** | Lost task completion | None (fails silently on CAS failure) | Mandatory completion retry with rebase | Dispatcher | `plans` | `SCHED-002` | `test_completion_cas_retry` | P0 |
| **ADV-SCHED-06** | Forged worker completion | SHA-256 evidence verification | None (already verified) | Dispatcher | `VerifiedEvidence` | `EVID-001` | `test_forged_completion_rejected` | P0 |
| **ADV-SCHED-07** | Late worker result after timeout | CAS state check (`from_state='running'`) | Explicit lease token revocation check | Dispatcher | `plans` | `SCHED-005` | `test_late_worker_result_discarded` | P1 |
| **ADV-SCHED-08** | Timeout / completion race | CAS atomicity | None (first commit wins) | SpaceKernel | `plans` | `SCHED-005` | `test_timeout_completion_race` | P2 |
| **ADV-SCHED-09** | Cancellation / completion race | CAS atomicity | Worker process termination (`kill`) | Dispatcher | `plans` | `SCHED-005` | `test_cancellation_race` | P1 |
| **ADV-SCHED-10** | Retry budget double-consumption | In-memory retry count | Serialized failure evaluation | ConvergenceEngine | `convergence_state` | `CONV-002` | `test_concurrent_retry_consumption` | P1 |
| **ADV-SCHED-11** | Convergence replan race | Plan CAS rejects superseded replan | Space-level convergence lock | ConvergenceEngine | `plans` | `CONV-003` | `test_convergence_replan_race` | P1 |
| **ADV-SCHED-12** | Cross-space task execution | `verify_space_identity` | None (kernel rejects) | SpaceKernel | `spaces` | `SPACE-001` | `test_cross_space_dispatch_rejected` | P0 |
| **ADV-SCHED-13** | Cross-space artifact collision | Space-partitioned artifact dirs | None (Phase 15.2 verified) | SpaceKernel | Filesystem | `SPACE-003` | `test_space_artifact_isolation` | P0 |
| **ADV-SCHED-14** | Resource starvation by single Space | `ResourceQueue` per resource | Multi-space worker quota | ResourceManager | `leases` | `SCHED-003` | `test_multi_space_fairness` | P1 |
| **ADV-SCHED-15** | Unbounded READY queue memory leak | None | Bounded queue with pagination | Scheduler | In-Memory / DB | `SCHED-004` | `test_bounded_ready_queue` | P1 |
| **ADV-SCHED-16** | Dispatcher daemon crash | `StartupRecoveryEngine` | None (Phase 12.8 verified) | Orchestrator | `execution_attempts` | `RECOVERY-001` | `test_recovery_after_crash` | P0 |
| **ADV-SCHED-17** | Worker process crash | Process returncode != 0 check | None (transitions to FAILED) | Invoker | `execution_attempts` | `WORKER-003` | `test_worker_crash_handling` | P1 |
| **ADV-SCHED-18** | Lease expiry during execution | `_sweep_expirations_locked` | Rejection of expired lease on commit | ResourceManager | `leases` | `RESOURCE-004` | `test_lease_expiry_during_task` | P1 |
| **ADV-SCHED-19** | Duplicate pulse publication | Idempotent pulse ID check | None (PulseBus verified) | PulseBus | `pulses` | `PULSE-008` | `test_duplicate_pulse_publication` | P2 |
| **ADV-SCHED-20** | Out-of-order completion pulses | Monotonic `position` in PostgreSQL | None (already supported) | PulseStore | `pulses` | `PULSE-009` | `test_out_of_order_pulses` | P2 |
| **ADV-SCHED-21** | Stale replay decision | Historical plans immutable | None (PlanStore verified) | PlanStore | `plan_history` | `REC-003` | `test_deterministic_replay` | P1 |
| **ADV-SCHED-22** | Approval gate race | `status="consumed"` CAS | Invalidation on version advance | AdmissionController | In-Memory / DB | `APPROVE-002` | `test_approval_gate_race` | P1 |
| **ADV-SCHED-23** | Budget overspend under concurrency | Budget lock | Atomic budget reservation | AdmissionController | In-Memory / DB | `SCHED-004` | `test_concurrent_budget_reservation` | P0 |
| **ADV-SCHED-24** | Node disappears during dispatch | Unreachable error raised | Re-queue task to READY | ResourceManager | `leases` | `NODE-003` | `test_node_disappearance` | P1 |
| **ADV-SCHED-25** | Scheduler restart during active DAG | Cold boot startup recovery | None (ADR-0042 verified) | Orchestrator | `execution_attempts` | `RECOVERY-004` | `test_scheduler_restart_active_dag` | P0 |

---

## 19. Existing Capabilities Reused (DO NOT REBUILD)

Coding agents must reuse the following verified components without modification or duplication:
1. **SpaceKernel & Plan CAS (`core/space/kernel.py`):** Single-writer CAS authority, isolation boundary, and checkpoint recovery.
2. **PostgresPlanStore (`core/plans/postgres_plan_store.py`):** Row-level locked Plan and history persistence.
3. **ResourceManager & LeaseManager (`core/resources/`):** Fractional capacity leases, contested queues, and sweep routines.
4. **TaskGraph (`core/plans/task_graph.py`):** Topological sorting, dependency validation, and readiness checking.
5. **StartupRecoveryEngine (`core/orchestrator/startup_recovery.py`):** Interrupted attempt detection and recovery classification.
6. **ExecutionAttemptStore (`core/orchestrator/execution_state.py`):** Attempt records and idempotency deduplication.
7. **PostgresPulseStore (`core/pulse_bus/`):** Monotonic BIGSERIAL pulse persistence and keyset pagination.
8. **RuntimeWorkerInvoker (`workers/invoker.py`):** Sandboxed worker process invocation and artifact sanitization.
9. **SpaceSafeArtifactManager (`core/space/artifact_paths.py`):** Space-scoped directory partitioning.

---

## 20. Missing Contracts

The following machine contracts must be defined under `contracts/registry/` prior to implementation:

### 1. `SCHED-001` — Bounded Concurrent Execution Invariant
- **Requirement:** Total concurrent workers across the daemon must not exceed `max_concurrent_workers`. Per-space concurrent tasks must not exceed `max_concurrent_tasks_per_space`.
- **Authority:** `ConcurrentDAGScheduler` (`core/orchestrator/`).
- **Invariants:** Worker pool is strictly bounded; no unbounded threads or processes may be spawned.

### 2. `SCHED-002` — Optimistic Plan CAS Rebase Invariant
- **Requirement:** Task lifecycle state transitions (`READY` $\to \dots \to$ `COMPLETED`) must automatically rebase against concurrent plan mutations up to `max_rebases = 3`.
- **Authority:** `DeterministicDispatcher` / `SpaceKernel`.
- **Invariants:** If rebases are exhausted, hardware leases are immediately released.

### 3. `SCHED-003` — Multi-Space Fairness Invariant
- **Requirement:** The scheduler must allocate worker execution slots across active Spaces using fair-share scheduling, preventing starvation.
- **Authority:** `ConcurrentDAGScheduler`.

### 4. `SCHED-004` — Atomic Admission Budget Pre-Reservation
- **Requirement:** `AdmissionController` must deduct estimated task cost at admission time and reconcile upon completion/failure.
- **Authority:** `AdmissionController` (`core/capabilities/`).

### 5. `SCHED-005` — Terminal Transition Exclusivity
- **Requirement:** A task may enter a terminal state (`COMPLETED`, `FAILED`, `TIMED_OUT`, `CANCELLED`) exactly once. Late updates from defunct attempts are rejected.
- **Authority:** `SpaceKernel` Plan CAS.

---

## 21. Missing Pulses

The following pulse types are required to provide observability for concurrent scheduling:

| Pulse Type | Producer | Consumer | Payload Fields | Causal Parent | Persistence | Replay Semantics |
|:---|:---|:---|:---|:---|:---:|:---:|
| `task.dispatch_blocked` | Scheduler | Monitor / UI | `task_id`, `space_id`, `reason`, `queue_position` | `task.ready` | YES | Preserved |
| `scheduler.capacity_exhausted` | Scheduler | ResourceMgr | `space_id`, `active_workers`, `max_workers` | None | YES | Preserved |
| `task.cas_rebased` | Dispatcher | Monitor | `task_id`, `old_version`, `new_version`, `rebase_attempt` | Previous CAS | YES | Preserved |

*Note:* Standard execution pulses (`task.started`, `task.completed`, `task.failed`, `resource.conflict`) already exist in the registry and will be emitted by the concurrent pipeline.

---

## 22. Database Requirements

**No new migrations are required for Phase 15.4.**  
Existing tables are structurally sufficient:
- `plans` (Migration 007): Stores authoritative TaskGraph state per Space with row-level locks.
- `plan_history` (Migration 007): Stores immutable historical plan versions.
- `execution_attempts` (Migration 006): Stores unique attempts with status and timestamps.
- `leases` (Migration 002): Stores active resource leases.
- `pulses` (Migrations 001, 008): Stores events with keyset pagination indexes.

---

## 23. Recommended Scheduler Architecture

```text
                     ┌─────────────────────────────────────────┐
                     │          SpaceOrchestrator              │
                     └────────────────────┬────────────────────┘
                                          │ Coordinates
                                          ▼
                     ┌─────────────────────────────────────────┐
                     │        ConcurrentDAGScheduler           │
                     │  (core/orchestrator/scheduler.py)       │
                     ├─────────────────────────────────────────┤
                     │ - Bounded Ready Queue                   │
                     │ - Multi-Space Fair-Share Allocator      │
                     │ - ThreadPoolExecutor (max_workers=16)   │
                     └─────────────┬───────────────────────────┘
                                   │ Spawns task workers
         ┌─────────────────────────┼─────────────────────────┐
         ▼                         ▼                         ▼
┌──────────────────┐      ┌──────────────────┐      ┌──────────────────┐
│  Worker Thread 1 │      │  Worker Thread 2 │      │  Worker Thread N │
├──────────────────┤      ├──────────────────┤      ├──────────────────┤
│ Deterministic    │      │ Deterministic    │      │ Deterministic    │
│ Dispatcher       │      │ Dispatcher       │      │ Dispatcher       │
│ Pipeline         │      │ Pipeline         │      │ Pipeline         │
└────────┬─────────┘      └────────┬─────────┘      └────────┬─────────┘
         │                         │                         │
         │ Optimistic CAS          │ Optimistic CAS          │ Optimistic CAS
         │ Rebase (max=3)          │ Rebase (max=3)          │ Rebase (max=3)
         ▼                         ▼                         ▼
┌──────────────────────────────────────────────────────────────────────┐
│                      SpaceKernel (Authority)                         │
│  - AdmissionController (Atomic Budget Pre-Reservation)               │
│  - ResourceManager (Fractional Leases & Contested Queues)             │
│  - PostgreSQL PlanStore (Row-Locked Transactional CAS)               │
└──────────────────────────────────┬───────────────────────────────────┘
                                   │ Invokes via Protocol
                                   ▼
┌──────────────────────────────────────────────────────────────────────┐
│                    RuntimeWorkerInvoker                              │
│  - PythonWorker / ShellWorker / ResearchWorker / TestRunnerWorker    │
│  - Space-Safe Artifact Isolation (<space_id>/<worker>/<task_id>_*)   │
└──────────────────────────────────────────────────────────────────────┘
```

### Architecture Key Answers
1. **What owns scheduling?** `ConcurrentDAGScheduler` in `core/orchestrator/scheduler.py`.
2. **What owns task state?** `SpaceKernel` via PostgreSQL `PlanStore` CAS.
3. **What owns resource leases?** `ResourceManager`.
4. **What owns queue/readiness state?** `ConcurrentDAGScheduler` queries `TaskGraph.get_ready_tasks()`.
5. **What executes workers?** Bounded `ThreadPoolExecutor` driving `RuntimeWorkerInvoker`.
6. **What persists attempts?** `ExecutionAttemptStore` in PostgreSQL.
7. **What wakes the scheduler?** Task completion events, lease availability callbacks, and periodic poll tick.
8. **What happens when multiple tasks are READY?** Added to bounded ready queue, dispatched up to Space concurrency quota.
9. **What happens when two tasks compete for one resource?** `ResourceManager` grants one, enqueues other in `ResourceQueue` (state `LEASE_PENDING`).
10. **What happens when the scheduler crashes?** `StartupRecoveryEngine` scans `execution_attempts`, sweeps leases, recovers tasks.
11. **What happens when a worker crashes?** Process exit detected, attempt marked failed, failure passed to `ConvergenceEngine`.
12. **How is duplicate dispatch prevented?** Unique `idempotency_key` checked in `execution_attempts` before spawn.
13. **How is bounded concurrency enforced?** Fixed-size thread pool and per-space task quotas.
14. **How is backpressure enforced?** Cap on ready queue size; dependency unblocking pauses when queue is full.
15. **How is Space isolation enforced?** Kernel rejects mismatched `space_id` on every CAS, lease, and artifact operation.
16. **How is deterministic replay preserved?** Committed `PlanDelta` sequence in `plan_history` is immutable.
17. **How are convergence races handled?** Convergence evaluations are serialized per Space.
18. **How are cancellation/timeout races handled?** CAS on `from_state='running'` ensures single terminal winner; loser released.

---

## 24. Concurrency Boundary

**Concurrency belongs strictly in `core/orchestrator/` as a coordination and execution harness.**

- **NOT in `SpaceKernel`:** `SpaceKernel` is a synchronous, deterministic state and authority engine. It must remain single-threaded per transaction via row-level CAS locks.
- **NOT in `workers/`:** Workers are execution units that run sandboxed tasks. They do not decide scheduling or DAG progression.
- **NOT in `channels/`:** Channels are client adapters.

---

## 25. Bounded Concurrency Model

### Configuration Limits
- `max_concurrent_workers`: **16** (daemon-wide maximum execution threads).
- `max_concurrent_tasks_per_space`: **4** (prevents any single Space from monopolizing the daemon).
- `max_queued_tasks_per_space`: **100** (prevents memory exhaustion on large DAGs).
- `max_cas_rebases`: **3** (ceiling on optimistic plan rebase attempts).
- `task_timeout_default_seconds`: **300.0** (5 minutes default task execution ceiling).

---

## 26. Failure and Recovery Model

### Target Failure Ladder
$$\text{Worker Error} \longrightarrow \text{Scheduler Thread} \longrightarrow \text{Dispatcher Evidence Verification} \longrightarrow \text{PlanReconciler} \longrightarrow \text{SpaceKernel CAS} \longrightarrow \text{Human Gate}$$

1. **Worker Error:** Process exits with non-zero code or uncaught exception.
2. **Scheduler Thread:** Catches exit, releases hardware lease immediately.
3. **Dispatcher:** Proposes task transition to `FAILED` with failure details.
4. **Convergence:** Serialized convergence check evaluates retry eligibility (up to 3 retries for transient errors).
5. **Replan / Escalate:** If retries exhausted or terminal error, proposes replan or escalates to human gate.

---

## 27. Deterministic Replay Model

- Replay executes sequentially from `plan_history` and PostgreSQL `pulses`.
- Replay does not re-run thread scheduling. It verifies that:
  1. Every task transition committed via a valid `PlanDelta`.
  2. Every delta satisfied CAS invariants.
  3. All artifacts match recorded SHA-256 digests.

---

## 28. Minimal Implementation Surface

| File / Module | Responsibility | Required Change | Contract / Spec |
|:---|:---|:---|:---|
| `core/orchestrator/scheduler.py` | New concurrent scheduler coordinator | Implement `ConcurrentDAGScheduler` with bounded worker pool | `SCHED-001`, `SCHED-003` |
| `core/orchestrator/dispatch_model.py` | Task pipeline execution | Add bounded CAS rebase loop to `dispatch_task` and completion transition | `SCHED-002`, `SCHED-005` |
| `core/capabilities/admission.py` | Capability admission | Add atomic budget pre-reservation and release methods | `SCHED-004` |
| `contracts/registry/` | Contracts definition | Add `SCHED-001` through `SCHED-005` schemas | Contract Governance |
| `harness/cases/scheduler/` | Verification test cases | Implement Unit, Integration, and Chaos test suites | Spec Map |

---

## 29. Proposed Implementation Sequence

- **Phase 15.4.0:** Contracts & Invariant Specification (`SCHED-001`..`005`, ADR-0048).
- **Phase 15.4.1:** Admission Budget Pre-reservation (`core/capabilities/admission.py`).
- **Phase 15.4.2:** Optimistic CAS Rebase Hardening in `DeterministicDispatcher`.
- **Phase 15.4.3:** Bounded Ready Queue & State Machine Verification.
- **Phase 15.4.4:** `ConcurrentDAGScheduler` Core Implementation (`core/orchestrator/scheduler.py`).
- **Phase 15.4.5:** Resource Contention & Lease Coordination Integration.
- **Phase 15.4.6:** Multi-Space Fair Queueing & Deficit Round-Robin.
- **Phase 15.4.7:** Concurrent Failure, Cancellation, and Timeout Handling.
- **Phase 15.4.8:** Crash Recovery Integration Verification.
- **Phase 15.4.9:** Vertical Slices Verification (Slices A through L).
- **Phase 15.4.10:** Documentation Hardening, Spec-Map, and Project Memory.

---

## 30. Vertical Slices

- **Slice A (Independent Tasks):** Tasks A, B, C with no dependencies run concurrently; verify all 3 complete in parallel.
- **Slice B (Diamond DAG):** $A \to (B, C) \to D$; verify B and C run concurrently after A completes; D waits for both.
- **Slice C (Resource Contention):** Tasks A and B require exclusive resource; verify one runs while other queues in `LEASE_PENDING`.
- **Slice D (Disjoint Resources):** Task A requires GPU, Task B requires CPU; verify both run concurrently.
- **Slice E (Concurrent Completion CAS Race):** B and C finish at identical times; verify optimistic rebase succeeds and both commit.
- **Slice F (Concurrent Failure / Retry):** B and C fail simultaneously; verify convergence engine processes both without double-counting retries.
- **Slice G (Scheduler Crash with Active Tasks):** Kill scheduler mid-run; reboot daemon; verify `StartupRecoveryEngine` recovers tasks.
- **Slice H (Worker Crash + Lease Cleanup):** Terminate worker subprocess via SIGKILL; verify lease is released immediately.
- **Slice I (Cross-Space Isolation):** Space 1 and Space 2 run concurrently; verify zero state or artifact leakage.
- **Slice J (Bounded Queue Backpressure):** DAG with 500 tasks; verify active queue never exceeds configured bounds.
- **Slice K (Cancellation Race):** Cancel Space while 4 tasks run; verify workers killed and terminal states cleanly set to CANCELLED.
- **Slice L (Deterministic Replay):** Capture pulse log of concurrent run; replay and verify exact plan sequence reconstructed.

---

## 31. Acceptance Criteria

- **SCHED-001:** Independent READY tasks execute concurrently within configured bounds.
- **SCHED-002:** Dependency ordering remains strictly respected across DAG branches.
- **SCHED-003:** Duplicate dispatch is prevented via unique idempotency keys.
- **SCHED-004:** Resource contention is handled authoritatively by `ResourceManager`.
- **SCHED-005:** Task state transitions remain CAS-safe under concurrent commits.
- **SCHED-006:** Concurrent completions cannot corrupt Plan state or lose updates.
- **SCHED-007:** Scheduler crash does not produce duplicate task executions on restart.
- **SCHED-008:** Worker crashes release held resource leases immediately.
- **SCHED-009:** Retry budgets cannot be double-consumed during parallel failures.
- **SCHED-010:** Cross-Space execution, leakage, or resource borrowing remains impossible.
- **SCHED-011:** Scheduler concurrency is strictly bounded (`max_concurrent_workers`).
- **SCHED-012:** Ready queue depth is bounded and provides backpressure.
- **SCHED-013:** Timeout and completion races resolve deterministically via CAS.
- **SCHED-014:** Cancellation races terminate worker processes and release leases safely.
- **SCHED-015:** Replay preserves deterministic control and plan history.
- **SCHED-016:** Zero Core Boundary Rule violations (`core/` imports 0 non-core modules).

---

## 32. Governance Impact

Prior to or during Phase 15.4 implementation, the following governance updates must occur:
1. **ADR-0048:** `adr/0048-concurrent-dag-scheduler.md` (Decision on Bounded Thread-Based Coordinator with Optimistic CAS Rebase).
2. **Contract Additions:** `contracts/registry/` update with `SCHED-001` through `SCHED-005`.
3. **Spec Map:** `harness/spec_map.yaml` updated to map `SCHED-001`..`016` to new harness test cases.
4. **Project Memory:** `PROJECT_MEMORY/0030-phase-15-4-concurrent-dag-scheduler.md` created upon completion.

---

## 33. Non-Goals

The following areas are strictly OUT OF SCOPE for Phase 15.4:
- **Finding F-05 (Semantic Memory / Experience Retrieval):** Retains deferred status.
- **Finding F-06 (Durable Convergence State):** Persisting retry counters across daemon reboots in PostgreSQL is deferred.
- **Finding F-07 (Agent Hierarchy Integration):** Worker hierarchies remain as defined in Phase 12.
- **Findings F-08 through F-13:** Retain deferred status.
- **Unbounded Parallelism / Ray / Celery Integration:** RYU maintains a deterministic, self-contained runtime.
- **Moving Authority to Workers or Schedulers:** SpaceKernel remains the sole authority.

---

## 34. Risks and Conditions

| Risk ID | Risk Description | Severity | Mitigation Condition |
|:---|:---|:---:|:---|
| **RSK-01** | CAS livelock under high parallelism | Medium | Exponential jittered backoff on rebase; max 3 rebases. |
| **RSK-02** | Budget overrun from parallel admission | High | Implement atomic budget pre-reservation in `AdmissionController`. |
| **RSK-03** | Git index lock collisions in RepositoryWorker | High | Enforce single-lease repository serialization or git worktrees. |
| **RSK-04** | Hardware lease leakage on failed task CAS | Critical | Enforce mandatory lease release in `finally` and on CAS failure. |
| **RSK-05** | Core Boundary Rule violation | High | Maintain `ConcurrentDAGScheduler` in `core/orchestrator/` using `WorkerInvokerProtocol`. |

---

## 35. Final Gate

### **PHASE 15.4 AUDIT — READY WITH CONDITIONS**

**Audit Completed:** Phase 15.4 Architecture Audit complete.  
**Mode Constraint Observed:** AUDIT ONLY — Zero implementation code written, zero tests modified, zero migrations executed.  
**Next Step:** Await explicit user authorization to proceed to Phase 15.4 Implementation.
