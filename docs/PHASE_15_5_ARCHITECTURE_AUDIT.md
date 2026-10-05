# PHASE 15.5 ARCHITECTURE AUDIT: SEMANTIC MEMORY & EXPERIENCE RETRIEVAL
**Finding:** F-05 — Semantic Memory & Experience Retrieval  
**Priority:** P1  
**Baseline Commit:** `3c8ed391a4a4900a6c94dcbbf598eb37c7b10eb4`  
**Governing ADRs:** ADR-0033, ADR-0034, ADR-0035, ADR-0036, ADR-0043, ADR-0044, ADR-0048  
**Mode:** ARCHITECTURE AUDIT ONLY (Zero Code / Zero Migrations / Zero Contracts / Zero Remote Push)  
**Document Version:** 1.1.0 (Hardened Pre-Implementation Audit)  
**Date:** 2026-10-05  

---

## 1. EXECUTIVE VERDICT

### Architectural Verdict
**`READY WITH CONDITIONS`**

### Summary of Findings
1. **Semantic Retrieval is Completely Missing in HEAD:** While Phase 10 established the long-term memory protocol boundary (`SpaceMemoryProtocol`, `Reflector`, `PromotionPipeline`) and Phase 13 closed the execution observation loop (`ADAPT-001..005`), **zero semantic similarity retrieval currently exists in RYU AI**.
2. **Current Retrieval is Recency-Truncated + In-Memory Substring Filtering:**
   - In `PostgreSQLMemoryAdapter.query_similar_experiences()`: Queries `SELECT ... FROM space_experiences WHERE space_id = %s ORDER BY stored_at DESC LIMIT %s`, followed by naive in-memory substring matching in Python on those few rows. If a highly relevant historical failure/repair occurred 10 tasks ago, but 5 unrelated tasks executed subsequently, the historical record is **completely invisible and unretrievable**.
   - In `InMemoryMemoryAdapter.query_similar_experiences()`: Performs an unindexed $O(N)$ scan of all records in the Space in Python memory, performing substring matching on `situation_hint` dictionary values against concatenated string fields.
3. **Existing `list_experiences()` Path is Unbounded:** `list_experiences(space_id)` contains no `LIMIT` or keyset pagination parameter in either adapter, representing an unmitigated memory exhaustion vulnerability for mature Spaces. Semantic retrieval MUST NOT call this path, and this path must be formally bounded in Phase 15.5 under contract `MEM-SEM-001`.
4. **No Vector Storage or Embedding Infrastructure Exists in Memory:**
   - `QdrantAdapterStub` (54 lines) raises `NotImplementedError` on all methods.
   - `Neo4jAdapterStub` (54 lines) raises `NotImplementedError` on all methods.
   - `llm/provider.py` provides chat completion only (`LiveHTTPLLMProvider`, `MockLLMProvider`); it contains zero embedding endpoints or vector models.
   - `deploy/migrations/005_create_memory_tables.sql` contains no vector columns, no embedding model metadata, and no vector indexes.
5. **Authority Separation is Structurally Intact:**
   - In `core/orchestrator/dispatch_model.py`, memory remains strictly advisory (`ConvergenceProposal.adaptation_hints`). Memory cannot mutate `TaskGraph`, cannot commit plans, cannot allocate resources, and cannot bypass Admission Control.
   - In replay mode (`self.replay_mode = True`), live memory queries are already skipped, ensuring historical replays do not query live memory.
6. **Evidence-Bounded Architectural Status:**
   - The claims in this document represent **ARCHITECTURAL TARGETS** and design specifications.
   - Implementation-level properties (e.g. bounded candidate generation, deterministic tie-breaking, degradation paths) become **IMPLEMENTATION VERIFIED**, **INTEGRATION VERIFIED**, and **REPLAY VERIFIED** only through the executable test suite during Phase 15.5 implementation.
7. **Conditions for Implementation:**
   - **Condition C-01 (Storage Realism & pgvector Distinction):** PostgreSQL is authoritative for durable persistence; a hermetic `InMemoryMemoryAdapter` with deterministic vector cosine calculation is authoritative for unit tests and environments where Docker/PostgreSQL is unavailable. If `pgvector` is available in PostgreSQL, it may provide production index-assisted retrieval; if unavailable, the system must employ bounded metadata pre-filtering with exact candidate scoring. Unindexed JSONB storage alone is **not** equivalent to an indexed vector search.
   - **Condition C-02 (Embedding Boundary & Core Ownership):** Core owns the abstract `EmbeddingProviderProtocol`; provider implementations live strictly outside `core/` (`memory/embeddings/`). `core/` MUST NOT import `llm/`, Ollama SDKs, PyTorch, Sentence-Transformers, or NumPy. A deterministic mock provider is mandatory for hermetic tests; live Ollama/local providers remain optional, replaceable, and non-blocking.
   - **Condition C-03 (Explicit Candidate Generation & Bounded Budgets):** Candidate selection must be bounded by $C_{max} \le 50$ using a multi-prong candidate strategy (fingerprint match $\rightarrow$ capability/error-class match $\rightarrow$ recency window) before semantic ranking. The system MUST NOT load unbounded Space histories into memory. Returned Top-$K$ hints must be bounded ($K \le 5$). Ties must be resolved deterministically using `(similarity_score_rounded, stored_at_desc, experience_id_asc)`.
   - **Condition C-04 (Bounded `list_experiences` Hardening):** The existing unbounded `list_experiences()` path must be hardened with bounded/paginated semantics under `MEM-SEM-001` or isolated as tracked technical debt. Semantic retrieval must remain strictly decoupled from `list_experiences()`.

---

## 2. BASELINE AUDIT

### Repository State
- **Git HEAD Commit:** `3c8ed391a4a4900a6c94dcbbf598eb37c7b10eb4`
- **Branch:** `main` (tracked with `origin/main`)
- **Working Tree:** Clean (zero uncommitted or staged changes)
- **Python Version:** 3.11.9
- **Platform:** Windows (PowerShell)

### Governance & Verification Suite Status
| Governance Tool | Target / Command | Result | Notes |
|:---|:---|:---|:---|
| `scripts/dep_guard.py` | `core/` forbidden import check | **PASS** | Zero imports from `agents/`, `workers/`, `memory/`, `llm/` |
| `scripts/contract_sync.py` | Registry vs Architecture §16 | **PASS** | 50 registered pulse types; all 38 architecture types covered |
| `scripts/v1_audit_governance.py` | ADR inventory & contracts (V1-005) | **PASS** | ADRs 0001..0048 valid; schemas 1:1 mapped |
| `scripts/v1_audit_spec_coverage.py`| Spec-map criteria (V1-001) | **PASS** | 177 criteria, 245 contracts, 203 spec mappings |
| `pytest memory/tests` | Existing Memory test suite | **PASS** | 90 passed, 1 skipped (PostgreSQL integration skip-guarded) |
| `mypy core memory` | Type verification | **PASS** | Source code fully typed; minor mock assign lints in test files |
| `ruff` | Linter | **UNVERIFIED** | Not installed in environment |

### Infrastructure Availability
- **PostgreSQL / Docker:** Offline in local development environment. Integration tests require `RYU_INTEGRATION_TESTS=1` and skip-guard cleanly when services are unavailable.
- **Redis:** Offline in local development environment (skip-guarded in integration tests).
- **Ollama / Embedding Services:** Not running locally on `http://localhost:11434`.

---

## 3. CURRENT MEMORY ARCHITECTURE

### Component Implementation Inventory

```text
                  [ DeterministicDispatcher ] (core/)
                               │
               TaskExecutionOutcome (core-neutral)
                               ▼
               [ ExperienceObserverProtocol ] (core/)
                               │
              implements       │ injected at composition root
                               ▼
             [ ExecutionExperienceObserver ] (memory/)
                               │ (sanitizes secrets, formats situation)
                               ▼
                         [ Reflector ] (memory/)
                               │
                ┌──────────────┴──────────────┐
                ▼                             ▼
       [ SpaceMemoryProtocol ]      [ experience.stored Pulse ]
       (core/space/memory_protocol) (contracts/registry)
                │
    ┌───────────┴───────────┐
    ▼                       ▼
[ InMemoryMemoryAdapter ] [ PostgreSQLMemoryAdapter ]
(memory/adapters)         (memory/adapters)
```

| Component | Location | Responsibility | Authority | Persistence | Retrieval Behavior | Verification Status | Classification |
|:---|:---|:---|:---|:---|:---|:---|:---|
| `SpaceMemoryProtocol` | `core/space/memory_protocol.py` | Abstract interface for Space-scoped memory operations | Core contract | N/A (Protocol) | Defines `get_experience`, `list_experiences`, `query_similar_experiences` | `UNIT_VERIFIED` | `IMPLEMENTED` |
| `ExperienceRecord` | `core/space/memory_protocol.py` | Immutable structured experience record with mandatory counterfactual | Core contract | N/A (Dataclass) | N/A | `UNIT_VERIFIED` | `IMPLEMENTED` |
| `KnowledgeEntry` | `core/space/memory_protocol.py` | Global promoted knowledge record | Core contract | N/A (Dataclass) | N/A | `UNIT_VERIFIED` | `IMPLEMENTED` |
| `PromotionAuthorization` | `core/space/memory_protocol.py` | Cryptographic capability token (HMAC-SHA256) for global promotion | Core contract | N/A (Dataclass) | N/A | `UNIT_VERIFIED` | `IMPLEMENTED` |
| `ExperienceQuery` | `core/space/memory_protocol.py` | Space-scoped query parameters (`space_id`, `situation_hint`, `limit`) | Core contract | N/A (Dataclass) | N/A | `UNIT_VERIFIED` | `IMPLEMENTED` |
| `TaskExecutionOutcome` | `core/space/memory_protocol.py` | Core-neutral verified execution outcome reported by Dispatcher | Core contract | N/A (Dataclass) | N/A | `UNIT_VERIFIED` | `IMPLEMENTED` |
| `ExperienceHint` | `core/space/memory_protocol.py` | Immutable advisory hint for planner/replan | Advisory only | N/A (Dataclass) | N/A | `UNIT_VERIFIED` | `IMPLEMENTED` |
| `AdaptationLayer` | `core/memory/adaptation.py` | Read-only translation of query results into `ExperienceHint`s | Advisory only | None | Calls `store.query_similar_experiences()`, filters negative/positive | `UNIT_VERIFIED` | `IMPLEMENTED` |
| `ExecutionExperienceObserver` | `memory/experience_observer.py` | Bridges Dispatcher to Reflector; sanitizes secrets | Memory layer | None | Ingestion only | `UNIT_VERIFIED` | `IMPLEMENTED` |
| `Reflector` | `memory/reflector.py` | Persists `ExperienceRecord` to store; emits `experience.stored` Pulse | Memory layer | Via adapter | Ingestion only | `UNIT_VERIFIED` | `IMPLEMENTED` |
| `PromotionPipeline` | `memory/promotion.py` | Manages human gate approval & cryptographic token issuance | Governance gate | Via adapter | Queries store for experience verification | `UNIT_VERIFIED` | `IMPLEMENTED` |
| `EvaluationModule` | `memory/evaluation.py` | Deterministic benchmark of experiences against `FrozenTraceCorpus` | Evaluation | In-memory corpus | Evaluates capability deltas | `UNIT_VERIFIED` | `IMPLEMENTED` |
| `InMemoryMemoryAdapter` | `memory/adapters/in_memory.py` | Hermetic thread-safe memory store for unit testing | Memory adapter | Memory (`dict`) | In-memory substring matching on space records | `UNIT_VERIFIED` | `IMPLEMENTED` |
| `PostgreSQLMemoryAdapter` | `memory/adapters/postgres.py` | Authoritative durable memory store backed by PostgreSQL | Memory adapter | PostgreSQL `space_experiences` | Recency query (`ORDER BY stored_at DESC LIMIT %s`) + in-memory filter | `UNIT_VERIFIED` (Mock) / `INTEGRATION_VERIFIED` (Env-guarded) | `IMPLEMENTED` |
| `QdrantAdapterStub` | `memory/adapters/qdrant_stub.py` | Extension stub for future vector database | None | None | Raises `NotImplementedError` | Untested | `STUB` |
| `Neo4jAdapterStub` | `memory/adapters/neo4j_stub.py` | Extension stub for future graph database | None | None | Raises `NotImplementedError` | Untested | `STUB` |
| `RetrievalWorker` | `workers/retrieval/__init__.py` | Stub for retrieval worker | None | None | 2-line stub | Untested | `STUB` |

---

## 4. CURRENT RETRIEVAL AUDIT & TECHNICAL DEBT DISPOSITION

### Inventory of All Existing Retrieval Paths

| Retrieval Path | Caller | Target Store / Query | Space Filter | Limit | Ordering | Complexity | Risk Description | Current Verification |
|:---|:---|:---|:---|:---|:---|:---|:---|:---|
| `get_experience()` | `PromotionPipeline`, Diagnostics | `SELECT ... WHERE space_id = %s AND experience_id = %s` / Dict lookup | Strict (`space_id`) | 1 row | Exact key | $O(1)$ | Low. Primary key lookup. | `UNIT_VERIFIED` |
| `list_experiences()` | Diagnostic CLI, Testing | `SELECT ... WHERE space_id = %s ORDER BY stored_at DESC` / Dict values | Strict (`space_id`) | **NONE (Unbounded)** | `stored_at DESC` | $O(N)$ memory & bandwidth | **HIGH.** Unbounded query; can exhaust memory on mature spaces. | `UNIT_VERIFIED` |
| `query_similar_experiences()` (InMemory) | `AdaptationLayer.generate_hints()` | `self._experiences.get(query.space_id, {}).values()` | Strict (`space_id`) | Post-sort slice `[:query.limit]` | `(score, stored_at) DESC` | $O(N)$ full space scan | **MEDIUM.** Scans entire space in Python memory; substring match only. Ties on same `stored_at` have no deterministic secondary key. | `UNIT_VERIFIED` |
| `query_similar_experiences()` (Postgres) | `AdaptationLayer.generate_hints()` | `SELECT ... WHERE space_id = %s ORDER BY stored_at DESC LIMIT %s;` followed by Python loop | Strict (`space_id`) | Database `LIMIT %s` | `stored_at DESC` in SQL, then `score DESC` in Python | $O(K)$ where $K = \text{limit}$ | **CRITICAL ARCHITECTURAL FLAW.** Only inspects the most recent $K$ records. Earlier highly relevant experiences are completely missed. Not semantic. | `UNIT_VERIFIED` |
| `get_global_knowledge()` | `AdaptationLayer`, Promotion | `SELECT ... WHERE knowledge_id = %s` / Dict lookup | Global table | 1 row | Exact key | $O(1)$ | Low. Authoritative key lookup. | `UNIT_VERIFIED` |

### Disposition of the Unbounded `list_experiences()` Path
**Decision: Option A — Bounded/Paginated Hardening in Phase 15.5 under Contract `MEM-SEM-001`.**
1. **Separation from Semantic Retrieval:** Semantic retrieval **DOES NOT** call `list_experiences()` and does not materialize full Space histories. Candidate generation is bounded independently ($C_{max} \le 50$).
2. **Defensive Hardening:** To eliminate technical debt and ensure repository consistency with Phase 15.3 bounded retrieval standards (`F-03`), `SpaceMemoryProtocol.list_experiences()` will be updated in Phase 15.5 to accept optional bounding parameters:
   ```python
   def list_experiences(
       self,
       space_id: str,
       limit: int = 50,
       before_stored_at: datetime | None = None,
   ) -> list[ExperienceRecord]: ...
   ```
   A hard ceiling of `MAX_EXPERIENCE_LIST_LIMIT = 100` will be enforced. If called without a limit, a default of 50 is applied.
3. **Explicit Invariant Statement:**
   > *"Semantic retrieval does not materialize the full Space history. Candidate generation is bounded independently. The existing `list_experiences()` path is separately bounded/paginated in Phase 15.5 under contract `MEM-SEM-001` to eliminate the unbounded scan risk."*

---

## 5. EXPERIENCE DATA MODEL AUDIT

### Audit of Existing Data Structures

```python
@dataclass(frozen=True)
class ExperienceRecord:
    experience_id: str             # Unique UUID
    space_id: str                  # Owning Space boundary (Law 1, Law 4)
    situation: dict[str, Any]      # Task situation (task_id, capability, params, deps)
    action: dict[str, Any]         # Executed action (capability, exit_code, duration)
    outcome: str                   # Outcome summary string
    counterfactual: str            # Mandatory actionable learning guidance (MEM-002)
    applicable_context: dict[str, Any] # Context (error_class, fingerprint, refs)
    stored_at: datetime            # Timestamp
```

### Representation Capability Matrix

| Attribute | Can Represent Today? | Existing Location / Field | Required for Semantic Retrieval? | Proposed Change / Action |
|:---|:---:|:---|:---:|:---|
| **Space ID** | **YES** | `record.space_id` | **YES (Mandatory Filter)** | Retain. Primary isolation key. |
| **Task ID** | **YES** | `situation["task_id"]` | **YES** | Retain in `situation`. |
| **Plan ID / Version** | **YES** | `situation["plan_version"]` | **YES** | Retain in `situation`. |
| **Goal ID** | PARTIAL | Implicit in context | Advisory | Keep in `applicable_context["goal_id"]`. |
| **Execution Outcome** | **YES** | `record.outcome` | **YES (Embedding Text)** | Embed in semantic representation. |
| **Success / Failure** | **YES** | Parsed from `outcome` & context | **YES (Metadata Filter)** | Add explicit filter flag in query. |
| **Failure Fingerprint** | **YES** | `applicable_context["failure_fingerprint"]` | **YES (Exact Match Filter)** | Use as high-priority exact filter before semantic fallback. |
| **Repair Attempt / Result** | **YES** | `applicable_context["repair_iteration"]` | **YES** | Retain in `applicable_context`. |
| **Successful Strategy** | **YES** | `applicable_context["suggested_alternative"]` | **YES** | Retain in `applicable_context`. |
| **Capability** | **YES** | `action["capability"]` | **YES (Metadata Filter)** | Pre-filter or match candidate capability. |
| **Artifacts & Evidence** | **YES** | `applicable_context["artifact_refs"]` | **YES (Provenance)** | Retain in `applicable_context`. |
| **Provenance** | PARTIAL | Correlation ID in pulses, task/space IDs | **YES** | Add structured provenance block. |
| **Embedding Vector** | **NO** | *Missing* | **YES** | **Add `embedding: tuple[float, ...]` to semantic record**. |
| **Embedding Metadata** | **NO** | *Missing* | **YES** | **Add `embedding_model`, `embedding_dim`, `embedding_version`**. |

### Detailed Rationale for Proposed Semantic Fields

| Proposed Field | Why Required? | Source / Origin | Authority Owner | Persisted? | Replay Impact? |
|:---|:---|:---|:---|:---:|:---:|
| `embedding` | Vector representation for cosine similarity retrieval | Computed by `EmbeddingProvider` upon reflection | `Reflector` / Memory store | **YES** | Deterministic if computed with fixed model/weights; recorded in proposal during execution. |
| `embedding_model` | Identifies model used (e.g. `all-MiniLM-L6-v2` or `mock-v1`) to prevent cross-model cosine comparison | `EmbeddingConfig` | `EmbeddingProvider` | **YES** | Prevents comparing query vector against incompatible index vectors. |
| `embedding_dim` | Vector dimensionality validation (e.g. 384 or 128) | Provider spec | `EmbeddingProvider` | **YES** | Hard validation against dimension mismatch. |
| `embedding_version` | Monotonic version for index migration and invalidation | Config version | `Reflector` | **YES** | Ensures stale vectors are detected and refreshed. |
| `provenance_ref` | Cryptographic link to task evidence (`evidence_hash`, `task_id`, `plan_version`) | `TaskExecutionOutcome` | `DeterministicDispatcher` | **YES** | Guarantees experience cannot be forged or detached from execution evidence. |

---

## 6. SEMANTIC RETRIEVAL GAP ANALYSIS

```text
CURRENT STATE:
  Query Situation ──> SQL: ORDER BY stored_at DESC LIMIT 5 ──> 5 rows ──> Substring match ──> Misses relevant past

DESIRED STATE:
  Query Situation
        │
        ▼
  Multi-Prong Bounded Candidate Selection (C <= 50):
    1. Exact Fingerprint Matches (WHERE failure_fingerprint = :fp)
    2. Capability / Error-Class Matches (WHERE capability = :cap OR error_class = :err)
    3. Recent Space Recency Window (ORDER BY stored_at DESC LIMIT 20)
        │ (Deduplicated union clamped to C <= 50 WITHOUT scanning full space)
        ▼
  Semantic Vector Similarity (Cosine Distance) + Exact Fingerprint Priority Boost
        │
        ▼
  Deterministic Tie-Breaking: (round(similarity, 4), stored_at DESC, experience_id ASC)
        │
        ▼
  Bounded Top-K (K <= 5)
        │
        ▼
  Advisory ExperienceHints ──> ConvergenceProposal
```

---

## 7. EMBEDDING ARCHITECTURE & PROTOCOL BOUNDARIES

### Core Boundary Rule Compliance (`AGENTS.md §7`)
The embedding boundary enforces absolute separation between the deterministic core and external ML frameworks:

> **"Core owns the embedding contract/protocol; provider implementations are injected from outside core."**

```text
core/space/memory_protocol.py
        │
        │ defines protocol (pure standard library types)
        ▼
class EmbeddingProviderProtocol(Protocol):
    def embed_text(self, text: str) -> tuple[float, ...]: ...
    def embed_batch(self, texts: list[str]) -> list[tuple[float, ...]]: ...
    @property
    def dimension(self) -> int: ...
    @property
    def model_name(self) -> str: ...

                    ▲
                    │ implements (injected at composition root)
      ┌─────────────┴─────────────┐
      │                           │
memory/embeddings/         memory/embeddings/
deterministic_mock.py      ollama_provider.py
(Hermetic Unit Tests)      (Optional Live Local)
```

### Governing Architectural Invariants:
1. `core/` defines `EmbeddingProviderProtocol` using only standard library typing (`tuple[float, ...]`).
2. `core/` **MUST NOT** import `llm/`.
3. `core/` **MUST NOT** import Ollama SDK or HTTP client code.
4. `core/` **MUST NOT** import PyTorch, Sentence-Transformers, NumPy, or other third-party ML frameworks.
5. All concrete provider implementations reside strictly in `memory/embeddings/`.
6. A hermetic `DeterministicMockEmbeddingProvider` (deterministic pseudo-random unit vectors derived from SHA-256 token hashes) is mandatory for unit and harness testing.
7. A live `OllamaEmbeddingProvider` (connecting to `http://localhost:11434/api/embeddings`) is an optional, replaceable extension that must fail safely without blocking execution.

### Embedding Specifications
- **Target Vector Dimension:** 384 dimensions (standard for edge models e.g. `all-MiniLM-L6-v2`) or 128 dimensions for hermetic mock tests.
- **Normalization:** Vectors MUST be $L_2$-normalized upon generation ($\sum v_i^2 = 1.0$), reducing cosine similarity calculation to a pure dot product:
  $$\text{sim}(u, v) = \sum_{i=1}^D u_i \cdot v_i$$
- **Truncation & Input Bounds:** Input text for embedding MUST be truncated to a hard ceiling of 2,048 characters to prevent unbounded tokenization latency or OOM.
- **Batch Ceiling:** Maximum batch size for embedding generation is capped at $B_{max} = 16$.
- **Timeout:** Embedding requests must enforce a strict timeout of 500ms. If timed out, fall back immediately.

---

## 8. STORAGE OPTIONS & VECTOR INDEXING ARCHITECTURE

### Structural Storage Clarification: JSONB vs. Vector Indexing

| Storage Tier | Role & Authority | Indexing Mechanism | Fallback / Degraded Behavior |
|:---|:---|:---|:---|
| **Tier 1: Hermetic InMemory** | Authoritative for unit tests, offline development, and hermetic CI runs | Pure-Python deterministic dot-product scan over bounded candidate set ($C \le 50$) | N/A (Always available, zero dependencies) |
| **Tier 2: PostgreSQL with pgvector** | Authoritative durable persistence when `pgvector` extension is installed | Native `vector(384)` column with HNSW or IVFFlat index | If index build fails, fall back to Tier 2 degraded |
| **Tier 2 Degraded: PostgreSQL without pgvector** | Authoritative durable persistence when `pgvector` extension is absent | Structured `embedding JSONB` storage + bounded candidate pre-filtering ($C \le 50$) | Exact in-memory cosine computation over the $\le 50$ fetched candidates |
| **Tier 3: Qdrant / Neo4j** | Non-authoritative extension boundaries per ADR-0033 | External vector/graph database | Stubs raise `NotImplementedError` (Phase 11+) |

### Important Architectural Invariant:
**JSONB storage alone must NOT be presented as equivalent to an indexed vector search.**  
- Storing vectors in JSONB allows durable persistence and retrieval of raw floats, but requires fetching candidates into application memory to compute similarity.
- Therefore, when `pgvector` is absent, bounded multi-prong candidate selection ($C \le 50$) is **mandatory** to prevent fetching unbounded rows.

---

## 9. TARGET BOUNDED CANDIDATE GENERATION & RETRIEVAL PIPELINE

### Detailed Candidate Selection Strategy ($C_{max} \le 50$)
To ensure $C_{max} \le 50$ is a **genuine resource bound** and not a recency-biased window or post-hoc slice of an unbounded query, candidate generation operates as follows:

```text
QUERY CONTEXT:
  space_id: str
  capability: str | None
  error_class: str | None
  failure_fingerprint: str | None
  query_text: str

PRONG 1: EXACT FINGERPRINT CANDIDATES (Slot Priority 1)
  SELECT ... FROM space_experiences 
  WHERE space_id = :space_id AND failure_fingerprint = :fp
  LIMIT 10;
  (Guarantees past repairs for the identical error signature are ALWAYS present in candidates)

PRONG 2: METADATA CAPABILITY & ERROR-CLASS CANDIDATES (Slot Priority 2)
  SELECT ... FROM space_experiences
  WHERE space_id = :space_id 
    AND (action->>'capability' = :cap OR applicable_context->>'error_class' = :err)
  ORDER BY stored_at DESC
  LIMIT 25;
  (Guarantees domain-relevant candidates for the same capability or error type)

PRONG 3: RECENT SPACE WINDOW (Slot Priority 3)
  SELECT ... FROM space_experiences
  WHERE space_id = :space_id
  ORDER BY stored_at DESC
  LIMIT 20;
  (Captures immediate execution context)

MERGE & CLAMP (Pre-Ranking Resource Bound):
  1. Deduplicate by experience_id across all three prongs.
  2. Enforce hard ceiling: C = Union(Prong1, Prong2, Prong3)[:50].
  3. Zero unbounded materialization: at most 55 rows ever read from DB before deduplication.
```

### Exact Specifications for Candidate Selection:
1. **Candidate Sources:** Prong 1 (Fingerprint), Prong 2 (Capability/Error), Prong 3 (Recency).
2. **Candidate Ordering & Merging:** Merged in priority order: Prong 1 $\rightarrow$ Prong 2 $\rightarrow$ Prong 3. Deduplicated by `experience_id`.
3. **Candidate Ceiling:** Exactly $\le 50$ candidates passed to semantic scoring.
4. **Space Isolation:** `WHERE space_id = :space_id` strictly enforced on all three prongs.
5. **Behavior with Fewer than 50 Candidates:** If total candidates $N < 50$, all $N$ candidates are scored without error or artificial padding.
6. **Behavior with More than 50 Candidates:** Truncated to exactly 50 candidates using the priority hierarchy.
7. **Fingerprint Match Invariant:** Exact fingerprint matches are included within the 50-candidate ceiling (given slot priority 1, up to 10 slots); they receive an additive score bonus (+100.0) during ranking.
8. **Vector-Index Interaction:** If `pgvector` HNSW index is active, an approximate nearest neighbor search within `space_id` can serve as Prong 4 (allocating 15 slots), clamped within the same overall $C \le 50$ ceiling.
9. **InMemory vs. PostgreSQL Semantic Equivalence:**
   - In `PostgreSQLMemoryAdapter`: Assembled via bounded SQL union query.
   - In `InMemoryMemoryAdapter`: Space-partitioned secondary indexes (a `dict[fingerprint, list]`, `dict[capability, list]`, and recency ring-buffer) assemble $\le 50$ candidates without iterating the entire Space history.
10. **Resource Bound Guarantee:** Neither PostgreSQL nor InMemory ever loads the full Space history during retrieval.

### Post-Candidate Semantic Ranking & Top-$K$ Slicing
```text
Candidates C (<= 50)
        │
        ▼
Semantic Cosine Scoring (Dot Product over L2-normalized vectors)
        │
        ▼
Threshold Filter: score >= 0.65 (MIN_SIMILARITY_THRESHOLD)
  (Exact fingerprint matches bypass threshold via +100.0 bonus)
        │
        ▼
Deterministic Quantized Sort Key:
  Sort Order: (
      round(score, 4),           # 4-decimal floating point quantization
      candidate.stored_at,        # Recency preference
      candidate.experience_id     # Lexicographical tie-breaker (Absolute determinism)
  ) DESC
        │
        ▼
Bounded Top-K Slicing: K <= 5
        │
        ▼
Synthesize ExperienceHints (Max 200 chars per counterfactual summary)
        │
        ▼
ConvergenceProposal.adaptation_hints (Advisory Only)
```

---

## 10. DETERMINISM, REPLAY & EVIDENCE STATUS CLASSIFICATION

### Evidence Hierarchy Status
To prevent premature or overclaimed implementation statements, this audit formally classifies properties across five verification stages:

| Dimension | Architectural Target | Implementation Verified | Integration Verified | Security Verified | Replay Verified |
|:---|:---:|:---:|:---:|:---:|:---:|
| **Bounded Retrieval ($C \le 50, K \le 5$)** | Targeted in Audit | Phase 15.5 Slice A/C | Phase 15.5 Slice D | Phase 15.5 ADV-07/08 | Verified in Harness |
| **Space Isolation** | Targeted in Audit | Phase 15.5 Slice C/D | Phase 15.5 Slice D | Phase 15.5 ADV-01/02 | Verified in Harness |
| **Deterministic Tie-Breaking** | Targeted in Audit | Phase 15.5 Slice B | Phase 15.5 Slice C | Phase 15.5 ADV-13 | 100-run loop verification |
| **Advisory Authority Preservation** | Targeted in Audit | Phase 15.5 Slice E | Phase 15.5 Slice E | Phase 15.5 ADV-03/04 | Phase 13/15.4 test suite |
| **Replay Compatibility** | Targeted in Audit | Phase 15.5 Slice E | Phase 15.5 Slice E | Phase 15.5 ADV-18 | Replay mode zero-query check |

### Replay Compatibility Architecture
- **Target Architecture:** Bounded, Space-scoped, provenance-aware, and replay-compatible semantic retrieval architecture, subject to implementation and verification.
- **Live Execution Mode (`replay_mode = False`):**
  - When replanning occurs, `ConvergenceEngine` queries `AdaptationLayer.generate_hints()`.
  - Retrieved `ExperienceHint`s are embedded directly into the immutable `ConvergenceProposal.adaptation_hints` tuple.
  - When committed via `SpaceKernel CAS`, hints become immutable historical evidence.
- **Replay Mode (`replay_mode = True`):**
  - In `core/orchestrator/dispatch_model.py` (lines 3333 and 3517):
    ```python
    if self.adaptation_layer is not None and not self.replay_mode:
        hints = self.adaptation_layer.generate_hints(...)
    ```
  - During replay, the system **DOES NOT RE-QUERY LIVE MEMORY**. It reads recorded `adaptation_hints` from the historical `ConvergenceProposal` or checkpoint. Live memory index mutations cannot alter historical replay outcomes.
- **Offline Retrieval Evaluation Determinism:**
  - Guaranteed by:
    - Fixed model seed / deterministic mock embeddings.
    - Quantized similarity scoring (`round(score, 4)`).
    - Lexicographical tie-breaking on `experience_id ASC`.

---

## 11. SPACE ISOLATION AUDIT

### Space Isolation Invariant (SCCA Law 1 & Law 4)
No operation inside Space A may ever inspect, query, or infer experiences from Space B unless those experiences have been formally promoted to `global_knowledge` through the authenticated Human Approval Gate (`MEM-005`, `MEM-006`).

```text
[ Space A ]                                  [ Space B ]
     │                                            │
     ▼                                            ▼
query(space_id="A")                          query(space_id="B")
     │                                            │
     ├───────────── STRICT PARTITION ─────────────┤
     │ (WHERE space_id = 'A')   (WHERE space_id = 'B')
     ▼                                            ▼
Only Space A Memories                        Only Space B Memories
```

---

## 12. SECURITY THREAT MODEL & DEFENSES

| Threat ID | Threat Description | Attack Surface | Existing Defense | Missing Defense to Implement in 15.5 | Severity | Required Adversarial Test |
|:---|:---|:---|:---|:---|:---:|:---|
| **THREAT-01** | Cross-Space Memory Leakage | `query_similar_experiences(query)` | `WHERE space_id = %s` in SQL; dict partition in InMemory | SQL composite index enforcement `(space_id, ...)` | **P0** | `MEM-SEM-ADV-01` |
| **THREAT-02** | Forged / Empty Space ID | Caller passes `space_id=""` or `space_id=None` | `SpaceIsolationViolation` raised in `AdaptationLayer` | Protocol-level dataclass validation | **P1** | `MEM-SEM-ADV-02` |
| **THREAT-03** | Prompt Injection in Stored Experience | Malicious text in `counterfactual` or `outcome` trying to hijack LLM | Text stored as raw string | Strict sanitization, length truncation (200 chars), prompt framing isolation | **P1** | `MEM-SEM-ADV-03` |
| **THREAT-04** | Poisoned Experience Injection | Malicious worker storing bad advice to induce infinite loops | Failure fingerprint loop guard in `ConvergenceEngine` | Exact failure fingerprint match ceiling (`MAX_RETRY=3` -> `ESCALATE`) | **P1** | `MEM-SEM-ADV-04` |
| **THREAT-05** | Forged Provenance References | Fabricating `task_id` or `artifact_ref` | None in memory adapter | Provenance validation against Space task graph nodes | **P2** | `MEM-SEM-ADV-05` |
| **THREAT-06** | Vector Dimension Mismatch | Query vector $D=128$ compared against index $D=384$ | None | Strict vector dimension validation in `EmbeddingProvider` & store | **P1** | `MEM-SEM-ADV-06` |
| **THREAT-07** | Oversized Retrieval / OOM Attack | Flooding memory to return megabytes of hint text | None | Bounded Top-$K$ ($K \le 5$) and per-field character limits | **P1** | `MEM-SEM-ADV-07` |
| **THREAT-08** | Unbounded Candidate Scanning | Calling retrieval on space with 100,000 experiences | `LIMIT` on SQL (broken recency) | Explicit multi-prong $C_{max} \le 50$ candidate bound | **P1** | `MEM-SEM-ADV-08` |
| **THREAT-09** | Concurrency Race on Experience Append | Two tasks in same space writing experience simultaneously | RLock in InMemory; `ON CONFLICT DO NOTHING` in Postgres | Verified thread-safety under concurrent scheduler tasks | **P1** | `MEM-SEM-ADV-09` |
| **THREAT-10** | Embedding Provider DoS / Timeout | Remote embedding provider hangs indefinitely | None | Strict 500ms timeout with fallback to `CONTINUE_WITHOUT_MEMORY` | **P1** | `MEM-SEM-ADV-10` |
| **THREAT-11** | Stale / Incompatible Embedding Model | Index built with Model A; query embedded with Model B | None | `embedding_model` metadata check; reject or re-index | **P2** | `MEM-SEM-ADV-14` |
| **THREAT-12** | Unauthorized Cross-Space Promotion | Injecting experiences directly into `global_knowledge` | HMAC-SHA256 `PromotionAuthorization` verification | Verified intact (ADR-0035) | **P0** | `MEM-SEM-ADV-19` |

---

## 13. PROVENANCE AND EVIDENCE HIERARCHY

### Evidence Superiority Invariant
In RYU AI, **Similarity Score Never Establishes Truth**.

The evidence hierarchy remains absolute:
$$\text{Artifact SHA-256} > \text{Signed Tool Output} > \text{Verified Test Result} > \text{Process Exit Code} > \text{Telemetry} > \text{Model Assertion} > \text{Memory Hint}$$

### Provenance Tracking Requirements
Every retrieved `ExperienceRecord` and synthesized `ExperienceHint` must retain:
1. `source_space_id`: Owning Space where the experience occurred.
2. `task_id`: Task that produced the outcome.
3. `plan_version`: Plan version under which the task was executed.
4. `failure_fingerprint`: Deterministic hash of normalized failure traceback (if negative).
5. `artifact_refs`: Cryptographic hashes / paths of output artifacts.

If an experience lacks a valid `source_space_id` or `task_id`, it is flagged as unverified and excluded from adaptation ranking.

---

## 14. MEMORY AUTHORITY BOUNDARY AUDIT

### SCCA Authority Invariants
1. **Memory is Strictly Advisory:**
   - Memory **CANNOT** directly mutate `TaskGraph`.
   - Memory **CANNOT** commit a plan or plan delta.
   - Memory **CANNOT** allocate resources or create leases.
   - Memory **CANNOT** grant capabilities or bypass Admission Control.
   - Memory **CANNOT** override evidence (e.g. claim a task succeeded when exit code was non-zero).
2. **Authority Flow:**
   $$\text{Memory} \longrightarrow \text{ExperienceHint} \longrightarrow \text{ConvergenceProposal} \longrightarrow \text{PlanDelta} \longrightarrow \text{SpaceKernel CAS} \longrightarrow \text{Execution}$$
3. **Repository Inspection Verification:**
   - In `core/memory/adaptation.py`, `AdaptationLayer` generates hints only; it contains zero mutation methods.
   - In `core/orchestrator/dispatch_model.py`, `ConvergenceEngine` uses hints solely to suggest alternative capabilities in a `ConvergenceProposal`. The proposal must be explicitly accepted and committed by `SpaceKernel.commit_plan_delta()`.
   - **Result:** Architectural authority boundaries are 100% clean and uncompromised.

---

## 15. RETENTION, GROWTH & OPERATIONAL DEFAULTS

### Operational Retention Ceiling
- **Default Bound:** 1,000 experiences per Space is an **operational implementation default** subject to configuration and future evidence; it is **NOT** an SCCA invariant or immutable architectural law.
- **Pruning Strategy:** When the operational ceiling is breached, prune oldest non-promoted experiences with zero failure fingerprint matches (`FIFO` by `stored_at`).
- **Promoted & Valuable Knowledge Protection:** Formally promoted knowledge in `global_knowledge` and experiences with active repair fingerprints are pinned and exempted from pruning.
- **Deduplication:** Repeated identical task outcomes with identical failure fingerprints do not insert duplicate rows; they update `last_observed_at` and increment `occurrence_count`.

---

## 16. PERFORMANCE AND COMPLEXITY TARGETS

| Operation | Current Complexity | Target Complexity | Target Latency Budget | Target Resource Bound |
|:---|:---:|:---:|:---:|:---:|
| **Experience Ingestion** | $O(1)$ append | $O(1)$ append + embedding | $< 50\text{ ms}$ | 1 vector (1.5 KB) |
| **Embedding Generation** | N/A | $O(L)$ where $L \le 2048$ chars | $< 200\text{ ms}$ | $\le 16\text{ texts per batch}$ |
| **Metadata Candidate Pre-Filter** | $O(N)$ scan | $O(\log N + C)$ via B-Tree index | $< 10\text{ ms}$ | $C_{max} \le 50$ rows |
| **Similarity Ranking** | $O(N)$ string scan | $O(C \cdot D)$ dot product | $< 5\text{ ms}$ | $C \le 50, D \le 384$ |
| **Total Retrieval Pipeline** | Unbounded / Recency-only | Bounded Multi-Prong Pipeline | $< 300\text{ ms}$ | $K_{max} \le 5$ hints |

---

## 17. CONCURRENCY & SCHEDULER INTEGRATION

### Concurrent DAG Execution (Phase 15.4) Invariants
In Phase 15.4, multiple independent tasks execute concurrently across worker threads.
1. **Non-Blocking Writes:** Ingestion of task outcomes via `ExecutionExperienceObserver.observe_task_outcome()` must not block concurrent task workers or scheduler dispatch.
2. **Thread Safety:**
   - `InMemoryMemoryAdapter`: Synchronized via fine-grained `threading.RLock()` per Space.
   - `PostgreSQLMemoryAdapter`: Connection-pooled, parameterized SQL execution with row-level transaction isolation.
3. **Zero Global Scheduler Locks:** Memory retrieval must NEVER acquire locks across Spaces or block the scheduler event loop.

---

## 18. FAILURE AND GRACEFUL DEGRADATION

### Graceful Degradation Taxonomy

| Failure Mode | Detection Point | Action / Behavior | System State | Impact on Execution |
|:---|:---|:---|:---|:---|
| **Embedding Provider Offline / Timeout** | `EmbeddingProvider.embed_text()` raises timeout/connection error | Catch exception, log warning, skip vector similarity | `DEGRADED (Metadata Fallback)` | Execution proceeds normally; hints derived from metadata only |
| **PostgreSQL Outage** | `PostgreSQLMemoryAdapter` raises `MemoryFailure` | Catch in observer/adaptation; log warning | `CONTINUE_WITHOUT_MEMORY` | Zero impact on execution; tasks complete and plan converges |
| **Vector Dimension Mismatch** | `EmbeddingProvider.dimension != store.dimension` | Raise `ValueError`, log error, skip semantic score | `DEGRADED (Metadata Only)` | Execution proceeds normally |
| **Malformed Experience Record** | Schema validator / JSON decode error | Reject row, record audit event | `HEALTHY` | Malformed row ignored; valid rows retrieved |
| **Zero Retrieval Results** | `query_similar_experiences()` returns `[]` | Return empty hint list `[]` | `HEALTHY` | Execution proceeds with standard heuristic replanning |

**Core Rule:** Memory failure is an informational/advisory loss; it must **NEVER** abort task execution, cancel leases, or corrupt plan CAS state.

---

## 19. CRASH RECOVERY (PHASE 12.8 / F-06 BOUNDARIES)

1. **Advisory State Separation:** Memory state is NOT required for crash recovery. Space crash recovery (Phase 12.8) restores `TaskGraph`, leases, and execution attempts from PostgreSQL.
2. **Crash During Embedding Generation:** If the system crashes after task execution but before embedding generation, the task completion evidence is already durable in PostgreSQL (`006_create_execution_attempts_and_convergence_state.sql`). Memory reconciliation can re-observe completed tasks asynchronously without holding locks.
3. **Preserving Finding F-06 Boundary:** Finding F-06 (Durable Convergence State) is explicitly deferred. Phase 15.5 introduces zero durable convergence tables.

---

## 20. CONTRACT IMPACT ANALYSIS

### Existing Contracts to Reuse
- `MEM-001`: Space Memory Isolation (Preserved & Enforced)
- `MEM-002`: Experience Schema with Counterfactual (Preserved)
- `MEM-003`: Experience Persistence Across Restarts (Preserved)
- `MEM-004`: Actionable Learning on Frozen Traces (Preserved)
- `MEM-005`: Promotion Gate Authentication (Preserved)
- `MEM-006`: Authenticated Approver for Promotion (Preserved)
- `ADAPT-001`..`005`: Closed-Loop Adaptation Contracts (Preserved)

### Proposed New Contracts for Phase 15.5
1. **`MEM-SEM-001` (Bounded Candidate Retrieval & Paginated Experience Listing):**
   - *Invariant:* Memory candidate retrieval is strictly Space-scoped ($C_{max} \le 50$); `list_experiences()` enforces bounded pagination ($limit \le 100$).
   - *Owner:* `core/space/memory_protocol.py` & memory adapters.
2. **`MEM-SEM-002` (Deterministic Semantic Similarity Ranking):**
   - *Invariant:* Similarity scoring must use quantized $L_2$-normalized dot product with secondary tie-breaking on `stored_at DESC` and `experience_id ASC`.
   - *Owner:* `memory/retrieval/ranker.py` / `SpaceMemoryProtocol`.
3. **`MEM-SEM-003` (Decoupled Embedding Protocol Boundary):**
   - *Invariant:* `core/` defines `EmbeddingProviderProtocol`; zero ML or LLM library imports in core. Provider implementations reside strictly outside core.
   - *Owner:* `core/space/memory_protocol.py`.
4. **`MEM-SEM-004` (Graceful Degradation on Retrieval Failure):**
   - *Invariant:* Embedding provider or memory store failure must degrade to `CONTINUE_WITHOUT_MEMORY` or metadata fallback without blocking task execution or plan convergence.
   - *Owner:* `core/memory/adaptation.py` & `core/orchestrator/dispatch_model.py`.
5. **`MEM-SEM-005` (Bounded Advisory Experience Hints):**
   - *Invariant:* Hint generation is bounded by $K_{max} \le 5$, with total summary length capped at 1,000 characters.
   - *Owner:* `core/memory/adaptation.py`.

---

## 21. PULSE IMPACT ANALYSIS

### Pulse Assessment
- Current registered pulse types (50 total):
  - `memory.updated` (already registered)
  - `experience.stored` (already registered)
  - `knowledge.promotion.requested` (already registered)
  - `knowledge.promotion.approved` (already registered)
  - `knowledge.promotion.rejected` (already registered)

### Recommendation: ZERO NEW PULSES REQUIRED
- Adding pulses such as `memory.retrieved` or `memory.similarity_scored` on every query would cause bus flooding (amplification risk) during concurrent task replanning.
- Telemetry for retrieval is local diagnostic logging and recorded in `ConvergenceProposal.adaptation_hints`.
- `experience.stored` and `memory.updated` already provide complete pub/sub eventing for experience storage.

---

## 22. DATABASE & MIGRATION DESIGN

### Required Migration: `009_add_semantic_embeddings_to_space_experiences.sql`

```sql
-- Migration 009: Add embedding vector and metadata to space_experiences table
-- Phase 15.5 Semantic Memory Retrieval (Finding F-05, MEM-SEM-001..005)

-- 1. Add vector and embedding metadata columns
ALTER TABLE space_experiences
    ADD COLUMN IF NOT EXISTS embedding JSONB DEFAULT '[]'::jsonb,
    ADD COLUMN IF NOT EXISTS embedding_model VARCHAR(100) DEFAULT '',
    ADD COLUMN IF NOT EXISTS embedding_dim INTEGER DEFAULT 0,
    ADD COLUMN IF NOT EXISTS embedding_version INTEGER DEFAULT 1,
    ADD COLUMN IF NOT EXISTS failure_fingerprint VARCHAR(255) DEFAULT '';

-- 2. Add composite index for high-speed Space-scoped recency retrieval
CREATE INDEX IF NOT EXISTS idx_space_exp_space_stored 
    ON space_experiences (space_id, stored_at DESC);

-- 3. Add index for exact failure fingerprint matching
CREATE INDEX IF NOT EXISTS idx_space_exp_fingerprint 
    ON space_experiences (space_id, failure_fingerprint) 
    WHERE failure_fingerprint <> '';
```

*Architectural Storage Clarification:* Storing `embedding JSONB` ensures durable persistence across PostgreSQL instances. If `pgvector` is available, an additional vector index may be added for hardware-accelerated nearest-neighbor searches; when unavailable, the multi-prong $C \le 50$ candidate query ensures bounded memory usage and fast in-memory dot-product scoring.

---

## 23. INTEGRATION WITH PHASE 13 (EXPERIENTIAL ADAPTATION)

The existing Phase 13 learning loop remains authoritative:
```text
Task Execution Outcome (DeterministicDispatcher)
        │
        ▼
ExecutionExperienceObserver (Scrub secrets, construct situation/action)
        │
        ▼
Reflector.reflect() (Compute embedding -> Persist ExperienceRecord -> experience.stored Pulse)
        │
        ▼
AdaptationLayer.generate_hints() (Space-scoped semantic retrieval -> ExperienceHints)
        │
        ▼
ConvergenceEngine (Include hints in ConvergenceProposal -> PlanDelta -> SpaceKernel CAS)
```
Phase 15.5 **enhances the internal selection precision** of `AdaptationLayer.generate_hints()`. It replaces recency-truncated substring scanning with bounded semantic vector retrieval.

---

## 24. INTEGRATION WITH PHASE 14 (RESEARCH & SOFTWARE ENGINEERING WORKFLOW)

Phase 14 introduced:
- `normalize_failure_trace()` and `compute_repair_fingerprint()` (`core/space/repair_protocol.py`)
- Software test repair loop with iteration ceilings (`REPAIR-001..004`)
- Patch provenance and research synthesis (`PROVENANCE-001..003`)

In Phase 15.5:
1. When a repair fails, `ExecutionExperienceObserver` captures the `failure_fingerprint`.
2. When the repair loop iterates, `ConvergenceEngine` queries `AdaptationLayer` with `fingerprint`.
3. The semantic retrieval engine performs an **exact fingerprint match first (Prong 1)**, retrieving the exact counterfactual repair guidance from past attempts before falling back to semantic similarity.

---

## 25. INTEGRATION WITH PHASE 15.4 (CONCURRENT DAG SCHEDULER)

- Concurrent tasks executing on multiple worker threads call `ExecutionExperienceObserver.observe_task_outcome()`.
- Reads and writes to `InMemoryMemoryAdapter` are synchronized via fine-grained reentrant locks per Space (`_experiences_lock[space_id]`), preventing lock contention across different Spaces.
- Database writes in `PostgreSQLMemoryAdapter` execute within isolated transactions with zero table-level locks.
- Scheduler latency is completely decoupled: experience reflection occurs after task completion is recorded.

---

## 26. ADVERSARIAL TEST MATRIX (MEM-SEM-ADV-01..20)

| ID | Adversarial Test Scenario | Expected Defensive Invariant | Verification Mechanism |
|:---|:---|:---|:---|
| **MEM-SEM-ADV-01** | Cross-Space Retrieval Attempt | Space A cannot retrieve Space B experiences under any semantic query | Assert 0 results returned from Space B |
| **MEM-SEM-ADV-02** | Forged / Empty Space ID | Query with `space_id=""` or `'   '` rejected immediately | Raises `SpaceIsolationViolation` |
| **MEM-SEM-ADV-03** | Malicious Stored Prompt Injection | Counterfactual containing "IGNORE PREVIOUS INSTRUCTIONS" | Stored as raw text; truncated to 200 chars; never evaluated as code |
| **MEM-SEM-ADV-04** | Poisoned Experience Loop Injection | Counterfactual advising repeating a failed action | Failure fingerprint loop guard escalates after 3 repeats |
| **MEM-SEM-ADV-05** | Forged Provenance References | Experience referencing non-existent task ID or artifact | Provenance validation flags record as unverified |
| **MEM-SEM-ADV-06** | Invalid Embedding Dimension | Vector dimension 128 submitted to 384-dim index | Validation error raised; degrades to metadata fallback |
| **MEM-SEM-ADV-07** | Oversized Retrieval Flooding | Space with 10,000 experiences queried | Retrieved hints strictly capped at $K \le 5$; candidates capped at $C \le 50$ |
| **MEM-SEM-ADV-08** | Unbounded Query Execution | Querying `list_experiences()` on mature Space | Pagination / limit enforced; memory bounded |
| **MEM-SEM-ADV-09** | Concurrent Write Race Condition | 20 threads simultaneously writing experiences to same Space | Zero corrupted records; all 20 persisted or deduplicated |
| **MEM-SEM-ADV-10** | Embedding Provider Timeout | Mock provider introduces 2.0s delay | 500ms timeout triggers; returns metadata fallback |
| **MEM-SEM-ADV-11** | Embedding Provider Total Outage | Provider raises `ConnectionRefusedError` | System degrades gracefully to `CONTINUE_WITHOUT_MEMORY` |
| **MEM-SEM-ADV-12** | Database Disconnection Mid-Query | Postgres connection drops during semantic query | `MemoryFailure` caught; convergence proceeds unblocked |
| **MEM-SEM-ADV-13** | Deterministic Tie Collision | 10 experiences with identical cosine similarity and timestamp | Sorted deterministically by `experience_id ASC` across 100 runs |
| **MEM-SEM-ADV-14** | Stale Embedding Model Version | Records stored with Model v1; queried with Model v2 | Version mismatch detected; fallback to lexical/metadata |
| **MEM-SEM-ADV-15** | Memory / Index Inconsistency | Experience deleted or corrupted in store | Missing record skipped safely; no crash |
| **MEM-SEM-ADV-16** | Concurrent Cross-Space Queries | Simultaneous queries across Space A, B, and C | Zero cross-talk; complete thread isolation |
| **MEM-SEM-ADV-17** | Memory Flooding Denial-of-Service | 5,000 duplicate failure experiences ingested | Deduplicated by fingerprint; oldest non-promoted pruned |
| **MEM-SEM-ADV-18** | Replay Divergence Attack | Live memory mutated after plan execution; re-running replay | Replay reads recorded hints; zero live queries made |
| **MEM-SEM-ADV-19** | Forged Global Knowledge Promotion | Attacker attempts to inject global knowledge without token | Rejects with `PermissionError` (HMAC verification fails) |
| **MEM-SEM-ADV-20** | NaN / Inf Vector Component Injection | Query vector containing `float('nan')` or `float('inf')` | Vector validation rejects NaN/Inf; falls back safely |

---

## 27. VERTICAL IMPLEMENTATION SLICES

The implementation of Phase 15.5 must be decomposed into 7 strictly ordered, independently verifiable vertical slices:

```text
Slice A: Protocol Abstractions & Core Boundaries
  └── core/space/memory_protocol.py: EmbeddingProviderProtocol, SemanticQuery, bounds, list_experiences bounding

Slice B: Hermetic Deterministic Embedding & Similarity Engine
  └── memory/embeddings/: DeterministicMockEmbeddingProvider, cosine distance, quantized tie-breaker

Slice C: In-Memory Adapter Semantic Extension (Hermetic CI Tier)
  └── memory/adapters/in_memory.py: Vector indexing, multi-prong candidate pre-filtering, Top-K

Slice D: PostgreSQL Migration & Durable Adapter Extension
  └── deploy/migrations/009_*.sql & memory/adapters/postgres.py: Composite index, vector storage

Slice E: Adaptation Layer & Convergence Integration
  └── core/memory/adaptation.py: Semantic query dispatch, fingerprint boosting, hint limits

Slice F: Graceful Degradation & Timeout Hardening
  └── Timeout guards, fallback pathways, failure containment (SCCA Law 6)

Slice G: Adversarial Matrix & Verification Harness
  └── memory/tests/test_phase15_5_semantic_memory.py (MEM-SEM-ADV-01..20)
```

---

## 28. ACCEPTANCE CRITERIA & EVIDENCE CLASSIFICATION

| Requirement | Contract | Implementation Boundary | Test Verification | Target Evidence Status |
|:---|:---|:---|:---|:---:|
| Semantic retrieval returns semantically related experiences | `MEM-SEM-002` | `memory/adapters/in_memory.py` | Cosine similarity query test | `UNIT_VERIFIED` |
| Retrieval is strictly Space-scoped | `MEM-SEM-001` | `SpaceMemoryProtocol` | `MEM-SEM-ADV-01` cross-space test | `UNIT_VERIFIED` |
| Candidate set is bounded ($C \le 50$) via multi-prong strategy | `MEM-SEM-001` | Memory adapters | Bounded query test | `UNIT_VERIFIED` |
| `list_experiences()` bounded with pagination default | `MEM-SEM-001` | Memory adapters | Bounded list query test | `UNIT_VERIFIED` |
| Top-$K$ hints strictly bounded ($K \le 5$) | `MEM-SEM-005` | `AdaptationLayer` | Top-$K$ ceiling test | `UNIT_VERIFIED` |
| Deterministic tie-breaking guaranteed | `MEM-SEM-002` | Ranking logic | 100-run tie-collision test | `UNIT_VERIFIED` |
| Provenance metadata preserved | `PROVENANCE-001` | `ExperienceRecord` | Provenance round-trip test | `UNIT_VERIFIED` |
| Prompt injections sanitized and neutralized | `ADAPT-004` | `ExecutionExperienceObserver` | `MEM-SEM-ADV-03` injection test | `UNIT_VERIFIED` |
| Memory cannot mutate plans directly | SCCA Law 5 | `AdaptationLayer` | Zero plan mutation test | `UNIT_VERIFIED` |
| Embedding failure degrades gracefully | `MEM-SEM-004` | `AdaptationLayer` | Timeout & outage fallback test | `UNIT_VERIFIED` |
| Concurrent writes do not corrupt state | `ADAPT-001` | Memory adapters | Multi-threaded race test | `UNIT_VERIFIED` |
| Historical replay does not query live memory | SCCA Determinism | `ConvergenceEngine` | Replay zero-query test | `UNIT_VERIFIED` |
| Core Boundary Rule intact (`dep_guard`) | `AGENTS.md §7` | `core/` vs `memory/` | `scripts/dep_guard.py` | `GATE_VERIFIED` |

---

## 29. EXPLICIT NON-GOALS

The following capabilities are **STRICTLY PROHIBITED** from Phase 15.5:
1. **Finding F-06 (Durable Convergence State):** No new tables or migrations for convergence attempt persistence.
2. **Finding F-07 (Agent Hierarchy & Dynamic Delegation):** No agent delegation, subagent nesting, or swarm coordination.
3. **External Qdrant / Neo4j Clusters:** No external vector database containers or network services.
4. **Autonomous Self-Modification:** Memory hints cannot rewrite core rules, bypass governance, or reconfigure permissions.
5. **Global Shared Memory Without Human Gate:** No un-promoted cross-space memory sharing.
6. **Online Model Fine-Tuning / Weight Updating:** Zero neural weight training or parameter fine-tuning.

---

## 30. RISK REGISTRY

| Risk ID | Severity | Probability | Risk Description | Mitigation Strategy |
|:---:|:---:|:---:|:---|:---|
| **R-01** | **P1** | High | PostgreSQL/Docker unavailable in local offline dev environment | Implement 100% hermetic `InMemoryMemoryAdapter` with vector similarity; guard Postgres tests with `RYU_INTEGRATION_TESTS` |
| **R-02** | **P1** | Medium | External Ollama embedding service latency causing scheduler slowdown | Enforce strict 500ms timeout and background non-blocking ingestion; mock provider for tests |
| **R-03** | **P2** | Medium | Floating-point similarity score discrepancies across platforms | Round similarity scores to 4 decimal places before ranking; deterministic tie-breakers |
| **R-04** | **P2** | Low | Index size growth in long-running spaces | Enforce operational per-space 1,000 record retention ceiling with FIFO pruning |
| **R-05** | **P3** | Low | Code duplication between in-memory and Postgres ranking | Extract pure ranking algorithm into shared helper in `memory/retrieval/` |

---

## 31. GOVERNANCE & CONTRACT TRACEABILITY

### Contract Updates Required (Upon Moving to Implementation)
1. Add `MEM-SEM-001` through `MEM-SEM-005` to `contracts/registry/` and `docs/CONTRACT_MATRIX.md`.
2. Map new contracts in `harness/spec_map.yaml`.
3. File ADR-0049: `adr/0049-bounded-semantic-memory-retrieval.md`.
4. Create Project Memory entry upon phase completion: `PROJECT_MEMORY/0031-phase-15-5-semantic-memory-retrieval.md`.

---

## 32. FINAL GATE & ARCHITECTURAL CONCLUSION

### Gate Verdict
```text
============================================================
FINAL GATE: READY WITH CONDITIONS
============================================================
```

### Explicit Conditions for Implementation:
1. **Condition 1 (Hermetic In-Memory Foundation):** Implementation must provide a complete, standalone, hermetic vector similarity index inside `InMemoryMemoryAdapter` using standard Python math ($L_2$-normalized dot product), ensuring all unit tests and CI suites run with zero Docker or network dependencies.
2. **Condition 2 (Decoupled Embedding Protocol):** Core must define `EmbeddingProviderProtocol` without importing `llm/` or third-party ML packages. A deterministic mock provider must be used for testing; live HTTP providers (Ollama) must be optional and non-blocking. Provider implementations reside strictly outside core.
3. **Condition 3 (Multi-Prong Bounded Candidate Generation):** Candidate generation must enforce $C_{max} \le 50$ via multi-prong selection (fingerprint $\rightarrow$ capability/error $\rightarrow$ recency) before semantic scoring. The system must not materialize full Space histories. Top-$K$ hints must be bounded ($K \le 5$).
4. **Condition 4 (Graceful Fallback Invariant):** Any embedding or vector retrieval failure must immediately fall back to `CONTINUE_WITHOUT_MEMORY` or metadata filtering. Memory failure must NEVER block task execution, lease allocation, or plan convergence.
5. **Condition 5 (Zero Expansion Beyond F-05):** Do not implement F-06 (Durable Convergence State), F-07 (Agent Hierarchy), or external Qdrant/Neo4j infrastructure.

---

### Core Question Conclusion

> *"Can Ryu acquire and retrieve relevant historical experience semantically, at bounded cost, inside the correct Space, with trustworthy provenance and deterministic behavior, while memory remains advisory and never becomes a hidden second execution authority?"*

**Architectural Evaluation:**  
**YES, subject to implementation and verification.**  
By maintaining the protocol inversion boundary in `core/space/memory_protocol.py`, implementing Space-partitioned vector indexing with multi-prong candidate bounds ($C \le 50$) and Top-$K$ ceilings ($K \le 5$), enforcing quantized deterministic tie-breaking, treating memory hints strictly as advisory inputs to `ConvergenceProposal`, and skipping live retrieval during replay, RYU AI defines a bounded, Space-scoped, provenance-aware, and replay-compatible semantic retrieval architecture.
