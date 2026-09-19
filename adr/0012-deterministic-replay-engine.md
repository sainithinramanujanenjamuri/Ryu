# ADR-0012: Deterministic Replay Engine and Equivalence Taxonomy

## Status
Accepted (Phase 5)

## Context
Phase 5 requires that any observed Agent execution be reproducible from recorded traces (ROADMAP Phase 5 Exit Gate). In stochastic systems, replay cannot call live LLMs, as external APIs exhibit temperature variance, network jitter, and non-reproducible model outputs. Furthermore, claiming "bit-identical" replay without explicitly controlling sources of nondeterminism (timestamps, UUIDs, clocks) leads to false architectural claims (Correction 2).

## Decision
1. **Nondeterminism Control & Classification (Correction 2):**
   - **Recorded and Replayed:** LLM prompts, model responses, structured outputs, parameters, token usage.
   - **Injected Deterministically:** Injected clocks/timestamps (`FakeClock`), canned correlation sequences, deterministic seeded generators.
   - **Regenerated and Verified:** Agent state machine transitions, parsed `AgentProposal`s, proposal validation outcomes, PlanDelta proposals.
   - **Intentionally Excluded:** Wall-clock time during live replay execution (unless a deterministic clock is injected).

2. **Replay Engine Architecture:**
   - Implement `ReplayLLMProvider` satisfying `LLMProvider`.
   - During replay, `ReplayLLMProvider` retrieves recorded responses from `LLMRecorder` by `(space_id, correlation_id, call_index)`.
   - **Hard Invariant:** Live LLM provider calls during replay are strictly 0 (`calls_to_live_llm == 0`).

3. **Equivalence Taxonomy & Claim Standard:**
   - RYU distinguishes and reports the exact level of equivalence proven:
     - `response-identical`: The exact model output string and structured dictionary match the recorded call.
     - `decision-identical`: The parsed `AgentProposal` (action, intent, parameters) matches identically.
     - `state-transition-identical`: The full state sequence (`IDLE -> THINKING -> PROPOSING -> ...`) matches bit-for-bit.
     - `event-sequence-identical`: The sequence of emitted Pulses matches in type, severity, and payload.
     - `byte-identical`: Proven for deterministic structures when time/seeds are held constant via `FakeClock`.

## Consequences
- Guarantees true reproducibility without relying on external model provider stability.
- Prevents overclaiming equivalence while maintaining rigorous verification standards.

