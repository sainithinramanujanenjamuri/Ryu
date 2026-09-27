# RYU AI — Phase 12 Engineering Specification: Space Execution Dispatcher & Plan Convergence Engine

**Document Status:** FORMAL ARCHITECTURAL SPECIFICATION  
**Author:** RYU Architecture Team  
**Milestone:** Phase 12 (Autonomous Plan Execution & Task Dispatch Engine)  
**Governing Architecture:** Space-Centric Cognitive Architecture (SCCA) — Frozen Baseline  
**Foundational Input:** `docs/NEXT_PHASE_ARCHITECTURE_AUDIT.md` (Commit `5324bdf`)  
**Architectural Decision:** `adr/0041-autonomous-task-dispatcher-dag-traversal-and-plan-convergence-engine.md`  
**Core Boundary Authority:** `AGENTS.md` §7 (Deterministic Core Independence)  

---

## 1. Executive Overview & Objective

### 1.1 The Objective
The objective of Phase 12 is to implement the missing **Space Execution Dispatcher** and its surrounding convergence runtime.

RYU AI v1.0.0 and v1.0.1 established and independently verified every prerequisite subsystem:
* **Authority Root:** Space Kernel, Admission Control, Plan CAS, Escalation Windows (`core/space/`, `core/capabilities/`).
* **Resource Governance:** Fractional CPU/GPU leases, anti-starvation queues, idempotency caches (`core/resources/`).
* **Execution Boundary:** Sandboxed Workers, Seccomp/platform containment, and signed MCP skills (`workers/`, `skills/`).
* **Hardware Binding:** Node Runtime, cryptographic device grants, dual invalidation flow (`node/`, `node_runtime/`).
* **Human Gate:** Cryptographic WebCrypto `token-hmac-v1` approvals (`channels/approval/`).
* **Failure Reconciliation:** Failure taxonomy mapping, bounded retries, and CAS plan deltas (`core/orchestrator/reconciler.py`).
* **Audit & Projection:** PostgreSQL event store, Redis Streams live transport, and Desktop Command Center (`core/pulse_bus/`, `apps/ryu-desktop/`).

Prior to Phase 12, the runtime execution flow terminates immediately after planning:
$$\text{User Prompt} \longrightarrow \text{GoalAnalyzer} \longrightarrow \text{Planner} \longrightarrow \text{Kernel CAS} \longrightarrow \text{TeamBuilder} \longrightarrow \mathbf{task.assigned} \longrightarrow \mathbf{STOP}$$

Phase 12 builds the deterministic engine that moves from `task.assigned` to:
$$\text{Task Admitted} \longrightarrow \text{Lease Acquired} \longrightarrow \text{Worker Dispatched} \longrightarrow \text{Execution Observed} \longrightarrow \text{Artifact Produced} \longrightarrow \text{Result Evaluated} \longrightarrow \text{Next DAG Node Unblocked} \longrightarrow \text{Plan Converged}$$

### 1.2 Non-Negotiable Invariants
1. **SCCA Has Exactly Six Immutable Laws:** Laws 1 through 6 are permanent. No "Law 7" exists.
2. **The Core Boundary Rule (AGENTS.md §7):** `core/` **MUST NOT** import from `agents/`, `workers/`, `skills/`, `workflows/`, `llm/`, `channels/`, or `memory/`. Dispatcher coordination within `core/orchestrator/` relies strictly on dependency inversion protocols (`WorkerInvokerProtocol`, `GoalEvaluatorProtocol`).
3. **Desktop & CLI Are Channels:** Channels are unprivileged clients and **never** authority roots.
4. **Authority Separation:** The Dispatcher does **not** grant permissions, mint leases, bypass budgets, or approve human gates. It requests them from the authoritative managers.

---

## 2. Phase A — Architectural Baseline & Subsystem Reuse

Phase 12 invents zero duplicate subsystems. It binds the existing, verified components:

```text
┌──────────────────────────────────────────────────────────────────────────────┐
│                            SPACE EXECUTION DISPATCHER                        │
│                   (core/orchestrator/dispatcher.py)                          │
├──────────────────────────────────────┬───────────────────────────────────────┤
│ Existing Authority Subsystem         │ Dispatcher Integration Contract       │
├──────────────────────────────────────┼───────────────────────────────────────┤
│ SpaceKernel (core/space/kernel.py)   │ Enforces Space isolation (SPACE-001)  │
│ PlanStore (core/plans/plan_store.py) │ Single-writer CAS commits (PLAN-001)  │
│ Admission (core/capabilities/)       │ CapabilityRequest / Response (KERNEL) │
│ ResourceManager (core/resources/)    │ Fractional Leases / Queues (RESOURCE) │
│ DeviceGrantManager (node/grants.py)  │ Lease-backed Device Grants (GRANT)    │
│ ApprovalManager (channels/approval/) │ Attention budget & HMAC gates (AUTH)  │
│ BaseWorker (workers/base.py)         │ ExecutionRequest / Result (WORKER)    │
│ PlanReconciler (core/orchestrator/)  │ Bounded retry & PlanDelta replanning  │
│ Monitor (core/orchestrator/)         │ Pulse-derived timeline tracking (ORCH)│
│ PulseBus (core/pulse_bus/)           │ Durable persistence & replay (PULSE)  │
└──────────────────────────────────────┴───────────────────────────────────────┘
```

---

## 3. Phase B — TaskNode Execution Model & State Machine

### 3.1 Lifecycle States

A `TaskNode` in the `TaskGraph` progresses through an explicit, monotonic 11-stage primary sequence:

1. **`PENDING`:** Node is registered in the `TaskGraph`, but has uncompleted upstream dependencies in the DAG.
2. **`READY`:** All upstream prerequisite nodes are `COMPLETED`. The node is eligible for scheduling.
3. **`ADMISSION_PENDING`:** Dispatcher has submitted a `CapabilityRequest` to `AdmissionController`.
4. **`ADMITTED`:** Budget and risk checks passed. If Tier 2/3, human gate approval has been verified.
5. **`LEASE_PENDING`:** Dispatcher has requested hardware/compute units from `ResourceManager`.
6. **`LEASED`:** Valid `Lease` token issued. If hardware-bound, `DeviceGrant` is active.
7. **`DISPATCHED`:** `ExecutionRequest` handed off to the `WorkerInvokerProtocol`.
8. **`RUNNING`:** Sandboxed worker process has started execution.
9. **`OBSERVING`:** Worker has finished; artifacts and execution metrics are being SHA-256 digested and verified.
10. **`EVALUATING`:** Task output is evaluated against its expected acceptance criteria.
11. **`COMPLETED`:** Task succeeded. Downstream dependent nodes in the DAG are unblocked.

### 3.2 Exceptional & Failure States
* **`BLOCKED`:** Admission denied due to budget exhaustion or pending human approval gate.
* **`FAILED`:** Worker raised a terminal error or failed evaluation.
* **`TIMED_OUT`:** Worker exceeded execution time limits (`ExecutionLimits.timeout_seconds`).
* **`RETRY_PENDING`:** Transient failure recorded; awaiting backoff window for retry attempt ($N \le 3$).
* **`ESCALATED`:** Retry budget or replan limit exhausted; escalated to operator per SCCA Law 6.
* **`CANCELLED`:** Plan was superseded by a winning CAS delta or explicitly aborted by human operator.

### 3.3 State Transition Matrix

| Source State | Trigger Event | Target State | Responsible Owner | Pulse Published | Persistence Layer |
|:---|:---|:---|:---|:---|:---|
| `PENDING` | Upstream dependencies completed | `READY` | Dispatcher DAG Scheduler | `task.ready` | Memory / Cache |
| `READY` | Dispatcher selects task | `ADMISSION_PENDING` | Dispatcher Scheduler | None | Memory |
| `ADMISSION_PENDING`| `CapabilityResponse(status="ok")` | `ADMITTED` | Admission Controller | `capability.admitted` | Postgres `pulses` |
| `ADMISSION_PENDING`| `CapabilityResponse(status="denied")` | `BLOCKED` | Admission Controller | `space.budget.exceeded`| Postgres `pulses` |
| `ADMITTED` | `acquire()` submitted | `LEASE_PENDING` | Resource Manager | `resource.requested` | Postgres `pulses` |
| `LEASE_PENDING` | `ResourceAcquisitionResult(granted=True)`| `LEASED` | Resource Manager | `resource.granted` | Postgres `pulses` |
| `LEASED` | Worker invoker invoked | `DISPATCHED` | Runtime Dispatcher | None | Memory |
| `DISPATCHED` | Worker process starts | `RUNNING` | Worker Sandbox | `task.started` | Postgres `pulses` |
| `RUNNING` | Worker process finishes | `OBSERVING` | Dispatcher Collector | `worker.tool.succeeded`| Postgres `pulses` |
| `OBSERVING` | Artifact SHA-256 verified | `EVALUATING` | Goal / Task Evaluator | None | Memory |
| `EVALUATING` | Task criteria satisfied | `COMPLETED` | Kernel Plan CAS | `task.completed` | Postgres `pulses` |
| `EVALUATING` | Task criteria unsatisfied | `FAILED` | Kernel Plan CAS | `task.failed` | Postgres `pulses` |
| `FAILED` | Transient error & attempt $< 3$ | `RETRY_PENDING` | Plan Reconciler | `task.retried` | Postgres `pulses` |
| `FAILED` | Terminal error or attempt $\ge 3$| `ESCALATED` | Plan Reconciler | `failure.escalated` | Postgres `pulses` |

---

## 4. Phase C — Dependency-Aware DAG Traversal & Scheduling

### 4.1 Traversal Invariants
1. **Topological In-Degree Traversal:** A node $T_i$ is `READY` if and only if:
   $$\forall T_j \in \text{Parents}(T_i), \quad \text{State}(T_j) = \mathbf{COMPLETED}$$
2. **Parallel Scheduling:** All nodes in state `READY` whose required capabilities and resource leases do not conflict are dispatched concurrently up to the Space's concurrency limit.
3. **Optional Nodes:** If an upstream node with `optional: true` fails, downstream nodes treat the dependency as resolved with empty/null input.
4. **Cycle Prevention:** Before submitting any `TaskGraph` to the Kernel, the Planner and Dispatcher run cycle detection via Tarjan's strongly connected components algorithm. Any detected cycle aborts plan creation with `InvalidPlanError`.

### 4.2 Handling Plan Evolution (CAS Rebasing)
* If an execution failure triggers `PlanReconciler.reconcile_task_failure()`, an atomic `PlanDelta` is proposed to the Kernel.
* If a concurrent delta was committed (`plan.version.superseded`), the Dispatcher executes bounded rebase ($N \le 3$) per ADR-0003:
  1. Fetch authoritative `plan_version = V_{current}` from `PlanStore`.
  2. Rebase pending tasks onto new graph structure.
  3. Re-evaluate `READY` tasks against completed nodes.

---

## 5. Phase D — Idempotency Architecture

### 5.1 Idempotency Key Composition
Every execution dispatch is uniquely and deterministically identified by:
$$\text{IdempotencyKey} = \text{SHA-256}\Big(\text{space\_id} \parallel \text{plan\_version} \parallel \text{task\_id} \parallel \text{attempt\_number}\Big)$$

### 5.2 Deduplication Rules
1. **Pulse Redelivery:** If a redelivered `task.assigned` pulse is processed, the Dispatcher checks `Monitor.TimelineState`. If the task is already `RUNNING` or `COMPLETED`, the pulse is discarded as a no-op.
2. **Lease Deduplication:** `ResourceManager.acquire()` evaluates `idempotency_key`. If a lease was already issued for this exact key, the existing `Lease` token is returned without consuming additional units.
3. **Worker Deduplication:** The Worker Sandbox checks its active execution table before spawning child processes. If `request_id` matches an active process, duplicate execution is blocked.

---

## 6. Phase E — Authority Chain & Authority Matrix

```text
[Human Intent]
      │ (Defines GoalSpec per SCCA Law 5)
      ▼
[Space Kernel] ◄─── (Highest Deterministic Authority per SCCA Law 1)
      │
      ├───────────────────────┬───────────────────────┐
      ▼                       ▼                       ▼
[Admission Controller]   [Plan Store (CAS)]   [Approval Manager]
(Capability Authority)   (Plan Versioning)    (Human Gate Authority)
      │                       ▲                       ▲
      ▼                       │ (Proposes PlanDelta)  │ (Signs HMAC)
[Space Execution Dispatcher] ─┘                       │
      │                                               │
      ├───────────────────────┐                       │
      ▼                       ▼                       │
[Resource Manager]      [Worker Runtime]              │
(Hardware Lease Token)  (Sandbox Process Boundary)    │
      │                       │                       │
      ▼                       ▼                       │
[Node Runtime]          [Signed Skills / MCP]         │
(Device Hardware Grant) (Tool Execution)              │
      │                       │                       │
      └───────────────────────┴───────────────────────┘
                              ▼
                     [Generated Artifact]
                 (Space-scoped SHA-256 Digest)
```

### Authority Matrix

| Action | Component with Authority | Component Prohibited from Owning | Enforcement Mechanism |
|:---|:---|:---|:---|
| Create / Terminate Space | `SpaceKernel` | Dispatcher, Workers, Channels | Kernel identity check |
| Commit Plan State / CAS | `SpaceKernel.plan_store` | Dispatcher, Agents, Workers | Compare-And-Swap version check |
| Admit Capability Request | `AdmissionController` | Dispatcher, Workers, LLM | Space budget balance & policy |
| Allocate Hardware Lease | `ResourceManager` | Dispatcher, Node Runtime | LeaseManager atomic token table |
| Grant Device Hardware | `DeviceGrantManager` / Node | Dispatcher, Workers | Cryptographic derived credentials |
| Approve Human Gate | Human Operator (`token-hmac-v1`)| Dispatcher, Kernel, Agents | WebCrypto HMAC preimage verification |
| Execute Native Code | `WorkerSandbox` | Dispatcher, Core, Kernel | OS process, Seccomp, path whitelists |
| Promote Memory Globally | `KnowledgePromotionGate` | Dispatcher, Agents, Workers | SCCA Law 4 human/system gate |

---

## 7. Phase F — Worker Dispatch & Inversion-of-Control Protocol

### 7.1 The Protocol Boundary (Core Independence)
In `core/orchestrator/dispatcher.py`, the Dispatcher imports **zero** worker modules:

```python
# core/orchestrator/protocols.py
class WorkerInvokerProtocol(Protocol):
    """Dependency inversion protocol for invoking capability workers."""
    def invoke(self, request: ExecutionRequest) -> ExecutionResult:
        ...
```

### 7.2 The Runtime Coordinator
In `runtime/dispatcher.py` (outside `core/`), the `RuntimeWorkerInvoker` implements the protocol:
1. **Capability Matching:** Inspects `request.capability`. Maps to `PythonWorker`, `ShellWorker`, `MCPWorker`, or `NodeWorker`.
2. **Skill Resolution:** Resolves signed skill from `skills/` registry. Validates SHA-256 manifest.
3. **Sandbox Configuration:** Configures `FilesystemPolicy` (space directory only), `NetworkPolicy` (deny-by-default), and `ExecutionLimits` (memory, CPU, timeout).
4. **Execution:** Calls `worker.execute(request)` and returns `ExecutionResult`.

---

## 8. Phase G — Resource Manager & Lease Integration

The Dispatcher never implements scheduling or leasing logic directly:
1. **Pre-Dispatch Request:** Before invoking a worker, the Dispatcher invokes:
   ```python
   acq = resource_mgr.acquire(
       space_id=space_id,
       requester_id=task_id,
       identity=required_resource_identity,
       units=required_units,
       duration_seconds=task_timeout,
       idempotency_key=task_idempotency_key,
   )
   ```
2. **Queueing & Contention:** If `acq.granted == False`, the task enters `LEASE_PENDING`. The Dispatcher subscribes to `resource.granted` pulses to resume dispatch when units become available.
3. **Mandatory Release:** In all termination paths (`COMPLETED`, `FAILED`, `TIMED_OUT`, `CANCELLED`), the Dispatcher executes:
   ```python
   resource_mgr.release(space_id=space_id, requester_id=task_id, lease_token=lease.lease_token)
   ```

---

## 9. Phase H — Execution Observation & Evidence Hierarchy

The Dispatcher refuses to accept unverified claims of completion. Evidence must follow this strict hierarchy:

$$\mathbf{Artifact\ (SHA-256)} \succ \mathbf{Signed\ Tool\ Output} \succ \mathbf{OS\ Process\ Exit\ Code\ (0)} \succ \mathbf{Typed\ Pulse\ Telemetry}$$

* **Level 1 — Artifact Existence:** If the task claims to produce a file or report, the file must exist on disk within the space directory, match its expected MIME type, and match its registered SHA-256 hash.
* **Level 2 — Execution Metrics:** `ExecutionMetrics` must record non-zero duration and valid memory peak bytes.
* **Level 3 — Taint Verification:** If tool output was tainted, `security.taint.detected` must be propagated forward-only.

---

## 10. Phase I — Semantic Goal Evaluation & Quality Verification

### 10.1 Goal Evaluator Protocol
The Goal Evaluator determines whether the collected evidence satisfies the human's `GoalSpec`:

```python
# core/orchestrator/protocols.py
class GoalEvaluationStatus(str, Enum):
    SATISFIED = "satisfied"
    UNSATISFIED = "unsatisfied"
    INCONCLUSIVE = "inconclusive"

@dataclass(frozen=True)
class GoalEvaluationResult:
    status: GoalEvaluationStatus
    confidence: float
    reasoning: str
    missing_criteria: list[str]

class GoalEvaluatorProtocol(Protocol):
    def evaluate(self, goal_spec: GoalSpec, evidence: list[Any]) -> GoalEvaluationResult:
        ...
```

### 10.2 Deterministic Core Independence
* The basic evaluator is deterministic: it verifies task completion status, required artifact presence, and constraint checks without calling an LLM.
* Higher-level cognitive evaluation (semantic text analysis) is injected from `agents/` via `GoalEvaluatorProtocol`, keeping `core/` completely provider-independent.

---

## 11. Phase J — Plan Convergence, Replanning & Stopping Conditions

### 11.1 Convergence Actions
* **`CONTINUE`:** Next ready tasks in the DAG are scheduled.
* **`RETRY`:** Transient failure; task retried under exponential backoff ($N \le 3$).
* **`REPLAN`:** Structural failure or `UNSATISFIED` evaluation; `PlanReconciler` proposes a `PlanDelta`.
* **`ESCALATE`:** Budgets exhausted; human intervention requested via Attention Queue.
* **`ABORT`:** Terminal unrecoverable violation; Space enters `space.completed` with failure flag.

### 11.2 Convergence Controls & Limits

| Control Dimension | Authority Root | Hard Ceiling | Enforcement Reaction |
|:---|:---|:---:|:---|
| **Task Retry Budget** | `PlanReconciler` | Max 3 attempts | Escalates to `FAILED` / replan |
| **Plan Rebase Limit** | `PlanStore` (CAS) | Max 3 rebases | Emits `failure.escalated` |
| **Space Spend Budget** | `AdmissionController` | Configured \$ limit | Emits `space.budget.exceeded`, hard stop |
| **Task Time Limit** | `ExecutionLimits` | 30.0s (default) | SIGTERM / SIGKILL worker |
| **Attention Queue** | `AttentionBudget` | Max 3 concurrent gates | Pauses new capability requests |

---

## 12. Phase K — Failure Containment (SCCA Law 6)

Failures are contained to their origin scope and surfaced deterministically upward:

```text
Level 1: Tool Failure (e.g. Non-zero exit, network timeout)
  └── Captured by Worker -> Mapped to failure-taxonomy.json -> Emits worker.tool.failed

Level 2: Worker Failure (e.g. OOM, Seccomp violation, timeout)
  └── Captured by Runtime Dispatcher -> Releases Lease -> Emits task.failed

Level 3: Task Failure (e.g. Dependency failed, evaluation failed)
  └── Captured by Space Execution Dispatcher -> Hands off to PlanReconciler

Level 4: Plan Failure (e.g. Rebase limit breached, no valid fallback)
  └── Captured by Space Orchestrator -> Escalates to Space Kernel

Level 5: Space Failure (e.g. Budget exhausted, security breach)
  └── Captured by Space Kernel -> Emits failure.escalated -> Suspends for Human Operator
```

---

## 13. Phase L — Persistence & Durable Recovery Architecture

The Dispatcher is fully crash-resilient and restart-safe:
1. **System of Record:** PostgreSQL remains the authoritative store of record.
2. **In-Flight Task State:** Task state transitions are recorded via durable pulses in the `pulses` table.
3. **Startup Recovery:** Upon daemon or process restart:
   - Dispatcher reads uncompleted spaces from `SpaceKernel`.
   - Queries `PulseReplayer` to reconstruct active `TaskGraph` state from position 0.
   - Detects tasks in `RUNNING` or `DISPATCHED` state whose worker processes no longer exist.
   - Transitions abandoned tasks to `FAILED` with `transient.worker_crash`, triggering clean reconciliation.

---

## 14. Phase M — Deterministic Replay Semantics

To uphold RYU's architectural guarantee that **Replay is a first-class debugging verb**:
* **Deterministic Control Flow:** Traversal decisions, readiness calculations, admission approvals, and lease allocations are strictly deterministic functions of input pulses.
* **Side-Effect Isolation:** During replay, the Dispatcher runs in `REPLAY_MODE`:
  - Worker invocations are **simulated from recorded pulses**.
  - No external OS processes, shell commands, or network connections are initiated.
  - The exact historical sequence of states and plan CAS versions is reproduced with bitwise parity.

---

## 15. Phase N — Security Architecture & Threat Model

| Threat Vector | Attack Scenario | Mitigation Control | Verification Test |
|:---|:---|:---|:---|
| **T-01: Forged Pulse** | Malicious client publishes `task.completed` directly | Dispatcher verifies pulse source identity against authorized worker | `test_adversarial_forged_completion` |
| **T-02: Duplicate Dispatch** | Network redelivery causes duplicate worker execution | Content-addressed `IdempotencyKey` in active dispatch cache | `test_idempotent_duplicate_dispatch` |
| **T-03: Stale Plan Execution** | Worker finishes after plan was updated via CAS | CAS version check on completion (`plan_version == current`) | `test_stale_plan_version_rejection` |
| **T-04: Malicious Artifact** | Worker writes malicious binary or path traversal | Path sanitization within space root; SHA-256 digest validation | `test_artifact_path_traversal_defense`|
| **T-05: Tainted Data Leak** | Untrusted tool output injected into cognitive context | Forward-only `taint: true` propagation; taint clearance gates | `test_taint_propagation_through_tasks` |
| **T-06: Resource Starvation**| Runaway worker exhausts CPU/GPU units | Leases bound by wall-clock TTL; automatic reaper on expiration | `test_lease_expiry_and_reaping` |
| **T-07: Infinite Loop** | Plan repeatedly replans failing task indefinitely | Hard budget of 3 CAS rebases and 3 retries; escalates to human | `test_convergence_loop_bounding` |
| **T-08: Cross-Space Breach** | Dispatcher in Space A attempts to run task in Space B | Strict kernel space verification (`kernel.verify_space_identity`) | `test_cross_space_dispatch_rejection` |
| **T-09: Lease Theft** | Worker attempts to use another worker's lease token | Lease identity check binds `requester_id` to `worker_id` | `test_lease_token_impersonation` |
| **T-10: Secret Leakage** | Worker attempts to dump credentials in task output | Recursive string sanitization (`sanitize_value`) strips tokens | `test_secret_redaction_in_task_output`|

---

## 16. Phase O — Contract Traceability Matrix

### 16.1 Reused Existing Contracts
* `SPACE-001..006`: Space isolation, resource scoping, identity.
* `PLAN-001..003`: Single-writer CAS, PlanDelta ops, conflict rejection.
* `KERNEL-001..003`: Admission control, budget windows, capability response.
* `RESOURCE-001..009`: Lease lifecycle, queue disciplines, anti-starvation.
* `WORKER-001..005`: Worker lifecycle, execution request/result, sandbox containment.
* `GRANT-001..004`: Device grants, dual invalidation, node profiles.

### 16.2 Proposed New Phase 12 Contracts

| Contract ID | Contract Name | Required Invariant | Implementation Boundary | Test Suite |
|:---|:---|:---|:---|:---|
| **DISPATCH-001** | Dependency-Aware Scheduling | Nodes enter `READY` only when all upstream dependencies are `COMPLETED`. | `core/orchestrator/dispatcher.py` | `test_dag_scheduling.py` |
| **DISPATCH-002** | Atomic Pre-Dispatch Gating | No worker may be invoked without pre-dispatch Admission AND Lease grant. | `core/orchestrator/dispatcher.py` | `test_pre_dispatch_gating.py` |
| **DISPATCH-003** | Idempotent Dispatch Execution | Duplicate pulses or redeliveries yield zero duplicate worker processes. | `core/orchestrator/dispatcher.py` | `test_dispatch_idempotency.py` |
| **DISPATCH-004** | Execution Evidence Verification| Completion requires verified artifact SHA-256 and non-zero execution metrics. | `core/orchestrator/dispatcher.py` | `test_evidence_verification.py`|
| **DISPATCH-005** | Bounded Plan Convergence | Unresolvable task failures escalate to `failure.escalated` within 3 rebases. | `core/orchestrator/dispatcher.py` | `test_plan_convergence.py` |

---

## 17. Phase P — Pulse Architecture & Event Taxonomy

Phase 12 operates primarily using the **38 registered pulse types**:
* **Dispatch Sequence:** `plan.created` $\rightarrow$ `task.assigned` $\rightarrow$ `resource.requested` $\rightarrow$ `resource.granted` $\rightarrow$ `task.started` $\rightarrow$ `worker.tool.called` $\rightarrow$ `worker.tool.succeeded` $\rightarrow$ `task.completed`.
* **Failure Sequence:** `worker.tool.failed` $\rightarrow$ `task.failed` $\rightarrow$ `task.retried` $\rightarrow$ `plan.delta` $\rightarrow$ `failure.escalated`.

### Proposed Candidate Extensions (For Contract Hardening Gate):
1. `task.ready` (*Optional telemetry*): Indicates a DAG node has cleared all dependencies and is queueable.
2. `goal.evaluated` (*Optional telemetry*): Publishes structured evaluation output (`status`, `confidence`, `missing_criteria`).

---

## 18. Phase Q — ADR-0041 Traceability

The design of the Space Execution Dispatcher is formally codified in:
[`adr/0041-autonomous-task-dispatcher-dag-traversal-and-plan-convergence-engine.md`](file:///d:/RYU/adr/0041-autonomous-task-dispatcher-dag-traversal-and-plan-convergence-engine.md).

---

## 19. Phase R — Comprehensive Test & Verification Strategy

The implementation of Phase 12 must satisfy **25 mandatory verification scenarios**:

```text
┌────────────────────────────────────────────────────────────────────────┐
│                   PHASE 12 VERIFICATION BATTERY                        │
├────────────────────┬───────────────────────────────────────────────────┤
│ UNIT (1-5)         │ Single task, Multi-task DAG, Parallel tasks,     │
│                    │ In-degree dependency blocking, Cycle rejection    │
├────────────────────┼───────────────────────────────────────────────────┤
│ ADVERSARIAL (6-10) │ Forged completion, Duplicate dispatch, Stale plan,│
│                    │ CAS conflict rebase, Tainted data propagation     │
├────────────────────┼───────────────────────────────────────────────────┤
│ INTEGRATION (11-15)│ Admission hard stop, Attention budget saturation, │
│                    │ Resource lease acquisition, Lease timeout release,│
│                    │ MCP tool execution roundtrip                      │
├────────────────────┼───────────────────────────────────────────────────┤
│ CHAOS (16-18)      │ Worker process SIGKILL, Node disconnect,          │
│                    │ Database network interruption                     │
├────────────────────┼───────────────────────────────────────────────────┤
│ PERSISTENCE (19-21)│ Dispatcher restart recovery, Causal chain replay, │
│                    │ PostgreSQL outbox reconciliation                  │
├────────────────────┼───────────────────────────────────────────────────┤
│ EVALUATION (22-23) │ Goal satisfied completion, Replanning on failure  │
├────────────────────┼───────────────────────────────────────────────────┤
│ E2E (24-25)        │ End-to-end autonomous multi-step software task,   │
│                    │ Replay bitwise byte equivalence verification      │
└────────────────────┴───────────────────────────────────────────────────┘
```

---

## 20. Phase S — Phased Implementation Milestones

```text
Milestone 12.1: Dispatcher Contracts & Task Graph Model
  ├── Update TaskNode dataclass with dependencies list
  └── Codify DISPATCH-001..005 schemas

Milestone 12.2: Deterministic DAG Traversal Engine
  ├── Implement core/orchestrator/dispatcher.py (in-degree scheduling)
  └── Verify parallel and sequential ready task resolution

Milestone 12.3: Admission & Lease Coordination
  ├── Integrate with AdmissionController and ResourceManager
  └── Verify pre-dispatch gating and atomic lease acquisition

Milestone 12.4: Runtime Worker Invoker Protocol
  ├── Implement runtime/dispatcher.py (outside core/)
  └── Verify sandboxed worker process execution and lease binding

Milestone 12.5: Evidence Collection & Verification
  ├── Implement artifact digestion and metrics collection
  └── Verify Level 1-3 evidence validation rules

Milestone 12.6: Plan Reconciler & Convergence Loop
  ├── Wire worker failures into PlanReconciler
  └── Implement bounded retry, CAS rebase, and escalation

Milestone 12.7: Deterministic Goal Evaluator
  ├── Implement GoalEvaluatorProtocol
  └── Verify SATISFIED / UNSATISFIED plan completion

Milestone 12.8: Crash Recovery & Persistence
  ├── Implement startup pulse scan and abandoned task cleanup
  └── Verify survival across daemon and PostgreSQL restarts

Milestone 12.9: Full Vertical Slice & E2E Verification
  ├── Execute 25-scenario master verification battery
  └── Prove autonomous execution from prompt to goal completion
```

---

## 21. Phase T — Explicit Non-Goals

To maintain strict architectural discipline, the following capabilities are **explicitly excluded** from Phase 12:
1. **Voice / Audio Interaction (STT / TTS):** No microphone inputs, audio streams, or speech synthesis.
2. **Video & Streaming WebRTC:** No video frame processing or camera device grants.
3. **External Graph Databases (Neo4j):** No graph query languages or external graph dependencies.
4. **Unrestricted Cognitive Self-Modification:** No agents altering their own prompts or architecture without human gate approval (Law 5).
5. **Desktop Authority Expansion:** Desktop UI remains strictly an unprivileged Channel/Client over loopback HTTP.
6. **Bypassing Human Gates:** Tier 2/3 capabilities must never execute without verified WebCrypto HMAC preimages.
