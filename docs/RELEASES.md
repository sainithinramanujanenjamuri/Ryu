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
| **v1.0.1** | 2026-09-28 | `85b5036` | `v1.0.1` | **GATE: PASS** (DESKTOP-001..005) | 72 pass, 1 skip, 0 fail (channels) | ADR-0040 | `0016-v101-desktop-capability-exposure.md` | **CAPABILITY EXPOSURE RELEASE** |


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

---

### RYU AI v1.0.1

* **Release Date:** September 28, 2026  
* **Release Baseline Commit:** `85b5036` (`fix(desktop): bypass Windows icon cache by linking ryu_crest.ico in Desktop and Start Menu`)  
* **Initial Exposure Commit:** `638d134` (`feat(release): v1.0.1 Desktop Command Center capability exposure and zero-privilege preview`)  
* **Release Status:** `RELEASE VERIFIED (GATE: PASS)`  
* **Architecture Milestone:** Space-Centric Cognitive Architecture (SCCA) v1.0.1 Capability Exposure  


#### Verification Summary (`docs/V1.0.1_RELEASE_VERIFICATION_REPORT.md`)

All thirteen mandatory release criteria passed with strict Boolean AND logic:

| Gate Code | Evaluation Category | Status | Key Proof Metric | Report Path |
|:---|:---|:---:|:---|:---|
| **GATE-01** | Full Repository Regression | **PASS** | 664 passed, 1 skipped, 0 failed in 104.52s against real PostgreSQL 16 & Redis 7 | `docs/V1.0.1_RELEASE_VERIFICATION_REPORT.md` |
| **GATE-02** | v1.0.1 Specific Tests | **PASS** | 71 passed, 1 skipped, 0 failed in `channels/tests` | `docs/V1.0.1_RELEASE_VERIFICATION_REPORT.md` |
| **GATE-03** | Space Isolation Battery | **PASS** | Zero cross-space leakage across history, files, artifacts, memory | `docs/V1.0.1_RELEASE_VERIFICATION_REPORT.md` |
| **GATE-04** | Authority Boundary | **PASS** | Desktop UI remains unprivileged Channel/Client; forged HMACs rejected | `docs/V1.0.1_RELEASE_VERIFICATION_REPORT.md` |
| **GATE-05** | HTML Sandbox Security | **PASS** | `<iframe sandbox="allow-scripts">` strictly without `allow-same-origin` | `docs/V1.0.1_RELEASE_VERIFICATION_REPORT.md` |
| **GATE-06** | File Ingress Security | **PASS** | Traversal, oversized (>2MB), and non-whitelisted blocked; taint emitted | `docs/V1.0.1_RELEASE_VERIFICATION_REPORT.md` |
| **GATE-07** | Artifact Security | **PASS** | SHA-256 byte integrity verified, cross-space traversal returns 404 | `docs/V1.0.1_RELEASE_VERIFICATION_REPORT.md` |
| **GATE-08** | History Architecture | **PASS** | PulseStore is authoritative, JSONL is CQRS projection cache | `docs/V1.0.1_RELEASE_VERIFICATION_REPORT.md` |
| **GATE-09** | HTML Auto-Extraction | **PASS** | Deterministic extraction on turn save, whitespace/empty blocks skipped | `docs/V1.0.1_RELEASE_VERIFICATION_REPORT.md` |
| **GATE-10** | Persistence & Restart | **PASS** | State survives complete daemon restart | `docs/V1.0.1_RELEASE_VERIFICATION_REPORT.md` |
| **GATE-11** | Desktop E2E Live Flow | **PASS** | Unmocked full roundtrip against live daemon | `docs/V1.0.1_RELEASE_VERIFICATION_REPORT.md` |
| **GATE-12** | Core Independence & Contracts | **PASS** | `dep_guard.py` PASS (0 forbidden imports), `contract_sync.py` PASS (38 pulse types) | `docs/V1.0.1_RELEASE_VERIFICATION_REPORT.md` |
| **GATE-13** | Desktop Build & Binaries | **PASS** | `npm run build` PASS (242.30 kB bundle), Tauri `cargo check` PASS (16.94s) | `docs/V1.0.1_RELEASE_VERIFICATION_REPORT.md` |

#### Architectural Scope Delivered (Work Packages 1–6)

1. **WP-1 — Conversation History Rehydration:** Durable PulseStore authoritative playback with space-local CQRS JSONL cache (`GET /api/v1/spaces/{space_id}/history`).
2. **WP-2 — Space Lifecycle & Dynamic Switching:** Full multi-space creation, listing, switching, and inspection without daemon restarts (`GET /api/v1/spaces`, `POST /api/v1/spaces`, `GET /api/v1/spaces/{id}`).
3. **WP-3 — Zero-Privilege Sandboxed HTML Preview:** Pure `sandbox="allow-scripts"` isolation forbidding `allow-same-origin` for generated HTML artifacts and message blocks.
4. **WP-4 — Space Artifact Explorer:** Content-addressed SHA-256 artifact indexing, cross-space isolation, search, filtering, download, and sandboxed preview (`GET /api/v1/spaces/{id}/artifacts`, `GET /api/v1/spaces/{id}/artifacts/{id}/content`).
5. **WP-5 — Sandboxed File Ingress with Taint Tracking:** Strictly validated ($\le 2$MB limit, text format whitelist, path traversal defense) file ingress emitting `security.taint.detected` pulses (`POST /api/v1/spaces/{id}/files`).
6. **WP-6 — Read-Only System Visibility:** Zero-authority node inspection (`GET /api/v1/nodes`) and space memory reflection (`GET /api/v1/spaces/{id}/memory`) preserving SCCA Law 4.

#### Key Documentation & Evidence Links
* **Release Narrative:** [`PROJECT_MEMORY/0016-v101-desktop-capability-exposure.md`](file:///d:/RYU/PROJECT_MEMORY/0016-v101-desktop-capability-exposure.md)
* **Master Specification:** [`docs/V1.0.1_DESKTOP_COMMAND_CENTER_SPEC.md`](file:///d:/RYU/docs/V1.0.1_DESKTOP_COMMAND_CENTER_SPEC.md)
* **Release Verification Report:** [`docs/V1.0.1_RELEASE_VERIFICATION_REPORT.md`](file:///d:/RYU/docs/V1.0.1_RELEASE_VERIFICATION_REPORT.md)
* **Decisions:** [`adr/0040-desktop-capability-exposure-artifact-lifecycle-and-sandbox-preview.md`](file:///d:/RYU/adr/0040-desktop-capability-exposure-artifact-lifecycle-and-sandbox-preview.md)
