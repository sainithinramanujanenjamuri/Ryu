# RYU AI — Project Memory

## Entry 0012 — Phase 10 Space Memory Adapters & Adaptation Loop

**Date:** 2026-09-23  
**Phase:** 10 — Memory Adapters + Adaptation Loop  
**Status:** COMPLETE (PHASE 10 GATE: PASS)  
**Previous Baseline:** Phase 9 (Skills & MCP Extensibility Layer) — commit `355be25`

---

### Summary

Phase 10 introduces the long-term cognitive substrate of RYU AI under Space-Centric Cognitive Architecture (SCCA): Space Memory and the Adaptation Loop. It establishes experience-driven behavioral adaptation without model-weight learning, fine-tuning, or neural parameter optimization.

1. **Dependency Inversion & Storage Protocols (`core/space/memory_protocol.py`, ADR-0033):**
   - Core defines `SpaceMemoryProtocol`, `ExperienceRecord`, `KnowledgeEntry`, `PromotionAuthorization`, and typed failure exceptions (`MemoryFailure`, `SpaceIsolationViolation`).
   - One-way dependency direction (AGENTS.md §4): `core/` contains ZERO concrete memory adapter imports (enforced by `scripts/dep_guard.py` via AST).
   - Authoritative backends: `InMemoryMemoryAdapter` (hermetic unit tests) and `PostgreSQLMemoryAdapter` (durable production store).
   - Extension boundaries: `QdrantAdapterStub` and `Neo4jAdapterStub` provide typed stubs raising `NotImplementedError("spec §4 — Phase 11+")`. Neither running Qdrant nor Neo4j instances are required for the Phase 10 gate.

2. **Experience Reflection & Three-Layer Counterfactual Enforcement (ADR-0034, MEM-002, MEM-003):**
   - Mandatory `counterfactual` field enforced across three independent layers:
     - Layer 1 (Dataclass): `ExperienceRecord.__post_init__` raises `ValueError` if missing or whitespace-only.
     - Layer 2 (Pulse Validator): `PulseBus` schema validation rejects `experience.stored` without `counterfactual`.
     - Layer 3 (Relational): `deploy/migrations/005_create_memory_tables.sql` enforces `CHECK (counterfactual <> '')`.
   - `Reflector` persists experiences to `SpaceMemoryProtocol` BEFORE emitting `experience.stored` and `memory.updated` Pulses.
   - Failures raise `MemoryFailure`; no event exists if state mutation failed (Law 6).

3. **Behavioral Adaptation via Frozen Trace Benchmarking (`memory/evaluation.py`, `core/memory/adaptation.py`, MEM-004, ADR-0036):**
   - Dedicated evaluation structure: `FrozenTraceCorpus`, `FrozenTrace`, `EvaluationResult`, and `EvaluationModule` completely decoupled from `llm/LLMRecord`.
   - `AdaptationLayer` in `core/memory/adaptation.py` is strictly a read-only query layer. It CANNOT mutate plans, create `PlanDelta`, emit Pulses, or call Space Kernel.
   - Produces `ExperienceHint` dataclasses injected into `GoalSpec.metadata["experience_hints"]`.
   - `Planner` proposes alternatives based on hints, and `SpaceKernel` retains sole plan commit authority via CAS.

4. **Cryptographic Knowledge Promotion & Anti-Forgery Defense (`memory/promotion.py`, MEM-005, MEM-006, ADR-0035):**
   - Direct writes to global knowledge are prohibited.
   - `PromotionPipeline` is bound to an immutable `requesting_space_id` verified via `SpaceKernel.verify_space_identity()`. Cross-space promotions are rejected pre-dispatch.
   - Human Gate authority: Integrates directly with `SpaceKernel.approval_mgr` (`ApprovalManager`). Rejects empty, unknown, unauthenticated, or unauthorized approvers.
   - Cryptographic capability token (`PromotionAuthorization`): signed using the Kernel's decision signing key (`compute_promotion_signature`).
   - Single-use and anti-replay: Consumed tokens are recorded in `_consumed_promotions` and `promotion_audit`. Replayed tokens raise `PermissionError`.
   - Anti-tampering: blake2b hash of the `ExperienceRecord` is verified during `approve()` to detect post-evaluation tampering.
   - Out-of-band forged approved Pulses are caught by `handle_unauthorized_approved_pulse()` and audited.

---

### Architectural Invariants & Verification Evidence

1. **Law 1 — Everything Happens Inside a Space:**
   - Memory storage and queries are strictly Space-local. Space A cannot access Space B's records (`MEM-001`).
   - Artifacts and memory records remain isolated (`SPACE-004`).
2. **Law 2 — Capabilities Are Requested, Never Owned:**
   - AdaptationLayer cannot grant permissions or acquire resources.
3. **Law 3 — Components Communicate Through Pulses:**
   - Memory updates emit typed `experience.stored`, `memory.updated`, `knowledge.promotion.requested`, `knowledge.promotion.approved`, and `knowledge.promotion.rejected` Pulses.
4. **Law 4 — Knowledge Belongs to the Space First:**
   - Knowledge originates in the owning Space (`ARC-004`).
   - Cross-space promotion requires explicit EvaluationModule benchmark and authenticated human approval (`MEM-005`, `MEM-006`).
5. **Law 5 — Humans Define Goals; Ryu Organizes Execution:**
   - Global knowledge promotion requires authenticated human approval via `ApprovalManager`.
6. **Law 6 — Failures Are Contained, Escalated, and Never Silent:**
   - Memory failures raise typed `MemoryFailure` exceptions and are never silently swallowed as `[]` or `None`.

---

### Verification Summary

- [x] Full test suite: **604 passed**, **11 skipped**, **0 failed** (regression free, +64 new Phase 10 tests)
- [x] Un-skipped `test_space_artifact_isolation` in `harness/cases/space/test_space_future.py`: **PASS** (`SPACE-004`)
- [x] All 14 adversarial security attack cases: **PASS** (`test_promotion_pipeline.py`)
- [x] Dependency Guard: **PASS** (`scripts/dep_guard.py` confirms zero forbidden imports in `core/`)
- [x] Contract Sync: **PASS** (`scripts/contract_sync.py` verifies all 38 Pulse types)
- [x] Linting: **PASS** (`ruff check` clean across core, memory, and harness)
- [x] Type checking: **PASS** (`mypy` clean across 19 source files)
- [x] Rust Node Runtime: **PASS** (`cargo check` clean)
- [x] Database Schema: `deploy/migrations/005_create_memory_tables.sql` created
- [x] ADRs: ADR-0033, ADR-0034, ADR-0035, ADR-0036 written and accepted
- [x] Traceability: `harness/spec_map.yaml` updated with `MEM-001..006`, `ARC-004`, `SPACE-004` marked `GATE_VERIFIED`
