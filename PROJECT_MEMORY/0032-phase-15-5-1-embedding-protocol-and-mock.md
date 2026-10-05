# Project Memory: 0032 — Phase 15.5.1 Embedding Protocol + Deterministic Mock

**Date:** 2026-10-05  
**Author:** Ryu Autonomous Core Agent  
**Baseline:** Phase 15.5.0 Governance Verified (`3c8ed39`)  
**Status:** COMPLETE (GATE-15.5.1: VERIFIED WITH EXPLICIT LIMITATIONS)  
**Governing ADR:** ADR-0049 (Semantic Memory and Experience Retrieval Governance)  
**Governing Contracts:** MEM-SEM-003 (Decoupled Embedding Boundary)  
**Governing Architecture:** Space-Centric Cognitive Architecture (SCCA)  

---

## 1. Context & Purpose

Phase 15.5.0 established the formal governance foundation (ADR-0049, Section 30I of `CONTRACT_MATRIX.md`, and 5 canonical contracts `MEM-SEM-001` through `MEM-SEM-005`).

Phase 15.5.1 implements the **Embedding Abstraction Boundary and Hermetic Deterministic Mock Provider**:
- In accordance with SCCA Law 1, Law 2, and the Core Boundary Rule (`AGENTS.md §7`), the deterministic core must never couple to heavy ML runtimes (such as PyTorch, Sentence-Transformers, fastembed, or NumPy) or external services (such as Ollama or vector databases).
- To enable hermetic testing, deterministic vector ranking, and offline replay, `core/space/memory_protocol.py` defines the canonical, provider-independent protocol abstractions and immutable result models using standard-library types only.
- Concrete providers reside strictly outside core under `memory/embeddings/`. The hermetic `DeterministicMockEmbeddingProvider` generates reproducible, L2-normalized 128-dimensional vectors derived from SHA-256 counter digests of canonicalized text.

---

## 2. What Changed

1. **Core Memory Protocol Extensions (`core/space/memory_protocol.py`):**
   - Defined `EmbeddingProviderProtocol` using standard-library typing only:
     - Properties: `model_name -> str`, `dimension -> int`, `version -> str`.
     - Methods: `embed(text: str) -> EmbeddingResult`, `embed_batch(texts: list[str]) -> list[EmbeddingResult]`.
   - Defined `EmbeddingResult` as a frozen, immutable dataclass:
     - Fields: `vector: tuple[float, ...]`, `model: str`, `dimension: int`, `version: str`.
     - Defensive validation: enforces dimension match, rejects `NaN` and `Inf`, verifies positive dimensions, enforces non-empty model and version.
     - Deterministic serialization: `to_dict() -> dict[str, Any]` and `@classmethod from_dict(data) -> EmbeddingResult`.
     - Vector helper: `norm() -> float` computing Euclidean L2 norm.
   - Defined canonical input normalization:
     - `normalize_embedding_input(text, max_chars=2048, fail_on_oversized=False) -> str`:
       - Unicode NFKC normalization (`unicodedata.normalize`).
       - Whitespace collapsing (`" ".join(text.split())`).
       - Non-empty / non-whitespace validation (raises `ValueError`).
       - Bounded length clamping / fail-closed error with trailing whitespace stripping (`.rstrip()`), guaranteeing normalization idempotency.

2. **Deterministic Mock Provider (`memory/embeddings/deterministic_mock.py`):**
   - Implemented `DeterministicMockEmbeddingProvider` adhering strictly to `EmbeddingProviderProtocol`.
   - Generates 128-dimensional L2-normalized unit vectors ($\sum v_i^2 = 1.0$) via counter-mode SHA-256 blocks.
   - 100% hermetic, offline, fast, cross-process reproducible, and free of ML framework dependencies.
   - Enforces batch bounds ($B_{max} \le 16$) and preserves exact input order.
   - Exposes `compute_similarity(res1, res2) -> float` for cosine similarity via dot product.

3. **Package Wiring (`memory/embeddings/__init__.py`, `memory/__init__.py`):**
   - Exported `DeterministicMockEmbeddingProvider` and `compute_similarity`.

4. **Dedicated Verification Suite (`memory/tests/test_phase15_5_1_embeddings.py`):**
   - Created 25 comprehensive unit and adversarial tests covering EMB-001 through EMB-018:
     - Protocol conformance, single-call determinism, cross-process determinism via Python subprocess, batch ordering, dimension enforcement, L2 norm unit tolerance, metadata integrity, empty input rejection, oversized input bounding, Unicode equivalence, whitespace canonicalization, NaN/Inf rejection, invalid dimension rejection, batch limits, zero network access, zero ML dependencies, dep_guard compliance, and serialization round-trip.
     - Adversarial cases: non-string input, duplicate batch items, zero-norm degenerate vector fallback, negative zero float components, model/dimension mismatch in similarity computation.

---

## 3. What Was NOT Changed / Implemented (Strict Boundaries)

To preserve the sequential roadmap integrity:
- **Zero PostgreSQL / vector storage changes:** No database migrations, no pgvector extension, no vector columns.
- **Zero semantic retrieval algorithms:** Multi-prong candidate selection ($C_{max} \le 50$) and top-$K$ ranking ($K_{max} \le 5$) deferred to Phase 15.5.3.
- **Zero AdaptationLayer modifications:** `generate_hints()` remains untouched.
- **Zero ConvergenceEngine modifications:** Replanning logic remains untouched.
- **Zero pulse additions:** Authoritative pulse registry maintains exactly 50 pulse types.
- **Zero external network access:** No Ollama runtime or remote HTTP client in core.
- **Zero Git pushes:** No push to remote.

---

## 4. Verification & Audit Results

| Verification Suite | Target | Status | Metrics |
|:---|:---|:---:|:---|
| `memory/tests/test_phase15_5_1_embeddings.py` | Phase 15.5.1 Suite | **PASS** | 25 passed in 2.03s |
| `memory/tests/` | All Memory Unit Tests | **PASS** | 87 passed in 2.37s |
| `core/space/tests/` | Core Space Unit Tests | **PASS** | 134 passed in 2.76s |
| `core/orchestrator/tests/` | Core Orchestrator Unit Tests | **PASS** | 182 passed in 10.96s |
| `harness/cases/memory/` | Memory Harness Suite | **PASS** | 33 passed, 1 skipped (live pg) |
| `scripts/dep_guard.py` | Core Boundary Check | **PASS** | 0 forbidden imports in `core/` |
| `scripts/contract_sync.py` | Pulse Registry Sync | **PASS** | 50 registered types preserved |
| `scripts/v1_audit_governance.py` | V1-005 Governance Audit | **PASS** | ADR Inventory 0001..0049 complete |
| `scripts/v1_audit_spec_coverage.py` | V1-001 Spec Coverage Audit | **PASS** | 208/208 executable mappings, 0 orphans |
| `ruff check` | Code Linter | **PASS** | All checks passed |
| `mypy` | Type Checker | **PASS** | 0 type errors across modified files |

---

## 5. Contract Status Assessment

- `MEM-SEM-003` (Decoupled Embedding Boundary): **PROTOCOL IMPLEMENTATION VERIFIED (UNIT_VERIFIED)**
  - Protocol is defined in `core/space/memory_protocol.py` with zero external dependencies.
  - Concrete provider `DeterministicMockEmbeddingProvider` is implemented outside core in `memory/embeddings/`.
  - Full end-to-end integration into `AdaptationLayer` remains deferred to subsequent slices.
- `MEM-SEM-001`, `MEM-SEM-002`, `MEM-SEM-004`, `MEM-SEM-005`: Remain strictly **`ARCHITECTURAL_TARGET`**.

---

## 6. Exit Gate Status

**GATE-15.5.1: VERIFIED WITH EXPLICIT LIMITATIONS**  
*(Limitation: Hermetic mock provider verified offline; live PostgreSQL vector storage and Ollama HTTP integration are deferred to subsequent Phase 15.5 slices).*

---

## 7. Next Steps (Phase 15.5.2 Roadmap)

1. **Phase 15.5.2:** Storage & Schema Migration (PostgreSQL vector/JSONB schema, migration `009`, in-memory secondary indices).
2. **Phase 15.5.3:** Bounded Candidate Generation & Deterministic Similarity Ranking ($C_{max} \le 50$, $K_{max} \le 5$).
3. **Phase 15.5.4:** AdaptationLayer Integration & Graceful Degradation Hardening.
