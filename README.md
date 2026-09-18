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

## Current Phase

```text
Phase 0 — Repository Scaffold
```

Phase 0 establishes repository structure, contract registry, code-generation foundations, Rust workspace skeleton, and the initial deterministic Pulse Bus verification harness. No Phase 1+ runtime behavior is implemented.

---

## Development Commands

```bash
make setup      # Environment check & setup
make contracts  # Contract synchronization check
make codegen    # Execute code generators
make test       # Run Phase 0 Pulse Bus unit tests
make harness    # Run executable harness cases
make lint       # Run Ruff linter and dependency guard
make node       # Validate Rust workspace (cargo check & clippy)
make all        # Execute full Phase 0 verification suite
```

