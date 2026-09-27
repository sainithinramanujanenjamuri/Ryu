# RYU AI

RYU AI is an agentic AI system engineered on the **Space-Centric Cognitive Architecture (SCCA)**.

---

## The Six Immutable Laws of Ryu

1. **Everything Happens Inside a Space.** All work, memory, agents, resources, and artifacts exist within a Space.
2. **Capabilities Are Requested, Never Owned.** Agents request; Workers execute; Tools provide capabilities.
3. **Components Communicate Through Pulses.** Pulses are the nervous system of Ryu; every Pulse has a fixed, typed schema.
4. **Knowledge Belongs to the Space First.** Promote to global knowledge only after validation or human approval.
5. **Humans Define Goals; Ryu Organizes Execution.** You define what; Ryu decides how.
6. **Failures Are Contained, Escalated, and Never Silent.** Failures escalate deterministically through Tool → Worker → Agent → Space Orchestrator → Human.

---

## Architecture Hierarchy

```text
Runtime
  ↓
Spaces
  ↓
Execution Plans
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

---

## Build Order

```text
Contracts
  ↓
Pulse Bus
  ↓
Harness
  ↓
Core
  ↓
Mock Cognitive Layer
  ↓
Agents
  ↓
Workers
  ↓
Nodes
  ↓
Memory
```

---

## Core Boundary Rule

```text
core/ MUST NOT import agents/, workers/, skills/, or workflows/.
```

The dependency direction is strictly one-way. Deterministic core infrastructure must remain independent of higher-level agentic and execution layers. This boundary is enforced by automated AST checks.

---

## Operational State & Release Baseline

```text
v1.0.0 — Production Release (Phases 0–11 Complete & Gate-Verified)
```

RYU AI v1.0.0 has passed the Master Release Gate battery (`V1-001` through `V1-006`) with 649 passing tests, 0 failures, 100% core independence, deterministic replay equivalence, and Desktop Command Center integration. See [`docs/RELEASES.md`](docs/RELEASES.md) for full release details.

---

## Verification & Development Commands

```bash
python scripts/contract_sync.py              # Verify pulse contract registry & codegen sync
python scripts/dep_guard.py                  # Verify core/ AST import independence
python scripts/v1_audit_governance.py        # Verify ADRs, pulse types, schemas, & matrix
python scripts/v1_audit_spec_coverage.py     # Verify spec criteria & executable test mappings
python scripts/v1_release_gate.py            # Execute complete v1.0 master release battery
pytest                                       # Run full test suite (649 tests)
```

