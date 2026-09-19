# ADR-0011: Secret Sanitization and Leakage Prevention in LLM Recording

## Status
Accepted (Phase 5, Resolves `docs/CONTRACT_MATRIX.md` `OPEN-007`)

## Context
`docs/CONTRACT_MATRIX.md` defines `OPEN-007`: "Secret sanitization boundary for LLM recording".
In RYU AI, credentials must never leak into persisted logs, traces, prompts, or LLM recordings (Architecture §10, Law 1). Phase 2 established `SecretStore` and `SecretRef` (`secret://<provider>/<name>`), where credentials remain opaque URIs until execution inside a secure boundary. If LLM prompts, completions, tool results, or error messages contain resolved secret values, persisting them in `LLMRecord` or Event Timelines would breach security containment.

## Decision
1. **Opaque Reference Preservation:**
   - URIs matching `secret://<provider>/<name>` are recognized as valid opaque references. They must NOT be redacted or altered, as they carry no plaintext credential data.

2. **Resolved Secret Sanitization Boundary:**
   - Reuses the existing Phase 2 `SecretStore` / `SecretResolver` architecture.
   - The `SecretSanitizer` intercepts all data passed to `LLMRecorder.record()`:
     - Prompts and message arrays
     - Model response text and structured outputs
     - Nested dictionaries, lists, and primitives
     - Exception and error details
     - Metadata and tool execution outputs
   - Any string or payload matching an active registered secret value in `SecretStore` is masked with `[REDACTED_SECRET]`.

3. **No Direct Secret Authority in Agent/LLM (Correction 4):**
   - The LLM does NOT receive a `SecretResolver` capability.
   - The Agent does NOT directly own secret resolution authority.
   - Any malicious `AgentProposal` attempting `read_secret` or direct extraction is rejected deterministically before any capability execution.

4. **Inviolable Invariant:**
   - `resolved_secret not in persisted_record` across all 10 adversarial vectors:
     1. Secret in prompt
     2. Secret in model response
     3. Secret in structured JSON
     4. Secret inside nested JSON
     5. Secret inside an exception
     6. Secret inside metadata
     7. Secret inside tool output
     8. Multiple distinct secrets
     9. Secret appearing multiple times
     10. Secret embedded inside a longer string

## Consequences
- Guarantees zero credential leakage into LLM traces, replay logs, or Event Timelines.
- Fully resolves `CONTRACT_MATRIX` `OPEN-007`.

