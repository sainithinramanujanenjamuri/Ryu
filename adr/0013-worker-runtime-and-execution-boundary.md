# ADR-0013: Worker Runtime, Execution Boundary, and Constitutional Non-Authority

## Status
Accepted (Phase 6)

## Context
In RYU AI Space-Centric Cognitive Architecture (SCCA), Workers execute authorized physical capabilities (e.g. running Python scripts, shell commands, file modifications, network lookups, subagent handoffs). If Workers possessed inherent host privileges, directly mutated plans, acquired resources without leases, or treated external data as executable instructions, the system would be vulnerable to privilege escalation, arbitrary code execution, and prompt injection attacks.

## Decision
1. **Governing Invariant:**
   Workers execute authorized capabilities, but Workers never receive unrestricted host authority.
   The full execution chain is strictly enforced:
   ```text
   Human Intent -> Space Orchestrator -> Agent -> LLM -> AgentProposal ->
   ProposalValidator -> Space Kernel / Admission -> ResourceManager / Lease ->
   Worker -> Sandbox -> OS / Seccomp -> Execution
   ```
2. **Worker State Machine:**
   Workers operate under a deterministic lifecycle:
   `CREATED -> READY -> ADMITTED -> STARTING -> RUNNING -> OBSERVING -> COMPLETED`.
   Terminal/failure states: `FAILED`, `TIMED_OUT`, `RESOURCE_EXHAUSTED`, `SANDBOX_VIOLATION`, `CANCELLED`.
3. **Lease and Space Pre-Execution Validation:**
   Before any capability execution begins, the Worker validates:
   - The execution request specifies an active, unexpired lease issued by `ResourceManager`.
   - The lease is bound to the Worker's `space_id`.
   - Any request with a missing, forged, expired, released, or cross-space lease is rejected immediately with structured `ExecutionError(error_class="LEASE_INVALID")`.
4. **Data vs. Instruction Separation (WORKER-002, TAINT-001):**
   - External output from sandboxed processes and tools enters RYU solely as passive data (e.g. `output_data` or `payload` on Pulses).
   - Workers are structurally prohibited from evaluating or interpreting output as instructions.
   - Any external content (e.g., from web or untrusted network fetch) is stamped with `taint: true`.
5. **Secret Containment (SECRET-002, SECRET-005):**
   - Workers receive opaque secret references (`secret://...`).
   - Resolution occurs at the innermost sandbox execution boundary.
   - Raw credentials are never published on Pulses, never included in logs or traces, and sanitized from stdout/stderr.

## Consequences
- Workers cannot bypass Space admission, leases, or isolation.
- Malicious external payloads cannot hijack execution flow.
- Replayable, deterministic execution records are maintained via typed Pulses.
