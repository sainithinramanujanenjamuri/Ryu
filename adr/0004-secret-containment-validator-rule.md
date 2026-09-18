# ADR 0004: Secret Containment and Validator Exact-Match Rule

## Status
Accepted

## Context
In RYU AI Space-Centric Cognitive Architecture (SCCA), secrets and credentials must be strictly contained within authorized execution boundaries. Secrets are referenced in `CapabilityRequest.params` using URIs conforming to `secret://<provider>/<name>`. Resolving a secret must happen at the latest possible moment inside the execution boundary (sandbox).

Law 6 and security requirements mandate that resolved secret values must **never** leak into Pulse payloads, logs, event streams, or persistent stores. Using heuristic pattern matching (such as regex detection for high-entropy strings) introduces severe false positives and flakiness. Therefore, a deterministic, machine-enforced rule must be codified for secret containment validation.

## Decision

1. **Definition of Registered Secret:**
   - A secret reference registered in `SecretStore` maps a canonical URI (`secret://<provider>/<name>`) to an exact sensitive value (e.g., token, password, private key).
   - Secret values have a minimum non-empty length requirement (minimum 6 characters to prevent trivial single-letter substring collisions).
2. **Definition of Resolved Secret Value:**
   - The plain string payload obtained when an authorized sandbox calls `SecretResolver.resolve("secret://...")`.
3. **Exact Matching Semantics (No Heuristics):**
   - The Pulse Validator inspects incoming Pulse payloads against the exact string representations of all active registered secret values.
   - If any registered secret string appears as an exact substring within any string field in the payload, the payload is deemed leaked.
   - Heuristic pattern-based guessing is forbidden; only known, registered secret values trigger rejection.
4. **Recursive Payload Traversal:**
   - The validator recursively traverses all dictionary keys, dictionary values, and array elements within `Pulse.payload`.
   - Any string value found at any depth is tested for exact containment of any registered secret.
5. **Types Evaluated:**
   - String values are evaluated directly.
   - Non-string primitive values (integers, floats, booleans, null) do not trigger secret containment rules unless coerced into a string structure.
6. **Serialization Boundary:**
   - The containment check executes synchronously inside `PulseValidator.validate()` before any store append (`store.append`) or transport publication (`transport.publish`).
7. **Rejection Behavior:**
   - Upon detection of a secret value, the bus synchronously raises:
     ```python
     PulseRejectedError(
         reason="secret_leak_detected",
         offending_type=pulse.type,
         details="Pulse payload contains a resolved secret value."
     )
     ```
   - The rejected Pulse **never** enters PostgreSQL.
   - The rejected Pulse **never** enters Redis Streams.
   - The error message **must not** echo or log the leaked secret value.
8. **Historical Data Invariance:**
   - Rejection is enforced at publication time.
   - Replaying historical Pulses from PostgreSQL reads persisted events without re-evaluating secret rules, guaranteeing historical replay consistency.
9. **Secret Reference (`SecretRef`) Safety:**
   - Passing `secret://<provider>/<name>` in parameters is valid and expected. The URI string itself is not a secret value and is not rejected.

## Consequences
- Guaranteed zero-leakage of active credentials to the Pulse Bus and durable event history.
- Deterministic, false-positive-free validation.
- Clear separation between capability parameterization (references) and capability execution (resolution).

