# ADR-0041: Autonomous Task Dispatcher, DAG Traversal, and Plan Convergence Engine

**Status:** Accepted for Phase 12 Specification  
**Date:** 2026-09-28  
**Author:** RYU Architecture Team  
**Scope:** Core Orchestration, Execution Dispatch, and Plan Convergence Runtime  
**Context Milestone:** Post-v1.0.1 Architecture Audit (`docs/NEXT_PHASE_ARCHITECTURE_AUDIT.md`)  

---

## 1. Context and Problem Statement

The post-v1.0.1 architecture audit (`docs/NEXT_PHASE_ARCHITECTURE_AUDIT.md`, commit `5324bdf`) identified the single architectural bottleneck preventing autonomous progression in RYU AI:
> **The Autonomous Plan Execution & Task Dispatch Disconnect.**

Prior to Phase 12, the end-to-end execution flow in RYU operated as follows:
```text
User Prompt 
  → Channel Daemon (POST /api/v1/spaces/{id}/prompt)
  → GoalAnalyzer (analyzes intent, emits goal.defined)
  → AdaptationLayer (generates avoidance hints from past negative experiences)
  → Planner (generates proposed TaskGraph DAG)
  → SpaceKernel CAS (commits plan_version=1, emits plan.created)
  → TeamBuilder (maps tasks to roles, emits task.assigned)
  ✖ [THE EXECUTION LOOP HALTS HERE]
```

Although RYU already possesses fully implemented, individually verified subsystems for:
* Space Kernel authority, execution budgets, and Plan CAS (`core/space/`)
* Pre-dispatch capability admission (`core/capabilities/admission.py`)
* Fractional resource leasing and priority queues (`core/resources/`)
* Sandboxed workers with Seccomp/platform containment (`workers/`)
* Signed skills and Model Context Protocol (MCP) clients (`skills/`)
* Node runtime device grants and hardware binding (`node/`)
* Cryptographic human approval gates (`channels/approval/`)
* Failure taxonomy mapping, bounded retries, and CAS plan deltas (`core/orchestrator/reconciler.py`)

**There is no autonomous runtime component that listens to `task.assigned`, traverses the TaskGraph DAG, requests capability admission, acquires resource leases, issues device grants, invokes the Worker Sandbox, observes execution results, evaluates outcomes, and converges the plan toward goal completion.**

In all previous phases (Phases 0 through 11 and release gates), execution only occurred because test suites or script runners procedurally and manually executed each step in code.

---

## 2. Decision

We establish the **Space Execution Dispatcher** (`core/orchestrator/dispatcher.py` and its runtime coordinator `runtime/dispatcher.py`) as the authoritative, deterministic execution engine responsible for driving an assigned `TaskGraph` to convergence.

### A. The Six SCCA Laws Preservation
The Dispatcher operates strictly in accordance with the Six Immutable Laws of SCCA:
1. **Law 1 (Inside a Space):** The Dispatcher is strictly Space-scoped (`space_id`). It cannot observe, dispatch, or query tasks belonging to another Space.
2. **Law 2 (Capabilities Requested):** The Dispatcher holds zero native execution capabilities. It must request capability admission from `SpaceKernel.admission` before every dispatch.
3. **Law 3 (Pulse Communication):** All lifecycle events, state mutations, and telemetry are published as typed, contract-governed Pulses over the Pulse Bus.
4. **Law 4 (Knowledge Space-First):** Generated artifacts, execution logs, and reflections are stored within the Space's local stores (`SpaceArtifactStore`, `SpaceHistoryStore`).
5. **Law 5 (Human Defines Goals; Ryu Organizes Execution):** The Dispatcher never redefines or drifts from the human's `GoalSpec`. It executes the committed DAG and evaluates convergence against the goal's objective.
6. **Law 6 (Failures Contained, Escalated, Never Silent):** Every failure (tool failure, lease loss, timeout, admission denial) escalates deterministically: Tool $\rightarrow$ Worker $\rightarrow$ Dispatcher $\rightarrow$ PlanReconciler $\rightarrow$ SpaceKernel $\rightarrow$ Human Gate. Zero exceptions are silently swallowed.

### B. Core Boundary Rule Preservation (AGENTS.md §7)
To comply with the mandatory Core Boundary Rule (`core/` MUST NOT import `workers/`, `agents/`, `skills/`, `workflows/`, `llm/`, `channels/`, or `memory/`), the Dispatcher in `core/orchestrator/` is split into:
1. **Deterministic Core Dispatcher (`core/orchestrator/dispatcher.py`):**
   - Owns DAG dependency traversal, ready task identification, idempotency checks, lease lifecycle coordination, and plan state transitions.
   - Depends only on `core/` interfaces and defines abstract protocols:
     ```python
     class WorkerInvokerProtocol(Protocol):
         def invoke_task(self, request: ExecutionRequest) -> ExecutionResult: ...

     class GoalEvaluatorProtocol(Protocol):
         def evaluate_goal(self, goal_spec: GoalSpec, evidence: list[Any]) -> GoalEvaluationResult: ...
     ```
2. **Runtime Execution Adapter (`runtime/dispatcher.py`):**
   - Implements `WorkerInvokerProtocol` in the runtime layer above core.
   - Instantiates sandboxed workers (`workers/base.py`), binds skills (`skills/`), and executes capabilities under lease tokens.

### C. Authority Matrix
The Dispatcher is a coordinator; it is **NOT** an authority layer:

| Domain | Absolute Authority | Dispatcher Role |
|:---|:---|:---|
| **Space State & Isolation** | `SpaceKernel` | Subordinate client; verified by space ID |
| **Plan Versioning & State** | `SpaceKernel.plan_store` (CAS) | Proposes status updates via atomic CAS |
| **Capability Admission** | `AdmissionController` | Requests admission via `CapabilityRequest` |
| **Hardware & Leases** | `ResourceManager` | Requests leases via `acquire()`; releases on completion |
| **Device Execution** | `DeviceGrantManager` / Node | Passes lease token to receive valid grant |
| **Human Approvals** | `ApprovalManager` / Authenticator | Pauses dispatch when attention saturated or gate pending |
| **Execution Sandbox** | `WorkerSandbox` | Invokes worker within strict OS limits |
| **Failure Convergence** | `PlanReconciler` | Submits failure events for bounded retry or delta replanning |

---

## 3. Execution Lifecycle & State Machine

A `TaskNode` in the `TaskGraph` progresses through an explicit, deterministic 11-stage lifecycle:

```text
               ┌──────────────┐
               │   PENDING    │ (Awaiting upstream dependencies)
               └──────┬───────┘
                      │ Upstream nodes completed
                      ▼
               ┌──────────────┐
               │    READY     │ (Eligible for scheduling)
               └──────┬───────┘
                      │ Request Capability Admission
                      ▼
          ┌───────────────────────┐
          │   ADMISSION_PENDING   │
          └───────────┬───────────┘
                      │
        ┌─────────────┴─────────────┐
        ▼                           ▼
 ┌──────────────┐            ┌──────────────┐
 │   ADMITTED   │            │    BLOCKED   │ (Budget exceeded / Tier 2/3 gate needed)
 └──────┬───────┘            └──────────────┘
        │ Request Lease
        ▼
 ┌──────────────┐
 │    LEASED    │ (Hardware / CPU / GPU token acquired)
 └──────┬───────┘
        │ Dispatch to Worker Runtime
        ▼
 ┌──────────────┐
 │  DISPATCHED  │
 └──────┬───────┘
        │ Worker starts execution
        ▼
 ┌──────────────┐
 │   RUNNING    │
 └──────┬───────┘
        │ Worker returns ExecutionResult + Artifacts
        ▼
 ┌──────────────┐
 │  OBSERVING   │ (Digest SHA-256, verify integrity, check taint)
 └──────┬───────┘
        │ Evaluate task completion criteria
        ▼
 ┌──────────────┐
 │  EVALUATING  │
 └──────┬───────┘
        │
   ┌────┴───────────────────────────┐
   ▼                                ▼
┌──────────────┐             ┌──────────────┐
│  COMPLETED   │             │    FAILED    │
└──────┬───────┘             └──────┬───────┘
       │                            │
       ▼                            ▼
Unblock Downstream           Trigger PlanReconciler
Dependent Nodes              (Retry / Replan / Escalate)
```

---

## 4. Idempotency & Replay Semantics

### A. Idempotency Token
Every execution attempt is identified by a deterministic, content-derived idempotency key:
$$\text{IdempotencyKey} = \text{SHA-256}(\text{space\_id} \parallel \text{plan\_version} \parallel \text{task\_id} \parallel \text{attempt})$$
* If a duplicate `task.assigned` or dispatch pulse arrives, the Dispatcher checks the authoritative store and ignores the duplicate without launching duplicate worker processes.
* The PostgreSQL `store.append()` operation remains idempotent via `ON CONFLICT DO NOTHING`.

### B. Deterministic Replay
During replay:
* The Dispatcher does **not** re-execute external tools or workers.
* The Dispatcher re-reads the historical sequence of `task.assigned`, `task.started`, `worker.tool.succeeded`, and `task.completed` pulses from PostgreSQL.
* The state machine deterministically reproduces the exact historical task states and CAS versions.

---

## 5. Convergence Controls & Loop Bounding

To prevent infinite autonomous execution loops, the Dispatcher enforces strict, unbypassable convergence budgets:
1. **Task Attempt Budget:** Maximum 3 retries for transient errors (`transient.*`).
2. **Plan Revision Budget:** Maximum 3 CAS rebases per replan cycle (`max_rebases=3`).
3. **Space Spend Budget:** Pre-dispatch hard stop enforced by `AdmissionController`.
4. **Execution Time Budget:** Per-task execution limits (`ExecutionLimits.timeout_seconds`) and global session timeouts.
5. **Attention Queue Limit:** Maximum concurrent human approval gates enforced by `AttentionBudget`.

When any budget is exhausted, execution halts immediately and deterministically escalates via `failure.escalated` (Law 6).

---

## 6. Alternatives Considered and Rejected

1. **Direct Agent Execution (Cognitive Loop Ownership):**
   * *Proposal:* Allow LLM agents to call worker tools directly in a while-loop.
   * *Rejected:* Violates SCCA Law 2 (capabilities are requested, never owned), eliminates deterministic CAS plan versioning, bypasses Kernel admission, and makes audit trails non-reproducible.
2. **Channel Daemon Execution Loop:**
   * *Proposal:* Implement the task loop inside `channels/daemon/server.py`.
   * *Rejected:* Violates the Desktop/Daemon Channel contract (the channel is an unprivileged client, not an authority root).
3. **Placing Worker Implementations inside `core/`:**
   * *Proposal:* Put the dispatcher and workers together inside `core/orchestrator/`.
   * *Rejected:* Violates the Core Boundary Rule ([`AGENTS.md` §7](file:///d:/RYU/AGENTS.md#L206-L230)) and fails `scripts/dep_guard.py`.

---

## 7. Consequences

### Positive
* Closes the autonomous execution gap: RYU can execute multi-task DAGs from goal submission to completion.
* Preserves all six SCCA laws and the Core Boundary Rule.
* Reuses existing verified subsystems (`AdmissionController`, `ResourceManager`, `WorkerSandbox`, `PlanStore`).
* Fully traceable, replayable, and bounded against infinite loops.

### Negative / Overhead
* Requires coordination across multiple asynchronous pulse boundaries.
* Introduces additional state management for in-flight task tracking in PostgreSQL.

---

## 8. Governing Invariant

> **The Dispatcher owns scheduling and coordination; the Kernel owns Space authority; the Resource Manager owns hardware leases; the Worker Sandbox owns execution isolation; and the Human owns the goal.**
