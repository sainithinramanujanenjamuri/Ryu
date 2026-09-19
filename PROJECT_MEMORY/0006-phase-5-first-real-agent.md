# RYU AI — Project Memory

## Entry 0006 — Phase 5 First Real Agent, LLM Boundary, Recording, Deterministic Replay, and Context Scopes

**Date:** 2026-09-19  
**Phase:** 5 — First Real Agent + LLM Boundary + Recording + Deterministic Replay + Context Scopes  
**Status:** COMPLETE (PHASE 5 GATE: PASS)  
**Previous Baseline:** 87a6ea4 (Phase 4)

---

### Summary

Phase 5 introduces the first genuine stochastic intelligence layer into RYU AI under the Space-Centric Cognitive Architecture (SCCA). The fundamental governing invariant of Phase 5 is:
> **LLMs may provide cognition; they do not receive authority.**

The LLM operates strictly as a stochastic transition function for the deterministic Agent state machine during `AgentState.THINKING`. At no point can an LLM or Agent directly mutate the authoritative TaskGraph, allocate resources, mint leases, bypass Space Kernel admission or budget, self-approve human gates, access raw secrets, execute arbitrary shell commands, or breach Space isolation boundaries. All capability executions remain strictly mediated by the Space Kernel and Resource Manager.

Phase 5 rigorously incorporated and verified all 10 Mandatory Corrections:
1. **Strict Dependency Direction Enforced:** `core/` contains 0 imports of `agents`, `workers`, `skills`, `workflows`, `llm`, `openai`, `anthropic`, `ollama`, or `transformers`. AST dependency guard enforced in CI.
2. **Honesty in Replay Equivalence:** Formal distinction between `response-identical`, `decision-identical`, `state-transition-identical`, `event-sequence-identical`, and `byte-identical` replay modes. Zero live provider calls during replay is strictly proven.
3. **Secret Sanitization in Recordings:** All 10 adversarial vectors are masked to `[REDACTED_SECRET]` prior to persistence; opaque `secret://...` URIs are preserved. Resolves `OPEN-007` per ADR-0011.
4. **Zero Secret Authority:** The Agent and LLM possess no `SecretResolver` instance and no authority to inspect raw credentials. Attempted secret resolution is rejected deterministically.
5. **Context Scopes Hierarchy:** Three distinct nested scopes: `SpaceContext` > `AgentContext` > `TaskContext`. Visibility does not imply mutation authority: upward mutations (`Task -> Agent`, `Task -> Space`, `Agent -> Space`) are rejected with `PermissionError`.
6. **Real 500-Turn Context Compaction:** Unpinned observation turns are evicted (>400 turns evicted across 500 turns), while pinned goals, safety constraints, and `HandoffNote` artifacts strictly survive.
7. **Minimal Researcher Role:** Implemented `ResearcherRole` without multi-agent framework creep.
8. **Malicious LLM Adversarial Test Suite:** Proved deterministic defense against prompt injections, hallucinated authority, malformed schemas, and unpermitted capabilities.
9. **Invariant Proof Over Arbitrary Test Counts:** Full coverage of all constitutional non-authority guarantees.
10. **Formal 12-Attack Authority Proof Table:** All 12 critical authority bypass attacks verified and blocked.

---

### Architecture & Contract Foundations

- **ADR-0009:** `adr/0009-llm-provider-boundary-and-recorder.md` — Defines provider-neutral abstraction (`LLMProvider`, `LLMRequest`, `LLMResponse`, `LLMError`, `LLMUsage`), structured output schemas, and Space-scoped call recording (`LLMRecorder`).
- **ADR-0010:** `adr/0010-agent-state-machine-and-context-scopes.md` — Formalizes the 9-state deterministic Agent lifecycle, structured `AgentProposal` validation, 3-tier context hierarchy, upward mutation rejection, and 80% compaction threshold.
- **ADR-0011:** `adr/0011-secret-sanitization-in-llm-recording.md` — Resolves `docs/CONTRACT_MATRIX.md` `OPEN-007`. Formulates the sanitization boundary across 10 adversarial vectors, preserving opaque `secret://` handles and replacing raw resolved values with `[REDACTED_SECRET]`.
- **ADR-0012:** `adr/0012-deterministic-replay-engine.md` — Defines the deterministic replay engine (`ReplayLLMProvider`), proves 0 live provider calls during replay, and formalizes the 5-level equivalence taxonomy.
- **Contract Traceability:** `docs/CONTRACT_MATRIX.md` updated with `GATE_VERIFIED` for `AGENT-001` through `AGENT-007`. `OPEN-007` marked resolved.
- **Spec Map:** `harness/spec_map.yaml` updated with all Phase 5 mappings.

---

### What Was Built

1. **LLM Subsystem (`llm/`):**
   - `llm/provider.py`: `LLMRequest`, `LLMResponse`, `LLMUsage`, `LLMError` (inheriting from `Exception`), `LLMProvider` Protocol, `MockLLMProvider` with canned responses and failure simulation.
   - `llm/sanitizer.py`: `SecretSanitizer` integrating with `core.security.secrets.SecretStore` to mask resolved credentials across all 10 adversarial vectors while preserving opaque references.
   - `llm/recorder.py`: `LLMRecord` and `InMemoryLLMRecorder` with automatic pre-persistence sanitization and Space isolation enforcement.
   - `llm/replay.py`: `ReplayLLMProvider` delivering deterministic recorded responses, asserting `live_calls_count == 0`, and enforcing correlation and sequence isolation.

2. **Agent Subsystem (`agents/`):**
   - `agents/base.py`: `AgentState` enum, `AgentProposal`, `ProposalValidator` (intercepting 12 direct authority bypass attempts), and `BaseAgent` state machine (`IDLE` -> `THINKING` -> `PROPOSING` -> `WAITING` -> `EXECUTING` -> `OBSERVING` -> `REFLECTING` -> `COMPLETED`/`FAILED`).
   - `agents/context.py`: `ContextScope` (`TASK`, `AGENT`, `SPACE`), `ContextEntry`, `HandoffNote` recovery contract, and `ContextManager` enforcing upward mutation rejection and real 500-turn compaction.
   - `agents/roles/researcher.py`: Minimal `ResearcherRole` demonstrating goal breakdown and evidence gathering without authority bypass.

3. **Harness & Verification Suites (`harness/cases/agents/`):**
   - `test_agents_future.py`: Activated `AGENT-001` (state machine), `AGENT-002` (dependency boundary), `AGENT-006` (500-turn compaction).
   - `test_authority_boundaries.py`: 12-attack authority proof table proving LLM/Agent cannot bypass Kernel, Resources, or Secrets.
   - `test_adversarial_llm.py`: Adversarial prompt injections, malformed responses, and privilege escalation attempts.
   - `test_deterministic_replay.py`: Proves `calls_to_live_llm == 0`, `state-transition-identical`, and `decision-identical` replay.
   - `test_context_scopes_and_compaction.py`: Scopes isolation, upward mutation blocks (`Task -> Agent`, `Agent -> Space`), and 500-turn compaction.
   - `test_secret_leakage_adversarial.py`: Proves end-to-end secret containment across SecretStore -> LLM -> Recorder -> Replay across all 10 vectors.
   - `test_chaos_v3.py`: 18 chaos and degradation scenarios (timeouts, unavailabilities, crashes, CAS collisions, approval timeouts).
   - `harness/cases/kernel/test_kernel_future.py`: Activated `KERNEL-005` proving Agent execution supervision by Space Kernel Admission.

---

### Verification Evidence

- **Pytest Suite:** **261 passed, 16 skipped in 5.72s** (0 failures).
- **Phase 5 Specific Tests:** **76 passed in 0.59s**.
- **Dependency Guard (`scripts/dep_guard.py`):** **PASS** (zero imports of `agents`, `workers`, `skills`, `workflows`, `llm`, `openai`, `anthropic`, `ollama`, `transformers` inside `core/`).
- **Contract Synchronization (`scripts/contract_sync.py`):** **PASS** (all pulse models synchronized with registry).
- **Linter (`ruff check .`):** **PASS** (0 errors).
- **Type Checker (`mypy .`):** **PASS** (0 errors).
- **Rust Workspace (`cargo check`):** **PASS** (compiles cleanly).

---

### What Was NOT Implemented (Phase Boundary Discipline)

Per Phase 5 discipline, the following remain strictly unimplemented:
- No Worker execution or process spawning (Phase 6).
- No Sandbox containment or seccomp filtering (Phase 6).
- No Node Runtime client daemon or hardware node execution (Phase 7).
- No Human communication channels / Slack / Web UI (Phase 8).
- No MCP client or external skill integrations (Phase 9).
- No Vector database or Cross-space knowledge promotion (Phase 10).

