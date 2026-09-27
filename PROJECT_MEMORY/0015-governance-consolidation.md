# RYU AI — Project Memory

## Entry 0015 — Governance System Consolidation & Project Management Hardening

**Date:** 2026-09-26  
**Milestone:** Post-v1.0.0 Repository Governance Consolidation  
**Status:** VERIFIED (GATE: PASS)  
**Baseline:** v1.0.0 Production Release (`8ce151a`)  

---

### Executive Summary

Following the completion and verification of the RYU AI v1.0.0 release (Phases 0–11), an audit of repository governance revealed that several foundational governance documents still reflected Phase 0 bootstrap states (e.g. `AGENTS.md` and `README.md` retaining Phase 0 restrictions), while `docs/RELEASES.md` was absent.

This entry records the formal consolidation and hardening of the **RYU AI Long-Term Governance System**, establishing an unambiguous, non-competing separation of concerns across all governance layers as the repository evolves beyond v1.0.0.

---

### Target Governance Model Established

```text
                    RYU AI GOVERNANCE
                           │
       ┌───────────────────┼───────────────────┐
       │                   │                   │
   AGENTS.md              ADRs             CONTRACTS
       │                   │                   │
      HOW                  WHY                WHAT
       │                   │                   │
       └───────────────────┼───────────────────┘
                           │
              ┌────────────┴────────────┐
              │                         │
       PROJECT_MEMORY              RELEASES
              │                         │
           HISTORY                    SHIPPED
              │                         │
              └────────────┬────────────┘
                           │
                       Git / Tags
                           │
                        v1.0.0
                        v1.0.1
                        v1.1.0
                        ...
```

### Document Ownership & Roles

| Domain | File Location | Purpose & Ownership | Governance Question Answered |
|:---|:---|:---|:---|
| **Agent Operating Contract** | `AGENTS.md` | Mandatory operating rules for coding agents & CI | *How must coding agents operate?* |
| **Architectural Reasoning** | `adr/NNNN-*.md` | Permanent, append-only architectural decision records | *Why was this decision made?* |
| **Machine Contracts** | `contracts/registry/` | Authoritative JSON schemas, pulse types, taxonomies | *What does the system guarantee (machine)?* |
| **Contract Traceability** | `docs/CONTRACT_MATRIX.md` | Human-readable contract-to-evidence index | *What does the system guarantee (traceability)?*|
| **Project History** | `PROJECT_MEMORY/NNNN-*.md`| Continuous, chronological milestone memory | *What happened and what state was reached?* |
| **Release Index** | `docs/RELEASES.md` | Version $\rightarrow$ Commit $\rightarrow$ Evidence mapping | *What exact software versions were shipped?* |
| **Public Overview** | `README.md` | Project overview & architecture summary | *What is RYU AI?* |
| **Future Trajectory** | `ROADMAP.md` | Multi-phase planning & exit gate definitions | *What are we planning to build next?* |

---

### Core Governance Invariants & Policies

1. **Continuous History (No Version Partitioning):**
   * ADRs remain under `adr/` with global monotonic numbering (`0001` through `0039`+). Historical ADRs are immutable. No release-specific ADR directories (`adr/v1.0/`) are permitted.
   * Project Memory remains under `PROJECT_MEMORY/` with monotonic numbering (`0001` through `0015`+). No release-specific memory directories (`PROJECT_MEMORY/v1.0/`) are permitted.
   * Contract IDs remain permanent, continuous architectural identities. No release-specific contract folders (`contracts/v1.0/`) are permitted.
2. **Mandatory 12-Step Agent Operating Loop:**
   Agents must strictly follow the sequence:
   $$\text{Request} \rightarrow \text{Repo Search} \rightarrow \text{Contract Search} \rightarrow \text{ADR Search} \rightarrow \text{Memory Search} \rightarrow \text{Impl Search} \rightarrow \text{Evidence Search} \rightarrow \text{Classify State} \rightarrow \text{Minimum Change} \rightarrow \text{Implement} \rightarrow \text{Verify} \rightarrow \text{Document}$$
   *Rule:* Agents must determine whether a capability already exists, is partially implemented, is disconnected, is contract-only, is documented-only, or is genuinely missing before writing code.
3. **Desktop Command Center Boundary Hardened:**
   The Desktop Command Center (`apps/ryu-desktop`) is formally documented as a **CHANNEL / CLIENT**, possessing **zero independent authority**. All interactions route through the Channel Daemon (`http://127.0.0.1:8420`) and human approvals require client-side HMAC signing (`token-hmac-v1`).
4. **Version Semantics Formally Defined:**
   * `v1.0`: Conceptual milestone / release family
   * `v1.0.0`: Immutable, gate-verified production baseline
   * `v1.0.1`: Patch release (bug fixes, stability hardening, zero contract changes)
   * `v1.1.0`: Minor release (backward-compatible feature additions & UI enhancements)
   * `v2.0.0`: Major release (breaking architectural changes requiring formal ADR)

---

### Changes Applied in This Milestone

1. **`AGENTS.md`**: Completely rewritten to serve as the permanent, post-v1.0 operating contract across 16 formal sections. Stripped obsolete Phase 0 restrictions while preserving all SCCA invariants, core independence rules, and security controls.
2. **`docs/RELEASES.md`**: Created the canonical release history index, documenting `v1.0.0` (commit `8ce151a`, master gate verification at `bea8ac3`, 649 tests passed, 0 failures, 1 skip).
3. **`docs/CONTRACT_MATRIX.md`**: Synchronized V1-001 through V1-006 statuses from `SPECIFIED` to `GATE_VERIFIED` with exact evidence pointers; updated status footer to post-v1.0.0 operational baseline.
4. **`README.md`**: Updated operational status to `v1.0.0` production baseline and replaced obsolete make targets with current Python verification scripts.
5. **`PROJECT_MEMORY/0015-governance-consolidation.md`**: Added this entry to document governance consolidation.

---

### Verification Results

All automated governance and contract verification scripts were executed and passed cleanly:

```bash
# 1. Contract synchronization check
d:\RYU\.env\Scripts\python.exe scripts/contract_sync.py
# Result: PASS -- All 38 types in registry and synchronized with codegen.

# 2. Dependency direction & AST import guard
d:\RYU\.env\Scripts\python.exe scripts/dep_guard.py
# Result: PASS -- No forbidden imports found in core/ (100% Core Independence).

# 3. Governance & Documentation hygiene audit
d:\RYU\.env\Scripts\python.exe scripts/v1_audit_governance.py
# Result: PASS -- All 39 ADRs valid, all 38 types registered, schemas complete, matrix integrity valid.

# 4. Dynamic spec coverage audit
d:\RYU\.env\Scripts\python.exe scripts/v1_audit_spec_coverage.py
# Result: PASS -- 141 criteria, 177 contract IDs, 157 mappings, 0 orphans, 0 missing tests.
```

---

### Historical Preservation Verification

* **Historical ADRs:** `adr/0001` through `adr/0039` remain completely untouched, retaining their original numbers, dates, and text.
* **Historical Project Memory:** `PROJECT_MEMORY/0001` through `PROJECT_MEMORY/0014` remain completely untouched.
* **Release Evidence:** `build/v1_evidence/V1_GATE_RESULT.json` and `build/v1_evidence/reports/*.json` remain untouched.
* **Core Architecture:** `docs/Architecture` remains frozen.

---

### What Remains / Next Steps

1. **Desktop Command Center Product Completion (v1.0.1+ Scope):**
   * UI-001: Chat & pulse history hydration on app launch from PostgreSQL pulses.
   * UI-002: Interactive multi-space switching and space creation via Channel Daemon.
   * UI-003: Interactive HTML sandbox preview iframe in `MarkdownMessage.tsx`.
   * UI-004: Artifact file explorer panel.
