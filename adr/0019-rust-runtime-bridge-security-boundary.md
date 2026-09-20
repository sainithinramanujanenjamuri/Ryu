# ADR-0019: Rust Runtime Bridge Security Boundary

## Context
Phase 7 requires a compiled native runtime component (`ryu-node`) to perform hardware discovery, local cryptographic grant validation, and local audit logging. The boundary between the Python core and the Rust native runtime must remain narrow, typed, and secure against arbitrary code execution.

## Decision
1. **Narrow Typed Operations Only:** The Rust bridge exposes only explicitly defined operations via CLI subcommands / structured JSON:
   - `inspect`: Read-only platform and device topology discovery.
   - `validate-grant`: Cryptographic HMAC-SHA256 signature and expiry verification.
   - `bind`: Controlled device binding and audit logging.
   - `release`: Controlled device release.
   - `health`: Local system responsiveness and metrics.
   - `audit-verify`: SHA-256 hash-chain verification of local audit records.
2. **Prohibition of Arbitrary Execution:** No generic execution escape hatch (`rust.execute(...)` or arbitrary command runner) is permitted.
3. **Non-Authority of Rust Layer:** The Rust bridge executes operations only upon receiving an authorized request from Python runtime. It cannot independently allocate resources, mint leases, bypass Space isolation, or resolve secrets.
4. **Transport-Only Crash Recovery:** Restarting the Rust bridge is an infrastructure recovery operation. A bridge restart does NOT restore authority or blindly replay non-idempotent operations. In-flight operations are resumed only if protected by an explicit idempotent contract; otherwise they are surfaced as `INDETERMINATE`.

## Alternatives Considered
- *In-process C-ABI FFI (cdylib):* While performant, memory safety violations or native crashes in Rust could destabilize the Python runtime process. Standalone subprocess with structured JSON communication provides crash isolation.
- *Arbitrary Native Command Invocation:* Allowing the bridge to execute arbitrary shell commands on behalf of workers. Rejected as a critical security vulnerability.

## Consequences
- Clean separation between policy/authorization (Python) and platform enforcement/inspection (Rust).
- Hardware inspection and grant enforcement execute natively on the host (`NODE-001`, `NODE-002`).
- Subprocess crashes are isolated and cannot silently replay non-idempotent work.

## Date
2026-09-20
