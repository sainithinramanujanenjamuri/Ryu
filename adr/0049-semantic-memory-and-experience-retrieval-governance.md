# ADR-0049: Semantic Memory & Experience Retrieval Governance

## Status
Accepted

## Context & Problem Statement

In the RYU AI Framework (v1.0.0 through Phase 15.4), long-term experience memory is established via `SpaceMemoryProtocol`, `Reflector`, and `AdaptationLayer` (ADR-0033, ADR-0034, ADR-0036, ADR-0043). In Phase 13, the closed-loop observation pipeline was connected: `DeterministicDispatcher` reports `TaskExecutionOutcome` to `ExecutionExperienceObserver`, which sanitizes secrets and persists structured `ExperienceRecord` entries containing mandatory counterfactuals.

However, as formally audited in `docs/PHASE_15_5_ARCHITECTURE_AUDIT.md` (Finding F-05, Priority: P1), existing retrieval mechanisms lack semantic ranking:
1. **Recency-Truncated Scanning in PostgreSQL:** `PostgreSQLMemoryAdapter.query_similar_experiences()` queries:
   ```sql
   SELECT ... FROM space_experiences WHERE space_id = %s ORDER BY stored_at DESC LIMIT %s;
   ```
   followed by naive in-memory substring matching in Python on that recent slice. If a highly relevant historical failure, repair, or strategy occurred in an earlier task and $K$ unrelated tasks have executed since, the historical record is **completely excluded and unretrievable**.
2. **Unindexed O(N) Scanning in In-Memory Store:** `InMemoryMemoryAdapter.query_similar_experiences()` scans all records in the Space in Python memory and evaluates substring presence of hint terms against concatenated text fields.
3. **Unbounded Enumeration Path:** `list_experiences(space_id)` contains no limit or pagination parameters, posing an unbounded memory growth risk on mature Spaces.
4. **Distinction of Capabilities:**
   - *Existing Capability:* Space-scoped experience persistence, secret sanitization, mandatory counterfactual schemas (`MEM-002`), reflection events (`experience.stored`), advisory hint data structures (`ExperienceHint`), and human-gated cross-space promotion (`MEM-005`, `MEM-006`).
   - *Intended Capability (Phase 15.5):* Bounded Space-scoped candidate generation ($C_{max} \le 50$), deterministic semantic vector similarity ranking ($K_{max} \le 5$), exact failure fingerprint match prioritization, decoupled embedding protocol boundary, and graceful degradation paths.
   - *Deferred Capability:* Durable convergence attempt tables (Finding F-06), dynamic agent delegation (Finding F-07), and external vector database clusters (Qdrant/Neo4j stubs remain Phase 11+ extension boundaries).

Semantic retrieval does not yet exist in the repository; this ADR defines its authoritative governance foundation.

---

## Decision

We establish the governance, contract, and architectural boundary for Finding F-05 (Semantic Memory & Experience Retrieval):

### 1. Authority Hierarchy & Advisory Invariant (SCCA Law 2 & Law 5)
The constitutional authority hierarchy is strictly preserved:
```text
Human Goal
    ↓
SpaceKernel
    ↓
Plan / TaskGraph
    ↓
ConvergenceEngine
    ↓
DeterministicDispatcher / Scheduler
    ↓
AdmissionController / ResourceManager
    ↓
Workers
    ↓
Evidence
    ↓
Memory / Experience
```
- **Memory is Strictly Advisory:** An `ExperienceHint` **CANNOT** directly mutate `TaskGraph`, commit a `Plan`, allocate resources, create leases, grant capabilities, bypass Admission Control, or override execution evidence.
- **Convergence Flow:** Memory hints may only inform an advisory `ConvergenceProposal`. The proposal must be explicitly validated and committed via `SpaceKernel CAS` (`ORCH-003`, `ADAPT-003`).

### 2. Core Boundary & Protocol Ownership (AGENTS.md §7)
- **Protocol in Core:** `core/space/memory_protocol.py` defines the abstract `EmbeddingProviderProtocol` using standard library types only.
- **Injected Implementations Outside Core:** All concrete embedding providers reside in `memory/embeddings/` (e.g. `DeterministicMockEmbeddingProvider` for hermetic testing; optional `OllamaEmbeddingProvider` for local HTTP).
- **Forbidden Core Dependencies:** `core/` **MUST NOT** import `llm/`, Ollama SDKs or HTTP clients, PyTorch, Sentence-Transformers, NumPy, or third-party ML frameworks. `scripts/dep_guard.py` enforces this boundary.

### 3. Bounded Multi-Prong Candidate Generation ($C_{max} \le 50$)
Candidate generation MUST occur prior to semantic scoring to enforce a genuine resource bound ($C_{max} \le 50$):
- Semantic retrieval MUST NOT load an unbounded Space history into memory.
- Candidates are drawn from three bounded, Space-scoped sources:
  1. *Prong 1 (Exact Failure Fingerprint Matches):* Bounded query for experiences with matching `failure_fingerprint` (priority slot).
  2. *Prong 2 (Domain Metadata Matches):* Bounded query matching `capability` or `error_class`.
  3. *Prong 3 (Recent Space Window):* Bounded query capturing recent execution recency.
- The candidate set is merged, deduplicated by `experience_id`, and clamped to $C \le 50$.
- If native vector indexing (e.g. `pgvector`) is active, approximate nearest-neighbor search within `space_id` may serve as an additional candidate source within the overall $C \le 50$ envelope.

### 4. Deterministic Semantic Ranking & Result Ceiling ($K_{max} \le 5$)
- Given identical candidate inputs, embeddings, and configuration, semantic ranking MUST produce a deterministically ordered result.
- Result set is strictly bounded by $K_{max} \le 5$.
- Exact failure-fingerprint matches receive deterministic priority allocation over pure semantic similarity.
- Tie-breaking is deterministic: sorted by quantized similarity (`round(score, 4)`), descending `stored_at`, and ascending `experience_id`.

### 5. Bounded Experience Enumeration (`list_experiences`)
- The existing unbounded `list_experiences(space_id)` path must be hardened with bounded pagination parameters (`limit: int = 50`, `before_stored_at: datetime | None = None`) and a hard ceiling of 100 under contract `MEM-SEM-001`.
- Semantic retrieval is decoupled from `list_experiences()` and MUST NOT call it.

### 6. Graceful Degradation & Failure Containment (SCCA Law 6)
- Embedding provider unavailability, timeout, or vector dimension mismatch MUST NOT halt task execution, cancel leases, or corrupt plan CAS state.
- System degrades gracefully to metadata/lexical fallback or returns zero hints (`CONTINUE_WITHOUT_MEMORY`).
- Memory failure is an informational loss, never an execution authority escalation.

### 7. Replay Compatibility
- Live semantic retrieval MUST NOT become a hidden dependency of deterministic replay.
- During execution replay (`self.replay_mode = True`), the engine does NOT re-query live memory. Replay relies strictly on recorded execution evidence and historical proposal hints.

### 8. Storage Model & Operational Defaults
- PostgreSQL remains the authoritative durable storage layer.
- `InMemoryMemoryAdapter` provides hermetic, deterministic exact similarity for unit tests and environments where database services are offline.
- Storing vectors in JSONB provides raw float persistence; it is NOT equivalent to an indexed vector search.
- 1,000 experiences per Space is an operational implementation default, not an SCCA invariant.

---

## Non-Goals

The following are strictly out of scope for Phase 15.5:
1. Unrestricted AGI, autonomous self-modification, or memory becoming plan authority.
2. Global un-promoted memory sharing across Spaces.
3. Mandatory external vector database clusters (Qdrant, Neo4j).
4. Mandatory pgvector extension installation.
5. Online neural weight fine-tuning or model parameter updates.
6. New pulse types (no pulse additions in Phase 15.5.0).
7. Finding F-06 (Durable Convergence State) and Finding F-07 (Dynamic Agent Hierarchy).

---

## Consequences

### Positive
- Prevents recency-masking: highly relevant historical failure counterfactuals and successful strategies become retrievable.
- Enforces strict resource bounds: at most 50 candidates evaluated and at most 5 hints returned per query.
- Preserves Core Boundary Rule: core depends only on standard library protocol abstractions.
- Eliminates unbounded scan technical debt in `list_experiences()`.
- Guarantees deterministic replay by decoupling historical replay from live memory indices.

### Trade-offs & Mitigations
- *Trade-off:* Without native `pgvector`, candidate generation relies on metadata/recency pre-filtering before in-memory dot product scoring.
- *Mitigation:* The multi-prong candidate strategy guarantees exact fingerprint and metadata matches are always represented in the top 50 candidates, maintaining high relevance even without hardware-accelerated vector indexing.

---

## Date
2026-10-05
