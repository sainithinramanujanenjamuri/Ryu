# Project Memory: 0024 — Phase 14.6 Bounded Test-Repair Loop

**Date:** 2026-10-02  
**Author:** Ryu Autonomous Core Agent  
**Baseline:** Phase 14.5 Sandboxed Test Evidence Runtime (`27520a6`)  
**Status:** COMPLETE (GATE-14.6: PASS)  
**Governing ADR:** ADR-0044  
**Governing Architecture:** Space-Centric Cognitive Architecture (SCCA)  

---

## 1. Context & Purpose

Phase 14.5 established sandboxed test execution and structured evidence verification (`EVIDENCE-001..003`).  
Phase 14.6 establishes the bounded, evidence-driven software repair convergence loop (`REPAIR-001..004`).

Phase 14.6 answers the architectural question:  
*"When a test fails inside a Space, can RYU deterministically diagnose the failure, derive an anti-loop fingerprint, propose a bounded atomic patch via Plan CAS, and re-execute tests until convergence or graceful escalation?"*

### Implemented Contracts
- **`REPAIR-001` (Bounded Test-Repair Loop Ceilings):** Maximum 3 repair iterations per failure key (`MAX_REPAIR_ITERATIONS = 3`). A 4th repair cycle deterministically triggers `ConvergenceDecision.ESCALATE`.
- **`REPAIR-002` (Repair Failure Fingerprinting & Loop Prevention):** Normalizes failure traces and derives SHA-256 fingerprints. Repeated identical failure fingerprints or oscillating failure cycles ($F_1 \rightarrow F_2 \rightarrow F_1$) immediately trigger `ConvergenceDecision.ESCALATE`.
- **`REPAIR-003` (Repair Memory Counterfactual Guidance):** Integrates with `AdaptationLayer` to incorporate advisory `ExperienceHint` guidance and counterfactual recommendations into replan proposals without giving memory direct plan authority.
- **`REPAIR-004` (Replan Budget Deduction & CAS Integration):** Translates repair proposals into bounded `PlanDelta` diffs (patch task + retest task + rollback of failed test node) committed strictly via single-writer `SpaceKernel` CAS.

### Strict Scope Invariants
- **No Sixth Convergence Decision:** Allowed outcomes remain strictly `CONTINUE`, `RETRY`, `REPLAN`, `ESCALATE`, `ABORT`. Automated repair maps to `ConvergenceDecision.REPLAN` with `repair_iteration > 0` and bounded `plan_delta`.
- **Zero Bypass of Authority Boundaries:** `ConvergenceEngine` and `RepairDiagnostic` cannot directly mutate repositories, files, or task graphs. All mutations flow through `PlanDelta` $\rightarrow$ `SpaceKernel.commit_plan_delta()` $\rightarrow$ `PlanStore` CAS.
- **Advisory Model & Memory Subordination:** LLMs and adaptation hints are strictly advisory inputs. They cannot override failing test evidence, bypass patch ceilings, or suppress escalation.
- **Strict Patch Ceilings:** Validated against maximum 5 files (`MAX_CHANGED_FILES = 5`) and 500 diff lines (`MAX_DIFF_LINES = 500`), path traversal denylist, and sensitive credential paths (`.env*`, `id_rsa*`, `*.pem`, `*.key`, `.aws/*`, `.ssh/*`, `.kube/*`).
- **Core Independence Boundary:** All repair protocol models reside in `core/space/repair_protocol.py` with zero imports from higher-level cognitive, worker, or channel layers (verified via `scripts/dep_guard.py`).

---

## 2. What Changed

1. **Core Repair Protocol (`core/space/repair_protocol.py`):**
   - Core typed enums: `FailureClassification` (`ASSERTION_FAILURE`, `SYNTAX_FAILURE`, `IMPORT_FAILURE`, `RUNTIME_FAILURE`, `TIMEOUT`, `PERMISSION_FAILURE`, `SANDBOX_FAILURE`, `EVIDENCE_INCONSISTENCY`, `REPOSITORY_STATE_MISMATCH`, `UNKNOWN_INCONCLUSIVE`).
   - Core data models: `RepairDiagnostic`, `RepairProposal`, `RepairLoopHistory`.
   - Trace normalization and fingerprinting: `normalize_failure_trace()` (strips volatile memory addresses, timestamps, process IDs, durations, Windows backslashes, collapses whitespace), `compute_repair_fingerprint()` (`SHA256(space_id : task_id : normalized_trace)`), `classify_failure()`.
   - Bounded proposal validation: `validate_repair_proposal()` enforcing ceilings (`MAX_REPAIR_ITERATIONS = 3`, `MAX_CHANGED_FILES = 5`, `MAX_DIFF_LINES = 500`), path traversal defenses, and sensitive path denylist.

2. **Durable Convergence State Tracking (`core/orchestrator/execution_state.py`):**
   - Extended `ConvergenceStateRecord` with `repair_count: int` and `repair_fingerprints: tuple[str, ...]`.
   - Extended `ConvergenceStateStore` protocol and `InMemoryConvergenceStateStore` with `increment_repair()`, `add_repair_fingerprint()`, and `get_repair_fingerprints()`.

3. **Convergence Engine Extension (`core/orchestrator/dispatch_model.py`):**
   - Extended `ConvergenceProposal` with `repair_iteration: int`, `repair_proposal: RepairProposal | None`, `repair_diagnostic: RepairDiagnostic | None`.
   - Extended `ConvergenceEngine` with `MAX_REPAIR_ITERATIONS = 3`, `bus: Any | None`, durable repair tracking helpers (`_get_repair_count`, `_increment_repair`, `_add_repair_fingerprint`, `_get_repair_fingerprints`).
   - Extended `evaluate_and_propose()` with `repair_diagnostic` and `repair_proposal` parameters.
   - Implemented `_handle_repair()`:
     - Cross-space validation (SCCA Law 1, `SPACE-001`).
     - Inconclusive evidence & state mismatch escalation (`EVIDENCE-001`, `REPO-005`, `REPAIR-004`).
     - Repeated fingerprint & oscillation detection (`REPAIR-002`, Slices B & D).
     - Iteration ceiling check (`MAX_REPAIR_ITERATIONS = 3`, Slice C).
     - Proposal bounds & security validation (`REPAIR-002`).
     - Advisory memory queries for counterfactual guidance (`REPAIR-003`).
     - `repair.loop_iterated` pulse emission with `taint: True` matching JSON schema.
     - Bounded `PlanDelta` construction with rollback op, patch add op, and retest add op.

4. **Dedicated Verification Suites (`core/space/tests/test_repair_protocol.py` & `workers/tests/test_phase14_6_repair_loop.py`):**
   - 32 unit tests for data models, normalization, fingerprinting, and security bounds.
   - 46 integration, vertical slice (A through H), and adversarial tests (26 security vectors).

5. **Contract Matrix Synchronization (`docs/CONTRACT_MATRIX.md`):**
   - Updated `REPAIR-001`, `REPAIR-002`, `REPAIR-003`, and `REPAIR-004` to `INTEGRATION_VERIFIED`.

---

## 3. What Was Verified

### Test Counts & Execution Metrics (Zero Double Counting)

- **Dedicated Phase 14.6 Tests:**
  - `core/space/tests/test_repair_protocol.py`: 32 passed, 0 skipped, 0 failed.
  - `workers/tests/test_phase14_6_repair_loop.py`: 46 passed, 0 skipped, 0 failed.
  - **Total Dedicated Phase 14.6 Tests:** **78 passed**, 0 skipped, 0 failed.

- **Full Regression Test Suites:**
  - `core` & `workers` suites: 695 passed, 1 skipped, 0 failed (contains the 78 dedicated tests).
  - `harness` suite: 325 passed, 12 skipped, 0 failed.
  - **Combined Regression Suite Total:** **1,020 passed**, 13 skipped, 0 failed.

### Architecture & Governance Gates
- `scripts/dep_guard.py`: PASS (0 forbidden imports in `core/`).
- `scripts/contract_sync.py`: PASS (All contracts in sync).
- `ruff check core workers`: PASS (All checks passed).
- `mypy`: PASS (Success: no issues found in 4 source files).
- `cargo check`: PASS (Rust node runtime compiled cleanly).
- `scripts/v1_audit_governance.py`: PASS (ADR 0001..0044, 1:1 schema coverage, contract matrix integrity).
- `scripts/v1_audit_spec_coverage.py`: PASS (161 criteria, 224 contracts, 0 orphaned entries).

---

## 4. What Remains / Next Steps

Phase 14.6 concludes the bounded test-repair loop implementation.  
The next phases on the Phase 14 roadmap involve higher-level research workflows and multi-agent synthesis under ADR-0044:
- Phase 14.7: Autonomous Research Synthesis & Multi-Source Reconciliation (`RESEARCH-001..005`, `PROVENANCE-001..003`).
- Phase 14.8: Integrated Autonomous Software Engineer (End-to-End Task Lifecycle).
