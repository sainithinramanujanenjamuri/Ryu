# Phase 14.1 — Research Protocol & Provenance Foundation Verification Report

**Phase:** 14.1  
**Title:** Research Protocol & Provenance Foundation  
**Previous Phase Baseline:** Phase 14.0 (`a2b2b52`)  
**Status:** **GATE-14.1: PASS**  
**Date:** 2026-10-01  
**Governing Architecture:** Space-Centric Cognitive Architecture (SCCA)  
**Governing ADR:** ADR-0044  

---

## 1. Executive Summary

Phase 14.1 implements the protocol and cryptographic provenance foundation for the RYU Autonomous Research Runtime. It strictly adheres to:
- **AGENTS.md §7 (Deterministic Core Independence):** `core/space/research_protocol.py` is entirely self-contained within Python standard library modules (`dataclasses`, `enum`, `hashlib`, `json`, `posixpath`, `re`, `datetime`, `typing`). It contains zero imports from `agents/`, `workers/`, `skills/`, `workflows/`, `llm/`, `channels/`, `memory/`, or external network/browser dependencies.
- **SCCA Law 1 (Everything Happens Inside a Space):** All research sources, retrieved contents, provenance records, and conflicts are strictly space-scoped with mandatory space ID validation.
- **SCCA Law 2 (Capabilities Are Requested, Never Owned):** Research sources are governed by `SourceAuthorizationPolicyProtocol` with mandatory default-deny enforcement (`DefaultDenySourcePolicy`).
- **TAINT-001 & RESEARCH-005:** External research content enters the system with `taint: True` by default.

---

## 2. Inventory of Changes

### A. Created Files
1. `core/space/research_protocol.py` (553 lines):
   - Exceptions: `ResearchError`, `SourceNotAuthorizedError`, `SourceNotFoundError`, `ContentUnavailableError`, `ContentInvalidError`, `ProvenanceInvalidError`, `ProvenanceIntegrityError`, `ResearchSpaceIsolationViolation`, `ResearchConflictError`.
   - Canonical Hashing & Serialization: `canonical_json`, `compute_sha256`, `canonicalize_locator`.
   - Data Models: `SourceIdentity` (with credential/secret detection), `SourceAuthorizationDecision`, `SourceAuthorizationPolicyProtocol`, `DefaultDenySourcePolicy`.
   - Research Content: `ResearchContent` (SHA-256 integrity check, mandatory `taint: True` default).
   - Provenance Models: `TransformationStage`, `EvidenceRelationship`, `ProvenanceRecord` (frozen, deterministic canonical hash, parent link continuity).
   - Validation: `verify_provenance_chain` (unbroken lineage, cycle detection, space isolation, canonical hash checks).
   - Contradiction: `ResearchConflict` (formal conflict entity).
   - Result: `ResearchResult` (immutable contract binding content and provenance).
   - Abstract Protocol: `ResearchSourceProtocol` (`describe_source`, `authorize`, `retrieve`, `extract`).

2. `core/space/tests/test_research_protocol.py` (467 lines):
   - 31 unit, contract, and adversarial security tests covering `RESEARCH-001..005`, `PROVENANCE-001..003`, `RES-SEC-01..05`, and `PROV-SEC-01..05`.

3. `PROJECT_MEMORY/0019-phase-14-1-research-protocol-and-provenance.md`:
   - Historical monotonic milestone entry.

4. `docs/PHASE_14_1_VERIFICATION_REPORT.md`:
   - This verification report.

---

## 3. Contract Traceability

| Contract ID | Invariant | Implementation Boundary | Test Proof | Status |
| :--- | :--- | :--- | :--- | :--- |
| **RESEARCH-001** | Source Allowlist Enforcement & Default Deny | `core/space/research_protocol.py` | `test_default_deny_source_policy`, `test_allowlist_source_policy_permitted`, `test_allowlist_source_policy_denied` | `PASS` |
| **RESEARCH-002** | Explicit Provenance Required on Results | `core/space/research_protocol.py` | `test_research_result_valid`, `test_research_source_protocol_full_flow` | `PASS` |
| **RESEARCH-003** | Content Hash Verification & Tampering Detection | `core/space/research_protocol.py` | `test_research_content_integrity_violation`, `test_research_result_content_provenance_hash_mismatch` | `PASS` |
| **RESEARCH-004** | Conflict State Detection & Representation | `core/space/research_protocol.py` | `test_research_conflict_valid`, `test_research_conflict_rejects_self_conflict`, `test_research_result_conflicting_status_requires_conflict_entity` | `PASS` |
| **RESEARCH-005** | Mandatory Taint Tracking on External Content | `core/space/research_protocol.py` | `test_research_content_taint_default`, `test_research_source_protocol_full_flow` | `PASS` |
| **PROVENANCE-001** | Multi-Stage Transformation Chain Continuity | `core/space/research_protocol.py` | `test_provenance_record_extracted_requires_parent`, `test_verify_provenance_chain_success` | `PASS` |
| **PROVENANCE-002** | Cryptographic Canonical Hashing & Immutability | `core/space/research_protocol.py` | `test_provenance_record_immutability`, `test_provenance_record_tampered_canonical_hash_rejected` | `PASS` |
| **PROVENANCE-003** | Space-Scoped Provenance Isolation | `core/space/research_protocol.py` | `test_research_result_cross_space_isolation_rejected`, `test_verify_provenance_chain_cross_space_rejected` | `PASS` |

---

## 4. Adversarial Security Verification

| Vector ID | Description | Threat Scenario | Verification Result |
| :--- | :--- | :--- | :--- |
| **RES-SEC-01** | Unapproved Source Retrieval | Worker attempts retrieval from unapproved domain | Denied by `SourceAuthorizationPolicyProtocol` (`test_allowlist_source_policy_denied`) |
| **RES-SEC-02** | Credential Leakage in Source Locator / Metadata | Source locator embeds query tokens, basic auth credentials, or metadata keys containing `api_key` | Instantiation rejected with `ValueError` (`test_source_identity_rejects_credentials_in_locator`, `test_source_identity_rejects_credential_labels_in_metadata`) |
| **RES-SEC-03** | Taint Stripping Attempt | Research content created without explicit taint | `ResearchContent.taint` defaults to `True` unconditionally (`test_research_content_taint_default`) |
| **RES-SEC-04** | Cross-Space Content Leakage | Result in Space B attempts to incorporate research content or provenance from Space A | Rejected with `ResearchSpaceIsolationViolation` (`test_research_result_cross_space_isolation_rejected`) |
| **RES-SEC-05** | Silent Contradiction Merging | Contradictory findings reported without explicit conflict record | Rejected with `ValueError` requiring `ResearchConflict` entity (`test_research_result_conflicting_status_requires_conflict_entity`) |
| **PROV-SEC-01** | Provenance Record Tampering | Forged `canonical_hash` supplied in provenance record | Rejected with `ProvenanceInvalidError` (`test_provenance_record_tampered_canonical_hash_rejected`) |
| **PROV-SEC-02** | Provenance Chain Cycle | Duplicate provenance IDs or cyclical parent pointers injected | Rejected with cycle detection error (`test_verify_provenance_chain_cycle_or_duplicate_rejected`) |
| **PROV-SEC-03** | Broken Lineage Injection | EXTRACTED stage record omits or mismatches parent pointer | Rejected with broken transformation link error (`test_verify_provenance_chain_broken_link_rejected`) |
| **PROV-SEC-04** | Mutation of Historical Provenance | Modifying immutable fields on created provenance record | Rejected by `dataclass(frozen=True)` raising `FrozenInstanceError` (`test_provenance_record_immutability`) |
| **PROV-SEC-05** | Cross-Space Provenance Injection | Multi-step provenance chain injects a step from an foreign Space | Rejected with cross-space violation (`test_verify_provenance_chain_cross_space_rejected`) |

---

## 5. Test & Quality Metrics

```text
Test Suite Execution:
- core/space/tests/test_research_protocol.py : 31 passed in 0.86s
- core/space/tests/ (all space tests)         : 56 passed in 1.25s
- Regression Suite (Phases 12, 12.8, 13, 14)  : 120 passed in 5.10s
- Total Verified Tests in Run                 : 207 passed, 0 failed

Static Analysis:
- scripts/dep_guard.py                       : PASS (0 forbidden imports in core/)
- scripts/contract_sync.py                   : PASS (50 pulse types registered)
- scripts/v1_audit_governance.py             : PASS (V1-005 satisfied)
- scripts/v1_audit_spec_coverage.py          : PASS (V1-001 satisfied, 224 contracts)
- ruff check                                 : PASS (0 errors)
- mypy                                       : PASS (Success: no issues found)
```

---

## 6. Scope Boundaries & Deferred Capabilities

Phase 14.1 deliberately implements **only** the protocol and provenance data layer:
- **No concrete worker processes:** `workers/research/worker.py` is deferred to Phase 14.2.
- **No live network or scraping:** No HTTP client (`requests`, `httpx`, `aiohttp`) was introduced into `core/`.
- **No vector search or embeddings:** Deferred to Phase 14.6.
- **No LLM agent execution:** LLM interactions remain bounded outside `core/`.
- **No repository file mutators:** `repo.patch_applied` execution is deferred to Phase 14.3.

---

## 7. Gate Conclusion

All exit criteria for Phase 14.1 are satisfied:
- Protocol and provenance data models implemented.
- SCCA Laws 1 & 2 verified.
- Deterministic Core Independence strictly maintained.
- Cryptographic hashing and immutability enforced.
- All adversarial vectors neutralized.

**GATE-14.1: PASS**  
The repository is prepared for **Phase 14.2: Autonomous Research Worker & Execution**.
