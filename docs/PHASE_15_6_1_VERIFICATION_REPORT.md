# Phase 15.6.1 Verification Report: Convergence Correctness & Strategy Oscillation Detection

**Date:** 2026-10-08  
**Author:** Principal Software Architect & Core Engine Team  
**Baseline Commit:** `30c3d30d5208d22e8442dd15058d7faba92d6e3d`  
**Status:** PHASE 15.6.1 VERIFIED & FROZEN  
**Governing ADR:** ADR-0050 (`adr/0050-production-semantic-loop-consolidation-and-database-hardening.md`)  
**Governing Contracts:**
- `PLAN-ROLLBACK-001` (Deterministic Task Rollback & Parameter Reconciliation) — Status: `UNIT_VERIFIED`
- `CONV-OSC-001` (Bounded Capability Strategy Oscillation Detection) — Status: `UNIT_VERIFIED`
- `ORCH-003` / `PLAN-001` (Single-Writer SpaceKernel Plan CAS) — Status: `PRESERVED`

---

## 1. Executive Summary

Phase 15.6.1 closes the convergence correctness and oscillation blind spots identified during the post-F-05 architecture audit (`F05-AUDIT-05` and `F05-AUDIT-06`).

Prior to this phase:
1. `ConvergenceEngine._propose_replan()` stored advisory replan parameters under `"payload"` in `PlanDelta` rollback ops, whereas `PlanStore` inspected only `"params"`, causing counterfactual advice and suggested alternative capabilities to be silently lost.
2. Failed tasks remained immutably stuck in `TaskState.FAILED`, preventing automated replanning from re-attempting execution under new parameters.
3. The convergence loop guard relied solely on exact string equality of `failure_fingerprint`. Alternating cycles between complementary capabilities (e.g. $A \rightarrow B \rightarrow A \rightarrow B$) with slight error message fluctuations bypassed the loop guard and ungracefully exhausted replan budgets.

Phase 15.6.1 resolves these issues deterministically, preserving single-writer SpaceKernel CAS authority and the strict Core Boundary Rule.

```text
Task Failure (Structural / Replan)
        │
        ▼
ConvergenceEngine._propose_replan()
        │
        ├─► Query Advisory Adaptation Hints (ADAPT-002, ADAPT-003)
        ├─► Record Strategy Sequence (Bounded N <= 10, Space-Scoped)
        ├─► Detect Strategy Oscillation (Direct Reversal / 2-Cycle / Stagnant / 3-Cycle)
        │       ├── [Oscillation Confirmed] ──► ConvergenceDecision.ESCALATE (Human Intervention)
        │       └── [Progressive / Clean]   ──► Bounded PlanDelta (Reconciled payload + params)
        ▼
SpaceKernel.commit_plan_delta() (Single-Writer CAS)
        │
        ▼
PlanStore.commit_delta()
        │
        ├─► Parameter Reconciliation (Merges params + payload + metadata)
        ├─► Legal State Reset (FAILED/TIMED_OUT/BLOCKED/ESCALATED -> READY or PENDING)
        └─► Monotonic Plan Version Increment (v_base -> v_base + 1)
```

---

## 2. Implementation Deliverables & Architectural Changes

### 2.1 PlanDelta Rollback Parameter Reconciliation (`PLAN-ROLLBACK-001`)
- **`core/orchestrator/dispatch_model.py`:**
  - In `_propose_replan()`, rollback operations now populate both `"payload": payload_data` and `"params": payload_data`, establishing bidirectional compatibility across all caller conventions.
- **`core/plans/plan_store.py` & `core/plans/postgres_plan_store.py`:**
  - Parameter extraction in `commit_delta()` / `_apply_delta_ops()` reconciles parameters from `op_payload.get("params")`, `op_payload.get("payload")`, and flattened top-level metadata keys (`reason`, `suggested_alternative`, `counterfactual_recommendation`, `replan_attempt`, `failure_fingerprint`).
  - Sets `target_node.params["suggested_alternative"]` and updates `target_node.params` with counterfactual advice.
  - Clears `target_node.error = None` upon successful rollback commit.

### 2.2 Task State Machine Reconfiguration
- **`core/plans/task_graph.py`:**
  - Updated `LEGAL_TRANSITIONS` so that `failed`, `timed_out`, and `escalated` can legally transition to `ready` and `pending` under canonical rollback.
  - Completed and cancelled states remain immutable terminal states.
- **Dependency-Aware Reset Logic:**
  - Upon committing a rollback op, if the target node is in `failed`, `timed_out`, `blocked`, or `escalated`:
    - Checks upstream dependencies: if all dependencies are in `completed` (or optional failed/cancelled), transitions to `ready`.
    - If dependencies are not yet completed, transitions to `pending`, preventing premature dispatch.

### 2.3 Deterministic Bounded Capability Sequence Tracking (`CONV-OSC-001`)
- **`core/orchestrator/dispatch_model.py`:**
  - Added `self.MAX_STRATEGY_HISTORY = 10` and `self._strategy_history: dict[str, list[str]] = {}` in `ConvergenceEngine`.
  - Added public methods:
    - `record_strategy(replan_key: str, strategy: str) -> None`: Appends strategy and bounds history to $N \le 10$.
    - `get_strategy_history(replan_key: str) -> list[str]`: Returns chronological strategy list.
    - `clear_strategy_history(replan_key: str) -> None`: Clears history for replan key.
    - `detect_strategy_oscillation(replan_key: str) -> tuple[bool, str]`: Evaluates deterministic sequence patterns:
      1. **Stagnant repeating loop:** $A \rightarrow A \rightarrow A$ (same strategy repeated 3 consecutive times).
      2. **Direct strategy reversal:** $A \rightarrow B \rightarrow A$ (where $A \neq B$).
      3. **Alternating 2-cycle:** $A \rightarrow B \rightarrow A \rightarrow B$ (where $A \neq B$).
      4. **Period-3 cycle:** $A \rightarrow B \rightarrow C \rightarrow A \rightarrow B \rightarrow C$ (over sequence of length $\ge 6$).
  - When oscillation is detected in `_propose_replan()`, immediately returns `ConvergenceProposal(decision=ConvergenceDecision.ESCALATE, ...)`.
  - In `reset_task_budgets(task_id: str)`, clears `self._strategy_history.pop(task_id, None)`.
  - Strictly Space-scoped (`self.space_id`), zero cross-space state leakage, zero ML or probabilistic heuristics.

### 2.4 Diagnostic Memory Adapter Hardening
- **`memory/adapters/in_memory.py`:**
  - Hardened `list_experiences()` to handle both `datetime` and numeric `float`/`int` timestamps in sorting and pagination filters, preventing type errors during space memory diagnostic inspections.

---

## 3. Verification Evidence

### 3.1 Dedicated Test Suites
Two comprehensive test suites were created with 24 dedicated test cases:

#### A. `core/orchestrator/tests/test_phase15_6_1_convergence.py` (17 tests — ALL PASS)
| Test ID | Objective | Verdict |
| :--- | :--- | :--- |
| `test_rollback_param_reconciliation_with_params_dict` | Verifies rollback updates node params from `params` dict | **PASS** |
| `test_rollback_param_reconciliation_with_payload_dict` | Verifies rollback updates node params from `payload` dict | **PASS** |
| `test_rollback_param_reconciliation_with_top_level_keys` | Verifies rollback updates node params from flattened metadata | **PASS** |
| `test_rollback_resets_failed_task_to_ready_when_deps_satisfied` | Verifies failed task with satisfied deps transitions to `ready` | **PASS** |
| `test_rollback_resets_failed_task_to_pending_when_deps_unsatisfied` | Verifies failed task with unsatisfied deps transitions to `pending` | **PASS** |
| `test_rollback_resets_timed_out_blocked_escalated_states` | Verifies `timed_out`, `blocked`, and `escalated` reset to `ready` | **PASS** |
| `test_kernel_cas_authority_preserved_on_rollback` | Verifies stale base_version rollbacks are rejected by Kernel CAS | **PASS** |
| `test_oscillation_detection_direct_reversal_aba` | Verifies direct reversal $A \rightarrow B \rightarrow A$ detection | **PASS** |
| `test_oscillation_detection_alternating_2cycle_abab` | Verifies alternating 2-cycle $A \rightarrow B \rightarrow A \rightarrow B$ detection | **PASS** |
| `test_oscillation_detection_stagnant_repeating_loop_aaa` | Verifies stagnant loop $A \rightarrow A \rightarrow A$ detection | **PASS** |
| `test_oscillation_detection_period3_cycle` | Verifies period-3 cycle $A \rightarrow B \rightarrow C \rightarrow A \rightarrow B \rightarrow C$ detection | **PASS** |
| `test_oscillation_detection_non_oscillating_progressions` | Verifies progressive non-repeating sequence ($A \rightarrow B \rightarrow C \rightarrow D$) passes | **PASS** |
| `test_oscillation_space_isolation` | Verifies strategy histories are strictly Space-isolated | **PASS** |
| `test_oscillation_history_bounded_to_max_10` | Verifies history is bounded to $N \le 10$ entries | **PASS** |
| `test_reset_task_budgets_clears_strategy_history` | Verifies budget reset clears task strategy history | **PASS** |
| `test_propose_replan_escalates_on_strategy_oscillation` | Verifies end-to-end `evaluate_and_propose` returns `ESCALATE` on oscillation | **PASS** |
| `test_convergence_engine_replay_mode_determinism` | Verifies replay mode determinism without side-effects | **PASS** |

#### B. `core/orchestrator/tests/test_phase15_6_1_adversarial.py` (7 tests — ALL PASS)
| Test ID | Objective | Verdict |
| :--- | :--- | :--- |
| `test_adversarial_malformed_rollback_payload_non_dict` | Handles non-dict/malformed rollback payloads without crashing | **PASS** |
| `test_adversarial_rollback_on_completed_task_preserves_completed_state` | Completed tasks cannot be rolled back (terminal immutability) | **PASS** |
| `test_adversarial_convergence_engine_zero_direct_authority` | ConvergenceEngine cannot directly mutate plan; requires Kernel CAS | **PASS** |
| `test_adversarial_cross_space_rejection` | Rejects proposals across space boundaries (`PermissionError`) | **PASS** |
| `test_adversarial_oscillation_immune_to_varied_error_messages` | Tracks capability sequence independently of noisy error strings | **PASS** |
| `test_adversarial_early_escalation_before_budget_exhaustion` | Escalates immediately on 2nd replan oscillation without waiting for budget 3 | **PASS** |
| `test_adversarial_prompt_injection_safety_in_strategy_strings` | Treats hostile prompt injections and delimiters as literal tokens | **PASS** |

### 3.2 Regression Verification
- **Full Core & Orchestrator Regression:** 220 passed, 1 skipped (live PostgreSQL).
- **Full Memory Regression:** 183 passed.
- **Full Channels Regression:** 14 passed.
- **Total Repository Test Execution:** 676 passed, 2 skipped (live integration services).

### 3.3 Static & Architectural Audits
- **`scripts/dep_guard.py`:** `PASS` — Zero forbidden imports in `core/` (Core Boundary intact).
- **`scripts/contract_sync.py`:** `PASS` — All 38 registered types present and valid.
- **`scripts/v1_audit_governance.py`:** `PASS` — ADR inventory 0001..0050 valid, schemas 1:1, contracts intact.
- **`scripts/v1_audit_spec_coverage.py`:** `PASS` — 250 contract IDs, 208 executable mappings, 0 missing.
- **Ruff linter:** `PASS` — 0 errors across all modified and created files.
- **Mypy type checker:** `PASS` — 0 errors across 7 source and test files (`Success: no issues found in 7 source files`).
- **Cargo check:** `PASS` — `node_runtime` compiles cleanly in 0.06s.

---

## 4. Phase Boundary & Non-Goals

The following items are explicitly out of scope for Phase 15.6.1 and deferred to subsequent sub-phases:
- **Phase 15.6.2:** Non-blocking adaptation timeout & managed executor lifecycle (`F05-AUDIT-02`).
- **Phase 15.6.3:** PostgreSQL connection pooling & candidate expression indexing (`F05-AUDIT-04`).
- **Phase 15.6.4:** Durable experience embedding ingestion & outbox pipeline (`F05-AUDIT-01`).
- **Phase 15.6.5:** Operational memory lifecycle & retention policy (`F05-AUDIT-03`).

---

## 5. Phase 15.6.1 Exit Gate Verdict

```text
============================================================
PHASE 15.6.1 GATE: PASS
============================================================
  Rollback Payload Reconciliation:   [VERIFIED]
  Task State Machine Reset:          [VERIFIED]
  Strategy Sequence Tracking:        [VERIFIED]
  Cycle Oscillation Detection:       [VERIFIED]
  Space Isolation & Bounds:          [VERIFIED]
  Core Boundary Compliance:          [PASS]
  Contract Sync & Governance:        [PASS]
  Static Checks (Ruff, Mypy, Cargo): [PASS]
============================================================
```

