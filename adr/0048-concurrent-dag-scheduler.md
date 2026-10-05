# ADR-0048: Concurrent DAG Scheduler & Bounded Execution Engine

## Context & Problem Statement

In the RYU AI Framework (v1.0.0 through Phase 15.3), execution orchestration via `DeterministicDispatcher.execute_task_full_pipeline()` executes tasks sequentially in a blocking, single-threaded loop:
```text
execute_task_full_pipeline() -> invoker.invoke(task_req) -> blocks caller until worker completion
```
Even though `TaskGraph.get_ready_tasks()` can identify multiple concurrently ready tasks across independent DAG branches (e.g., $A \to B$ and $A \to C$), they are dispatched sequentially in caller loops. Attempting to execute tasks concurrently against `SpaceKernel` causes Compare-And-Swap (CAS) conflicts on `plan_version`, since each lifecycle transition commits a `PlanDelta`. Previously, any CAS conflict caused immediate task abort (`cas_failed`) rather than optimistic rebasing. Furthermore, `AdmissionController` checked budget without atomically reserving it, creating race conditions under parallel admissions.

This architecture audit is documented in `docs/PHASE_15_4_ARCHITECTURE_AUDIT.md` (Finding F-04, P1).

## Decision

We introduce a bounded, deterministic, multi-space concurrent execution engine centered in `core/orchestrator/scheduler.py` while strictly preserving all SCCA laws, authority boundaries, and deterministic replay guarantees:

1. **Constitutional Subordination & Core Boundary Rule:**
   - The scheduler is strictly an orchestration coordinator residing in `core/orchestrator/scheduler.py`.
   - It is **NOT** a new authority layer. It does not own Space state, Plan state, Budget, Resources, or Worker execution.
   - It subordinates to `SpaceKernel` (authoritative Plan CAS), `AdmissionController` (capability & budget authority), `ResourceManager` (sole lease authority), and `DeterministicDispatcher` (execution pipeline).
   - Core boundary rule is preserved: `scheduler.py` imports strictly within `core/` and interacts with workers via `WorkerInvokerProtocol`.

2. **Bounded Execution Model:**
   - Concurrency is bounded by configuration (`SchedulerConfig`):
     - `max_concurrent_workers`: Daemon-wide worker thread pool limit (default: 16).
     - `per_space_concurrency`: Maximum active tasks per Space (default: 4).
     - `ready_queue_capacity`: Maximum candidate tasks enqueued per Space (default: 100).
     - `max_cas_rebases`: Maximum optimistic CAS rebases per transition (default: 3).
     - `default_task_timeout`: Execution timeout ceiling (default: 300.0s).
   - Unlimited threads, unconstrained queues, or process-wide unbounded concurrency are prohibited.

3. **Atomic Budget Pre-Reservation (`SCHED-004`):**
   - `AdmissionController` atomically pre-reserves requested budget at admission time (`_budgets[space_id] -= req_budget`).
   - If resource leasing or Plan CAS fails post-admission, the pre-reserved budget is released back (`release_reservation`).
   - Upon execution completion, spend is reconciled (`reconcile_reservation`). This prevents budget overruns under concurrent admission.

4. **Optimistic Plan CAS Rebase & Lease Rollback (`SCHED-002`):**
   - Task transitions reload fresh `TaskGraph` state upon CAS conflict and recompute the transition if the task's source state remains valid, up to `max_cas_rebases = 3`.
   - A CAS conflict never re-executes the worker.
   - If a lease was acquired and CAS rebasing fails, the lease is immediately released via `ResourceManager.release()`.

5. **Multi-Space Fairness & Starvation Prevention (`SCHED-003`):**
   - Active Spaces are scheduled using fair-share round-robin allocation bounded by `per_space_concurrency`.
   - A single Space with many tasks cannot monopolize the global worker pool.

6. **Durable Idempotency & Duplicate Dispatch Protection (`SCHED-001`):**
   - Before worker dispatch, an execution attempt is registered in `ExecutionAttemptStore` keyed by unique `idempotency_key`.
   - Competing scheduler threads attempting the same task observe the existing attempt and abort.

7. **Terminal Transition Exclusivity (`SCHED-005`):**
   - Exactly one terminal transition (`COMPLETED`, `FAILED`, `TIMED_OUT`, `CANCELLED`) can win via CAS.
   - Late worker outputs arriving after timeout or cancellation are discarded and leases released.

8. **Deterministic Replay Invariant:**
   - Replay is verified from committed `PlanDelta` history and pulse streams, not wall-clock thread timing.
   - Deterministic candidate ordering uses `(-priority, topological_depth, task_id)`.

## Consequences

### Positive
- Independent DAG branches execute concurrently, eliminating the single-task execution bottleneck.
- Hardware resources (CPUs, GPUs) are utilized efficiently within fractional leases.
- Zero budget overruns under concurrent admission.
- Zero orphaned hardware leases on transition failures.
- Robust to crashes, timeouts, and cancellations.

### Trade-offs & Mitigations
- In-memory thread pools contend on PostgreSQL row locks during high-frequency Plan CAS commits; mitigated by row locks per Space, bounded rebasing, and localized delta ops.
- Shared git working trees require serialization or isolated worktrees; enforced via exclusive repository leases.
