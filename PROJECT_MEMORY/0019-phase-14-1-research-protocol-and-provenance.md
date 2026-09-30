# Project Memory: 0019 — Phase 14.1 Research Protocol & Provenance Foundation

**Date:** 2026-10-01  
**Author:** Ryu Autonomous Core Agent  
**Baseline:** Phase 14.0 (`a2b2b52`)  
**Status:** COMPLETE (GATE-14.1: PASS)  
**Governing ADR:** ADR-0044  
**Governing Architecture:** Space-Centric Cognitive Architecture (SCCA)  

---

## 1. Context & Purpose

Phase 14 expanded the RYU AI Framework to support autonomous research, software-engineering workflows, and iterative evidence-driven repair. Phase 14.0 established the contractual and governance baseline (ADR-0044, 6 pulse types, 6 schemas, 20 contracts).

Phase 14.1 establishes the domain-neutral protocol and cryptographic provenance foundation within `core/space/` without implementing live web crawling, search engines, vector databases, or concrete worker execution loops.

## 2. What Changed

1. **Protocol & Data Models (`core/space/research_protocol.py`):**
   - **Exceptions:** `ResearchError`, `SourceNotAuthorizedError`, `SourceNotFoundError`, `ContentUnavailableError`, `ContentInvalidError`, `ProvenanceInvalidError`, `ProvenanceIntegrityError`, `ResearchSpaceIsolationViolation`, `ResearchConflictError`.
   - **Canonical Serialization & Hashing:**
     - `canonical_json(data)`: Deterministic recursive key sorting, ISO 8601 UTC datetimes, uniform separators.
     - `compute_sha256(data)`: Cryptographic hash calculation over str or bytes.
     - `canonicalize_locator(source_type, raw_locator)`: Deterministic normalization of URIs and file paths.
   - **Source Identity & Security Policy:**
     - `SourceIdentity`: Frozen dataclass with embedded credential detection (rejects passwords, bearer tokens, API keys in locators and metadata).
     - `SourceAuthorizationDecision`: Immutable policy decision record.
     - `SourceAuthorizationPolicyProtocol`: Protocol for Space-scoped source authorization.
     - `DefaultDenySourcePolicy`: Mandatory default-deny policy implementation (SCCA Law 2, RESEARCH-001).
   - **Content & Taint Tracking:**
     - `ResearchContent`: Domain-neutral representation of retrieved content; verifies SHA-256 integrity on instantiation; enforces `taint=True` by default (TAINT-001, RESEARCH-005).
   - **Provenance & Transformation Lineage:**
     - `TransformationStage`: `RAW`, `EXTRACTED`, `SYNTHESIZED`.
     - `EvidenceRelationship`: `DERIVED_FROM`, `EXTRACTED_FROM`, `SYNTHESIZED_FROM`, `VALIDATES`, `SUPERSEDES`, `CONFLICTS_WITH`.
     - `ProvenanceRecord`: Frozen dataclass cryptographically binding source identity, space ID, task ID, plan version, worker producer, content hash, and canonical hash. Requires parent linkage for multi-stage transformations.
     - `verify_provenance_chain(chain, expected_space_id)`: Unbroken causal chain validation, cycle detection, space isolation enforcement, and canonical hash verification.
   - **Contradiction Representation:**
     - `ResearchConflict`: Explicit entity tracking conflicting evidence across sources (RESEARCH-004).
   - **Research Result Contract:**
     - `ResearchResult`: Immutable output contract linking content, provenance, conflict state, and space ID.
   - **Abstract Research Protocol:**
     - `ResearchSourceProtocol`: Structural runtime protocol defining `describe_source`, `authorize`, `retrieve`, and `extract`.

2. **Test & Verification Suite (`core/space/tests/test_research_protocol.py`):**
   - 31 comprehensive unit, contract, and adversarial security test cases:
     - `RESEARCH-001..005` contract coverage.
     - `PROVENANCE-001..003` contract coverage.
     - `RES-SEC-01..05` adversarial vectors (unapproved source denial, credential leakage detection, taint default, cross-space leakage, silent conflict prevention).
     - `PROV-SEC-01..05` adversarial vectors (hash tampering, cycle detection, broken parent linkage, provenance immutability, cross-space chain injection).

## 3. What Was Verified

- **Unit & Security Tests:** 31 passed (`core/space/tests/test_research_protocol.py`).
- **Space Tests:** 56 passed (`core/space/tests/`).
- **Regression Suite:** 120 passed across Phase 14 contracts, Phase 13 adaptation, Phase 12.8 crash recovery, and Phase 12 integrated execution.
- **Deterministic Core Independence (`scripts/dep_guard.py`):** PASS — 0 forbidden imports.
- **Contract & Codegen Sync (`scripts/contract_sync.py`):** PASS — 50 pulse types registered, all architecture types present.
- **Governance Audit (`scripts/v1_audit_governance.py`):** PASS — V1-005 satisfied.
- **Spec Coverage Audit (`scripts/v1_audit_spec_coverage.py`):** PASS — V1-001 satisfied (224 contract IDs).
- **Static Analysis:**
  - `ruff check`: 0 errors.
  - `mypy`: 0 issues found across all source and test files.

## 4. What Was Deferred (Non-Goals for Phase 14.1)

- Live HTTP crawling / external network fetching.
- Autonomous research worker implementation (`workers/research/worker.py` deferred to Phase 14.2).
- Repository workers, patch applicators, and test execution (`workers/repository/` deferred to Phase 14.3-14.4).
- Iterative repair loops and test runners (deferred to Phase 14.5).
- Search index / vector storage / embedding models (deferred to Phase 14.6).

## 5. Next Steps

- Proceed to **Phase 14.2: Autonomous Research Worker & Execution**.
