# Project Memory: 0025 — Phase 14.7 Bounded Research Synthesis & Evidence Reconciliation

**Date:** 2026-10-02  
**Author:** Ryu Autonomous Core Agent  
**Baseline:** Phase 14.6 Bounded Test-Repair Loop (`5452677`, doc hardening `bcfa7a7`)  
**Status:** COMPLETE (GATE-14.7: PASS)  
**Governing ADR:** ADR-0044  
**Governing Architecture:** Space-Centric Cognitive Architecture (SCCA)  

---

## 1. Context & Purpose

Phase 14.2 established bounded, read-only research retrieval (`ResearchWorker`, `BoundedSourceRetriever`).  
Phase 14.7 establishes the bounded, provenance-preserving research synthesis and evidence reconciliation layer (`RESEARCH-001..005`, `PROVENANCE-001..003`).

Phase 14.7 answers the architectural question:  
*"When RYU retrieves multiple external research sources into a Space, can RYU deterministically extract factual claims, detect agreements and contradictions without silent tie-breaking or model hallucination, bind strict cryptographic provenance to source byte hashes, and return structured evidence to the Space without granting research any direct authority over execution plans, files, or capabilities?"*

### Implemented Contracts
- **`RESEARCH-001` (Research Source Allowlist Enforcement):** Whitelist enforcement via `DefaultDenySourcePolicy`. Only approved domains, schemes, and path prefixes are accessible; unapproved targets and arbitrary web crawling are rejected pre-dispatch.
- **`RESEARCH-002` (Research Provenance & Transformation Tracking):** Every extracted claim and synthesized artifact links cryptographically via `provenance_ids` and SHA-256 content hashes to source inputs, task ID, and plan version. The transformation chain (`RETRIEVED -> EXTRACTED -> SYNTHESIZED`) is preserved end-to-end.
- **`RESEARCH-003` (Research Content Sanitization & Mandatory Taint):** All external research content is treated as untrusted and enters the runtime with `taint: True`. Adversarial prompt injection, shell command execution patterns, and instruction override vectors are neutralized as passive text. Any downstream synthesis inherits `taint: True` (`TaintLaunderingViolation` on attempted clearance).
- **`RESEARCH-004` (Research Conflict State Detection):** Opposing, mutually exclusive factual claims from different sources trigger an explicit `SynthesisStatus.CONTRADICTION` with zero confidence (`confidence = 0.0`), anti-reflexive relation tracking (`EvidenceRelationType.CONTRADICTS`), and pulse bus notification (`research.conflict_detected`). No automated tie-breaking, model hallucination, or premature goal satisfaction is permitted.
- **`RESEARCH-005` (Research Multi-Stage Artifact Synthesis):** Multi-stage synthesis produces structured audit reports (`{task_id}_synthesis_report.json`) and Markdown summaries (`{task_id}_synthesis_summary.md`) that link the full parent transformation chain back to raw source file bytes and SHA-256 hashes.
- **`PROVENANCE-001` (Transformation Chain Auditability):** Full auditability from synthesis claim to intermediate extraction note down to raw source bytes and cryptographic hashes via `verify_synthesis_provenance`.
- **`PROVENANCE-002` (Research & Artifact Source Immutability):** Source location metadata, SHA-256 hashes, and extraction spans are frozen upon retrieval in immutable dataclasses. Any post-retrieval tampering is deterministically detected and rejected during verification.
- **`PROVENANCE-003` (Cross-Space Provenance Isolation):** Provenance records, claims, and synthesis artifacts cannot reference entities belonging to another Space without formal promotion grant. Verified via `verify_space_identity` and `verify_synthesis_provenance` (`SPACE-001`).

### Strict Scope Invariants
- **Synthesis is an Information Transformation, NOT an Authority Transformation:** Synthesis output cannot directly mutate Plan, TaskGraph, files, budgets, or capabilities.
- **Core Independence Boundary (AGENTS.md §7):** `core/space/research_protocol.py` contains all synthesis data models and verification algorithms with zero imports from `workers/`, `agents/`, `skills/`, `workflows/`, `llm/`, or `memory/`. Verified via `scripts/dep_guard.py`.
- **Model Subordination:** LLM/model claims are strictly advisory (`is_model_assertion=True`). Models cannot override verified contradictions, invent citations, or bypass architectural ceilings.
- **Strict Synthesis Ceilings:** Validated against `MAX_SOURCES_PER_SYNTHESIS = 10`, `MAX_CLAIMS_PER_SYNTHESIS = 50`, `MAX_EVIDENCE_ITEMS = 50`, `MAX_SYNTHESIS_INPUT_BYTES = 512 KB`, `MAX_SYNTHESIS_DEPTH = 5`, `MAX_CONFLICT_RELATIONSHIPS = 50`, `MAX_SYNTHESIS_OUTPUT_BYTES = 1 MB`.

---

## 2. What Changed

1. **Core Research Protocol Extensions (`core/space/research_protocol.py`):**
   - Added synthesis ceiling constants (`MAX_SOURCES_PER_SYNTHESIS`, `MAX_EVIDENCE_ITEMS`, `MAX_CLAIMS_PER_SYNTHESIS`, `MAX_SYNTHESIS_INPUT_BYTES`, `MAX_SYNTHESIS_DEPTH`, `MAX_CONFLICT_RELATIONSHIPS`, `MAX_SYNTHESIS_OUTPUT_BYTES`).
   - Added typed enums: `SynthesisStatus` (`AGREEMENT`, `PARTIAL_AGREEMENT`, `CONTRADICTION`, `INSUFFICIENT_EVIDENCE`, `UNRELATED`) and `EvidenceRelationType` (`SUPPORTS`, `CONTRADICTS`, `QUALIFIES`, `DUPLICATES`, `DERIVED_FROM`).
   - Added frozen data models:
     - `ResearchClaim`: Immutable claim with text, canonical hash, confidence, supporting provenance IDs, source IDs, and advisory `is_model_assertion` flag.
     - `EvidenceRelation`: Immutable relationship between claims with anti-reflexive contradiction check.
     - `ResearchSynthesis`: Multi-source synthesis root with status, confidence, claims, relations, source hashes, space ID, and mandatory taint propagation.
   - Added security exceptions: `SynthesisLimitExceededError`, `ModelAssertionSubordinationError`, `TaintLaunderingViolation`.
   - Added cryptographic audit function: `verify_synthesis_provenance(synthesis, expected_space_id, known_provenance_records)`.

2. **Research Synthesizer Engine (`workers/research/synthesis.py`):**
   - Created `ResearchSynthesizer` and `SynthesisConfig`.
   - Implemented polarity and antonym contradiction detection (`_detect_polarity`, `_has_antonym_contradiction`) detecting opposing factual claims across sources without tie-breaking.
   - Implemented prompt injection and evasive text neutralization.
   - Implemented model assertion subordination verifying citations and preventing contradiction suppression.
   - Implemented offline deterministic synthesis and artifact serialization.

3. **Research Worker Extension (`workers/research/worker.py`):**
   - Extended `ResearchWorker` with `synthesizer: ResearchSynthesizer`.
   - Extended sandboxed execution dispatch: routes `capability == "research.synthesize"` or multi-source inputs to `_execute_synthesis`.
   - Generates provenance with `TransformationStage.SYNTHESIZED` and `EvidenceRelationship.SYNTHESIZED_FROM`.
   - Persists artifacts: `{task_id}_synthesis_report.json` and `{task_id}_synthesis_summary.md`.
   - Emits `research.conflict_detected` pulse on pulse bus when `status == SynthesisStatus.CONTRADICTION`.

4. **Dedicated Verification Suites (`core/space/tests/test_research_protocol.py` & `workers/tests/test_phase14_7_research_synthesis.py`):**
   - 35 unit tests in protocol suite.
   - 36 integration and security tests in synthesis suite covering Slices A through J and 25 Adversarial Vectors (ADV-01..ADV-25).

5. **Contract Matrix Synchronization (`docs/CONTRACT_MATRIX.md`):**
   - Updated `RESEARCH-001..005` and `PROVENANCE-001..003` to `INTEGRATION_VERIFIED`.

---

## 3. What Was Verified

### Test Counts & Execution Metrics (Zero Double Counting)

- **Dedicated Phase 14.7 Tests:**
  - `core/space/tests/test_research_protocol.py`: 35 passed, 0 skipped, 0 failed.
  - `workers/tests/test_phase14_7_research_synthesis.py`: 36 passed, 0 skipped, 0 failed.
  - **Total Dedicated Phase 14.7 Tests:** **71 passed**, 0 skipped, 0 failed.

- **Full Research Suite (Retrieval + Synthesis):**
  - `core/space/tests/test_research_protocol.py`: 35 passed.
  - `workers/tests/test_phase14_2_research_worker.py`: 34 passed.
  - `workers/tests/test_phase14_7_research_synthesis.py`: 36 passed.
  - **Combined Research Suite Total:** **105 passed**, 0 skipped, 0 failed.

- **Full Regression Test Suites:**
  - `core` & `workers` suites: 735 passed, 1 skipped, 0 failed (contains the 71 dedicated tests).
  - `harness` suite: 325 passed, 12 skipped, 0 failed.
  - **Combined Regression Suite Total:** **1,060 passed**, 13 skipped, 0 failed.

### Architecture & Governance Gates
- `scripts/dep_guard.py`: PASS (0 forbidden imports in `core/`).
- `scripts/contract_sync.py`: PASS (All contracts in sync).
- `ruff check core workers`: PASS (0 lint errors).
- `mypy core/space/research_protocol.py workers/research`: PASS (0 type errors in 6 source files).
- `cargo check --manifest-path node_runtime\Cargo.toml`: PASS (Rust node runtime compiled cleanly in 0.04s).
- `scripts/v1_audit_governance.py`: PASS (ADR 0001..0044, 1:1 schema coverage, contract matrix integrity).
- `scripts/v1_audit_spec_coverage.py`: PASS (161 criteria, 224 contracts, 0 orphaned entries).

---

## 4. What Remains / Next Steps

Phase 14.7 concludes the bounded research synthesis and evidence reconciliation capability.  
The next and final phase in the Phase 14 roadmap is:
- **Phase 14.8: Integrated Autonomous Software Engineer (End-to-End Task Lifecycle).**
  - Connects the complete research, repository inspection, atomic patching, sandboxed test execution, test-repair loop, and synthesis capabilities into an integrated goal-driven autonomous workflow governed strictly by SCCA and human oversight gates.
