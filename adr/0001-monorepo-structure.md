# ADR-0001 — Monorepo Structure Decision

**Status:** Accepted
**Date:** 2026-09-18
**Deciders:** Architecture team (single-architect)
**Affects spec sections:** docs/Architecture §1–§19, AGENTS.md §4, ROADMAP.md Phase 0

---

## Context

RYU AI is being rebuilt from a clean state as a Space-Centric Cognitive Architecture (SCCA). The system spans multiple languages (Python runtime, Rust node runtime), multiple architectural layers (core infrastructure, orchestration, agents, workers, skills, nodes), and requires strict dependency direction enforcement between those layers.

A structural decision was needed for how to organize the repository before any production code is written.

---

## Decision

**Use a single Git monorepo** with a language-specific workspace layout at the repository root.

The root repository structure is:

```
RYU/
├── core/          # Deterministic Python core — lowest dependency level
├── orchestrator/  # Thin Space Orchestrator
├── agents/        # Cognitive Agent state machines
├── workers/       # Execution Workers
├── skills/        # Reusable skill packages
├── workflows/     # Named workflow orchestrations
├── node_runtime/  # Rust Cargo workspace for Node Runtime
├── contracts/     # Machine-readable contract registry (source of truth)
├── harness/       # Executable specification harness
├── sdk/           # Future public SDK
├── memory/        # Memory adapters
├── channels/      # Human channel implementations
├── observability/ # Metrics, logging, tracing
├── deploy/        # Deployment configuration
├── docs/          # Frozen architecture documentation
├── adr/           # Architecture Decision Records
├── scripts/       # Dev tooling scripts
└── .github/       # CI workflows
```

---

## Dependency Direction

The core architectural rule (AGENTS.md §4, ROADMAP Operating Principle 4):

```
core/ MUST NOT import agents/, workers/, skills/, or workflows/
```

This is enforced by:
1. **Structural convention** — packages are organized in dependency order.
2. **AST-based guard** — `scripts/dep_guard.py` scans all Python files under `core/` and fails on any forbidden import.
3. **CI enforcement** — `.github/workflows/dep-guard.yml` runs on every push/PR affecting `core/`.

If `core/` requires an abstraction implemented by a higher layer, the abstraction is defined at the lower boundary and the higher layer implements it — not the reverse.

---

## Contract-First Architecture

Per ROADMAP Operating Principle 3:

> Contracts before code. Anything touching `contracts/registry/` merges before anything consuming it.

The machine-readable contract registry (`contracts/registry/`) is the single source of truth for:
- `pulse-types.json` — all 38 registered Pulse types
- `payload-schemas/` — JSON Schema per type
- `failure-taxonomy.json` — transient/terminal error classes
- `capability-risks.json` — risk tier definitions
- `grants.json` — ResourceGrant, NodeCapabilityGrant, Lease

Code generators in `contracts/codegen/` derive Python and Rust types from the registry. Manual duplication of contract definitions is forbidden.

---

## Harness as Specification

Per ROADMAP Operating Principle 2:

> The harness is the spec. A feature exists when its `harness/cases/` case passes AND its `harness/spec_map.yaml` entry maps to an ✅ acceptance criterion in `docs/Architecture`.

`harness/spec_map.yaml` maps every testable architecture criterion to:
- Its contract
- Its harness case file
- Its roadmap phase

A directory existing, a class compiling, or a README claiming — none of these constitute proof.

---

## Alternatives Considered

### Alternative 1: Separate repositories per layer
**Rejected.** Cross-repo dependency management during active development creates version coupling overhead without benefit at this stage. The dependency direction is already well-defined by the architecture; enforcing it within a monorepo is tractable via CI.

### Alternative 2: Python namespace packages with pip editable installs across repos
**Rejected.** Same overhead as separate repos. The contract-first architecture requires the registry to be the single source of truth; distributing it across repos increases sync risk.

### Alternative 3: Full polyglot monorepo tool (Bazel, Buck)
**Rejected for Phase 0.** The build complexity of a full monorepo build tool does not pay off until there are significantly more language boundaries. Phase 0 uses `cargo` for Rust and `pytest`/`ruff` for Python, orchestrated by a simple `Makefile`. This can be revisited in Phase 7+ when the Node Runtime requires cross-language build integration.

---

## Affected Architecture Sections

- `docs/Architecture` §6 (Pulse Bus)
- `docs/Architecture` §12 (Core Runtime)
- `docs/Architecture` §16 (Component Contracts)
- `AGENTS.md` §4 (Dependency Direction)
- `AGENTS.md` §9 (Phase Discipline)
- `ROADMAP.md` Phase 0 deliverables

---

## Consequences

**Positive:**
- Single source of truth for contracts, enforced by CI.
- Dependency direction is testable at every PR.
- Harness and implementation co-evolve in the same repo, reducing drift.
- Node Runtime (Rust) lives in the same repo as its Python consumers, ensuring protocol changes are visible.

**Constraints:**
- All contributors must follow the one-way dependency rule.
- The `contracts/registry/` directory is append-only except via deliberate versioned change.
- Architecture changes require the ADR process defined in `AGENTS.md §33`.

---

## Phase 7 Platform Note

The Node Runtime is structured as a platform-independent Cargo workspace (`node_runtime/`). The first physical-device validation target is a **Windows host**. The available Linux development environment is Ubuntu 22.04.5 LTS (WSL2/x86_64).

WSL2 is an accepted development and complementary validation environment but is not equivalent to native Linux physical hardware. Future platform implementations (Linux, macOS, Android, iOS, Raspberry Pi) must conform to the same Node Runtime contract, which is defined at `contracts/registry/` — not platform-specific code.

This structural decision does not implement any platform-specific Node Runtime behavior. That belongs to Phase 7.

