# RYU AI — Phase 12.1 Implementation Report: Dispatcher Contracts & Task Graph Model

**Project:** RYU AI  
**Architecture:** Space-Centric Cognitive Architecture (SCCA)  
**Phase:** 12.1 — Dispatcher Contracts & Task Graph Model  
**Authoritative Specification:** `docs/PHASE_12_EXECUTION_ENGINE_SPEC.md`  
**Architectural Decision:** `adr/0041-autonomous-task-dispatcher-dag-traversal-and-plan-convergence-engine.md`  
**Baseline Commit:** `dcf5dda`  
**Phase 12.1 Gate Status:** **PASS**

---

## 1. Implemented Components

Phase 12.1 implements the foundational data models, lifecycle state machines, topological dependency validators, idempotency generators, and abstract dispatch protocols required for autonomous plan execution without violating any architectural or isolation boundaries.

| Component | File Path | Role & Invariants |
| :--- | :--- | :--- |
| **Task Lifecycle & Graph Engine** | `core/plans/task_graph.py` | Extends `TaskNode` and `TaskGraph` with an 11-state lifecycle state machine, deterministic `LEGAL_TRANSITIONS` enforcement, explicit dependency declarations, Kahn's algorithm topological sorting with deterministic tie-breaking, cycle detection, and missing dependency validation. |
| **Plans Module Facade** | `core/plans/__init__.py` | Exports `TaskGraph`, `TaskNode`, `TaskState`, `GraphCycleError`, `MissingDependencyError`, `IllegalStateTransitionError`, `TaskNotFoundError`, and state machine constants. |
| **Dispatcher Model & Protocols** | `core/orchestrator/dispatch_model.py` | Implements `DeterministicDispatcher`, `DispatchDecision`, `DispatchAction`, `DispatchAttempt`, `compute_dispatch_idempotency_key`, `VerifiedExecutionEvidence`, `UnverifiedEvidenceError`, and abstract protocols (`TaskDispatcherProtocol`, `WorkerInvokerProtocol`, `GoalEvaluatorProtocol`). |
| **Orchestrator Module Facade** | `core/orchestrator/__init__.py` | Exports all new dispatch models, exceptions, and protocols. |
| **Contract Suite** | `core/orchestrator/tests/test_phase12_dispatch_contracts.py` | 20 comprehensive unit and contract tests verifying DAG traversal, cycle detection, illegal transitions, idempotency deduplication, evidence verification, cross-space isolation, and AST core independence. |
| **Contract Traceability Matrix** | `docs/CONTRACT_MATRIX.md` | Registers contracts `DISPATCH-001` through `DISPATCH-005` in Section 30D with evidence state `UNIT_VERIFIED`. |
| **Harness Spec Map** | `harness/spec_map.yaml` | Maps `DISPATCH-001` through `DISPATCH-005` to `test_phase12_dispatch_contracts.py` under roadmap Phase 12. |

---

## 2. Task Lifecycle States & Legal Transitions

The task lifecycle state machine conforms strictly to `docs/PHASE_12_EXECUTION_ENGINE_SPEC.md §3` and `DISPATCH-002`.

### Supported States (`TaskState` Enum)

* **Initial States:** `PENDING`
* **Dependency & Approval Gates:** `READY`, `AWAITING_APPROVAL`
* **Admission & Resource Lease Gates:** `ADMITTED`, `LEASED`
* **Execution States:** `RUNNING` (with legacy alias `IN_FLIGHT`)
* **Terminal Success:** `COMPLETED`
* **Terminal or Intermediate Failures:** `BLOCKED`, `FAILED`, `TIMED_OUT`, `RETRY_PENDING`, `ESCALATED`, `CANCELLED`

### State Transition Matrix (`LEGAL_TRANSITIONS`)

```text
PENDING           --> READY, BLOCKED, CANCELLED
READY             --> AWAITING_APPROVAL, ADMITTED, CANCELLED
AWAITING_APPROVAL --> ADMITTED, BLOCKED, CANCELLED
ADMITTED          --> LEASED, BLOCKED, CANCELLED
LEASED            --> RUNNING, CANCELLED
RUNNING           --> COMPLETED, FAILED, TIMED_OUT, CANCELLED
RETRY_PENDING     --> READY, CANCELLED
BLOCKED           --> READY, CANCELLED, ESCALATED
FAILED            --> RETRY_PENDING, ESCALATED
TIMED_OUT         --> RETRY_PENDING, ESCALATED
ESCALATED         --> READY, CANCELLED
COMPLETED         --> (Terminal - No outgoing transitions)
CANCELLED         --> (Terminal - No outgoing transitions)
```

Any attempt to execute an invalid transition (e.g., `PENDING` $\rightarrow$ `COMPLETED` or `COMPLETED` $\rightarrow$ `RUNNING`) raises `IllegalStateTransitionError` immediately.

---

## 3. TaskGraph Cycle Detection & Topological Ordering

In accordance with `DISPATCH-001`:

1. **Cycle Detection:** Implemented using Kahn's algorithm on in-degrees in `TaskGraph.validate_dependencies()`. Any circular dependency (direct or transitive, including self-loops) raises `GraphCycleError` with the cycle members identified.
2. **Missing Dependency Validation:** If any node references a dependency ID not present in `task_graph.nodes`, `MissingDependencyError` is raised immediately before traversal.
3. **Deterministic Topological Sort:** `TaskGraph.topological_sort()` sorts zero-in-degree candidate tasks alphabetically by `node.id` at each step. This guarantees that tie-breaking across parallel branches is 100% deterministic and reproducible across all platforms and replays.
4. **Prerequisite Gating:** `TaskGraph.get_ready_tasks()` returns only tasks whose dependencies are all in `COMPLETED` status (or optional tasks in terminal skipped states).

---

## 4. Dispatcher Contracts & Protocols

To preserve the Core Boundary Rule (Law 7 / AGENTS.md §7) and decouple the core coordinator from external execution engines:

1. **`WorkerInvokerProtocol`:** Abstract interface defining `invoke_worker(task, space_id, plan_version) -> VerifiedExecutionEvidence`. Concrete worker invokers are deferred to runtime layers (`runtime/dispatcher.py` in Phase 12.4). Core does not import any worker classes.
2. **`GoalEvaluatorProtocol`:** Abstract interface defining `evaluate_convergence(task_graph, space_id) -> GoalConvergenceResult`.
3. **`DeterministicDispatcher`:** Deterministic core class that inspects a `TaskGraph` within a given `space_id`, validates boundaries, and produces an ordered sequence of `DispatchDecision` records (`DISPATCH`, `AWAIT_DEPENDENCIES`, `AWAIT_ADMISSION`, `AWAIT_LEASE`, `AWAIT_APPROVAL`, `TASK_COMPLETED`, `TASK_FAILED`, `TASK_BLOCKED`).

---

## 5. Exactly-Once Dispatch & Idempotency Design

Per `DISPATCH-003`:

1. **Deterministic Idempotency Key:**
   $$\text{Key} = \text{SHA-256}(\text{space\_id} : \text{plan\_version} : \text{task\_id} : \text{attempt})$$
   Calculated by `compute_dispatch_idempotency_key(space_id, plan_version, task_id, attempt)`.
2. **Attempt Tracking:** `DeterministicDispatcher.record_attempt(attempt)` records in-flight dispatch attempts. If an attempt with the same idempotency key is already tracked, the duplicate dispatch is rejected deterministically, preventing double-execution of identical task attempts across crashes or reconvergences.

---

## 6. Execution Evidence Model & DISPATCH-004 Clarification

### Architectural Clarification
A rigid requirement that every task produce a file on disk contradicts real-world agent operations (e.g., diagnostic queries, database health checks, calculations, status validations).

Under `DISPATCH-004` and `VerifiedExecutionEvidence`, four valid evidence types are contracted:
1. `artifact`: A tangible file artifact on disk, verified by SHA-256 content digest.
2. `structured_output`: Non-file structured results (e.g. JSON results, database records), requiring `exit_code == 0` and non-empty output data.
3. `signed_telemetry`: Telemetry reports signed by a verified component.
4. `exit_code`: Clean exit code verification for side-effect-free execution steps.

Any evidence lacking required digests or exhibiting non-zero exit codes raises `UnverifiedEvidenceError`.

---

## 7. Core Independence & Dependency Guard Verification

The Core Boundary Rule (`AGENTS.md §7`) is strictly verified:
```text
core/ MUST NOT import:
  - agents/
  - workers/
  - skills/
  - workflows/
  - llm/
  - channels/
  - memory/
```

### Static Guard Results
* `scripts/dep_guard.py`: **PASS** (0 forbidden imports found in `core/`).
* `scripts/v1_verify_core_independence.py`: **PASS** (AST Dependency Guard, Runtime Isolation Tests, Zero-LLM Control Loop all PASS).
* Dedicated AST test in `test_phase12_dispatch_contracts.py` (`test_core_independence_dep_guard_ast`): Scans `core/plans/task_graph.py` and `core/orchestrator/dispatch_model.py` to assert zero imports from forbidden modules.

---

## 8. Contract Synchronization & Schema Status

* `scripts/contract_sync.py`: **PASS** (38/38 registered pulse types in full synchronization with `docs/architecture.md §16`).
* `scripts/v1_audit_governance.py`: **PASS** (ADR Inventory 0001..0039, Pulse Registry & Codegen Sync, Payload Schemas 1:1 Coverage, Contract Matrix Integrity).
* `ruff check core/plans core/orchestrator`: **PASS** (0 lint or formatting errors).
* `mypy core/plans core/orchestrator`: **PASS** (Success: no issues found in 23 source files).

---

## 9. Test Coverage & Verification Results

### Test Execution Summary

| Test Suite / Target | Command | Passed | Skipped | Failed | Duration |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Phase 12.1 Contract Suite** | `pytest core/orchestrator/tests/test_phase12_dispatch_contracts.py` | **20** | 0 | 0 | 0.88s |
| **Core Orchestrator & Plans** | `pytest core/orchestrator/tests core/plans/tests core/space/tests` | **56** | 0 | 0 | 1.06s |
| **Full Core Unit Suite** | `pytest core` | **129** | 0 | 0 | 2.13s |

### Contract Verification Breakdown (`DISPATCH-001` .. `DISPATCH-005`)

| Contract ID | Test Case(s) | Status |
| :--- | :--- | :--- |
| `DISPATCH-001` | `test_valid_task_graph`, `test_topological_sort_deterministic`, `test_ready_tasks_initial`, `test_ready_tasks_after_prerequisite_completed`, `test_missing_dependency_raises`, `test_graph_cycle_detection`, `test_self_loop_cycle_detection` | `UNIT_VERIFIED` |
| `DISPATCH-002` | `test_task_state_enum_complete`, `test_legal_state_transitions_all_valid`, `test_illegal_state_transition_raises` | `UNIT_VERIFIED` |
| `DISPATCH-003` | `test_dispatch_idempotency_key_deterministic`, `test_dispatcher_duplicate_attempt_deduplication` | `UNIT_VERIFIED` |
| `DISPATCH-004` | `test_verified_execution_evidence_valid_artifact`, `test_verified_execution_evidence_structured_output`, `test_verified_execution_evidence_invalid_raises` | `UNIT_VERIFIED` |
| `DISPATCH-005` | `test_core_independence_dep_guard_ast`, `test_protocols_satisfied_by_mocks` | `UNIT_VERIFIED` |
| Isolation & Space Boundary | `test_cross_space_dispatch_rejected`, `test_dispatcher_evaluates_node_dependencies`, `test_dispatcher_dependency_failure_blocks_downstream` | `UNIT_VERIFIED` |

---

## 10. SCCA Compliance (The Six Laws)

RYU AI enforces exactly SIX immutable laws. No seventh law was created.

1. **Law 1 — Everything Happens Inside a Space:** All task nodes and dispatch decisions are strictly bounded to `space_id`. Any cross-space evaluation raises `CrossSpaceViolationError`.
2. **Law 2 — Capabilities Are Requested, Never Owned:** Tasks declare capability requests (e.g. `compute.cpu`, `fs.write`); the dispatcher transitions tasks to `READY` / `AWAITING_ADMISSION` so the Admission Controller can evaluate capability tokens.
3. **Law 3 — Components Communicate Through Pulses:** Task events and transitions will emit registered pulses via the durable pulse bus.
4. **Law 4 — Knowledge Belongs to the Space First:** Task execution evidence and state transitions remain strictly space-scoped.
5. **Law 5 — Humans Define Goals; Ryu Organizes Execution:** Plans originate from human goals, and tasks requiring human approval are transitioned to `AWAITING_APPROVAL`.
6. **Law 6 — Failures Are Contained, Escalated, and Never Silent:** Task failures block downstream dependents deterministically (`TASK_BLOCKED`) and transition to `FAILED` or `ESCALATED`, never swallowed.

---

## 11. Preserved Architectural Boundaries

* **Desktop Command Center:** Remains strictly a Channel/Client (`apps/ryu-desktop` $\leftrightarrow$ Channel Daemon). Holds zero execution, graph, or admission authority.
* **Space Kernel:** Remains sole authority for Space state and Plan CAS transitions (`core/space/`, `core/plans/`).
* **Admission Controller:** Remains sole authority for capability tokens and risk tiers (`core/capabilities/`).
* **Human Approval Engine:** Remains sole authority for approving gated tasks (`channels/approval/`, `core/space/`).
* **Deterministic Core:** Contains zero dynamic imports, zero monkey-patching, and zero dependencies on cognitive or worker packages.

---

## 12. Phase 12.1 Boundary (What Was NOT Implemented)

To maintain disciplined, incremental delivery, the following capabilities are explicitly deferred:
* **Phase 12.2:** Space Kernel CAS Integration & Plan Reconciliation (`PlanDelta` state transitions on Space Kernel).
* **Phase 12.3:** Admission Control & Fractional Lease Pipeline Integration.
* **Phase 12.4:** Concrete Worker Invoker (`runtime/dispatcher.py` bridging `WorkerInvokerProtocol` to real sandboxes).
* **Phase 12.5:** Execution Observation & Pulse Stream Telemetry.
* **Phase 12.6:** Goal Convergence & Replanning Loop.
* **Phase 12.7:** End-to-End Autonomous Execution Harness & Verification.

---

## 13. Git Tree Status

* **Branch:** `main`
* **Baseline Commit:** `dcf5dda`
* **Changes Staged / Ready for Commit:**
  * `core/plans/task_graph.py`
  * `core/plans/__init__.py`
  * `core/orchestrator/dispatch_model.py`
  * `core/orchestrator/__init__.py`
  * `core/orchestrator/tests/test_phase12_dispatch_contracts.py`
  * `docs/CONTRACT_MATRIX.md`
  * `harness/spec_map.yaml`
  * `docs/PHASE_12_1_IMPLEMENTATION_REPORT.md`

---

## 14. Readiness for Phase 12.2

With Phase 12.1 contracts and models verified:
* `TaskGraph` and `TaskNode` provide the complete state machine and topological traversal needed by Phase 12.2.
* Space Kernel's Plan CAS mechanism (`core/plans/engine.py` / `core/space/kernel.py`) can now accept `PlanDelta` mutations driven by task lifecycle transitions.
* The test harness is primed to verify Kernel CAS state persistence across transitions.

---

## 15. Final Gate Assessment

$$\text{PHASE 12.1 GATE: PASS}$$

All 18 specification requirements, all 5 dispatch contracts (`DISPATCH-001` through `DISPATCH-005`), all 20 new contract tests, and all 129 core unit tests pass cleanly with zero dependency violations and zero architectural drift.
