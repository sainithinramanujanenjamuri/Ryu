# ADR-0045 — Durable PostgreSQL Plan Store and Cold-Boot Reconstruction

**Status:** Accepted  
**Date:** 2026-10-03  
**Phase:** 15.1 — Durable PostgreSQL Plan Store + Cold-Boot Reconstruction  
**Supersedes:** (none — builds upon ADR-0003, ADR-0041, ADR-0042)  
**See also:** ADR-0002 (Transport vs Record), ADR-0003 (Plan CAS Versioning), ADR-0041 (Dispatcher & Plan Convergence), ADR-0042 (Crash Recovery & Durable Execution State), ADR-0044 (Autonomous SE Runtime)

---

## 1. Context / Problem

The Phase 15 Architectural Audit (`docs/PHASE_15_ARCHITECTURE_AUDIT.md`) identified a critical durability gap in the RYU runtime, classified as **Finding F-01 (Recovery Cliff)**.

In Phase 12.8 (ADR-0042), durable execution tracking was introduced via the PostgreSQL `execution_attempts` and `convergence_state` tables. This allowed worker invocations, retry budgets, and replan budgets to survive process restarts. However, the governing `PlanStore` (`core/plans/plan_store.py`) remained an **in-memory** implementation storing `TaskGraph` instances in a Python dictionary (`self._graphs[space_id]`) protected only by an in-process threading lock (`threading.Lock`).

### The Recovery Cliff Failure Flow

```text
SpaceKernel
    ↓
PlanStore
    ↓
In-memory _graphs dict
    ↓
Process restart / crash
    ↓
PlanStore empty
    ↓
StartupRecoveryEngine cannot reconstruct TaskGraph
    ↓
Durable execution attempts exist in PostgreSQL
BUT their governing Plan / TaskGraph does not
    ↓
RECOVERY CLIFF (reconciliation aborted)
```

Specific vulnerabilities identified:
1. **Recovery Cliff:** When the RYU daemon crashed or was rebooted, `StartupRecoveryEngine` queried PostgreSQL for interrupted execution attempts (e.g. status = `running`), but the newly instantiated `SpaceKernel` had an empty or default uninitialized plan (version 1 with zero tasks). When recovery attempted to mark the interrupted task as failed, the transition failed because `task_id` did not exist in the graph.
2. **Process Concurrency Vulnerability:** Threading locks provided zero protection across separate OS worker processes or multiple daemon replicas, allowing concurrent CAS transitions to race and corrupt plan versions.
3. **No Durable Plan History:** Historical plan snapshots (`_history`) were ephemeral in-memory lists, losing the complete plan mutation timeline upon restart.
4. **Silent Degradation Risk:** Any recovery design that falls back to an empty in-memory plan upon database failure violates SCCA Law 6 (failures must be contained and never silent).

---

## 2. Decision

We establish PostgreSQL as the sole authoritative source of truth for Plans, TaskGraphs, and Plan History, implementing full cold-boot reconstruction capabilities.

### 2.1 Authoritative PostgreSQL Schema

We introduce two dedicated tables (`deploy/migrations/007_create_plans_and_plan_history_tables.sql`):

```sql
CREATE TABLE IF NOT EXISTS plans (
    space_id         VARCHAR(255) PRIMARY KEY,
    plan_version     INTEGER      NOT NULL,
    graph_json       JSONB        NOT NULL,
    status           VARCHAR(50)  NOT NULL DEFAULT 'active',
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS plan_history (
    history_id       BIGSERIAL    PRIMARY KEY,
    space_id         VARCHAR(255) NOT NULL,
    plan_version     INTEGER      NOT NULL,
    graph_json       JSONB        NOT NULL,
    delta_json       JSONB,
    recorded_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_plan_history_space_version UNIQUE (space_id, plan_version)
);

CREATE INDEX IF NOT EXISTS idx_plans_status ON plans (status);
CREATE INDEX IF NOT EXISTS idx_plan_history_space ON plan_history (space_id, plan_version);
```

### 2.2 Strict Single-Writer Atomic CAS via Row-Level Locking

`PostgresPlanStore.commit_delta()` and `commit_graph()` execute within atomic PostgreSQL transactions using `SELECT ... FOR UPDATE` on the target space row:
1. The transaction acquires an exclusive row-level lock on `plans WHERE space_id = %s`.
2. The current stored `plan_version` is validated against `delta.base_version`.
3. If versions do not match (`stored_version != delta.base_version`), the transaction aborts with `CASConflictError`, preventing lost updates across threads and OS processes.
4. If versions match, the delta ops are applied to the reconstructed graph.
5. The updated graph is validated and serialized to canonical JSON.
6. The `plans` table is updated with `plan_version = resulting_version`, `graph_json = serialized_json`, and `updated_at = NOW()`.
7. An immutable snapshot is appended to `plan_history`.
8. The transaction commits atomically. Any unexpected exception triggers a full rollback, leaving no partial state.

### 2.3 Deterministic TaskGraph Serialization (`core/plans/serialization.py`)

To eliminate cross-process differences, `pickle` is strictly forbidden. A deterministic canonical JSON serialization format is established:
- Node dictionary keys are sorted alphabetically.
- All required fields (`id`, `capability`, `state`, `dependencies`, `params`, `optional`, `retry_count`, `error`) are strictly validated.
- Node dependencies are checked for referential integrity (no references to non-existent nodes).
- Directed acyclic graph (DAG) structure is verified with cycle detection before serialization and after deserialization.
- Any malformed or invalid JSON fails closed with `DeserializationError`.

### 2.4 Formal `PlanStoreProtocol` and Store Separation

`core/plans/plan_store.py` defines a `@runtime_checkable` protocol:
- `PlanStoreProtocol`: defines `commit_delta`, `commit_graph`, `get_plan_version`, `get_task_graph`, `get_history`, `init_space_plan`, `list_active_spaces`, `load_all_plans`, and `restore_graph`.
- `PostgresPlanStore`: production-grade, authoritative PostgreSQL implementation.
- `InMemoryPlanStore` (aliased to `PlanStore`): lightweight, zero-dependency in-memory implementation retained exclusively for fast, isolated unit tests.

### 2.5 SpaceKernel Integration & Cold-Boot Recovery

`SpaceKernel` (`core/space/kernel.py`) is updated to accept `plan_store: PlanStoreProtocol | None = None`:
- When provided, `SpaceKernel` binds to the provided plan store.
- On kernel instantiation, if the space already exists in the store, `SpaceKernel` automatically loads the reconstructed `TaskGraph` from PostgreSQL.
- `SpaceKernel.create_checkpoint()` and `restore_checkpoint()` delegate directly to `plan_store.restore_graph()`.
- `SpaceKernel.propose_task_transition()` supports single-task recovery updates against the authoritative PostgreSQL store.
- `StartupRecoveryEngine` (`core/orchestrator/startup_recovery.py`) queries `PostgresPlanStore.load_all_plans()`, instantiates cold-boot kernels for each active space, and recovers interrupted worker tasks (`running` → `failed` with error classification `transient.worker_crash`).

---

## 3. Consequences

### Positive Consequences
- **Recovery Cliff Eliminated:** Cold-boot restarts seamlessly reconstruct complete TaskGraphs across all active spaces, allowing `StartupRecoveryEngine` to reconcile interrupted execution attempts and restore operational baseline.
- **Multi-Process Concurrency Safety:** PostgreSQL `SELECT ... FOR UPDATE` prevents concurrent CAS race conditions across separate processes.
- **Monotonic Audit Trail:** `plan_history` provides an immutable, append-only log of every plan version transition in production.
- **Fail-Closed Durability:** Malformed graph states or database disconnections fail closed deterministically without synthetic graph invention or silent in-memory fallback.
- **Core Boundary Compliance:** Pure SQL via `psycopg2` inside `core/plans/` preserves strict Core Independence; no imports from `agents/`, `workers/`, `skills/`, `workflows/`, or `memory/`.

### Negative / Operational Consequences
- **Database Dependency:** Production runtime startup requires a reachable, healthy PostgreSQL instance. Tests verifying durable plan recovery require live PostgreSQL (or WSL2 PostgreSQL in development environments).
- **Serialization Overhead:** Serializing and deserializing JSONB TaskGraphs on each CAS transition incurs minor CPU overhead, bounded by maximum DAG size constraints.

---

## 4. Governing Invariants

- **INV-PLAN-01:** PostgreSQL `plans` and `plan_history` are the sole authoritative production record for plans. In-memory state is an ephemeral cache.
- **INV-PLAN-02:** All plan updates require single-writer atomic CAS validation (`base_version == stored_version`).
- **INV-PLAN-03:** Serialization must be deterministic and fail closed on corrupt JSON, missing nodes, or cyclic dependencies.
- **INV-PLAN-04:** Every mutation and query is strictly space-isolated via `space_id`.
- **INV-PLAN-05:** Process restart followed by `StartupRecoveryEngine` execution must successfully reconstruct graphs and reconcile interrupted tasks without manual intervention.
