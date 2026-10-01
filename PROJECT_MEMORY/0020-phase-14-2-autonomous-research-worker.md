# Project Memory: 0020 — Phase 14.2 Autonomous Research Worker & Evidence Collection

**Date:** 2026-10-01  
**Author:** Ryu Autonomous Core Agent  
**Baseline:** Phase 14.1 (`c27ae80`)  
**Status:** COMPLETE (GATE-14.2: PASS)  
**Governing ADR:** ADR-0044  
**Governing Architecture:** Space-Centric Cognitive Architecture (SCCA)  

---

## 1. Context & Purpose

Phase 14.1 established the domain-neutral protocol and cryptographic provenance foundation (`core/space/research_protocol.py`).
Phase 14.2 turns that protocol into a real, bounded, capability-controlled execution capability (`workers/research/`), subordinating research retrieval to the existing `WorkerInvoker`, `DeterministicDispatcher`, `SpaceKernel`, `AdmissionControl`, and `ResourceManager` architectures.

## 2. What Changed

1. **Network Security & SSRF Defense (`workers/research/security.py`):**
   - Scheme enforcement: strictly `http://` and `https://` (unsupported schemes like `file://` rejected).
   - Credential leakage detection: basic auth credentials (`user:pass@host`) and embedded query tokens rejected.
   - Comprehensive SSRF validation:
     - Rejects loopback (`127.0.0.0/8`, `::1`, `localhost`).
     - Rejects private IPv4 networks (RFC 1918: `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`).
     - Rejects link-local / APIPA (`169.254.0.0/16`, `fe80::/10`).
     - Rejects cloud metadata endpoints (`169.254.169.254`, `metadata.google.internal`, `100.100.100.200`).
     - Rejects multicast, broadcast, and unspecified (`0.0.0.0`).
     - Supports explicit test fixture authorization for deterministic offline testing.

2. **Bounded Source Retriever (`workers/research/retrieval.py`):**
   - `RetrievalConfig`: configurable limits on response bytes (1MB default), redirects (3 default), timeouts (15s default), and allowed content types.
   - Per-hop redirect re-validation: SSRF and `SourceAuthorizationPolicyProtocol` are re-evaluated on every single redirect hop.
   - Streaming response reading with hard byte ceilings (`ContentTooLargeError`).
   - Content-Type policy enforcement (`text/plain`, `text/html`, `application/json`, etc.).
   - Clean, passive text extraction from HTML (`extract_text_from_html`) stripping scripts and styles without code execution.

3. **Autonomous Research Worker (`workers/research/worker.py`):**
   - Implements `ResearchWorker(BaseWorker)` supporting `research.retrieve`, `research.*`.
   - Strictly enforces SCCA Laws:
     - Law 1: Rejects cross-space requests.
     - Law 2: Requires explicit capability assignment and active resource lease.
     - Law 4: Output and artifacts strictly scoped to Space.
     - Law 6: Failures mapped deterministically to failure taxonomy.
   - Ingestion and Provenance:
     - Creates untrusted `ResearchContent` with mandatory `taint: True` (TAINT-001, RESEARCH-005).
     - Generates RAW `ProvenanceRecord` and EXTRACTED `ProvenanceRecord`.
     - Validates provenance chain continuity via `verify_provenance_chain`.
     - Emits `research.retrieved` Pulse matching JSON schema.
     - Detects contradictory research claims (`ResearchConflict`) and publishes `research.conflict_detected` Pulse (RESEARCH-004).
   - Real artifact generation: writes raw and extracted text to disk, generating `Artifact` objects with real SHA-256 digests.

4. **WorkerInvoker Integration (`workers/invoker.py` & `workers/__init__.py`):**
   - Registered `research.*` routing in `RuntimeWorkerInvoker.get_or_create_worker()`.
   - Exported `ResearchWorker` in `workers/__init__.py`.

5. **Test & Verification Suite (`workers/tests/test_phase14_2_research_worker.py`):**
   - 34 comprehensive tests including offline deterministic HTTP fixture server, SSRF vectors, redirect loops, prompt injection inertness, conflict detection, pulse schema validation, and the full end-to-end vertical slice from SpaceKernel to verified evidence.

## 3. What Was Verified

- **Research Worker Suite:** 34 passed (`workers/tests/test_phase14_2_research_worker.py`).
- **Research Protocol Suite:** 31 passed (`core/space/tests/test_research_protocol.py`).
- **Full Regression Suite:** 176 passed across Phase 14 governance, Phase 13 adaptation, Phase 12.8 recovery, Phase 12 integrated execution, and Space tests.
- **Deterministic Core Independence (`scripts/dep_guard.py`):** PASS — 0 forbidden imports.
- **Contract & Codegen Sync (`scripts/contract_sync.py`):** PASS — 50 pulse types registered, 38 architecture types.
- **Governance Audit (`scripts/v1_audit_governance.py`):** PASS — V1-005 satisfied.
- **Spec Coverage Audit (`scripts/v1_audit_spec_coverage.py`):** PASS — V1-001 satisfied (224 contract IDs).
- **Static Analysis:**
  - `ruff check`: 0 errors.
  - `mypy`: 0 issues found across all checked source files.

## 4. What Was Deferred (Non-Goals for Phase 14.2)

- Autonomous source discovery / web crawling beyond explicit task policy.
- Search-engine agent or browser automation.
- Repository worker and patch applicator (`workers/repository/` deferred to Phase 14.3-14.4).
- Iterative repair loop (deferred to Phase 14.5).
- Vector databases, embeddings, and semantic memory search (deferred to Phase 14.6).
- Direct connection between research worker and experiential memory or plan convergence.

## 5. Next Steps

- Proceed to **Phase 14.3: Autonomous Repository Worker & SE Protocol Foundation**.
