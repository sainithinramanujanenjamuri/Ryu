# RYU AI — Agent Engineering Instructions & Operating Contract

**Project:** RYU AI  
**Architecture:** Space-Centric Cognitive Architecture (SCCA)  
**Repository State:** Post-v1.0.0 Operational Baseline  
**Current Milestone:** v1.0.0 Released & Verified (Phases 0–11 Complete)  
**Architecture Status:** Frozen  

---

## 1. Purpose & Authority

This document defines the mandatory engineering operating contract for any AI coding agent, autonomous development system, human engineer, or CI automation operating on the RYU AI repository.

These rules are permanent. They apply across all versions, milestones, and phases.

The purpose is to ensure that all future development, integration, maintenance, and refactoring remain strictly faithful to:
1. The frozen Space-Centric Cognitive Architecture (SCCA).
2. Machine-readable contract specifications under `contracts/registry/`.
3. The executable test and verification harness under `harness/`.
4. The append-only architectural decision log under `adr/`.
5. The continuous chronological project history under `PROJECT_MEMORY/`.

**When this file conflicts with an implementation shortcut or prompt assumption, the implementation shortcut loses.**

---

## 2. Target Governance Model

The RYU AI repository operates under a strict, multi-layer separation of concerns:

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

| Document Domain | Location | Owner / Purpose | Authority Role |
|:---|:---|:---|:---|
| **Operating Rules** | `AGENTS.md` | Agent operating rules & engineering constraints | **HOW** agents operate |
| **Architectural Reasoning** | `adr/NNNN-*.md` | Monotonic decision history & trade-offs | **WHY** an architecture decision exists |
| **Machine Contracts** | `contracts/registry/` | Authoritative JSON schemas & taxonomies | **WHAT** the system promises (machine-readable) |
| **Contract Traceability** | `docs/CONTRACT_MATRIX.md` | Human-readable contract-to-evidence index | **WHAT** the system promises (traceability) |
| **Project History** | `PROJECT_MEMORY/NNNN-*.md`| Monotonic chronological milestones & state | **HISTORY** of how the system evolved |
| **Release Index** | `docs/RELEASES.md` | Version $\rightarrow$ Commit $\rightarrow$ Evidence mapping | **SHIPPED** release history |
| **Public Overview** | `README.md` | Project overview & architecture summary | **WHAT IS** RYU AI |
| **Future Direction** | `ROADMAP.md` | Multi-phase planning & exit gate definitions | **FUTURE** development trajectory |

**Rule:** Do not let two documents become competing sources of truth for the same information.

---

## 3. Version Semantics & Identity Rules

To maintain long-term repository coherence across v1.0.0, v1.0.1, v1.1.0, v2.0.0, and beyond:

```text
v1.0    = Release family / conceptual milestone
v1.0.0  = Specific immutable release baseline
v1.0.1  = Patch release (bug fixes, zero contract changes)
v1.1.0  = Minor release (backward-compatible feature additions)
v2.0.0  = Major release (breaking architectural changes via ADR)
```

### Governing Identity Invariants

1. **ADRs are Continuous and Monotonic:** ADR numbering (`0001`, `0002`, ... `0039`, `0040`) is GLOBAL. Never reset ADR numbering for a release. Never create version-specific ADR folders (e.g. `adr/v1.0/`).
2. **Contract IDs are Permanent:** Contract identifiers (`ARC-001`, `SPACE-001`, `PULSE-001`, `V1-001`) are continuous architectural identities. Never reset contract numbering. Never create versioned contract folders (`contracts/v1.0/`).
3. **Project Memory is Chronological:** Entries (`0001`, `0002`, ... `0014`, `0015`) represent continuous repository evolution. Never reorganize historical memory into version directories.
4. **Releases are Indexed in `docs/RELEASES.md`:** Release numbers belong to release management, Git tags, and `docs/RELEASES.md`, NOT directory namespaces.

---

## 4. Mandatory Agent Operating Workflow

Every agent executing a coding, refactoring, or integration task MUST follow this 12-step working loop:

```text
  1. REQUEST           -> Receive user prompt and verify scope.
  2. REPOSITORY SEARCH -> Search existing code to determine actual state.
  3. CONTRACT SEARCH   -> Check contracts/registry/ and docs/CONTRACT_MATRIX.md.
  4. ADR SEARCH        -> Review adr/ for applicable decisions.
  5. MEMORY SEARCH     -> Check PROJECT_MEMORY/ for historical context.
  6. IMPL SEARCH       -> Locate real runtime execution paths.
  7. EVIDENCE SEARCH   -> Inspect tests, harness cases, and build/v1_evidence/.
  8. CLASSIFY STATE    -> Classify: IMPLEMENTED, PARTIAL, DISCONNECTED, CONTRACT_ONLY, DOCUMENTED_ONLY, or GENUINELY_MISSING.
  9. MINIMUM CHANGE    -> Plan the smallest correct change adhering to SCCA.
 10. IMPLEMENT         -> Apply changes with zero architectural bypasses.
 11. VERIFY            -> Run unit, harness, contract_sync, and dep_guard checks.
 12. DOCUMENT          -> Update spec_map, memory, or releases if material.
```

### Mandated Pre-Implementation Classification Rule
> **Before implementing any requested capability, determine whether the capability already exists, is partially implemented, is disconnected, is contract-only, is documented-only, or is genuinely missing.**

Do not assume a feature is missing because it lacks a UI. Do not assume a feature exists because its name appears in documentation.

---

## 5. Frozen Architecture & The Six SCCA Laws

RYU AI uses the **Space-Centric Cognitive Architecture (SCCA)**. The architecture is frozen.

### Execution Hierarchy
```text
Runtime
  ↓
Spaces
  ↓
Execution Plans (DAGs)
  ↓
Teams
  ↓
Agents
  ↓
Workers
  ↓
Skills
  ↓
Memory
  ↓
Insights
```

### The Six Immutable Laws

#### Law 1 — Everything Happens Inside a Space
Every meaningful operation, state mutation, memory entry, and artifact belongs to a Space. Space is the primary authority and isolation boundary. Nothing may silently operate outside a Space.

#### Law 2 — Capabilities Are Requested, Never Owned
Components do not inherently own execution capabilities. Capabilities are requested through typed pulses and admitted via kernel Admission Control according to space budget, capability risk tier, and governance policy.

#### Law 3 — Components Communicate Through Pulses
All inter-component communication happens through typed Pulses validated against machine-readable contracts. No arbitrary hidden communication paths, global shared variables, or unvalidated RPC channels are permitted.

#### Law 4 — Knowledge Belongs to the Space First
Knowledge, experiences, reflections, and insights are Space-local by default. Promotion to shared or global memory requires explicit validation and human/governance authorization.

#### Law 5 — Humans Define Goals; Ryu Organizes Execution
Human intent defines the goal. RYU organizes planning, assignment, execution, monitoring, adaptation, and escalation. The system must never silently redefine or drift from human goals.

#### Law 6 — Failures Are Contained, Escalated, and Never Silent
Failures must remain contained within their originating scope and escalate deterministically:
$$\text{Tool failure} \longrightarrow \text{Worker} \longrightarrow \text{Agent} \longrightarrow \text{Space Orchestrator} \longrightarrow \text{Space Decision (Retry / Reassign / Escalate)} \longrightarrow \text{Human}$$
Silently swallowing failures is an architectural defect.

---

## 6. SCCA Authority Boundaries & Desktop Command Center Role

### Absolute Authority Separation

The core runtime holds sole authority over:
* Space lifecycle and isolation (`core/space/`)
* Plan CAS transitions (`core/plans/`)
* Capability admission and risk gating (`core/capabilities/`)
* Resource allocation and fractional leases (`core/resources/`)
* Human approval validation and HMAC verification (`channels/approval/`, `core/space/`)
* Device grants and hardware leases (`node/`, `core/resources/`)
* Secret resolution and containment (`core/security/`)
* Taint propagation and forward-only clearance (`core/pulse_bus/`)
* Skill supply chain verification (`skills/`)
* Memory promotion authorization (`memory/`)

### Desktop Command Center Operating Contract

The Desktop Command Center (`apps/ryu-desktop`) is a **CHANNEL / CLIENT**. It is **NOT** an authority layer.

1. **Zero Independent Authority:** The Desktop UI must NOT independently own Space, Admission, Resource, Approval, DeviceGrant, Secret, Skill, Memory promotion, Plan CAS, Security policy, or Node authority.
2. **Loopback Transport Only:** The Desktop UI communicates with the runtime strictly via the loopback Channel Daemon (`http://127.0.0.1:8420`) authenticated via a secure bearer token.
3. **Client-Side Cryptographic Signing:** Human approvals in the UI do not approve by setting a database flag; they sign an HMAC preimage using `token-hmac-v1` via WebCrypto and submit the signature to the daemon for kernel verification.
4. **No Direct Backend State Mutation:** The Desktop UI must never bypass the Channel Daemon, connect directly to PostgreSQL/Redis, or modify runtime files directly.

---

## 7. Deterministic Core Independence (The Core Boundary Rule)

The dependency direction is strictly one-way. The deterministic core must remain independent from higher-level cognitive, agentic, or channel layers.

```text
core/ MUST NOT import from:
  - agents/
  - workers/
  - skills/
  - workflows/
  - llm/
  - channels/
  - memory/
```

This boundary is mandatory and enforced by automated AST checks (`scripts/dep_guard.py`).

**Forbidden Techniques:**
* Dynamic imports (`importlib.import_module`, `__import__`) inside `core/` to reach higher layers.
* Runtime `sys.path` manipulation.
* Plugin registries that inject higher-layer dependencies into core without formal interfaces.
* Circular imports or monkey-patching.

---

## 8. Contracts & Schema Governance

Contracts under `contracts/registry/` are the authoritative definition of system behavior:

```text
contracts/registry/
├── pulse-types.json        # Authoritative registry of all pulse types
├── payload-schemas/        # 1:1 JSON schemas for every registered pulse type
├── failure-taxonomy.json   # Standardized failure taxonomy
├── capability-risks.json   # Capability risk tier definitions (TIER_0..TIER_3)
└── grants.json             # Device grant schemas and scopes
```

### Contract Modification Discipline
1. Contracts must be updated **before** code that consumes them.
2. Any contract change requires re-running code generation (`python contracts/codegen/python/generate_pulse_models.py`).
3. Automated contract sync (`scripts/contract_sync.py`) must pass cleanly.
4. Never silently modify a contract or payload schema to make a broken implementation or test pass.

---

## 9. ADR Discipline

Architectural Decision Records live permanently under:
```text
adr/NNNN-short-title.md
```

### When an ADR is Required
An ADR is required when a change introduces or materially modifies:
* Authority boundaries or data ownership
* Communication architecture or transport invariants
* Persistence or recovery models
* Security, isolation, or trust models
* Runtime topology or protocols
* Architectural invariants or dependency rules

### When an ADR is NOT Required
Do not create ADRs for:
* Ordinary bug fixes
* Routine UI presentation or styling
* Refactoring that preserves all boundaries and contracts
* Adding tests or harness cases
* Implementing an already-contracted feature

### ADR Rules
* ADR numbering is GLOBAL, MONOTONIC, and CONTINUOUS (0001, 0002, ...).
* ADRs are append-only. Never rewrite historical decisions. If a decision changes, file a new ADR that references and supersedes the former.
* Every ADR must contain: **Context/Problem**, **Decision**, and **Consequences**.

---

## 10. PROJECT_MEMORY Discipline

Project memory entries live permanently under:
```text
PROJECT_MEMORY/NNNN-short-title.md
```

### Criteria for New Memory Entries
Create a project memory entry only for material milestones:
* Major phase or release completion
* Architectural consolidation or governance hardening
* Significant security baseline establishment
* Resolution of complex architectural trade-offs

Do NOT create memory entries for trivial daily commits, styling fixes, or minor edits.

Every memory entry must document:
1. **WHAT changed**
2. **WHY it changed**
3. **WHAT was verified (with concrete test/gate metrics)**
4. **WHAT remains or is planned next**
5. **Exact commit or release baseline**

---

## 11. Release Governance

Release history is maintained in:
```text
docs/RELEASES.md
```

Every release entry must connect:
* Semantic version (`vX.Y.Z`)
* Release date
* Git commit hash
* Git tag name
* Verification gate status (e.g. V1-001 through V1-006 PASS)
* Test suite results (passed/skipped/failed)
* ADR range / relevant ADRs
* Project Memory reference
* Evidence report path

Git tags remain the exact cryptographic identity of a release. Do not tag releases without explicit human instruction.

---

## 12. Evidence & Verification Lifecycle

RYU AI enforces an evidence-based completion model. No feature or contract is complete without executable proof:

$$\text{SPECIFIED} \longrightarrow \text{CONTRACTED} \longrightarrow \text{IMPLEMENTED} \longrightarrow \text{UNIT\_VERIFIED} \longrightarrow \text{INTEGRATION\_VERIFIED} \longrightarrow \text{CHAOS\_VERIFIED} \longrightarrow \text{SECURITY\_VERIFIED} \longrightarrow \text{GATE\_VERIFIED}$$

* Never claim a feature is complete because a class exists or code compiles.
* Never use fake tests (`assert True`), mock-only verification for durable components, or weakened assertions.
* When integration services are required, use real PostgreSQL and Redis under `deploy/docker-compose.yml`.

---

## 13. Prohibited Behaviors

Coding agents operating on RYU AI are strictly prohibited from:
1. **Creating duplicate subsystems:** Do not build a second bus, a second approval engine, or a second memory manager.
2. **Bypassing architectural authority:** Do not grant capabilities, bypass CAS plan versions, or resolve secrets outside defined managers.
3. **Silently modifying contracts:** Do not alter schemas to make tests pass.
4. **Fabricating verification:** Never report "All tests passed" without executing tests.
5. **Claiming unsupported milestones:** Distinguish strictly between `IMPLEMENTED` and `GATE_VERIFIED`.
6. **Renaming historical files:** Never rename historical ADRs (`0001`..`0039`) or memory entries (`0001`..`0014`).
7. **Creating version-specific governance directories:** Do not create `adr/v1.0/`, `contracts/v1.0/`, or `PROJECT_MEMORY/v1.0/`.
8. **Placing cognitive logic in `core/`:** Core must remain strictly deterministic.
9. **Swallowing exceptions:** Always escalate, record, or publish failures via typed pulses.
10. **Committing secrets:** Never place real credentials or API keys into git, pulses, traces, or test fixtures.

---

## 14. Git Discipline & Safety

* **Focused commits:** Commit messages must follow conventional commits:
  * `feat(scope): ...`
  * `fix(scope): ...`
  * `test(scope): ...`
  * `docs(scope): ...`
* **Clean working tree:** Do not leave uninspected generated files or temporary test artifacts in the repository.
* **No force-pushing:** Never force-push or alter pushed Git history.
* **Never push without instruction:** Do not push to remote repositories unless explicitly instructed by the user.

---

## 15. Final Reporting Standard

Every substantial agent engineering turn must report:
1. **What changed:** Exact file paths modified, created, or deleted.
2. **What was verified:** Exact test commands run, test counts passed/skipped/failed.
3. **What was not implemented:** Clear boundaries of what was deferred.
4. **Applicable contracts & specs:** Relevant contract IDs and spec sections.
5. **ADR & Memory status:** Whether an ADR or Memory entry was added.
6. **Remaining blockers / next steps:** Concrete, actionable next tasks.

---

## 16. Governing Principle

> **If an architectural requirement cannot be traced from specification $\longrightarrow$ contract $\longrightarrow$ implementation boundary $\longrightarrow$ executable evidence, it is not proven.**

Build deterministically. Preserve history. Protect authority boundaries. Verify continuously.  
**The repository must earn every claim it makes.**
