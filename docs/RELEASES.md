# RYU AI — Official Release History & Verification Index

This document is the authoritative canonical index connecting released RYU AI software versions to their exact Git commits, verification gate results, test evidence, architectural decisions, and project memory records.

---

## 1. Version Semantics & Governance Invariants

RYU AI adheres to Semantic Versioning (`vMAJOR.MINOR.PATCH`) aligned with the Space-Centric Cognitive Architecture (SCCA):

```text
v1.0    = Release family / conceptual architecture milestone
v1.0.0  = Specific, immutable, gate-verified production release
v1.0.1  = Patch release (bug fixes, stability hardening, zero contract changes)
v1.1.0  = Minor release (backward-compatible feature additions & UI enhancements)
v2.0.0  = Major release (breaking architectural changes requiring formal ADR)
```

### Governing Principles

1. **Continuous History:** ADRs (`adr/`), Contracts (`contracts/registry/`), and Project Memory (`PROJECT_MEMORY/`) use continuous, monotonic global sequences. They are **never** partitioned into release-specific folders (e.g. no `adr/v1.0/` or `contracts/v1.0/`).
2. **Release Identity:** Releases are identified by exact Git commit hashes and immutable Git tags recorded in this document.
3. **Evidence-Backed Releases:** No software version may be indexed in this document without executable proof from the Master Release Gate (`scripts/v1_release_gate.py`) and recorded evidence under `build/v1_evidence/`.

---

## 2. Master Release Index

| Version | Release Date | Git Commit | Git Tag | Verification Gate | Tests Passed | ADR Range | Project Memory | Status |
|:---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| **v1.0.0** | 2026-09-24 | `8ce151a` | `v1.0.0` | **GATE: PASS** (V1-001..V1-006) | 649 pass, 1 skip, 0 fail | ADR-0001..0039 | `0014-v1-final-release.md` | **PRODUCTION RELEASE** |
| **v1.0.1** | 2026-09-27 | `HEAD` | `v1.0.1` | **GATE: PASS** (DESKTOP-001..005) | 57 pass, 1 skip, 0 fail (channels) | ADR-0040 | `0016-v101-desktop-capability-exposure.md` | **CAPABILITY EXPOSURE RELEASE** |

---

## 3. Release Details

### RYU AI v1.0.0

* **Release Date:** September 24, 2026  
* **Release Baseline Commit:** `8ce151a` (`feat(desktop): add live llm toggle, embedded static bundle, and unified desktop launcher`)  
* **Master Gate Verification Commit:** `bea8ac3` (`feat(v1): implement master release gate battery, verification tooling, and evidence audit`)  
* **Release Status:** `RELEASE VERIFIED (GATE: PASS)`  
* **Architecture Milestone:** Space-Centric Cognitive Architecture (SCCA) Phases 0 through 11 Complete  

#### Verification Summary (`build/v1_evidence/V1_GATE_RESULT.json`)

All six mandatory release criteria evaluated by `scripts/v1_release_gate.py` passed with strict Boolean AND logic:

| Gate Code | Criterion Name | Status | Key Proof Metric | Report Path |
|:---|:---|:---:|:---|:---|
| **V1-001** | Dynamic Spec Coverage Audit | **PASS** | 141 criteria, 177 contract IDs, 157 harness mappings, 0 orphans, 0 missing tests | `build/v1_evidence/reports/v1_spec_coverage_report.json` |
| **V1-002** | Core Independence Proof | **PASS** | AST check pass, 0 cognitive imports in `core/`, runtime isolation verified | `build/v1_evidence/reports/v1_core_independence_report.json` |
| **V1-003** | End-to-End Vertical Slice Execution | **PASS** | Space creation $\rightarrow$ Plan $\rightarrow$ `token-hmac-v1` Human Approval $\rightarrow$ Worker execution $\rightarrow$ Durable persistence | `build/v1_evidence/reports/v1_vertical_slice_report.json` |
| **V1-004** | Consolidated Security Battery | **PASS** | 12/12 security regression proofs green (replay, nonce reuse, forged grants, budget breach) | `build/v1_evidence/reports/v1_security_regression_report.json` |
| **V1-005** | Governance & Documentation Hygiene | **PASS** | 39 ADRs validated, 38 registered pulse types, 38 1:1 JSON payload schemas | `build/v1_evidence/reports/v1_governance_report.json` |
| **V1-006** | Deterministic Replay Equivalence | **PASS** | Exact artifact SHA-256 byte identity (`e24384b2...`) + 100% causal chain replay | `build/v1_evidence/reports/v1_replay_report.json` |
| **TEST-ALL**| Full Test Suite Execution | **PASS** | **649 passed, 1 skipped (Neo4j stub), 0 failed** in 93.79s | Executed against real PostgreSQL 16 & Redis 7 |

#### Architectural Scope Delivered (Phases 0–11)

1. **Phase 0 — Repository Scaffold & Foundational Tooling:** AST import guard (`dep_guard.py`), contract synchronization (`contract_sync.py`), Python and Rust workspaces.
2. **Phase 1 — Durable Pulse Bus:** PostgreSQL append-only event store, Redis Streams live pub/sub transport, forward-only taint tracking, causal chain replay (`core/pulse_bus/`).
3. **Phase 2 — Space Kernel:** Space isolation boundaries, execution budgets, Compare-And-Swap (CAS) plan versioning, kernel-level secret sanitization (`core/space/`).
4. **Phase 3 — Resource Manager & Chaos Harness:** Fractional CPU/GPU leases, anti-starvation FIFO/priority queues, idempotency at execution boundary (`core/resources/`).
5. **Phase 4 — Deterministic Orchestrator:** 5-submodule cognitive pipeline (`GoalAnalyzer`, `Planner`, `TeamBuilder`, `Monitor`, `Adapter`), `PlanReconciler` convergence loop (`core/orchestrator/`).
6. **Phase 5 — First Real Agent & LLM Recording:** Deterministic 9-state machine, `LLMProvider` abstraction (Ollama & OpenAI), Space-scoped LLM recording & deterministic replay engine (`agents/`, `llm/`).
7. **Phase 6 — Workers & Sandbox Isolation:** Filesystem deny-by-default, network egress policies, process tree isolation, Linux Seccomp & Windows platform containment (`workers/`).
8. **Phase 7 — Node Runtime MVP & Device Grants:** Cryptographic derived credentials, 5-state device grants, dual invalidation flow, Windows host and Linux/WSL2 execution profiles (`node/`, `node_runtime/`).
9. **Phase 8 — CLI Channel & Human Approval Gates:** Rich terminal interface, `token-hmac-v1` cryptographic signatures, human attention budgeting (`channels/cli/`, `channels/approval/`).
10. **Phase 9 — Signed Skills & MCP Extensibility:** SHA-256 signed skill supply chain, Model Context Protocol (MCP) stdio client, canary prompt injection defense (`skills/`).
11. **Phase 10 — Memory Adapters & Adaptation Loop:** Working, episodic (PostgreSQL), and semantic memory adapters, experience reflection cycle, knowledge promotion gate (`memory/`).
12. **Phase 11 — Multi-Platform Nodes & Desktop Shell:** Multi-node concurrency, restricted MDM tier allow-lists, loopback Channel Daemon (`channels/daemon/`), Tauri v2 + React 18 Desktop Command Center (`apps/ryu-desktop/`).

#### Key Documentation & Evidence Links
* **Release Narrative:** [`PROJECT_MEMORY/0014-v1-final-release.md`](file:///d:/RYU/PROJECT_MEMORY/0014-v1-final-release.md)
* **Master Gate Evidence:** [`build/v1_evidence/V1_GATE_RESULT.json`](file:///d:/RYU/build/v1_evidence/V1_GATE_RESULT.json)
* **Release Report:** [`RYU_AI_v1.0_Final_Release_Report.html`](file:///d:/RYU/RYU_AI_v1.0_Final_Release_Report.html)
* **Decisions:** [`adr/0001-monorepo-structure.md`](file:///d:/RYU/adr/0001-monorepo-structure.md) through [`adr/0039-restricted-node-tier-and-device-side-mdm-allow-lists.md`](file:///d:/RYU/adr/0039-restricted-node-tier-and-device-side-mdm-allow-lists.md)
