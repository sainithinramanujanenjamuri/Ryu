# 0018 — Phase 14.0: Architecture Baseline, Contracts & Governance

**Status:** GATE_VERIFIED (Phase 14.0 Governance Gate)  
**Date:** 2026-10-01  
**Phase:** Phase 14.0  
**Architecture:** Space-Centric Cognitive Architecture (SCCA)  
**Governing Rule:** AGENTS.md §7 (Deterministic Core Independence) & SCCA Six Laws  
**ADR Reference:** ADR-0044  
**Contracts:** RESEARCH-001..005, REPO-001..005, EVIDENCE-001..003, REPAIR-001..004, PROVENANCE-001..003  

---

## 1. What Changed

1. **Architecture Baseline & ADR-0044 (`adr/0044-autonomous-research-and-software-engineering-runtime.md`):**
   - Established ADR-0044 defining the complete architectural foundation, boundaries, authority models, failure models, recovery models, and implementation sequence for Phase 14.
   - Enforced the core boundary rule: core defines protocols (`ResearchSourceProtocol`, `RepositoryProtocol`), higher layers implement.
   - Enforced authority model: SpaceKernel is sole Plan CAS authority; ConvergenceEngine produces bounded proposals; Memory and LLM are strictly advisory.

2. **Phase 14 Contract Registration (`docs/CONTRACT_MATRIX.md` Section 30G):**
   - Registered 20 Phase 14 contracts across 5 families:
     - `RESEARCH-001` through `RESEARCH-005` (Allowlist, Provenance, Sanitization/Taint, Conflict Detection, Synthesis)
     - `REPO-001` through `REPO-005` (Workspace Scoping, Atomic Patching, Denylist, Size Ceilings, Reversibility)
     - `EVIDENCE-001` through `EVIDENCE-003` (Test Verification, Evidence Hierarchy, Artifact Graph Lineage)
     - `REPAIR-001` through `REPAIR-004` (Bounded Repair Ceilings, Fingerprinting, Counterfactuals, CAS Replan)
     - `PROVENANCE-001` through `PROVENANCE-003` (Transformation Chain, Immutability, Space Isolation)
   - Status marked strictly as `CONTRACT_ONLY` (no premature runtime claims).

3. **Pulse Registry & 1:1 Payload Schemas (`contracts/registry/`):**
   - Registered 6 new durable pulse types in `pulse-types.json` (increasing total types from 44 to 50):
     - `research.retrieved`
     - `research.conflict_detected`
     - `repo.patch_applied`
     - `repo.patch_reverted`
     - `test.executed`
     - `repair.loop_iterated`
   - Created 6 corresponding JSON schema files under `contracts/registry/payload-schemas/`.
   - Regenerated Python models via `contracts/codegen/python/generate_pulse_models.py`.

4. **Traceability Spine & Specification Coverage (`harness/spec_map.yaml`):**
   - Added all 20 contracts into `harness/spec_map.yaml`.
   - Verified bidirectional traceability via `scripts/v1_audit_spec_coverage.py`: 161 criteria, 224 contracts, 182 spec-map entries, 0 orphans, 0 duplicates.

5. **Phase 14.0 Governance Test Suite (`harness/cases/phase14/test_phase14_contracts_governance.py`):**
   - Created 8 executable tests verifying ADR-0044 completeness, pulse registry, schemas, codegen sync, SCCA law compliance, core independence, and authority separation.

---

## 2. Why It Changed

Phase 14 represents the controlled expansion of RYU into autonomous research and software engineering. Before any runtime worker or repair logic can be implemented, the contracts, schemas, authority boundaries, and anti-runaway ceilings must be established and frozen to prevent:
- Uncontrolled web crawling or prompt-injection exploitation.
- Direct repository modification without atomic rollback and size limits.
- Model assertions masquerading as verified execution evidence.
- Infinite self-repair and replan loops.

---

## 3. What Was Verified

### Verification Tool Suite
- `scripts/dep_guard.py` -> **PASS (0 forbidden imports in core/)**
- `scripts/v1_audit_governance.py` -> **PASS (ADRs 0001..0044, 50 pulses, 50 schemas, contract matrix)**
- `scripts/v1_audit_spec_coverage.py` -> **PASS (161 criteria, 224 contracts, 182 spec-map entries, 0 orphans)**
- `scripts/contract_sync.py` -> **PASS (all 38 arch types in registry, 50 total types)**
- `harness/cases/phase14/test_phase14_contracts_governance.py` -> **8/8 PASS in 0.56s**
- Regression battery (Phase 12, Phase 12.8, Phase 13) -> **85/85 PASS in 2.91s**

---

## 4. What Was Deferred

No Phase 14 runtime components were implemented in Phase 14.0:
- `ResearchWorker` and `ResearchSourceProtocol` runtime (deferred to Phase 14.1 & 14.2).
- `RepositoryProtocol` and `PatchWorker` (deferred to Phase 14.3 & 14.4).
- `TestRunnerWorker` (deferred to Phase 14.5).
- Bounded repair convergence loop (deferred to Phase 14.6).
- Asynchronous experience queue (deferred to Phase 14.7).
- Memory retention lifecycle (deferred to Phase 14.8).
- End-to-end vertical slices (deferred to Phase 14.9).
- Security, chaos, and replay hardening (deferred to Phase 14.10).
