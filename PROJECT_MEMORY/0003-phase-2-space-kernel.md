# RYU AI — Project Memory

## Entry 0003 — Phase 2 Space Kernel + Admission Control + Plan CAS

**Date:** 2026-09-19
**Phase:** 2 — Space Kernel + Admission Control + Plan CAS + Secret Containment + Human Gates
**Status:** COMPLETE (PHASE 2 GATE: PASS)
**Previous Baseline:** c096277 (Phase 1)

---

### Summary

Phase 2 establishes the core Space Kernel for RYU AI under the Space-Centric Cognitive Architecture (SCCA). Space isolation, admission control with windowed budget escalation deduplication, atomic Compare-And-Swap (CAS) plan versioning with livelock bounds, secret containment via pre-publish rejection of raw secret values, and human approval gates with attention budgeting have been implemented and verified.

All Phase 0 and Phase 1 invariants and tests (unit, harness, and integration) continue to pass with zero regressions.

---

### Architecture & Contract Foundations

- **ADR-0003:** `adr/0003-plan-cas-livelock-bound.md` — CAS livelock bound of 3 rebase retries, emitting `task.failed` with `terminal.plan_livelock` per Law 6.
- **ADR-0004:** `adr/0004-secret-containment-validator-rule.md` — Exact-match substring secret detection in `PulseValidator` (minimum secret length $\ge 6$ characters) rejecting pulses containing raw secrets before persistence.
- **Contract Traceability:** `docs/CONTRACT_MATRIX.md` updated with verified status for ARC-001, ARC-002, SPACE-001, SPACE-006, KERNEL-001..007, PLAN-001..006, SECRET-001..003, TAINT-005, and HUMAN-001..004.
- **Spec Map:** `harness/spec_map.yaml` updated with Phase 2 mappings.

---

### What Was Built

1. **Capabilities & Admission Subsystem (`core/capabilities/`):**
   - `windows.py`: `EscalationWindowManager` providing thread-safe deduplication of `space.budget.exceeded` notifications per window, acknowledgment, and replenishment window rotation.
   - `admission.py`: `AdmissionController` enforcing pre-dispatch capability requests, budget limits (`hard_stop`, `approval_required`, `degraded`), and rate limits.
   - `__init__.py`: Clean module exports.

2. **Plans & TaskGraph Subsystem (`core/plans/`):**
   - `task_graph.py`: `TaskNode` and `TaskGraph` tracking lifecycle states (`pending`, `in_flight`, `completed`, `failed`, `cancelled`).
   - `delta.py`: `PlanDelta` and `DeltaOp` (`add`, `remove`, `reassign`, `rollback`).
   - `inflight_resolve.py`: In-flight resolution policies (`finish`, `checkpoint`, `cancel`).
   - `plan_store.py`: Thread-safe CAS `PlanStore` ensuring single-winner delta application, version increments, `plan.delta` and `plan.version.superseded` pulses, and 3-retry bounded livelock resolution.
   - `__init__.py`: Clean module exports.

3. **Security & Secret Containment (`core/security/` & `core/pulse_bus/`):**
   - `secrets.py`: `SecretRef` (`secret://` URIs), `SecretStore`, and `SecretResolver` ensuring late resolution at execution boundaries.
   - `core/pulse_bus/src/ryu/pulse_bus/validator.py`: Integrated secret scanning in `PulseValidator` raising `PulseRejectedError("secret_leak_detected")` on raw secret values.
   - `__init__.py`: Clean module exports.

4. **Space Kernel & Human Approval Gates (`core/space/`):**
   - `approver.py`: `ApprovalManager` enforcing single authenticated `approver_id`, timeout policies (`default_deny`, `default_hold`), and stub-clock simulation.
   - `attention.py`: `AttentionBudget` enforcing max concurrent open approvals ($N=3$ default) with FIFO queueing.
   - `kernel.py`: `SpaceKernel` coordinating space lifecycle, identity enforcement, cross-space isolation, admission checks, plan store mutations, and taint canary grant rejection.
   - `__init__.py`: `create_space` factory and module exports.

---

### Executable Evidence

1. **Unit Tests (`core/*/tests/`):**
   - Phase 0/1 Pulse Bus unit tests: **39 passed**.
   - Phase 2 core unit tests: **28 passed** (Admission: 4, Windows: 5, Plan Engine: 7, Secrets: 3, Approvals: 5, Kernel: 4).
   - Total Unit Tests: **67 passed**.

2. **Harness Cases (`harness/cases/`):**
   - Total Harness Cases: **22 passed, 26 skipped** (Phase 3+ future cases skipped with specific spec references).
   - Phase 2 activated harness cases (9 passed):
     - `test_kernel_admission.py`: KERNEL-001 (pre-dispatch), KERNEL-002 (hard stop), KERNEL-003 (single escalation), PLAN-001 (CAS versioning), PLAN-003 (superseded notification).
     - `test_space_isolation.py`: SPACE-001 (cross-space capability isolation), SPACE-006 (space identity enforcement).
     - `test_security_containment.py`: SECRET-003 (secret containment in payload), TAINT-005 (tainted instruction canary grant rejection).

3. **Integration Harness (`harness/cases/pulse_bus_integration/`):**
   - Total: **11 passed** against live PostgreSQL 16 and Redis 7 services.

4. **Total Combined Test Suite:**
   - **100 passed, 26 skipped** with `RYU_INTEGRATION_TESTS=1`.

5. **Static Quality Verification:**
   - **Contract Sync:** PASS (38/38 types match Architecture §16).
   - **Dependency Guard:** PASS (`core/` has zero forbidden imports).
   - **Codegen Freshness:** PASS (`git diff contracts/codegen/` is empty).
   - **Ruff:** PASS (zero lint errors across 110 source files).
   - **Mypy:** PASS (0 errors across 110 source files).
   - **Rust Cargo:** PASS (`cargo check`, `cargo clippy -D warnings`, `cargo fmt --check`).

---

### What Was NOT Implemented (Phase Boundary Preserved)

- Resource Manager, Leases, and Lock Contention (Phase 3).
- Space Orchestrator, Goal Analyzer, Planner, Team Builder (Phase 4).
- Cognitive Agents, LLM reasoning and transitions (Phase 5).
- Workers, Sandbox syscall filters (seccomp/bpf), Container execution (Phase 6).
- Node Runtime hardware device execution (Phase 7).
- Human Channels / Web / Mobile / TUI (Phase 8).
- Tools Registry, Skills, MCP Ingestion (Phase 9).
- Space Memory Adapters, Reflection, Vector DBs (Phase 10).

