# RYU AI — Project Memory

## Entry 0001 — Phase 0 Bootstrap

**Date:** 2026-09-18
**Phase:** 0 — Repository Scaffold
**Status:** IN PROGRESS

---

### Summary

Phase 0 of the RYU AI clean rebuild has been started. This entry records the initial state and ground rules for all future contributors and agents operating in this repository.

---

### Architecture State

- **Architecture:** Space-Centric Cognitive Architecture (SCCA) — FROZEN
- **Architecture document:** `docs/Architecture` — do not overwrite, duplicate, or rename
- **Contract Matrix:** `docs/CONTRACT_MATRIX.md` — authoritative verification index
- **Roadmap:** `ROADMAP.md` — authoritative phase gates

The frozen architecture is defined by `docs/Architecture`. Changes are only permitted via the ADR process defined in `AGENTS.md §33`.

---

### Repository State

```
Phase 0 started          2026-09-18
Clean rebuild            true — no previous milestones, tests, or coverage inherited
Architecture frozen      docs/Architecture (preserved, not modified)
Contract-first           contracts/registry/ is source of truth
Harness-as-spec          harness/spec_map.yaml maps criteria to tests
```

---

### What Has Been Established (Phase 0)

- `contracts/registry/pulse-types.json` — 38 registered Pulse types
- `contracts/registry/payload-schemas/` — 38 JSON Schema files
- `contracts/registry/failure-taxonomy.json`
- `contracts/registry/capability-risks.json`
- `contracts/registry/grants.json`
- `core/pulse_bus/` — Phase 0 Pulse Bus (validate, reject, taint, subscribe, walk)
- `harness/cases/pulse_bus/` — 3 real harness test cases
- `harness/cases/{space,kernel,resources,orchestrator,agents,workers,node,security}/` — future cases (skipped)
- `harness/spec_map.yaml` — spec traceability map
- `scripts/dep_guard.py` — AST-based dependency boundary guard
- `scripts/contract_sync.py` — §16 ↔ registry sync check (Phase 0 scope)
- `node_runtime/` — Rust workspace (cargo check + clippy PASS)
- `adr/0001-monorepo-structure.md` — repository structure ADR

---

### What Has NOT Been Implemented

```
LLM integration          NOT IMPLEMENTED — Phase 5
Ollama/OpenAI            NOT IMPLEMENTED
Agent reasoning          NOT IMPLEMENTED — Phase 5
Worker execution         NOT IMPLEMENTED — Phase 6
Space Kernel             NOT IMPLEMENTED — Phase 2
Admission Control        NOT IMPLEMENTED — Phase 2
Resource Manager         NOT IMPLEMENTED — Phase 3
Sandbox                  NOT IMPLEMENTED — Phase 6
Node capabilities        NOT IMPLEMENTED — Phase 7
Node pairing/grants      NOT IMPLEMENTED — Phase 7
MCP                      NOT IMPLEMENTED — Phase 9
Memory adapters          NOT IMPLEMENTED — Phase 10
Vector database          NOT IMPLEMENTED — Phase 10
Learning/adaptation      NOT IMPLEMENTED — Phase 10
Voice runtime            NOT IMPLEMENTED — Phase 8
Production Pulse Bus     NOT IMPLEMENTED — Phase 1 (durable, PostgreSQL/Redis)
```

---

### Environment Note

| Component | Required | Found (Windows host) | Status |
|-----------|----------|---------------------|--------|
| Python    | >=3.12   | 3.11.9              | BLOCKED |
| cargo     | stable   | 1.98.1              | OK |
| ruff      | latest   | 0.15.20             | OK |
| pytest    | >=7.0    | 9.0.2               | OK |
| jsonschema | >=4.21  | not installed       | MISSING |

**Python 3.12+ is a non-negotiable project requirement.** The current host Python 3.11.9 is an environment blocker. The project specification must NOT be weakened to accommodate this.

To run tests locally, install Python 3.12+ or use a virtual environment / container with 3.12+, then `pip install jsonschema pytest`.

---

### Governing Rules

1. **Contracts before code.** `contracts/registry/` merges before consumers.
2. **Harness is the spec.** Evidence = passing harness case + spec_map.yaml entry.
3. **core/ must not import agents/, workers/, skills/, workflows/.** Enforced by dep-guard.
4. **Phase discipline.** Do not implement Phase 1+ behavior until Phase 0 exit gate passes.
5. **No silent failures.** Every rejection, error, and escalation must be surfaced.
6. **Architecture is frozen.** Changes only via ADR process.

---

### Phase 0 Exit Gate (ROADMAP)

```
make contracts && make test          must be green
3 real harness cases                 must PASS
Future harness cases                 must SKIP (with spec citation)
All CI workflows                     must be green on merge commit
adr/0001-monorepo-structure.md       must be merged
```

Phase 0 is NOT complete merely because the scaffold exists. Exit gate verification required.

