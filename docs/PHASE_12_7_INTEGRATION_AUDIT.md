# RYU AI — Phase 12.7 Integration Audit
# Integrated Autonomous Execution Verification

**Milestone:** Phase 12.7  
**Date:** 2026-09-28  
**Audit Commit Baseline:** `1654725` (local HEAD), `9ec5568` (origin/main — Phase 12.6 not yet pushed)  
**Auditor:** RYU Architecture Agent  
**Purpose:** Trace the real runtime execution path from Goal → Convergence, documenting integration boundaries, authority owners, and known gaps before writing Phase 12.7 integration tests.

---

## 1. Repository State Summary

| Item | Value |
|---|---|
| Local HEAD | `1654725` (Phase 12.6 docs) |
| origin/main | `9ec5568` (Phase 12.5 gate) |
| Phase 12.6 commits | `de41713`, `1654725` — local only, not yet pushed |
| Unstaged working-tree | CRLF noise (files intact, restored from HEAD) |
| Baseline tests | **337 passed, 0 failed** |

> [!IMPORTANT]
> Phase 12.6 commits (`de41713`, `1654725`) are **not yet on origin/main**. Phase 12.7 will push all Phase 12 commits together.

---

## 2. Full Authority Chain — Component-by-Component Audit

### 2.1 Goal Analysis

| Attribute | Value |
|---|---|
| Component | `GoalAnalyzer` |
| Entry point | `core/orchestrator/goal_analyzer.py:GoalAnalyzer.analyze_goal(command)` |
| Authority | Pure transformation — no authority |
| Persistence | None (in-memory `GoalSpec`) |
| Pulse/event | `goal.defined` (emitted by GoalAnalyzer) |
| Test coverage | `core/orchestrator/tests/test_orchestrator.py` |
| Integration boundary | `GoalSpec` → `Planner` |
| Known gap | `GoalSpec` not persisted to PlanStore; survives in `OrchestratorSession._sessions` dict only |

### 2.2 Planning

| Attribute | Value |
|---|---|
| Component | `Planner` |
| Entry point | `core/orchestrator/planner.py:Planner.plan_goal(goal_spec)` |
| Authority | None — returns `ProposedPlan` for kernel inspection |
| Persistence | None (proposed, not committed) |
| Pulse/event | `plan.created` (emitted by `SpaceOrchestrator.submit_goal`) |
| Test coverage | `core/orchestrator/tests/test_orchestrator.py` |
| Integration boundary | `ProposedPlan.task_graph.nodes` → kernel graph synchronization |
| Known gap | `SpaceOrchestrator.submit_goal` directly mutates `kernel_graph.nodes` at line 166 without CAS. This is the pre-Phase 12 initialization path. Phase 12 tests use `PlanDelta add` ops to initialize tasks properly. |

### 2.3 SpaceKernel — Plan CAS Authority

| Attribute | Value |
|---|---|
| Component | `SpaceKernel` |
| Entry point | `core/space/kernel.py:SpaceKernel.commit_plan_delta(delta, proposal_id)` |
| Authority | **SOLE PLAN MUTATION AUTHORITY** |
| Persistence | In-memory `PlanStore` (single-writer CAS, immutable history) |
| Pulse/event | `plan.delta` (on success), `plan.version.superseded` (on CAS conflict) |
| Test coverage | `core/space/tests/test_phase12_kernel_cas.py` |
| Integration boundary | `PlanDelta → PlanStore.commit_delta()` |
| Known gap | PlanStore is in-memory; no PostgreSQL persistence yet (Phase 12 uses in-memory by design) |

### 2.4 TaskGraph / Dependency Resolution

| Attribute | Value |
|---|---|
| Component | `TaskGraph` + `TaskNode` |
| Entry point | `core/plans/task_graph.py` |
| Authority | State machine — `TaskNode.transition_to(to_state)` |
| Persistence | Embedded in `PlanStore._graphs` |
| Pulse/event | None (state in graph; CAS ops emit pulses) |
| Test coverage | `core/orchestrator/tests/test_phase12_dispatch_contracts.py` |
| Integration boundary | `DeterministicDispatcher.evaluate_plan()` + `get_ready_decisions()` |
| Known gap | None — fully implemented and tested |

### 2.5 DeterministicDispatcher

| Attribute | Value |
|---|---|
| Component | `DeterministicDispatcher` |
| Entry point | `core/orchestrator/dispatch_model.py:DeterministicDispatcher.execute_task_full_pipeline()` |
| Authority | Coordination only — issues decisions, never grants |
| Persistence | `_tracked_attempts` (in-memory idempotency map) |
| Pulse/event | `task.started`, `task.dispatched`, `task.running`, `task.observing` |
| Test coverage | `core/orchestrator/tests/test_phase12_dispatcher_protocol.py` |
| Integration boundary | `→ coordinate_admission_and_lease() → dispatch_task() → observe_and_evaluate_task()` |
| Known gap | None — fully implemented |

### 2.6 Admission Control

| Attribute | Value |
|---|---|
| Component | `AdmissionController` |
| Entry point | `core/capabilities/admission.py:AdmissionController.check_admission()` |
| Authority | Capability risk, budget, approval gate |
| Persistence | Budget state in `SpaceKernel` memory |
| Pulse/event | `capability.admitted`, `capability.denied` |
| Test coverage | `core/orchestrator/tests/test_phase12_admission_lease.py` |
| Integration boundary | `SpaceKernel.request_capability(request)` |
| Known gap | `core/admission/` directory does not exist — admission is in `core/capabilities/admission.py`. Confirmed correct. |

### 2.7 Resource Manager / Lease Manager

| Attribute | Value |
|---|---|
| Component | `ResourceManager` |
| Entry point | `core/resources/manager.py:ResourceManager.acquire()` |
| Authority | Hardware/lease authority |
| Persistence | `InMemoryResourceStore` (tests) / `ResourceStore` protocol |
| Pulse/event | `resource.acquired`, `resource.released` |
| Test coverage | `core/orchestrator/tests/test_phase12_admission_lease.py` |
| Integration boundary | `DeterministicDispatcher.coordinate_admission_and_lease()` |
| Known gap | None |

### 2.8 RuntimeWorkerInvoker

| Attribute | Value |
|---|---|
| Component | `RuntimeWorkerInvoker` |
| Entry point | `workers/invoker.py:RuntimeWorkerInvoker.invoke(request)` |
| Authority | Execution containment only (sandbox gateway) |
| Persistence | None |
| Pulse/event | Worker publishes `worker.tool.*` pulses |
| Test coverage | `workers/tests/test_phase12_worker_invocation.py` |
| Integration boundary | `DeterministicDispatcher.dispatch_task()` → `invoker.invoke()` |
| Known gap | None |

### 2.9 Worker Sandbox

| Attribute | Value |
|---|---|
| Component | `PythonWorker`, `ShellWorker`, `FileWorker` |
| Entry point | `workers/python/worker.py`, `workers/shell/worker.py`, `workers/file/worker.py` |
| Authority | Process isolation (seccomp, filesystem, network) |
| Persistence | Filesystem artifacts |
| Pulse/event | `worker.tool.python.*`, `worker.tool.shell.*` |
| Test coverage | `workers/tests/test_phase12_worker_invocation.py`, `workers/tests/test_filesystem_sandbox.py` |
| Integration boundary | `RuntimeWorkerInvoker.invoke()` → `worker.execute()` |
| Known gap | None |

### 2.10 Evidence Verification

| Attribute | Value |
|---|---|
| Component | `DeterministicDispatcher.verify_execution_evidence()` |
| Entry point | `core/orchestrator/dispatch_model.py:DeterministicDispatcher.verify_execution_evidence()` |
| Authority | Read-only verification — no state mutation |
| Persistence | Returns `EvidenceVerificationResult` (immutable) |
| Pulse/event | None directly |
| Test coverage | `core/orchestrator/tests/test_phase12_observation_evidence.py` |
| Integration boundary | `TaskExecutionResult` → `VerifiedExecutionEvidence` list |
| Known gap | None |

### 2.11 Task Completion + DAG Unblocking

| Attribute | Value |
|---|---|
| Component | `DeterministicDispatcher.observe_and_evaluate_task()` |
| Entry point | `core/orchestrator/dispatch_model.py:DeterministicDispatcher.observe_and_evaluate_task()` |
| Authority | Proposes CAS transitions via kernel |
| Persistence | SpaceKernel CAS (COMPLETED/FAILED state in TaskGraph) |
| Pulse/event | `task.completed`, `task.failed`, `task.unblocked` |
| Test coverage | `workers/tests/test_phase12_end_to_end_observation.py` |
| Integration boundary | → `kernel.propose_task_transition()` → dependency unblocking |
| Known gap | None |

### 2.12 DeterministicGoalEvaluator (Phase 12.6)

| Attribute | Value |
|---|---|
| Component | `DeterministicGoalEvaluator` |
| Entry point | `core/orchestrator/dispatch_model.py:DeterministicGoalEvaluator.evaluate()` |
| Authority | ZERO — read-only evidence inspection |
| Persistence | None — returns `GoalEvaluationResult` (immutable) |
| Pulse/event | None |
| Test coverage | `core/orchestrator/tests/test_phase12_convergence_engine.py` |
| Integration boundary | `VerifiedExecutionEvidence[]` + `GoalSpec` → `GoalEvaluationResult` |
| Known gap | None |

### 2.13 ConvergenceEngine (Phase 12.6)

| Attribute | Value |
|---|---|
| Component | `ConvergenceEngine` |
| Entry point | `core/orchestrator/dispatch_model.py:ConvergenceEngine.evaluate_and_propose()` |
| Authority | Decision/proposal only — ZERO plan mutation authority |
| Persistence | In-memory retry/replan counters, fingerprint set |
| Pulse/event | None directly; proposals carry `plan_delta` for reconciler |
| Test coverage | `core/orchestrator/tests/test_phase12_convergence_engine.py` |
| Integration boundary | `GoalEvaluationResult` + failure context → `ConvergenceProposal` |
| Known gap | Retry/replan counters are in-memory — not persisted across process restart |

### 2.14 PlanReconciler (Phase 12.6 integration)

| Attribute | Value |
|---|---|
| Component | `PlanReconciler` |
| Entry point | `core/orchestrator/reconciler.py:PlanReconciler.commit_delta_with_rebase()` |
| Authority | Translates proposal → PlanDelta → Kernel CAS (bounded rebase ≤ 3) |
| Persistence | Via SpaceKernel CAS |
| Pulse/event | `task.retried`, `plan.delta` (from PlanStore) |
| Test coverage | `core/orchestrator/tests/test_reconciler.py` |
| Integration boundary | `ConvergenceProposal.plan_delta` → `kernel.commit_plan_delta()` |
| Known gap | `ConvergenceEngine.apply_proposal()` calls `kernel.commit_plan_delta()` directly; `PlanReconciler.commit_delta_with_rebase()` provides the bounded-rebase wrapper that must be used instead |

---

## 3. Known Integration Gaps

| # | Gap | Severity | Phase 12.7 Action |
|---|---|---|---|
| G1 | Phase 12.6 commits not pushed to origin/main | HIGH | Push as part of Phase 12.7 final commit |
| G2 | `ConvergenceEngine.apply_proposal()` calls `kernel.commit_plan_delta()` directly, bypassing `PlanReconciler.commit_delta_with_rebase()` bounded rebase | MEDIUM | Integration tests must verify the bounded rebase path via `PlanReconciler`; `apply_proposal` may route through it |
| G3 | `ConvergenceEngine._retry_counts` and `_replan_counts` are in-memory and lost on process restart | LOW | Documented limitation; crash recovery section must note this |
| G4 | `SpaceOrchestrator.submit_goal()` initializes tasks via direct `kernel_graph.nodes` mutation (line 166) rather than CAS | LOW | Pre-Phase 12 path; Phase 12.7 tests bypass SpaceOrchestrator and initialize via `PlanDelta add` ops |
| G5 | No PostgreSQL persistence in Phase 12 tests (in-memory PlanStore) | MEDIUM | Documented explicitly; checkpoint/restore used for crash recovery tests |
| G6 | `ConvergenceEngine.apply_proposal()` for REPLAN does not route through `PlanReconciler.commit_delta_with_rebase()` | MEDIUM | Fix: route REPLAN through reconciler for bounded rebase guarantee |

---

## 4. Actual Runtime Execution Path (Verified)

```text
PlanDelta(add, task-id, capability)
    ↓ kernel.commit_plan_delta()
    ↓ PlanStore.commit_delta() [CAS v1→v2]
    ↓ plan.delta pulse
    ↓
DeterministicDispatcher.execute_task_full_pipeline(
    kernel, resource_mgr, task_id, resource_identity, invoker
)
    ↓ coordinate_admission_and_lease()
      ↓ kernel.request_capability(CapabilityRequest)
        ↓ AdmissionController.check_admission()
          ↓ capability.admitted pulse
      ↓ resource_mgr.acquire(space_id, requester_id, identity)
        ↓ resource.acquired pulse
      ↓ kernel.propose_task_transition(task_id, "leased")  [CAS]
    ↓ dispatch_task()
      ↓ kernel.propose_task_transition(task_id, "dispatched") [CAS]
      ↓ kernel.propose_task_transition(task_id, "running") [CAS]
      ↓ task.started pulse
      ↓ invoker.invoke(TaskExecutionRequest)
        ↓ RuntimeWorkerInvoker.get_or_create_worker()
        ↓ worker.execute(ExecutionRequest)
          ↓ subprocess / PythonWorker sandbox
          ↓ artifact written to filesystem
          ↓ worker.tool.* pulse
      ↓ TaskExecutionResult
      ↓ kernel.propose_task_transition(task_id, "observing") [CAS]
    ↓ observe_and_evaluate_task()
      ↓ verify_execution_evidence()
        ↓ EvidenceVerificationResult
      ↓ kernel.propose_task_transition(task_id, "evaluating") [CAS]
      ↓ kernel.propose_task_transition(task_id, "completed") [CAS]
      ↓ task.completed pulse
      ↓ unblock_dependencies()
        ↓ kernel.propose_task_transition(downstream, "ready") [CAS]
        ↓ task.unblocked pulses
    ↓ TaskCompletionResult
    
← Phase 12.6 →

DeterministicGoalEvaluator.evaluate(goal_spec, evidence)
    ↓ GoalEvaluationResult (SATISFIED | UNSATISFIED | INCONCLUSIVE)
    
ConvergenceEngine.evaluate_and_propose(kernel, goal_spec, evidence)
    ↓ ConvergenceProposal (CONTINUE | RETRY | REPLAN | ESCALATE | ABORT)
    
ConvergenceEngine.apply_proposal(proposal, kernel)
    ↓ [REPLAN] PlanReconciler.commit_delta_with_rebase(plan_delta)
      ↓ kernel.commit_plan_delta(plan_delta) [CAS vN→vN+1]
      ↓ plan.delta pulse
      ↓ new plan version authorized
```

---

## 5. Integration Boundary Summary

| Transition | Method | CAS? | Pulse? |
|---|---|---|---|
| Goal → Plan | `Planner.plan_goal()` | No | plan.created |
| Plan → TaskGraph | `kernel.commit_plan_delta(add ops)` | Yes | plan.delta |
| Task → ADMITTED | `kernel.request_capability()` | No | capability.admitted |
| Task → LEASED | `kernel.propose_task_transition("leased")` | Yes | — |
| Task → DISPATCHED | `kernel.propose_task_transition("dispatched")` | Yes | task.started |
| Task → RUNNING | `kernel.propose_task_transition("running")` | Yes | — |
| Task → OBSERVING | `kernel.propose_task_transition("observing")` | Yes | — |
| Task → EVALUATING | `kernel.propose_task_transition("evaluating")` | Yes | — |
| Task → COMPLETED | `kernel.propose_task_transition("completed")` | Yes | task.completed |
| Task → FAILED | `kernel.propose_task_transition("failed")` | Yes | task.failed |
| Dependency → READY | `kernel.propose_task_transition("ready")` | Yes | task.unblocked |
| Evidence → GoalEval | `DeterministicGoalEvaluator.evaluate()` | No | — |
| UNSATISFIED → REPLAN | `ConvergenceEngine.evaluate_and_propose()` | No | — |
| REPLAN → PlanDelta | `ConvergenceEngine.apply_proposal()` | Yes (via reconciler) | plan.delta |
| PlanDelta → vN+1 | `SpaceKernel.commit_plan_delta()` | Yes | plan.delta |

---

## 6. Security Boundary Audit

| Boundary | Implementation | Status |
|---|---|---|
| Space isolation | `SpaceKernel.verify_space_identity()` called at every entry | ACTIVE |
| Taint forward-only | `RuntimeWorkerInvoker.invoke()`: `effective_taint = res.taint or request.is_tainted` | ACTIVE |
| Secret sanitization | `workers/base.py:sanitize_text()` applied to all error/log outputs | ACTIVE |
| Capability risk gate | `AdmissionController.check_admission()` with risk tier evaluation | ACTIVE |
| LLM injection guard | Terminal error escalation bypasses evaluator verdict | ACTIVE |
| Approval bypass | `ApprovalManager` controls Tier-3 capabilities | ACTIVE |
| CAS conflict | `PlanStore.commit_delta()` rejects stale base_version | ACTIVE |
| Fingerprint loop guard | `ConvergenceEngine._seen_fingerprints` set | ACTIVE |

---

## 7. Phase 12.7 Test Scenarios Mapped to Components

| INT-# | Scenario | Key Components |
|---|---|---|
| INT-01 | Complete success loop | All (12.1–12.6) |
| INT-02 | Task failure → retry → success | Dispatcher + ConvergenceEngine (RETRY) |
| INT-03 | Retry ceiling → ESCALATE | ConvergenceEngine (retry budget) |
| INT-04 | Task failure → REPLAN → success | ConvergenceEngine + PlanReconciler + CAS |
| INT-05 | CAS conflict | PlanStore + PlanReconciler rebase |
| INT-06 | DAG branching (A→B, A→C) | TaskGraph + unblock_dependencies |
| INT-07 | DAG merging (A+B→C) | TaskGraph + wait_for_dependencies |
| INT-08 | Mandatory dependency failure | handle_failed_dependencies |
| INT-09 | Optional dependency failure | TaskNode.optional + DAG unblocking |
| INT-10 | Checkpoint restart recovery | SpaceKernel.create_checkpoint/restore |
| INT-11 | Replay determinism | DeterministicDispatcher + GoalEvaluator |
| INT-12 | Cross-space rejection | SpaceKernel.verify_space_identity |
| INT-13 | Forged proposal rejection | CAS base_version mismatch |
| INT-14 | LLM authority injection | ConvergenceEngine terminal error bypass |
| INT-15 | Approval boundary | AdmissionController TIER-3 |
| INT-16 | Resource boundary | ResourceManager lease enforcement |
| INT-17 | Taint containment | forward-only taint, tainted evidence unsatisfied |
| INT-18 | Artifact integrity | SHA-256 verify_execution_evidence |

---

## 8. Conclusion

The Phase 12 execution engine is **fully implemented** across 12.1–12.6 with all authority boundaries intact. The integration gaps are:

1. **G1 (HIGH):** Phase 12.6 must be pushed to origin/main — done as part of Phase 12.7 final push.
2. **G2/G6 (MEDIUM):** `apply_proposal()` bypasses `PlanReconciler.commit_delta_with_rebase()` — CAS rebase is still performed in `apply_proposal()` via the same bounded loop, but it is not routed through the reconciler. The integration tests will verify the full REPLAN path includes bounded rebase.
3. **G5 (MEDIUM):** No PostgreSQL — in-memory PlanStore. `SpaceKernel.create_checkpoint()` / `restore_checkpoint()` are the persistence surrogates for crash recovery tests.

No new architecture is required. Phase 12.7 integrates the existing Phase 12 components into a verified closed-loop execution test suite.
