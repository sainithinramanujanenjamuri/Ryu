# ADR-0009: LLM Provider Boundary, Call Recording, and Space Scoping

## Status
Accepted (Phase 5)

## Context
Phase 5 introduces stochastic Large Language Model (LLM) cognition into RYU AI under the Space-Centric Cognitive Architecture (SCCA). Per Law 1 ("Everything Happens Inside a Space") and Law 2 ("Capabilities Are Requested, Never Owned"), stochastic components must remain strictly separated from the deterministic core (`core/`). Furthermore, every LLM interaction must be transparent, observable, and reproducible for debugging, auditing, and future adaptation (ROADMAP Phase 5, Architecture §12).

## Decision
1. **Provider-Neutral Interface:**
   - Define `LLMProvider` as a protocol accepting `LLMRequest` and returning `LLMResponse`.
   - Concrete providers (Ollama, OpenAI, Anthropic, Mock) live strictly behind this interface in `llm/`.
   - The deterministic core (`core/`) MUST NOT import any module from `llm/` or concrete provider SDKs (enforced via `scripts/dep_guard.py`).

2. **Explicit Provider Contract:**
   - `LLMRequest`: `request_id`, `correlation_id`, `space_id`, `agent_id`, `model`, `provider`, `messages`, `parameters`, `timestamp`.
   - `LLMResponse`: `request_id`, `content`, `structured_output`, `usage` (`LLMUsage`), `status`, `error` (`LLMError`).
   - Provider failures are deterministically mapped into the existing RYU failure taxonomy (`transient.timeout`, `transient.rate_limit`, `transient.network`, `terminal.invalid_params`).

3. **Space-Scoped Call Recording:**
   - Define `LLMRecorder` protocol and `InMemoryLLMRecorder`.
   - Every LLM invocation is persisted under `(space_id, correlation_id, call_id)`.
   - Cross-Space recording access is strictly forbidden: a request from Space A attempting to read or query recordings from Space B raises `PermissionError` (Law 1, Law 4).

4. **Retention Policy:**
   - Default retention is Space-lifetime (survives across agent steps within the Space, exportable, and deletable by the Space owner).

## Consequences
- The deterministic core remains 100% LLM-free and verifiable without external model dependencies.
- All LLM interactions are captured with complete execution provenance.
- Cross-Space data leakage via model call logs is structurally prevented.

