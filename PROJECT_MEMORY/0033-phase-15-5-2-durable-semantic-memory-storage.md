# Project Memory: 0033 — Phase 15.5.2 Durable Semantic Memory Storage + Schema

**Date:** 2026-10-05  
**Author:** Ryu Autonomous Core Agent  
**Baseline:** Phase 15.5.1 Embedding Protocol Verified (`0141528`)  
**Status:** COMPLETE (GATE-15.5.2: VERIFIED WITH EXPLICIT LIMITATIONS)  
**Governing ADR:** ADR-0049 (Semantic Memory & Experience Retrieval Governance)  
**Governing Contracts:** MEM-SEM-001 (Bounded Space-Scoped Candidate Retrieval), MEM-SEM-003 (Decoupled Embedding Boundary)  
**Governing Architecture:** Space-Centric Cognitive Architecture (SCCA)  

---

## 1. Context & Purpose

Phase 15.5.1 established the abstract `EmbeddingProviderProtocol`, validated `EmbeddingResult` model, and hermetic deterministic mock provider.

Phase 15.5.2 implements the **Durable Semantic Memory Storage & Schema Substrate**:
- To enable persistent storage of embedding vectors and retrieval metadata across process restarts without coupling to external vector service clusters, the database schema and storage adapters must support structured embedding persistence.
- In accordance with ADR-0049 and the Phase 15.5 Architecture Audit, this phase explicitly preserves the distinction between **durable JSONB/serialized vector storage** and **indexed vector search**.
- Storage remains strictly Space-scoped (SCCA Law 1, Law 4). An experience stored in Space A is strictly inaccessible to Space B.
- All historical experiences without embeddings remain 100% backward-compatible (nullable schema columns).
- The storage write path persists embeddings provided by callers or providers; the persistence layer **never** invokes embedding providers or models implicitly.
- The PostgreSQL embedding JSONB column and supporting indexes serve strictly as durable storage and candidate-generation support infrastructure; they do NOT provide vector search indexes, semantic indexes, or vector database functionality.

---

## 2. What Changed

1. **Database Migration (`deploy/migrations/009_add_semantic_embeddings_to_space_experiences.sql`):**
   - Added nullable columns to `space_experiences`:
     - `embedding JSONB DEFAULT NULL`
     - `embedding_model VARCHAR(100) DEFAULT NULL`
     - `embedding_dimension INTEGER DEFAULT NULL`
     - `embedding_version VARCHAR(50) DEFAULT NULL`
     - `failure_fingerprint VARCHAR(255) DEFAULT NULL`
     - `provenance_ref VARCHAR(255) DEFAULT NULL`
   - Added composite index: `idx_space_exp_space_stored` on `(space_id, stored_at DESC)` for candidate-generation and storage-support recency ordering.
   - Added partial index: `idx_space_exp_fingerprint` on `(space_id, failure_fingerprint)` where non-null and non-empty for candidate-generation failure fingerprint matching.

2. **Core ExperienceRecord Model Extension (`core/space/memory_protocol.py`):**
   - Extended `ExperienceRecord` dataclass with optional semantic metadata fields:
     - `embedding: tuple[float, ...] | None = None`
     - `embedding_model: str | None = None`
     - `embedding_dimension: int | None = None`
     - `embedding_version: str | None = None`
     - `failure_fingerprint: str | None = None`
     - `provenance_ref: str | None = None`
   - Added defensive validation in `__post_init__`:
     - Enforces tuple vector representation.
     - Validates positive `embedding_dimension` matching `len(embedding)`.
     - Rejects `NaN`, `+Inf`, `-Inf`, and non-float vector items.
     - Enforces non-empty model and version when embedding is present.
     - Rejects orphan `embedding_dimension` when embedding is absent.
     - Auto-extracts `failure_fingerprint` and `provenance_ref` fallback from `applicable_context`.
   - Added helper methods: `to_embedding_result() -> EmbeddingResult | None` and `with_embedding(EmbeddingResult) -> ExperienceRecord`.
   - Updated `SpaceMemoryProtocol.store_experience` to optionally accept `embedding: EmbeddingResult | None = None`.

3. **In-Memory Storage Adapter Parity (`memory/adapters/in_memory.py`):**
   - Updated `store_experience` to support attaching `EmbeddingResult`.
   - Preserves complete embedding metadata, failure fingerprint, and provenance across store and retrieval.
   - Enforces Space isolation and thread-safe mutation under `RLock`.

4. **PostgreSQL Storage Adapter Extension (`memory/adapters/postgres.py`):**
   - Updated `store_experience` to write all 14 columns atomically using parameterized SQL with `ON CONFLICT (experience_id, space_id) DO UPDATE SET ...`.
   - Updated `get_experience`, `list_experiences`, and `query_similar_experiences` queries to fetch all 14 columns.
   - Updated `_row_to_experience` to deserialize embedding JSON, floats, model, dimension, version, fingerprint, and provenance ref, while safely handling legacy 8-column rows without error.

5. **Dedicated Verification Test Suite (`memory/tests/test_phase15_5_2_storage.py`):**
   - Implemented 23 tests covering `STORAGE-001` through `STORAGE-020` and adversarial cases.

---

## 3. What Was NOT Changed / Implemented (Strict Boundaries)

To preserve the sequential execution plan of Phase 15.5:
- **Zero semantic retrieval algorithms:** Multi-prong candidate generation ($C_{max} \le 50$) and Top-$K$ ranking ($K_{max} \le 5$) deferred to Phase 15.5.3.
- **Zero AdaptationLayer modifications:** Hint generation logic remains untouched.
- **Zero ConvergenceEngine modifications:** Replanning logic remains untouched.
- **Zero pulse additions:** Authoritative pulse registry maintains exactly 50 pulse types.
- **Zero external vector databases:** Qdrant and Neo4j remain unimplemented stubs.
- **Zero remote Git pushes:** All changes staged and committed locally.

---

## 4. Verification & Audit Results

| Verification Suite | Target | Status | Metrics / Details |
|:---|:---|:---:|:---|
| `memory/tests/test_phase15_5_2_storage.py` | Phase 15.5.2 Suite | **PASS** | 23 passed in 0.43s |
| `memory/tests/` | All Memory Unit Tests | **PASS** | 110 passed in 1.39s |
| `core/space/tests/` | Core Space Unit Tests | **PASS** | 134 passed in 2.76s |
| `core/orchestrator/tests/` | Core Orchestrator Unit Tests | **PASS** | 182 passed in 3.42s |
| `harness/cases/memory/` | Memory Harness Suite | **PASS** | 33 passed, 1 skipped (live pg) |
| `scripts/dep_guard.py` | Core Boundary Check | **PASS** | 0 forbidden imports in `core/` |
| `scripts/contract_sync.py` | Pulse Registry Sync | **PASS** | 50 registered types preserved |
| `scripts/v1_audit_spec_coverage.py` | V1-001 Spec Coverage | **PASS** | 208/208 executable mappings, 0 orphaned |
| `scripts/v1_audit_governance.py` | V1-005 Governance Audit | **PASS** | ADR Inventory (0001..0049) complete |
| `ruff check` | Code Linter | **PASS** | All checks passed |
| `mypy` | Type Checker | **PASS** | Success: 0 type issues found across 4 files |

---

## 5. Contract Status Assessment

All Phase 15.5 contract definitions adhere strictly to canonical ADR-0049 definitions:
- `MEM-SEM-001` (Bounded Space-Scoped Candidate Retrieval): `ARCHITECTURAL_TARGET` (candidate generation bounded by $C_{max} \le 50$ and paginated listing deferred to Phase 15.5.3).
- `MEM-SEM-002` (Deterministic Semantic Similarity Ranking): `ARCHITECTURAL_TARGET` (similarity ranking bounded by $K_{max} \le 5$ deferred to Phase 15.5.3).
- `MEM-SEM-003` (Decoupled Embedding Boundary): **`UNIT_VERIFIED`** (protocol defined in core; mock provider implemented outside core; embedding metadata storage persistence verified).
- `MEM-SEM-004` (Graceful Semantic Retrieval Degradation): `ARCHITECTURAL_TARGET` (retrieval error containment in `AdaptationLayer` deferred to Phase 15.5.4).
- `MEM-SEM-005` (Bounded Advisory Experience Hints): `ARCHITECTURAL_TARGET` (advisory hint limits and non-authoritative injection deferred to Phase 15.5.4).

---

## 6. Exit Gate Status

**GATE-15.5.2: VERIFIED WITH EXPLICIT LIMITATIONS**  
*(Limitation: Migration syntax, SQL construction, and mocked PostgreSQL round-trip verified; live PostgreSQL container execution remains environment-limited due to local Docker daemon unavailability).*

---

## 7. Next Steps (Phase 15.5.3 Roadmap)

1. **Phase 15.5.3:** Bounded Candidate Generation & Deterministic Similarity Ranking ($C_{max} \le 50$, $K_{max} \le 5$, exact fingerprint prioritization, quantized tie-breaking).
2. **Phase 15.5.4:** AdaptationLayer Integration & Graceful Degradation Hardening.
