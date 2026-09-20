# ADR-0016: Worker Lifecycle, Failure Taxonomy Mapping, and Subagent Isolation

## Status
Accepted (Phase 6)

## Context
Execution errors, process crashes, timeouts, and resource exhaustion must not crash the RYU runtime, leave dangling background processes, leak leased resources, or silently swallow errors (Law 6). Furthermore, subagent execution must prevent uncontrolled recursive agent spawning and context pollution (WORKER-004, WORKER-005).

## Decision
1. **Failure Taxonomy Mapping (WORKER-004):**
   Every worker error maps deterministically onto the system failure taxonomy (`failure-taxonomy.json`):
   - Timeout during execution -> `transient.timeout`
   - Memory/process quota exceeded -> `transient.rate_limit` / `terminal.budget_exceeded`
   - Network failure or socket connection error -> `transient.network`
   - Invalid arguments or malformed request -> `terminal.invalid_params`
   - Filesystem traversal or unauthorized access -> `terminal.permission_denied`
   - Missing, expired, or invalid lease -> `terminal.permission_denied`
   - Seccomp violation or sandbox escape attempt -> `terminal.permission_denied`
   - Process crash (non-zero exit code or uncaught exception) -> structured execution error
2. **Lease Lifecycle and Cleanup Guarantee:**
   - On completion, failure, timeout, or cancellation, any lease held by the Worker is cleanly released or marked for release back to `ResourceManager`.
   - The process tree is unconditionally terminated before the Worker transitions to a terminal state.
3. **Structured Worker Observability:**
   - Every invocation emits `worker.tool.called`.
   - On success: emits `worker.tool.succeeded` with sanitized artifacts and metrics.
   - On error: emits `worker.tool.failed` with structured failure classification and attempt counter.
4. **Subagent Worker Isolation (WORKER-005):**
   - When a task delegates to a subagent, `SubagentWorker` initializes a fresh, isolated Agent context.
   - It is seeded *only* with the structured `HandoffNote` and the assigned plan node description.
   - It does NOT inherit conversational turn history, parent internal state, or ungranted capabilities.
   - It remains subordinate to the standard authority chain: `Space Orchestrator -> Agent -> Proposal -> Validation -> Admission -> ResourceManager -> SubagentWorker`.
   - It cannot spawn recursive subagent trees or mutate the Space TaskGraph directly.

## Consequences
- Failures are observable, typed, and contained within their originating scope.
- Resource leaks, orphan processes, and runaway child processes are eliminated.
- Subagent delegation preserves context boundaries and prevents authority amplification.
