# RYU AI — Phase 12.6 Implementation Report
# Convergence Engine / Plan Reconciliation

**Milestone:** Phase 12.6  
**Date:** 2026-09-28  
**Baseline:** Phase 12.5 — commit `9ec5568` (GATE_VERIFIED, pushed to `origin/main`)  
**Author:** RYU Architecture Agent  
**Governing Specification:** `docs/PHASE_12_EXECUTION_ENGINE_SPEC.md` §10–11  
**ADR:** `adr/0041-autonomous-task-dispatcher-dag-traversal-and-plan-convergence-engine.md`  

---

## 1. Objective

Phase 12.6 implements the **Convergence Engine / Plan Reconciliation** layer that closes the autonomous execution loop:

```
Goal
 ↓
Plan
 ↓
Task DAG Execution (Phases 12.1–12.5)
 ↓
Evidence Collection + Verification
 ↓
Goal Evaluation        ← Phase 12.6
 ↓
Convergence Decision   ← Phase 12.6
 ↓
Plan Proposal          ← Phase 12.6
 ↓
SpaceKernel CAS        ← existing authority (unchanged)
 ↓
Updated Plan / Escalation
```

---

## 2. Components Implemented

### 2.1 `GoalEvaluationStatus` (enum)
Three-valued formal verdict for goal evaluation results:
- `SATISFIED` — all evidence verified, constraints met, plan complete
- `UNSATISFIED` — evidence fails verification or constraints not met  
- `INCONCLUSIVE` — tasks still in-flight or no evidence collected

### 2.2 `GoalEvaluationResult` (frozen dataclass)
Immutable evaluation verdict with:
- `status: GoalEvaluationStatus`
- `confidence: float` — bounded `[0.0, 1.0]`, validated in `__post_init__`
- `reasoning: str` — human-readable explanation
- `missing_criteria: list[str]` — exhaustive list of unmet criteria (empty when SATISFIED)
- `evaluated_tasks`, `satisfied_tasks`, `evidence_count` — traceability metrics

**Zero plan mutation authority** — carries evidence verdict only.

### 2.3 `DeterministicGoalEvaluator`
LLM-free, evidence-bound goal evaluator. Evaluates via:

1. **Empty evidence guard** — returns INCONCLUSIVE with `confidence=0.0`
2. **Verification check** — all `VerifiedExecutionEvidence` items must have `verified=True` and `status="verified"`
3. **Taint check** — tainted evidence fails unless goal has `"allow_taint"` constraint
4. **Substantive evidence** — at least one artifact-SHA-256, process exit code 0, or structured output
5. **Constraint evaluation** — deterministic scan of `require_artifact:path` and `require_exit_code:N` constraints

Also provides `evaluate_from_task_graph()` for dual-source evaluation (TaskGraph state + evidence).

**Invariants:**
- Zero imports from `workers/`, `agents/`, `llm/`, `memory/`, `channels/` (AGENTS.md §7)
- Deterministic: identical inputs always produce identical output
- Replay-safe: no side effects, no external calls

### 2.4 `ConvergenceDecision` (enum)
Five action values per spec §11.1:
- `CONTINUE` — next ready tasks scheduled; no plan mutation
- `RETRY` — transient failure; bounded backoff retry (≤ 3)
- `REPLAN` — structural failure; `PlanReconciler` proposes `PlanDelta`
- `ESCALATE` — budget exhausted or loop detected; human intervention required
- `ABORT` — unrecoverable violation; Space enters failure state

### 2.5 `ConvergenceProposal` (frozen dataclass)
Strongly-typed, immutable proposal produced by `ConvergenceEngine`. Fields:
- `decision: ConvergenceDecision`
- `space_id`, `plan_version`, `reasoning`, `task_id`
- `retry_attempt`, `replan_attempt` — budget tracking
- `plan_delta: PlanDelta | None` — only populated for REPLAN; **NOT applied by ConvergenceEngine**
- `evaluation: GoalEvaluationResult | None`
- `escalation_reason`, `failure_fingerprint`

**The ConvergenceEngine has zero plan mutation authority.**  
The only path to plan mutation: `Proposal → PlanDelta → apply_proposal() → SpaceKernel.commit_plan_delta() → PlanStore CAS`

### 2.6 `ConvergenceEngine`
Primary orchestration class with:

#### Authority Boundaries (strictly enforced)
- CANNOT directly mutate plans, spaces, leases, approvals, or secrets
- `GoalEvaluator` CANNOT mutate `PlanStore` or `TaskGraph`
- LLM output is a proposal input only — terminal errors escalate REGARDLESS of evaluator verdict
- Only path: `Proposal → PlanDelta → SpaceKernel.commit_plan_delta()`

#### Convergence Control (hard ceilings, LLM cannot override)
| Control | Ceiling | Behavior on Exhaustion |
|---|---|---|
| `MAX_RETRY_BUDGET` | 3 | Falls through to REPLAN |
| `MAX_REPLAN_BUDGET` | 3 | Falls through to ESCALATE |
| Fingerprint loop guard | 1 repeated fingerprint | Immediate ESCALATE |

#### Failure Routing
```
error_class →
  "terminal.*"       → ESCALATE immediately (zero retries)
  "violation.*"      → ABORT
  "transient.*"      → RETRY (≤ 3) → REPLAN (≤ 3) → ESCALATE
  other              → REPLAN (≤ 3) → ESCALATE
```

#### `apply_proposal()` — The Only CAS-Touching Method
- CONTINUE / RETRY / ESCALATE / ABORT: `(True, current_version, None)` — no CAS
- REPLAN: commits `plan_delta` via `kernel.commit_plan_delta()` with bounded rebase loop

#### Helper Methods
- `is_plan_converged(graph)` — True if all non-optional tasks are in terminal states
- `is_plan_succeeded(graph)` — True if all non-optional tasks are COMPLETED
- `reset_task_budgets(task_id)` — resets retry/replan counters after successful replan

---

## 3. Discovery Classification

| Component | Status Before 12.6 | After 12.6 |
|---|---|---|
| `GoalEvaluatorProtocol` | EXISTS (line 228 `dispatch_model.py`) | REUSED |
| `PlanReconciler` | EXISTS (`reconciler.py`) | REUSED as reconciler dep |
| `GoalEvaluationStatus` | MISSING | IMPLEMENTED |
| `GoalEvaluationResult` | MISSING | IMPLEMENTED |
| `DeterministicGoalEvaluator` | MISSING | IMPLEMENTED |
| `ConvergenceDecision` | MISSING | IMPLEMENTED |
| `ConvergenceProposal` | MISSING | IMPLEMENTED |
| `ConvergenceEngine` | MISSING | IMPLEMENTED |

**No duplicate abstractions created.** All existing classes (`PlanReconciler`, `Adapter`, `Monitor`, `GoalAnalyzer`, `SpaceKernel`) reused without modification.

---

## 4. Files Modified / Created

| File | Change |
|---|---|
| `core/orchestrator/dispatch_model.py` | +~350 lines: Phase 12.6 convergence engine |
| `core/orchestrator/__init__.py` | Added 6 new exports to imports and `__all__` |
| `core/orchestrator/tests/test_phase12_convergence_engine.py` | **CREATED**: 34 unit tests |
| `workers/tests/test_phase12_end_to_end_convergence.py` | **CREATED**: 18 e2e tests |
| `docs/PHASE_12_6_IMPLEMENTATION_REPORT.md` | **CREATED**: this report |

**No changes to:**
- `reconciler.py` — reused as-is
- `adapter.py` — reused as-is  
- `monitor.py` — reused as-is
- `goal_analyzer.py` — reused as-is
- Any pulse contracts or schemas

---

## 5. Test Coverage

### Unit Tests (`core/orchestrator/tests/test_phase12_convergence_engine.py`) — 34 tests

| Category | Tests |
|---|---|
| `GoalEvaluationResult` construction + invariants | 4 |
| `DeterministicGoalEvaluator` — SATISFIED/UNSATISFIED/INCONCLUSIVE | 9 |
| `ConvergenceEngine` decisions | 8 |
| Bounded retry (≤ 3) | 2 |
| Bounded replan / escalation | 2 |
| Fingerprint loop detection | 1 |
| `apply_proposal` CAS authority boundary | 2 |
| `is_plan_converged` / `is_plan_succeeded` | 3 |
| Adversarial LLM injection safety | 1 |
| Frozen proposal mutation guard | 1 |
| Budget reset | 1 |

### E2E Integration Tests (`workers/tests/test_phase12_end_to_end_convergence.py`) — 18 tests

| Category | Tests |
|---|---|
| Satisfied goal → CONTINUE | 1 |
| Retry cycle → REPLAN vertical slice | 1 |
| Failure ceiling → ESCALATE | 1 |
| Terminal error immediate ESCALATE | 1 |
| Violation ABORT | 1 |
| Optional task failure → still SATISFIED | 1 |
| Tainted evidence UNSATISFIED | 1 |
| `allow_taint` constraint satisfied | 1 |
| Multi-task DAG full convergence | 1 |
| DAG with in-flight tasks not converged | 1 |
| Plan version advances after REPLAN | 1 |
| `apply_proposal` CONTINUE no CAS | 1 |
| Cross-space isolation | 1 |
| Adversarial always-SATISFIED LLM injection | 1 |
| Frozen proposal mutation | 1 |
| `require_exit_code` constraint | 1 |
| Deterministic evaluator replay-safe | 1 |
| Core boundary smoke-test import | 1 |

---

## 6. Verification Gate Results

| Gate | Script | Status |
|---|---|---|
| V1-002 Core Independence | `v1_verify_core_independence.py` | **PASS** (0 forbidden imports) |
| V1-001 Spec Coverage | `v1_audit_spec_coverage.py` | **PASS** (141 criteria, 187 contracts, 162 mappings) |
| V1-005 Governance | `v1_audit_governance.py` | **PASS** |
| V1-006 Replay Equivalence | `v1_verify_replay.py` | **PASS** |
| V1-004 Security Regression | `v1_run_security_regression.py` | **PASS** (12/12) |
| dep_guard | `scripts/dep_guard.py` | **PASS** (0 forbidden) |
| contract_sync | `scripts/contract_sync.py` | **PASS** (38 pulse types) |
| ruff | `ruff check core workers` | **PASS** |
| mypy | `mypy core workers` | **PASS** (notes only, no errors) |
| cargo check | `node_runtime/Cargo.toml` | **PASS** |
| unit tests | `pytest core workers` | **PASS: 337 passed, 0 failed** |

---

## 7. SCCA Laws Compliance

| Law | Compliance |
|---|---|
| Law 1 — Space-Centric | `verify_space_identity()` called before every operation |
| Law 2 — Capabilities Requested | `ConvergenceEngine` requests admission via existing AdmissionController; no direct grants |
| Law 3 — Pulse Communication | `PlanReconciler` publishes typed `task.retried` pulses; evaluator emits no pulses (read-only) |
| Law 4 — Knowledge Space-First | Evidence is space-scoped; no global promotion |
| Law 5 — Human Defines Goals | `ConvergenceEngine` never redefines the `GoalSpec`; evaluates against human-defined criteria |
| Law 6 — Failures Escalated | Bounded retry → REPLAN → ESCALATE → human gate; zero silent failures |

**Core Boundary Rule (AGENTS.md §7):** VERIFIED — `dep_guard.py` confirms 0 forbidden imports from `workers/`, `agents/`, `llm/`, `memory/` inside `core/`.

---

## 8. ADR Status

No new ADR was created. Phase 12.6 is fully covered by **ADR-0041** §2B (Core Boundary Rule Preservation), §2C (Authority Matrix), and §5 (Convergence Controls & Loop Bounding). The implementation confirms no new architectural decision was required beyond what ADR-0041 already codified.

---

## 9. Phase 12 Convergence Loop — Current State

```
User
 → GoalAnalyzer (Phase 4)
 → Planner (Phase 4)
 → SpaceKernel CAS (Phase 4)
 → TeamBuilder (Phase 4)
 → task.assigned
 → DeterministicDispatcher (Phase 12.1–12.2)
 → AdmissionController / ResourceManager (Phase 12.3)
 → WorkerInvoker / Worker Sandbox (Phase 12.4)
 → Evidence Collection + SHA-256 Verification (Phase 12.5)
 → DAG Dependency Unblocking (Phase 12.5)
 → DeterministicGoalEvaluator (Phase 12.6) ← NEW
 → ConvergenceEngine (Phase 12.6)          ← NEW
   → CONTINUE / RETRY / REPLAN / ESCALATE / ABORT
   → apply_proposal() → SpaceKernel CAS    ← NEW
```

---

## 10. What Was Not Implemented (Explicit Non-Goals)

Per spec §21 Phase T:
- Voice / Audio (STT/TTS): **not implemented**
- Video / WebRTC: **not implemented**  
- Neo4j graph databases: **not implemented**
- Autonomous self-modification: **not implemented**
- Desktop authority expansion: **not implemented**
- Bypassing Human Gates: **not implemented**

Phase 12.7 (Deterministic Goal Evaluator — full crash recovery integration) and Phase 12.8 (startup recovery) remain future work.

---

## 11. Next Steps

- **Phase 12.7:** Full crash recovery integration — startup pulse scan, abandoned task cleanup
- **Phase 12.8:** End-to-end 25-scenario master verification battery
- **Phase 12.9:** Replay bitwise equivalence verification
