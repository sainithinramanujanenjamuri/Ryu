# RYU AI — Project Memory

## Entry 0005 — Phase 4 Space Orchestrator + Plan Reconciler

**Date:** 2026-09-19  
**Phase:** 4 — Space Orchestrator + Goal Analysis + Planner + Team Builder + Monitor + Plan Reconciler  
**Status:** COMPLETE (PHASE 4 GATE: PASS)  
**Previous Baseline:** 623375a (Phase 3)

---

### Summary

Phase 4 establishes the deterministic Space Orchestrator and closed-loop Plan Reconciler for RYU AI under the Space-Centric Cognitive Architecture (SCCA). The Orchestrator decomposes a human goal command into a validated GoalSpec, synthesizes a directed TaskGraph plan, assigns nodes to agent/worker roles with resource requirement detection, monitors execution state purely by subscribing to typed Pulses, and reconciles failures and plan drift exclusively by proposing atomic Compare-And-Swap (CAS) `PlanDelta` mutations to the Space Kernel.

Crucially, the Space Orchestrator is constitutionally subordinate to the Space Kernel and Resource Manager:
- **No Plan Authority:** The Orchestrator holds no authority over the authoritative TaskGraph. Mutating or committing plan changes requires `SpaceKernel.commit_plan_delta()`. Stale base versions are rejected with `plan.version.superseded`.
- **No Resource Authority:** The Orchestrator holds no capability to mint leases or allocate hardware directly. Leases must be requested through `ResourceManager.acquire()` and require valid lease tokens.
- **No Admission Authority:** The Orchestrator cannot bypass Kernel pre-dispatch admission control or budget limits. $0 budget returns `CapabilityResponse(status="denied")` and halts tool execution.
- **No Human Gate Authority:** High-risk actions route through `ApprovalManager` and can only be approved by the authenticated `approver_id`. The Orchestrator cannot self-approve.
- **Strict Space Isolation:** Cross-space operations (goal submission, plan mutation, resource lease, budget access) across Space boundaries are rejected with `PermissionError`.
- **Zero LLM Core Independence:** The entire `core/orchestrator/` module is strictly deterministic, contains zero LLM calls, and imports zero modules from `agents/`, `workers/`, `skills/`, or `workflows/`.

---

### Architecture & Contract Foundations

- **ADR-0007:** `adr/0007-goal-idempotency-and-lifecycle.md` — Defines goal submission idempotency scoped to `(space_id, command_id)`; returns existing active session on duplicate; prevents duplicate plans, leases, and Pulses.
- **ADR-0008:** `adr/0008-orchestrator-reconciler-and-authority-boundaries.md` — Resolves `docs/CONTRACT_MATRIX.md` `OPEN-008`. Formalizes the 5-submodule pipeline (`GoalAnalyzer`, `Planner`, `TeamBuilder`, `Monitor`, `Adapter`), the convergence loop (`PlanReconciler`), and constitutional non-authority boundaries.
- **Contract Traceability:** `docs/CONTRACT_MATRIX.md` updated with `GATE_VERIFIED` for `ORCH-001` through `ORCH-007`, `SPACE-003`, `ARC-005`, and `ARC-007`. `OPEN-008` marked resolved.
- **Spec Map:** `harness/spec_map.yaml` updated with all Phase 4 mappings.

---

### What Was Built

1. **Goal Analyzer (`core/orchestrator/goal_analyzer.py`):**
   - Pure translation of human `Command` into `GoalSpec`.
   - Extracts constraints and required capabilities.
   - Evaluates `single_agent_eligible` flag heuristic (Law 5, §4, §18).
   - Emits typed `goal.defined` Pulse via the bus. Executes 0 tools, allocates 0 resources, creates 0 leases.

2. **Planner (`core/orchestrator/planner.py`):**
   - Synthesizes uncommitted `ProposedPlan` with directed acyclic `TaskGraph`.
   - Distinguishes mandatory vs optional nodes for degraded-mode execution (`KERNEL-006`).
   - Emits typed `plan.created` Pulse with `plan_version` and `task_count`.

3. **Team Builder (`core/orchestrator/team_builder.py`):**
   - Maps TaskGraph nodes to Agent and Worker roles (`TaskAssignment`, `AssignmentTable`).
   - Detects hardware resource requirements (e.g., GPU/CUDA) without allocating them.
   - Respects `single_agent_eligible` flag.
   - Emits typed `task.assigned` Pulses with `plan_version`.

4. **Monitor (`core/orchestrator/monitor.py`):**
   - Pure Pulse observer constructing read-only `TimelineState`.
   - Tracks authoritative `plan_version`, task lifecycle states, held leases, contention queues, and budget exhaustion.
   - Never mutates plans or leases directly; provides observable ground truth to the Reconciler.

5. **Adapter / Reflector (`core/orchestrator/adapter.py`):**
   - Synthesizes proposed `PlanDelta` mutations (reassignment, node pruning, fallback insertion) based on failure observations.
   - Formats lessons-learned into typed `experience.stored` Pulses.

6. **Plan Reconciler (`core/orchestrator/reconciler.py`):**
   - Convergence loop comparing desired vs actual state.
   - Implements bounded retry policy (max 3 attempts) for `transient.*` failures within budget.
   - Enforces immediate escalation for `terminal.permission_denied` and `terminal.budget_exceeded`.
   - Re-plans on `terminal.not_found` by proposing node-pruning `PlanDelta`.
   - Emits typed `plan.reconciled` and `task.retry.scheduled` Pulses.
   - Handles automated rebase up to 3 attempts on CAS contention.

7. **Space Orchestrator Coordinator (`core/orchestrator/orchestrator.py`):**
   - Thin coordinator sequencing the 5 sub-modules.
   - Enforces Space isolation and command idempotency (`ADR-0007`).
   - Submits goal to Kernel plan store and Resource Manager.
   - Coordinates plan adaptation, resource lease requests, and graceful shutdown.

8. **Space Checkpoint & Restore (`core/space/kernel.py`):**
   - Implemented `create_checkpoint()` and `restore_checkpoint()` on `SpaceKernel`.
   - Restores authoritative plan version and TaskGraph; emits typed `space.restored` Pulse (`KERNEL-006`).

---

### Executable Evidence

1. **Phase 4 Core Unit Tests (`core/orchestrator/tests/`):**
   - **20 passed in 0.81s**
   - `test_goal_analyzer.py` (3 tests)
   - `test_planner.py` (3 tests)
   - `test_team_builder.py` (2 tests)
   - `test_monitor.py` (2 tests)
   - `test_adapter.py` (3 tests)
   - `test_reconciler.py` (3 tests)
   - `test_orchestrator.py` (4 tests)

2. **Phase 4 Harness Cases (`harness/cases/orchestrator/`):**
   - **28 passed in 1.27s**
   - `test_orchestrator_future.py` (5 tests): Full-loop goal to plan (`ORCH-001`), degraded mode (`ORCH-002`), goal analyzer (`ORCH-003`), team builder (`ORCH-004`), and AST core independence (`ORCH-007`).
   - `test_authority_boundaries.py` (4 tests): Plan CAS rejection on stale delta, resource authority & forged lease block, admission $0 budget denial, and human gate self-approval block.
   - `test_concurrency_and_contention.py` (2 tests): Resource contention queueing and servicing, concurrent Orchestrator CAS race.
   - `test_space_isolation_attack.py` (1 test): Cross-space attacks across plan, resource, lease, and budget rejected.
   - `test_orchestrator_recovery.py` (1 test): Orchestrator crash and authoritative Space state survival.
   - `test_chaos_v2.py` (15 tests): 15 comprehensive chaos/fault injection scenarios.

3. **Kernel & Space Extensions:**
   - `harness/cases/kernel/test_kernel_future.py` (`KERNEL-006` checkpoint restore): PASS
   - `harness/cases/space/test_space_future.py` (`SPACE-003` agent isolation): PASS

---

### Phase 4 Exit Gate Evaluation

| Exit Gate Requirement | Evidence | Status |
| --------------------- | -------- | ------ |
| Deterministic Space Orchestrator transforms goal into TaskGraph | `test_orchestrator_future.py`, `test_orchestrator.py` | PASS |
| Plan CAS authority respected; Orchestrator cannot bypass CAS | `test_authority_boundaries.py`, `test_concurrency_and_contention.py` | PASS |
| Resource Manager authority respected; no direct lease minting | `test_authority_boundaries.py`, `test_concurrency_and_contention.py` | PASS |
| Admission Control & budget authority respected | `test_authority_boundaries.py`, `test_chaos_v2.py` | PASS |
| Human Gate authority respected; no self-approval | `test_authority_boundaries.py` | PASS |
| Space isolation strictly enforced across all operations | `test_space_isolation_attack.py`, `test_chaos_v2.py` | PASS |
| Reconciler converges failures and handles CAS rebasing | `test_reconciler.py`, `test_orchestrator_future.py` | PASS |
| Zero LLM in core / Core independence verified via AST scan | `dep_guard.py`, `test_orchestrator_core_independence` | PASS |

**PHASE 4 GATE: PASS**
