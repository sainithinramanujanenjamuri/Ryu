# RYU AI — Project Memory

## Entry 0004 — Phase 3 Resource Manager + Chaos Harness v1

**Date:** 2026-09-19
**Phase:** 3 — Resource Manager + Lease Manager + Contention + Chaos Harness v1
**Status:** COMPLETE (PHASE 3 GATE: PASS)
**Previous Baseline:** b11fad2 (Phase 2)

---

### Summary

Phase 3 establishes the deterministic Resource Manager, Lease system, contention resolution, and Chaos Harness v1 for RYU AI under the Space-Centric Cognitive Architecture (SCCA). Contested hardware and software resources resolve deterministically using a queue-with-lease model without uncoordinated distributed locks. Every failure class in the Failure Taxonomy escalates strictly as specified, idempotency with stored-response semantics ensures zero duplicate side-effects, and a seeded chaos run replays bit-identically.

All Phase 0, Phase 1, and Phase 2 invariants, unit tests, and harness cases continue to pass with zero regressions.

---

### Architecture & Contract Foundations

- **ADR-0005:** `adr/0005-resource-queue-discipline.md` — Strict FIFO by default, optional priority queueing with anti-starvation aging (max bypasses = 5) and `resource.conflict` notification with accurate 1-based queue positions (resolves OPEN-010).
- **ADR-0006:** `adr/0006-idempotency-ownership-and-retry.md` — Authoritative ownership of the idempotency guarantee at the execution boundary (`ResourceManager` for leases; capability providers for external side effects) with stored-response caching (resolves OPEN-003).
- **Contract Traceability:** `docs/CONTRACT_MATRIX.md` updated with verified status for `RESOURCE-001` through `RESOURCE-008`, `FAIL-001` through `FAIL-007`, `IDEM-001` through `IDEM-003`, `SPACE-002`, and `REC-005`.
- **Spec Map:** `harness/spec_map.yaml` updated with all Phase 3 mappings.

---

### What Was Built

1. **Clock Abstraction (`core/resources/clock.py`):**
   - `Clock` protocol, `SystemClock` (production UTC), and `FakeClock` (deterministic time advance without `time.sleep()`).

2. **Resource Identity & Descriptor (`core/resources/identity.py`):**
   - `ResourceIdentity`: 3-tuple `(resource_type, provider_id, instance_id)` with canonical handle string parsing and grants schema dict serialization.
   - `Resource`: Capacity accounting (`total_capacity`, `allocated_capacity`, `allocate()`, `deallocate()`).

3. **Lease Lifecycle (`core/resources/lease.py`):**
   - `Lease`: Authoritative grant lease with unique token, expiry, holder identity, units, renewal count, and states (`active`, `expired`, `released`, `revoked`).
   - `LeaseManager`: Issuance, renewal (rejecting wrong holder, cross-space, or expired leases), release, revocation, and expiration sweeps.

4. **Contention Queue (`core/resources/queue.py`):**
   - `ResourceQueue`: Thread-safe queue supporting `FIFO` and `PRIORITY_FIFO` with anti-starvation aging and cancellation.

5. **Rate Limiter (`core/resources/rate_limit.py`):**
   - `SpaceRateLimiter`: Per-Space tokens/sec and tool_calls/sec token bucket rate limiting; over-limit emits `rate.limited` pulse with `severity: info` and `retry_after`.

6. **Persistence Store (`core/resources/store.py` & `deploy/migrations/002_create_leases_table.sql`):**
   - `ResourceStore` protocol, `InMemoryResourceStore`, and production `PostgresResourceStore` with parameterized SQL.

7. **Resource Manager (`core/resources/manager.py`):**
   - `ResourceManager`: Single decision-maker for contested resources inside a Space. Handles registration, atomic acquisition, contention queueing with `resource.conflict` pulses, lease issuance with `resource.granted` pulses, explicit release with `resource.released` pulses and automatic queue drain, expiration sweeps, and crash recovery.

---

### Executable Evidence

1. **Phase 3 Core Unit Tests (`core/resources/tests/`):**
   - **22 passed in 1.03s**
   - `test_identity.py`: Handle formatting, dict parsing, capacity allocation.
   - `test_lease.py`: Issuance, expiration, renewal rejection for wrong holder/space/expired, release, revocation.
   - `test_queue.py`: FIFO ordering, priority ordering, anti-starvation aging, cancellation.
   - `test_rate_limit.py`: Token and tool call limits, `rate.limited` pulse with `severity: info`.
   - `test_manager.py`: Contention, multi-threaded acquisition race (20 workers, 0 double grants), capacity accounting, idempotency, expiration sweep, crash recovery.

2. **Phase 3 Chaos & Acceptance Harness (`harness/cases/resources/`):**
   - **24 passed in 1.45s**
   - `test_lease_race.py`: High-concurrency race testing zero double grants and accurate queue positions.
   - `test_fault_matrix.py`: Full taxonomy matrix verification (`transient.timeout`, `transient.rate_limit`, `transient.network`, `terminal.invalid_params`, `terminal.permission_denied`, `terminal.budget_exceeded`, `terminal.not_found`).
   - `test_idempotency_proof.py`: Stored-response semantics for `payment.charge` provider (3x retry -> 1 charge, 2 cached responses).
   - `test_chaos_scenarios.py`: 10 fault-injection scenarios (db unavailable, transaction failure, contention, lease expiration race, concurrent acquisition, concurrent release, manager restart, stale lease, duplicate request, cancellation race).
   - `test_chaos_replay.py`: 50-operation seeded chaos run replayed bit-identically.
   - `test_resources_future.py`: Real passing acceptance tests for `RESOURCE-001`, `RESOURCE-002`, `RESOURCE-005`, `RESOURCE-006`, `RESOURCE-008`.

3. **Combined Suite:**
   - **137 passed, 26 skipped** (Phase 4+ future tests properly skipped).

---

### What Was NOT Implemented (Phase Boundary Preserved)

- Space Orchestrator, Goal Analyzer, Planner, Team Builder (Phase 4).
- Cognitive Agents, LLM reasoning (Phase 5).
- Workers, Sandbox syscall filters (Phase 6).
- Node Runtime device execution (Phase 7).
- MCP, Skills, Learning, Vector DBs (Phases 8–10).

