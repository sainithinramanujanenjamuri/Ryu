# ADR-0050: Production Semantic Loop Consolidation & Database Hardening

## Status
Accepted

## Context & Problem Statement

Following the completion of Phase 15.5 and the formal closure of Finding F-05 (*Semantic Memory & Experience Retrieval*), a comprehensive systems and security audit (`docs/POST_F05_ARCHITECTURE_AUDIT.md`) identified six P1 production bottlenecks that prevent safe, long-running, multi-space autonomous execution:

1. **Disconnected Reflection-to-Embedding Pipeline (F05-AUDIT-01):** `Reflector.reflect()` stores newly created `ExperienceRecord` instances with `embedding=None`. There is no runtime ingestion pipeline or durable outbox to generate vector embeddings asynchronously. As a consequence, in live production execution, newly stored reflections are filtered out by `is_embedding_compatible()`, leaving semantic retrieval starved.
2. **Blocking Context-Manager Exit in Adaptation Timeout (F05-AUDIT-02):** In `AdaptationLayer.generate_hints()`, the nominal 500ms timeout wraps retrieval in `with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:`. In Python, `ThreadPoolExecutor.__exit__` unconditionally invokes `self.shutdown(wait=True)`. If a database query or model call hangs or runs long, the calling thread blocks until the worker thread terminates, defeating the 500ms caller SLA and risking orchestration starvation.
3. **Unbounded Experience Growth & Lack of Lifecycle Policies (F05-AUDIT-03):** `SpaceMemoryProtocol` contains zero primitives for time-to-live (TTL), maximum record counts, compaction, or pruning. Space memory accumulates monotonically forever, degrading storage efficiency and candidate scan performance over long runtimes.
4. **PostgreSQL Connection Churn & Unindexed Candidate Queries (F05-AUDIT-04):** `PostgreSQLMemoryAdapter` creates a new raw `psycopg2.connect()` TCP connection per query without pooling. Multi-prong candidate selection queries unindexed JSONB keys (`action->>'capability'`, `applicable_context->>'error_class'`), performing sequential table scans at scale.
5. **PlanDelta Rollback Payload Mismatch & Task State Immobility (F05-AUDIT-05):** In `ConvergenceEngine._propose_replan()`, advisory hint parameters are placed in `{"op": "rollback", "target_node_id": task_id, "payload": payload_data}`, whereas `PlanStore` applies `op_payload.get("params", {})`. The rollback parameters are discarded as `{}` and the failed node remains immutably in `TaskState.FAILED`.
6. **Strategy Oscillation Blind Spot in Convergence Engine (F05-AUDIT-06):** Replan infinite-loop protection currently checks only exact string equality on `failure_fingerprint`. Cycling between two alternating capabilities (e.g. $A \rightarrow B \rightarrow A \rightarrow B$) with slight error representation variations bypasses the loop guard and exhausts replan budgets ungracefully.

This ADR defines the authoritative architecture, boundaries, and governance for **Phase 15.6**, consolidating the semantic memory loop into a production-safe closed loop without altering existing constitutional authority or SCCA laws.

---

## Decision

We establish the architectural specifications, component boundaries, and operational invariants for Phase 15.6:

### 1. PlanDelta Rollback Reconciliation & State Reset (F05-AUDIT-05)
- **Canonical Payload Structure:** Reconcile `PlanDelta` rollback operations so that `payload` or `params` metadata containing alternative capabilities, counterfactual recommendations, and updated task parameters are deterministically merged into the target node's `params`.
- **Node State Machine Reconfiguration:** When a `rollback` op is committed via SpaceKernel CAS on a node currently in `TaskState.FAILED`, the node is deterministically transitioned to `TaskState.READY` (or `TaskState.PENDING` if dependencies are unsatisfied), adhering strictly to `LEGAL_TRANSITIONS`.
- **CAS Authority Preservation:** The rollback delta is committed strictly via single-writer `SpaceKernel CAS` (`ORCH-003`, `PLAN-001`). Memory hints provide advisory recommendations; the Kernel remains the sole state authority.

### 2. Bounded Strategy Oscillation Detection (F05-AUDIT-06)
- **Deterministic Strategy Sequence Tracking:** `ConvergenceEngine` shall maintain a Space-scoped, bounded chronological history of proposed strategies and capabilities per task/goal scope.
- **Cycle Detection:** If an alternating cycle (such as $A \rightarrow B \rightarrow A$ or $A \rightarrow B \rightarrow A \rightarrow B$) is detected within a configurable window (default threshold: 2 repeated cycles or 3 strategy reversals), the engine shall immediately propose `ConvergenceDecision.ESCALATE` to a human operator.
- **Zero Probabilistic / ML Logic:** Strategy oscillation detection uses pure, deterministic sequence matching over explicit bounded state. No hidden global state, machine learning, or heuristics are permitted.

### 3. Non-Blocking Adaptation Timeout & Managed Executor Lifecycle (F05-AUDIT-02)
- **Managed Executor Pool:** Replace ephemeral `with ThreadPoolExecutor(...)` context managers in `AdaptationLayer` with a managed, reusable, bounded executor pool (or explicit `shutdown(wait=False, cancel_futures=True)` discipline).
- **Strict 500ms Caller SLA:** If retrieval exceeds `timeout_seconds`, the caller receives an immediate `TimeoutError`, cancels the pending future, and degrades gracefully to metadata fallback without blocking on background worker completion.
- **Resource Containment:** Thread counts remain strictly bounded; timed-out background queries complete or discard their results without leaking connections, memory, or CPU.

### 4. PostgreSQL Runtime Hardening & Candidate Expression Indexes (F05-AUDIT-04)
- **Bounded Connection Pooling:** `PostgreSQLMemoryAdapter` shall utilize a thread-safe connection pool (`ThreadedConnectionPool`) with configurable min/max connections, deterministic acquisition, and guaranteed release in context managers.
- **Migration 010 (Candidate Expression Indexes):** Add functional/expression indexes on `space_experiences` for candidate extraction keys:
  - `(space_id, (action->>'capability'))`
  - `(space_id, (applicable_context->>'error_class'))`
- **Strict Query Bounds:** Candidate generation remains strictly capped at $C_{max} \le 50$; final ranked hints remain capped at $K_{max} \le 5$.

### 5. Durable Production Embedding Ingestion (F05-AUDIT-01)
- **Durable Outbox / Ingestion Schema:** Extend experience storage with durable embedding job tracking (`embedding_status: 'pending' | 'completed' | 'failed'`, `embedding_attempts: int`, `embedding_error: text`).
- **Asynchronous Ingestion Pipeline:** An `EmbeddingIngestionPipeline` claims pending records in bounded batches, generates normalized embeddings via `EmbeddingProviderProtocol`, validates numerical finiteness and dimensions, updates the record atomically, and marks the job complete.
- **Crash Recovery & Idempotency:** If the process dies during embedding, the job remains in `pending` status and is reclaimed upon recovery. Retries are strictly bounded (max 3 attempts).
- **Graceful Retrieval Fallback:** While an experience is pending embedding, it remains retrievable via metadata/lexical fallback. Semantic retrieval continues to operate safely over already-embedded records.

### 6. Operational Memory Lifecycle & Retention (F05-AUDIT-03)
- **Operational Policy (Not an SCCA Law):** Retention is an operational policy managed by `SpaceMemoryProtocol` adapters.
- **Space-Scoped Ceilings:** Configurable maximum experience count per Space (default: 1,000) and optional retention TTL.
- **Deterministic Evidence-Preserving Victim Selection:** When pruning is triggered, victim selection prioritizes:
  1. Failed un-embeddable or malformed records.
  2. Redundant duplicate experiences with identical fingerprints and outcomes.
  3. Oldest non-unique experiences exceeding TTL.
  - Validated working strategies and critical unique failure fingerprints are preserved.
- **Plan Safety & Isolation:** Memory pruning MUST NOT mutate plans, delete cross-space records, or compromise historical audit logs.

### 7. Security & Prompt Injection Defense
- **Untrusted Output as DATA:** Error messages and stderr from task executions are treated strictly as untrusted data. Control characters, markdown instruction delimiters, and prompt escape sequences are sanitized before inclusion in `counterfactual` and `reasoning_msg`.
- **Zero Advisory Execution Authority:** Semantic hints remain purely advisory. No hint can execute a tool, bypass Admission Control, or override terminal failure classifications.

### 8. Replay Trace Integrity
- **Replay Disconnection:** Replay mode continues to bypass live mutable memory (`replay_mode=True`).
- **Proposal Reconstruction:** Proposal traces record sufficient historical evidence so that replay does not depend on mutable memory states.

---

## Contract Mapping

Phase 15.6 maps existing contracts and establishes dedicated operational contracts:

| Contract ID | Canonical Name | Phase | Implementation Boundary |
| :--- | :--- | :--- | :--- |
| **`MEM-SEM-001`** | Bounded Space-Scoped Candidate Retrieval | Phase 15.5 / 15.6 | `core/space/memory_protocol.py`, `memory/adapters/` |
| **`MEM-SEM-002`** | Deterministic Semantic Similarity Ranking | Phase 15.5 / 15.6 | `memory/retrieval/ranker.py` |
| **`MEM-SEM-003`** | Decoupled Embedding Boundary | Phase 15.5 / 15.6 | `core/space/memory_protocol.py`, `memory/embeddings/` |
| **`MEM-SEM-004`** | Graceful Semantic Retrieval Degradation | Phase 15.5 / 15.6 | `core/memory/adaptation.py`, non-blocking timeout |
| **`MEM-SEM-005`** | Bounded Advisory Experience Hints | Phase 15.5 / 15.6 | `core/memory/adaptation.py`, `core/orchestrator/` |
| **`PLAN-ROLLBACK-001`** | Deterministic Task Rollback & Parameter Reconciliation | Phase 15.6 | `core/orchestrator/dispatch_model.py`, `core/plans/plan_store.py` |
| **`CONV-OSC-001`** | Bounded Capability Strategy Oscillation Detection | Phase 15.6 | `core/orchestrator/dispatch_model.py` |
| **`MEM-PG-001`** | PostgreSQL Connection Pooling & Candidate Indexing | Phase 15.6 | `memory/adapters/postgres.py`, migration 010 |
| **`MEM-INGEST-001`** | Durable Experience Embedding Ingestion & Outbox | Phase 15.6 | `memory/ingestion/`, `memory/adapters/` |
| **`MEM-RETAIN-001`** | Bounded Space Experience Retention & Pruning | Phase 15.6 | `core/space/memory_protocol.py`, `memory/adapters/` |

---

## Non-Goals

The following are strictly out of scope for Phase 15.6:
1. Unrestricted AGI, autonomous self-modification, or memory becoming plan authority.
2. Direct plan mutations by memory or workers.
3. External vector database clusters (Qdrant, Pinecone, Milvus).
4. `pgvector` introduction (deferred to future scale phases).
5. Moving concrete memory implementations into `core/`.
6. Weakening single-writer Plan CAS or kernel authority.
7. Probabilistic or machine-learning-based oscillation detection.
8. Modifying SCCA laws or creating an SCCA Law 7.

---

## Consequences

### Positive
- Closes the production reflection-to-embedding loop: newly captured experiences are asynchronously and durably embedded.
- Hardens the 500ms adaptation SLA: callers never block on background worker thread pool shutdown.
- Prevents database connection exhaustion: connection pooling and expression indexes ensure scalable candidate retrieval.
- Aligns PlanDelta rollback execution: rollback parameters properly update node attributes and reset failed tasks to eligible states.
- Prevents strategy oscillation: cycling between complementary capabilities escalates deterministically to a human operator.
- Prevents unbounded storage growth: space-level retention ceilings protect performance without sacrificing critical learning evidence.

### Trade-offs & Mitigations
- *Trade-off:* Asynchronous embedding creates a brief latency window between experience creation and vector availability.
- *Mitigation:* While pending embedding, experiences remain immediately retrievable via structured metadata and lexical fallback queries.
- *Trade-off:* Pruning older experiences removes historical data from mature spaces.
- *Mitigation:* Victim selection is evidence-aware, preserving unique failure fingerprints, verified working strategies, and high-utility counterfactuals.

---

## Date
2026-10-08

