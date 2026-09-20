# ADR-0022: Human Gate Lifecycle and CAS Transitions

## Context
Human approval gates protect critical capabilities (filesystem mutation, network connections, process execution, device grants, and budget expansions). Prior to Phase 8, approval requests suffered from ambiguity between the operational lifecycle state of an approval and its positioning in the human attention queue.

## Decision
1. **Separation of Concerns:**
   - **Approval Lifecycle State:** `PENDING`, `APPROVED`, `DENIED`, `EXPIRED`, `HELD`, `CONSUMED`. Represents the authoritative authorization status of the capability request.
   - **Attention Queue State:** `ACTIVE`, `QUEUED`, `RESOLVED`. Represents the dispatch position of the request within the human operator's cognitive budget.
2. **Atomic Compare-And-Swap (CAS):**
   - All state transitions are executed via atomic CAS operations in the durable store (`ApprovalStore.transition_cas`).
   - Valid forward paths:
     - `PENDING -> APPROVED` (authenticated human approval)
     - `PENDING -> DENIED` (authenticated human rejection or default_deny timeout)
     - `PENDING -> EXPIRED` (explicit expiration under default_deny)
     - `PENDING -> HELD` (timeout under default_hold budget policy)
     - `APPROVED -> CONSUMED` (atomic admission check before dispatch)
   - Retrograde transitions (e.g. `DENIED -> APPROVED`, `CONSUMED -> APPROVED`) are strictly rejected.
3. **Single-Use Consumption Semantics:**
   - An approved request can be consumed exactly once. Second consumption attempts fail-closed.
4. **Decision Integrity Signature:**
   - When a decision is resolved, a tamper-evident HMAC-SHA256 signature (`decision_signature`) is calculated using the Space Kernel internal authority secret and persisted with the record.

## Alternatives Considered
- *Single combined status field:* Rejected because a request can be in `PENDING` status while waiting in the attention queue (`QUEUED`), causing race conditions and semantic collisions.
- *In-memory only transitions:* Rejected because node or process restarts would lose unresolved gates or permit duplicate consumption.

## Consequences
- Guaranteed atomicity under high concurrency.
- Zero race windows between simultaneous approvers or between approver response and timeout evaluation.

## Date
2026-09-20

