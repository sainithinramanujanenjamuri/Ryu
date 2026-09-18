# RYU AI — Project Memory

## Entry 0002 — Phase 1 Durable Pulse Bus

**Date:** 2026-09-18
**Phase:** 1 — Pulse Bus + Contracts (Production)
**Status:** COMPLETE (PHASE 1 GATE: PASS)
**Previous Baseline:** b1be4e0, deb11ca (Phase 0)

---

### Summary

Phase 1 establishes the production Durable Pulse Bus for RYU AI. PostgreSQL 16 is established as the durable System of Record, and Redis Streams 7 is established as the publish/backbone transport layer. Replay, recovery, idempotency, taint preservation, schema fuzzing, and all Phase 1 harness acceptance criteria have been verified with real Docker services.

Phase 0 in-memory `PulseBus` has been preserved without regression.

---

### Architecture & Contract Foundations

- **ADR-0002:** `adr/0002-transport-vs-record.md` — Formally defines the PostgreSQL/Redis transaction boundary, recovery semantics, and authoritative source (resolves OPEN-001, OPEN-002, OPEN-006).
- **Runbook:** `docs/runbooks/bus-recovery.md` — Operational instructions for transport/record skew detection and reconciliation.
- **Contract Traceability:** `docs/CONTRACT_MATRIX.md` updated with verified status for PULSE-001 through PULSE-012 and SPACE-005.
- **Spec Map:** `harness/spec_map.yaml` updated with all Phase 1 test mappings.

---

### What Was Built

1. **Docker Compose Infrastructure (`deploy/`):**
   - `deploy/docker-compose.yml`: PostgreSQL 16 + Redis 7 with healthchecks.
   - `deploy/migrations/001_create_pulses_table.sql`: Canonical `pulses` table with JSONB payload, sequence position, indexes on `space_id`, `correlation_id`, `parent_pulse_id`, `type`, and `redis_published`.
   - `deploy/.env.example`: Configuration template.

2. **Core Modules (`core/pulse_bus/src/ryu/pulse_bus/`):**
   - `config.py`: Dataclass configurations for PostgreSQL, Redis, and DurableBus with environment variable resolution.
   - `store.py`: `PulseStore` protocol, `PostgresPulseStore` (psycopg2 parameterized SQL, idempotent appends), and `InMemoryPulseStore`.
   - `transport.py`: `PulseTransport` protocol, `RedisStreamTransport` (Redis Streams, consumer groups, ack, reclaim stale), and `NoopTransport`.
   - `taint.py`: `TaintResolver` formalizing forward-only clearance and parent chain inheritance.
   - `replay.py`: `PulseReplayer` supporting position replay, correlation replay, and causal ancestry walks.
   - `durable_bus.py`: `DurablePulseBus` orchestrating validation -> PostgreSQL persistence -> Redis transport -> dispatch -> reconciliation.
   - `validator.py`: Updated to utilize compiled `ALL_PULSE_TYPES` from codegen models.

---

### Executable Evidence

1. **Unit Tests (`core/pulse_bus/tests/`):**
   - Total: **39 passed** (17 Phase 0 + 22 Phase 1) in 1.17s.
   - Zero external service dependencies required for unit tests.

2. **Harness Cases (`harness/cases/`):**
   - **13 passed, 33 skipped** (future phases properly skipped).
   - `test_schema_fuzz.py`: Hypothesis property fuzzing passes 1,000 cases in smoke mode (supports 100,000 cases via `RYU_FUZZ_FULL=1`).
   - `test_validator_uses_generated_types.py`: Verified registry and generated model consistency.

3. **Integration Harness (`harness/cases/pulse_bus_integration/`):**
   - Total: **11 passed** against live PostgreSQL 16 and Redis 7 in 56.61s.
   - Verified areas: durable append, durable replay across process restarts, Redis publication, acknowledgment, consumer crash & reclaim, PostgreSQL failure handling, Redis failure durability preservation, duplicate pulse idempotency, cross-space boundary isolation, and backpressure.

4. **Static Quality Verification:**
   - **Contract Sync:** PASS (38/38 types match Architecture §16).
   - **Dependency Guard:** PASS (core/ has zero forbidden imports).
   - **Codegen Freshness:** PASS (`git diff contracts/codegen/` is empty).
   - **Ruff:** PASS (zero lint errors across entire repository).
   - **Mypy:** PASS (0 errors across 91 source files).
   - **Rust Cargo:** PASS (`cargo check`, `cargo clippy -D warnings`, `cargo fmt --check`).

---

### What Was NOT Implemented (Phase Boundary Preserved)

- Space Kernel, Admission Control, Budget accounting (Phase 2).
- Resource Manager, Leases, Contested concurrency (Phase 3).
- Space Orchestrator, Goal Analyzer, Planner (Phase 4).
- Cognitive Agents, LLM reasoning (Phase 5).
- Workers, Sandbox execution (Phase 6).
- Node Runtime device execution (Phase 7).
- Exactly-once distributed delivery (explicitly out of scope per ADR-0002).
