# Project Memory: 0034 — Phase 15.5.3 Semantic Retrieval & Deterministic Ranking

**Date:** 2026-10-06  
**Author:** Ryu Autonomous Core Agent  
**Baseline:** Phase 15.5.2 Terminology Correction (`b4e7d25`)  
**Status:** COMPLETE (GATE-15.5.3: VERIFIED WITH EXPLICIT LIMITATIONS)  
**Governing ADR:** ADR-0049 (Semantic Memory & Experience Retrieval Governance)  
**Governing Contracts:** MEM-SEM-001 (Bounded Space-Scoped Candidate Retrieval), MEM-SEM-002 (Deterministic Semantic Similarity Ranking), MEM-SEM-003 (Decoupled Embedding Boundary)  
**Governing Architecture:** Space-Centric Cognitive Architecture (SCCA)  

---

## 1. Context & Purpose

Phase 15.5.2 established durable schema and storage for embedding-backed experience records.

Phase 15.5.3 implements **Semantic Retrieval & Deterministic Ranking** (Finding F-05, Priority P1):
- **Candidate Boundedness (MEM-SEM-001):** To prevent unbounded database scans or excessive in-memory candidate sets, candidate generation enforces a strict multi-prong retrieval envelope with ceiling $C_{max} \le 50$:
  - Prong A: Exact failure fingerprint matching ($\le 10$ candidates)
  - Prong B: Execution context (capability + error_class) matching ($\le 25$ candidates)
  - Prong C: Space-scoped recency matching ($\le 20$ candidates)
  - Total candidate set deduplicated and clamped to $C \le 50$.
- **Bounded Pagination (MEM-SEM-001):** `list_experiences(space_id, limit=50, before_stored_at=None)` enforces a default limit of 50 and maximum ceiling of 100 with timestamp cursor pagination.
- **Deterministic Ranking & Tie-Breaking (MEM-SEM-002):** Ranking orders candidates using pure normalized cosine similarity without synthetic score bonuses (e.g. `score + 100`). Floating-point scores are quantized via `round(score, 4)` and tie-broken by `(round(score, 4), stored_at DESC, experience_id ASC)`. Results are strictly clamped to $1 \le top\_k \le 5$.
- **Compatibility Verification:** Candidates are strictly verified for model name, version, dimension, and non-NaN/Inf float values against the query vector before similarity computation. Incompatible candidates are excluded safely.
- **Authority Boundaries Preserved:** No changes made to `AdaptationLayer` or `ConvergenceEngine` (deferred to Phase 15.5.4). Core remains completely independent of ML/vector libraries.

---

## 2. What Changed

1. **Space Memory Protocol Abstractions (`core/space/memory_protocol.py`):**
   - Implemented `compute_cosine_similarity(vec_a, vec_b) -> float`: Pure Python normalized dot product with L2 Euclidean normalization, clamping to `[-1.0, 1.0]`, and zero-norm safety.
   - Added `SemanticExperienceQuery` dataclass: Enforces non-empty `space_id` (raising `SpaceIsolationViolation`), bounds $1 \le top\_k \le 5$, and validates finite `min_similarity` in `[-1.0, 1.0]`.
   - Added `ScoredExperienceRecord` dataclass: Immutable scored representation with `record`, `similarity_score`, `rank` (1-indexed, $1 \le rank \le 5$), and `exact_fingerprint_match: bool`.
   - Extended `SpaceMemoryProtocol` with:
     - `list_experiences(space_id, limit=50, before_stored_at=None) -> list[ExperienceRecord]`
     - `retrieve_semantic_experiences(query, embedding_provider=None) -> list[ScoredExperienceRecord]`.

2. **Semantic Retrieval Engine (`memory/retrieval/`):**
   - Created `memory/retrieval/__init__.py` exporting ranker utilities.
   - Created `memory/retrieval/ranker.py`:
     - `resolve_query_embedding(query, provider)`: Validates supplied query vector components are finite numbers, or embeds normalized text via `provider.embed()`.
     - `is_embedding_compatible(candidate, query_emb)`: Verifies non-null embedding, matching model name, matching model version, matching dimension, and finite float items.
     - `deterministic_rank_candidates(candidates, query_emb, top_k=5, min_similarity=0.0, target_fingerprint=None)`: Filters compatible candidates, computes cosine similarity, filters threshold, sorts with canonical tie-breaking key `(-round(score, 4), -stored_at.timestamp(), rec.experience_id)`, clamps output to $K \le 5$, and sets `exact_fingerprint_match`.

3. **In-Memory Storage Adapter (`memory/adapters/in_memory.py`):**
   - Added thread-safe secondary indexes: `_fingerprint_idx`, `_capability_idx`, `_error_class_idx`, and `_recency_idx`.
   - Implemented multi-prong candidate generation `_get_semantic_candidates(query)` bounded to $C \le 50$.
   - Hardened `list_experiences` with default limit 50, maximum limit 100, and timestamp pagination cursor.
   - Implemented `retrieve_semantic_experiences(query, embedding_provider=None)`.

4. **PostgreSQL Storage Adapter (`memory/adapters/postgres.py`):**
   - Implemented multi-prong parameterized SQL candidate generation:
     - Prong A: `WHERE space_id = %s AND failure_fingerprint = %s ORDER BY stored_at DESC LIMIT 10`
     - Prong B: `WHERE space_id = %s AND situation->>'capability' = %s AND applicable_context->>'error_class' = %s ORDER BY stored_at DESC LIMIT 25`
     - Prong C: `WHERE space_id = %s ORDER BY stored_at DESC LIMIT 20`
     - Result merged and clamped to $C \le 50$.
   - Hardened `list_experiences` with parameterized timestamp filtering and bounded limits.
   - Implemented `retrieve_semantic_experiences(query, embedding_provider=None)`.

5. **Stub Adapter Signatures (`memory/adapters/qdrant_stub.py`, `memory/adapters/neo4j_stub.py`):**
   - Updated protocol signatures to maintain complete interface parity.

6. **Contract Matrix & Spec Map Governance:**
   - Updated `docs/CONTRACT_MATRIX.md`: Marked `MEM-SEM-001`, `MEM-SEM-002`, and `MEM-SEM-003` as `UNIT_VERIFIED`.
   - Updated `harness/spec_map.yaml`: Updated `evidence_state` and test mappings for `MEM-SEM-001`, `MEM-SEM-002`, and `MEM-SEM-003`.
   - Updated `harness/cases/memory/test_phase15_5_contracts_governance.py` to allow `UNIT_VERIFIED` status in contract matrix checks.

---

## 3. What Was Verified

### Automated Test Suites
1. **Unit & Pipeline Tests (`memory/tests/test_phase15_5_3_retrieval.py`):**
   - 22 passed: Cosine similarity mathematics, vector compatibility checking, deterministic ranking and tie-breaking, threshold filtering, multi-prong candidate bounded generation, Space isolation, bounded pagination, and mocked PostgreSQL retrieval.
2. **Adversarial Verification Suite (`memory/tests/test_phase15_5_3_adversarial.py`):**
   - 20 passed covering `SEM-RET-ADV-01` through `SEM-RET-ADV-20`:
     - Cross-space memory traversal isolation
     - Empty/blank `space_id` rejection with `SpaceIsolationViolation`
     - Invalid `top_k` bounds ($< 1$ or $> 5$) rejection
     - Non-finite `min_similarity` rejection
     - Vector dimension mismatch handling
     - Model name and version mismatch handling
     - Non-finite (NaN/Inf) query and candidate vector handling
     - Null/empty candidate embedding handling
     - 100-run tie-collision stability by `experience_id ASC`
     - 4-decimal score rounding quantization
     - Candidate set $C_{max} \le 50$ ceiling enforcement
     - Top-$K$ output $K_{max} \le 5$ ceiling enforcement
     - Threshold filtering
     - Unbounded pagination protection ($limit \le 100$)
     - Exact fingerprint match flagging without score modification
     - Zero-norm vector degenerate handling
     - 20-thread concurrent query and write safety
     - Parameterized SQL injection defense
3. **Memory Regression Suite:**
   - 152/152 passed in `memory/tests/`.
4. **Full Regression Suite:**
   - 501 passed, 1 skipped (live PostgreSQL integration test requiring `RYU_INTEGRATION_TESTS=1`).

### Governance Audits
- `scripts/dep_guard.py`: PASS (0 forbidden imports from `core/`).
- `scripts/contract_sync.py`: PASS (All 38 architecture types present in registry).
- `scripts/v1_audit_spec_coverage.py`: PASS (V1-001 PASS, 0 orphaned criteria, 0 missing tests, 0 stale evidence).
- `scripts/v1_audit_governance.py`: PASS (V1-005 PASS, ADR inventory, contract matrix, pulse registry sync).
- `ruff check`: PASS (0 lint or style errors).
- `mypy`: PASS (0 issues found across all modified files).

---

## 4. Explicit Limitations

- **Hermetic / Mock PostgreSQL Verified:** PostgreSQL candidate generation queries and parameterization were verified with unit and mock tests. Live database execution against a running PostgreSQL container requires `RYU_INTEGRATION_TESTS=1`.
- **AdaptationLayer Integration Deferred:** Integration of semantic retrieval into `AdaptationLayer.generate_hints()` and `ConvergenceEngine` replanning is strictly deferred to Phase 15.5.4.
- **Advisory Experience Hints Deferred:** Generation and bounding of `ExperienceHint` objects (`MEM-SEM-004`, `MEM-SEM-005`) are deferred to Phase 15.5.4.

---

## 5. Next Steps

- **Phase 15.5.4:** Adaptation Layer & Convergence Integration
  - Integrate `retrieve_semantic_experiences` into `AdaptationLayer.generate_hints()`.
  - Implement graceful degradation fallback when embedding provider or retrieval fails (`MEM-SEM-004`).
  - Implement bounded advisory hint generation ($K \le 5$) with immutable advisory semantics (`MEM-SEM-005`).

