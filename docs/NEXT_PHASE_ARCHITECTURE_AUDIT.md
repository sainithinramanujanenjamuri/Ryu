# RYU AI — Post-v1.0.1 Architecture & Capability Audit

**Document Status:** CANONICAL ARCHITECTURE AUDIT  
**Date:** September 28, 2026  
**Repository State:** v1.0.1 Released & Verified (Commit `f24075d`)  
**Architecture:** Space-Centric Cognitive Architecture (SCCA) — Frozen Baseline  
**Authority:** Read-Only Audit (Zero Production Code Modified / No Feature Implementation)  

---

## 1. Executive Summary

Following the release freeze and verification of **RYU AI v1.0.1**, this audit examines the actual, executable state of the repository to identify the fundamental architectural capability that RYU is currently missing.

RYU v1.0.0 and v1.0.1 established and verified:
* An immutable, durable Pulse Bus with PostgreSQL and Redis Streams.
* Space Kernel authority boundaries, execution budgets, Compare-And-Swap (CAS) plan versioning, and secret containment.
* Resource lease management, queuing, and chaos resilience.
* Cryptographic human approval gates (`token-hmac-v1`) via WebCrypto and local Channel Daemon.
* Sandboxed workers, signed skills, and multi-platform node device grants.
* A hardened Desktop Command Center (Tauri v2 + React) exposing history rehydration, zero-privilege HTML sandbox preview, space artifacts, and file ingress.

However, an empirical tracing of the execution path reveals a critical architectural disconnect:

> **The Current Execution Disconnect:**  
> RYU can analyze a human goal, generate an acyclic Task Graph (DAG), validate the plan via Kernel CAS, and map tasks to agent/worker roles (`task.assigned`). **At that exact point, autonomous execution stops.**  
> There is no runtime component that listens to `task.assigned`, checks DAG dependencies, requests Kernel capability admission, acquires resource leases, issues device grants, invokes the Worker Sandbox, observes results, and converges the plan toward completion. All end-to-end execution to date has been manually driven by test harnesses or script runners.

This audit establishes that the **Autonomous Task Dispatch & Plan Execution Engine** is the single architectural bottleneck blocking all advanced autonomy (long-running execution, autonomous research, self-correcting coding, closed-loop learning, and multimodal interaction).

---

## 2. Repository Baseline & Methodological Invariants

### A. Governing Architecture: Space-Centric Cognitive Architecture (SCCA)

```text
Runtime
  ↓
Spaces
  ↓
Execution Plans (DAGs)
  ↓
Teams
  ↓
Agents
  ↓
Workers
  ↓
Skills
  ↓
Memory
  ↓
Insights
```

### B. The Six Immutable Laws of Ryu
1. **Law 1 — Everything Happens Inside a Space:** All work, memory, agents, resources, and artifacts exist within an isolated Space.
2. **Law 2 — Capabilities Are Requested, Never Owned:** Components do not own execution capabilities; they request them via typed pulses and kernel admission.
3. **Law 3 — Components Communicate Through Pulses:** Inter-component communication is strictly mediated by typed, contract-validated Pulses.
4. **Law 4 — Knowledge Belongs to the Space First:** Knowledge, reflections, and experiences are space-local by default; global promotion requires explicit authorization.
5. **Law 5 — Humans Define Goals; Ryu Organizes Execution:** Humans define the objective; RYU coordinates decomposition, planning, dispatch, and monitoring.
6. **Law 6 — Failures Are Contained, Escalated, and Never Silent:** Deterministic escalation from Tool $\rightarrow$ Worker $\rightarrow$ Agent $\rightarrow$ Space Orchestrator $\rightarrow$ Human.

### C. The Core Boundary Rule (AGENTS.md §7)
* `core/` **MUST NOT** import from: `agents/`, `workers/`, `skills/`, `workflows/`, `llm/`, `channels/`, or `memory/`.
* Enforced continuously via AST inspection (`scripts/dep_guard.py`).

---

## 3. Current Capability Inventory

Every capability is classified strictly by executable evidence:

| Capability Domain | Subsystem / Location | Implementation State | Test Evidence | Classification |
|:---|:---|:---|:---|:---:|
| **Runtime Lifecycle** | `core/space/kernel.py` | Space creation, isolation, state machine | `test_kernel.py`, `test_space_isolation.py` | **VERIFIED** |
| **Spaces & Isolation** | `core/space/`, `channels/daemon/` | Memory, artifact, and history hard boundaries | `test_space_isolation_history_and_artifacts` | **VERIFIED** |
| **Space Kernel** | `core/space/kernel.py` | Authority root, budgets, windows | `test_admission.py`, `test_windows.py` | **VERIFIED** |
| **Goals** | `core/orchestrator/goal_analyzer.py`| Goal parsing, single-agent eligibility | `test_goal_analyzer.py` | **VERIFIED** |
| **Planning** | `core/orchestrator/planner.py` | `TaskGraph` DAG generation, experience hints | `test_planner.py` | **VERIFIED** |
| **Plan CAS** | `core/space/plan_store.py` | CAS versioning, `PlanConflictError`, rebasing | `test_plan_cas.py` | **VERIFIED** |
| **Admission Control** | `core/space/admission.py` | Pre-dispatch budget checks, risk-tier gating | `test_admission.py` | **VERIFIED** |
| **Teams & Roles** | `core/orchestrator/team_builder.py`| Task-to-role assignment, `task.assigned` | `test_team_builder.py` | **VERIFIED** |
| **Agents** | `agents/base.py`, `agents/roles/` | 9-state machine, proposal validation | `test_base_agent.py` | **PARTIAL** |
| **Workers** | `workers/base.py`, `workers/` | Lifecycle, lease check, execution request | `test_workers.py` | **VERIFIED** |
| **Sandbox Isolation** | `workers/sandbox/` | Path deny-by-default, Windows/Linux containment | `test_sandbox.py` | **VERIFIED** |
| **Skills Registry** | `skills/` | SHA-256 signed skills, manifest verification | `test_signed_skills.py` | **VERIFIED** |
| **MCP Integration** | `skills/mcp/` | Stdio client, tool discovery, risk wrapping | `test_mcp_integration.py` | **VERIFIED** |
| **Resource Management**| `core/resources/` | Fractional CPU/GPU leases, anti-starvation FIFO | `test_resource_manager.py` | **VERIFIED** |
| **Secrets Containment**| `core/space/secrets.py` | Sanitization, non-authority in agents | `test_secrets.py` | **VERIFIED** |
| **Taint Tracking** | `core/pulse_bus/taint.py` | Forward-only propagation, clearance pulse | `test_taint_preservation.py` | **VERIFIED** |
| **Node Runtime** | `node/`, `node_runtime/` | Device capability enforcement, Rust bridge | `test_e2e_node_pipeline.py` | **VERIFIED** |
| **Device Grants** | `node/grants.py` | 5-state device grants, dual invalidation | `test_grants.py` | **VERIFIED** |
| **Pulse Bus** | `core/pulse_bus/` | PostgreSQL append-only, Redis Streams pub/sub | `test_durable_append.py`, `test_redis_publication.py` | **VERIFIED** |
| **Replay Engine** | `core/pulse_bus/replay.py` | Causal chain replay, deterministic artifacts | `test_causal_replay.py`, `v1_verify_replay.py` | **VERIFIED** |
| **Human Approvals** | `channels/approval/` | `token-hmac-v1` WebCrypto preimages | `test_approval_auth.py`, `demo_approval.py` | **VERIFIED** |
| **CLI Channel** | `channels/cli/` | Terminal interface, task/plan commands | `test_cli_shell.py`, `test_cli_parsing.py` | **VERIFIED** |
| **Desktop Shell** | `apps/ryu-desktop/` | Tauri v2 + React, history, sandbox, artifacts | `test_v101_hardening.py` | **VERIFIED** |
| **History Rehydration**| `channels/daemon/history.py`| Space-scoped turn recovery from pulses/cache | `test_history_rehydration_after_prompts` | **VERIFIED** |
| **File Ingress** | `channels/daemon/server.py` | <=2MB, path defense, taint tagging | `test_file_ingress_path_traversal_rejection` | **VERIFIED** |
| **Memory Adapters** | `memory/adapters/` | Working, episodic (Postgres), semantic (vector) | `test_memory_adapters.py` | **VERIFIED** |
| **Experience Storage** | `memory/` | `ExperienceRecord` creation, query similarity | `test_experience_reflection.py` | **VERIFIED** |
| **Promotion Gate** | `memory/` | Space-to-global promotion validation | `test_promotion_gate.py` | **VERIFIED** |
| **Behavioral Adaptation**| `core/memory/adaptation.py`| Read-only experience hints to Planner | `test_adaptation_loop.py` | **PARTIAL** |
| **LLM Boundary** | `llm/provider.py`, `recorder.py` | Provider abstraction, recording, replay | `test_llm_provider.py`, `test_recorder.py` | **VERIFIED** |
| **Task Dispatcher** | Missing | Autonomous loop driving DAG tasks to workers | None | **MISSING** |
| **Goal Evaluation** | Missing | Autonomous check of overall goal completion | None | **MISSING** |
| **Continuous Autonomy**| Missing | Observe-Evaluate-Reflect-Replan cycle | None | **MISSING** |
| **Voice / STT / TTS** | None | Microphone input, speech synthesis | None | **MISSING** |
| **Multimodal Vision** | None | Image/document cognitive ingestion | None | **MISSING** |

---

## 4. Trace the Real Execution Loop

### Step-by-Step Transition Mapping

```text
[Human / User]
      │
      ▼ (HTTP / SSE loopback)
[Channel Daemon: POST /api/v1/spaces/{id}/prompt]
      │
      ▼ (Command dataclass)
[GoalAnalyzer.analyze_goal()]
      │
      ├──────────────────────────────► [Pulse: goal.defined]
      ▼
[SpaceOrchestrator.submit_goal()]
      │
      ├─► [AdaptationLayer.generate_hints()] (Queries past negative experiences)
      │
      ├─► [Planner.plan_goal()] (Generates proposed TaskGraph DAG)
      │
      ├─► [SpaceKernel.get_task_graph()] (Initializes plan_version=1 via CAS)
      │         │
      │         └────────────────────► [Pulse: plan.created]
      │
      └─► [TeamBuilder.build_team()] (Maps TaskNodes to Roles)
                │
                └────────────────────► [Pulse: task.assigned]
                                             │
═════════════════════════════════════════════╪══════════════════════════════════
               THE RUNTIME CLIFF EDGE        ▼
══════════════════════════════════════════════════════════════════════════════════
                                             ✖ (NO RUNTIME DISPATCHER)
                                             │
   (What should happen next:)                │
   [Task Dispatcher] <───────────────────────┘
         │
         ├─► [SpaceKernel.admit_capability()] (Budget & Risk Tier Check)
         ├─► [Human Gate Approval] (If Tier 2/3)
         ├─► [ResourceManager.acquire()] (Lease Token)
         ├─► [DeviceGrantManager.create_grant()] (Node Grant)
         ├─► [WorkerSandbox.execute()] (Worker Invocation)
         ├─► [Observation & Artifact Storage]
         ├─► [Evaluation & Dependency Resolution]
         └─► [Kernel CAS Update & Next DAG Node]
```

### Detailed Transition Analysis

| Transition | Implementation File | Authority Responsible | Applicable Contract | Persistence | Failure Behavior | Test Evidence |
|:---|:---|:---|:---|:---|:---|:---|
| **1. User $\rightarrow$ Channel** | `apps/ryu-desktop/` | Human Operator | `APP-001` | React / Tauri | Connection retry | `test_v101_hardening.py` |
| **2. Channel $\rightarrow$ Goal** | `channels/daemon/server.py` | Channel Daemon | `DESKTOP-001` | DialogueTurn JSONL | 400 Bad Request | `test_daemon_v101.py` |
| **3. Goal $\rightarrow$ Plan** | `core/orchestrator/planner.py` | Orchestrator / Planner | `ORCH-003` | Memory / Session | Fallback task node | `test_planner.py` |
| **4. Plan $\rightarrow$ CAS** | `core/space/kernel.py` | Space Kernel | `SPACE-001` | Kernel `PlanStore` | `PlanConflictError` | `test_plan_cas.py` |
| **5. CAS $\rightarrow$ Assignment** | `core/orchestrator/team_builder.py` | Team Builder | `ORCH-004` | PostgreSQL `pulses` | Unassigned task | `test_team_builder.py` |
| **6. Assignment $\rightarrow$ Dispatch** | **NONE** | **UNCLAIMED** | **NONE** | **NONE** | **EXECUTION HALTS** | **NO EXECUTION TEST** |
| **7. Dispatch $\rightarrow$ Worker** | Manual in tests | Test Script / Harness | `WORKER-001` | In-memory | Manual assertion | `v1_run_vertical_slice.py` |
| **8. Worker $\rightarrow$ Tool** | `workers/base.py` | Worker Sandbox | `WORKER-002` | `pulses` table | `worker.tool.failed`| `test_workers.py` |
| **9. Tool $\rightarrow$ Artifact** | `channels/daemon/artifacts.py` | Space Artifact Store | `DESKTOP-004` | Filesystem + SHA | Traversal reject | `test_daemon_v101.py` |
| **10. Artifact $\rightarrow$ Memory**| `memory/adapters/` | Memory Manager | `MEM-001` | PostgreSQL JSONB | `MemoryFailure` | `test_memory_adapters.py` |

### Finding: Where Does the Current Autonomous Loop End?
**The autonomous loop ends at `task.assigned`.**  
In the production Channel Daemon (`channels/daemon/server.py` lines 413–455), submitting a prompt creates a `Command`, analyzes the goal, synthesizes a direct text response via `_synthesize_prompt_response`, and stores a chat turn. It does **not** dispatch the plan.  
In the core Orchestrator (`core/orchestrator/orchestrator.py` lines 114–198), `submit_goal()` builds the team, emits `task.assigned`, and stores the `OrchestratorSession`. It does **not** dispatch the workers.

---

## 5. Iterative Autonomy Analysis

We investigated whether RYU can execute the classical autonomous iteration loop:
$$\text{OBSERVE} \longrightarrow \text{EVALUATE} \longrightarrow \text{REFLECT} \longrightarrow \text{REPLAN} \longrightarrow \text{EXECUTE} \longrightarrow \text{VERIFY} \longrightarrow \text{CONTINUE}$$

### Stage-by-Stage Inventory

1. **OBSERVE (PARTIAL):**
   * *Existing:* `Monitor` (`core/orchestrator/monitor.py`) subscribes to the Pulse Bus and maintains `TimelineState` (tracking task states, failures, attempts, and leases).
   * *Missing:* The Monitor is a passive data collector. It does not synthesize higher-level environment state or detect semantic stall conditions.
2. **EVALUATE (MISSING):**
   * *Existing:* None.
   * *Missing:* There is no component that evaluates whether a worker's output artifact actually fulfilled the task's criteria or whether the overall goal's acceptance criteria were satisfied.
3. **REFLECT (PARTIAL):**
   * *Existing:* `BaseAgent` has a `REFLECTING` state enum, but `step()` transitions through it as a synchronous no-op (`agents/base.py` line 309). `core/orchestrator/adapter.py` creates `ExperienceRecord` objects when `record_experience()` is manually called.
   * *Missing:* Automated self-reflection on intermediate execution failures.
4. **REPLAN (PARTIAL):**
   * *Existing:* `PlanReconciler` (`core/orchestrator/reconciler.py`) has `reconcile_task_failure()`, which bounds retries to 3 for transient errors and requests a `PlanDelta` from `Adapter` for structural errors.
   * *Missing:* Reconciler is never automatically invoked by the runtime; it requires an external caller to feed it failure pulses.
5. **EXECUTE (DISCONNECTED):**
   * *Existing:* `BaseWorker`, `PythonWorker`, `ShellWorker`, `MCPWorker`, and `NodeWorker` are fully capable of executing sandboxed tasks.
   * *Missing:* No runtime dispatcher maps `task.assigned` to a worker instance, acquires its lease, and invokes `worker.execute()`.
6. **VERIFY (MISSING):**
   * *Existing:* Tool status codes (`worker.tool.succeeded` vs `failed`).
   * *Missing:* Semantic outcome verification (e.g., did the test pass? did the file compile? does the generated output answer the prompt?).
7. **CONTINUE (MISSING):**
   * *Existing:* None.
   * *Missing:* Dependency-driven DAG traversal that unblocks dependent nodes when prerequisites complete.

---

## 6. Learning Analysis (Phase 10 Architecture)

1. **Does RYU merely record experience?**  
   *No.* It records `ExperienceRecord` with situation, action, outcome, and counterfactuals, but also queries them during planning.
2. **Can experience influence future planning?**  
   *Yes.* `core/orchestrator/planner.py` (lines 40–50) inspects `experience_hints` generated by `AdaptationLayer`. If a capability previously failed in a similar situation, the planner substitutes an alternative or fallback capability.
3. **Can knowledge be promoted?**  
   *Yes.* `KnowledgePromotionGate` (`memory/`) enforces SCCA Law 4. Space-local knowledge requires human approval or an authenticated promotion pulse to enter global shared memory.
4. **Can promoted knowledge alter future behavior?**  
   *Yes.* Semantic memory retrieval queries both Space memory and Promoted Global memory.
5. **Can the system evaluate whether an adaptation actually improved behavior?**  
   *No.* RYU has no longitudinal metric tracking. It cannot compare whether substituting capability B after capability A failed yielded a higher success rate.
6. **Is there a closed learning loop?**  
   *No.* The learning loop is open. Experiences are stored upon manual or test invocation, and hints are consumed during planning, but there is no continuous automated pipeline that observes execution, generates counterfactuals, records experiences, and updates strategy without human intervention.

---

## 7. Long-Running Task Analysis

| Long-Running Criterion | Supported? | Implementation & Evidence | Architectural Assessment |
|:---|:---:|:---|:---|
| **Task State Durability** | **PARTIAL** | Pulses in PostgreSQL (`pulses` table); Kernel `PlanStore` in memory | Pulses survive crashes, but in-flight DAG state is not serialized to a durable task state table. |
| **Checkpoints** | **STUB** | `snapshot()` interface in `PulseStore` protocol | Method signature exists; not implemented in Postgres store. |
| **Resumability after Restart** | **DISCONNECTED**| `PulseReplayer` can reconstruct causal pulse chains | Can replay what happened historically, but cannot resume an uncompleted in-flight task graph. |
| **Worker Replacement** | **PARTIAL** | `Adapter.propose_delta()` can generate a reassignment delta | Mechanism exists in core plans, but no runtime watchdog detects crashed workers to trigger it. |
| **Human Approval Suspension** | **VERIFIED** | `AttentionBudget`, `ApprovalRequest` in Postgres | The system safely pauses when awaiting approval without losing state. |
| **Stopping Conditions** | **PARTIAL** | Budget hard stop (`space.budget.exceeded`), max rebases (3) | Budget exhaustion and loop bounding exist; semantic goal completion does not. |

---

## 8. Comparative Analysis of Candidate Next Directions

We analyzed nine potential architectural directions without arbitrary ranking:

### A. Memory Graph (Relational / Graph Memory)
* **Prerequisites Present:** Vector memory adapter, episodic Postgres store, Space memory protocol.
* **Missing Prerequisites:** Graph database engine (e.g. Neo4j driver), entity extraction pipeline, knowledge graph schema contracts.
* **Affected Boundaries:** Memory subsystem (`memory/`), SCCA Law 4 (Space-local knowledge graphs).
* **Impact:** Enriches contextual retrieval, but does not solve task execution.

### B. Lifelong Learning & Self-Evolving Models
* **Prerequisites Present:** `AdaptationLayer`, `ExperienceRecord`, `KnowledgePromotionGate`.
* **Missing Prerequisites:** Outcome evaluation metrics, automated counterfactual generator, parameter tuning / prompt optimization engine.
* **Affected Boundaries:** Orchestrator Adapter, Space Kernel.
* **Impact:** Requires tasks to actually execute repeatedly to collect evolutionary data.

### C. Autonomous Research
* **Prerequisites Present:** MCP client, Browser worker, retrieval workers, artifact store.
* **Missing Prerequisites:** Iterative query refiner, search evaluation heuristic, synthesis loop.
* **Affected Boundaries:** Workers, Agent cognitive loop.
* **Impact:** Cannot function without an autonomous execution engine driving multi-step research DAGs.

### D. Autonomous Software Engineering (Coding Agent)
* **Prerequisites Present:** File worker, Shell worker, Git worker, sandbox containment, artifact explorer.
* **Missing Prerequisites:** Test-driven repair loop, language-server protocol (LSP) integration, AST diffing/patching worker.
* **Affected Boundaries:** Workers, Sandbox, Execution Engine.
* **Impact:** Blocked by the lack of an execution loop (cannot run code, observe errors, and fix).

### E. Long-Running Autonomous Tasks
* **Prerequisites Present:** PostgreSQL pulse persistence, resource leasing, human attention budget.
* **Missing Prerequisites:** Durable task checkpointing, process crash resume daemon, worker heartbeat monitor.
* **Affected Boundaries:** Core runtime, Orchestrator, Channel Daemon.
* **Impact:** Directly extends the task execution lifecycle.

### F. Evaluation, Reflection & Replanning Loop
* **Prerequisites Present:** `PlanReconciler`, `Monitor`, `Adapter`, `PlanDelta` CAS rebasing.
* **Missing Prerequisites:** Semantic evaluator, automated pulse triggers connecting Monitor failures to Reconciler actions.
* **Affected Boundaries:** Orchestrator (`core/orchestrator/`).
* **Impact:** Bridges observation back to planning.

### G. Voice / STT / TTS Interaction
* **Prerequisites Present:** Desktop Command Center, loopback SSE stream.
* **Missing Prerequisites:** Audio input capture, local/cloud Whisper STT, TTS synthesis engine, audio stream pulses.
* **Affected Boundaries:** Channels (`channels/`, `apps/ryu-desktop/`), Node Runtime (microphone device grants).
* **Impact:** High UX value, but adds a new input channel without solving backend execution.

### H. Multimodal Interaction (Vision & Document Ingestion)
* **Prerequisites Present:** File ingress endpoint (`POST /files`), artifact explorer, zero-privilege sandbox.
* **Missing Prerequisites:** Vision model adapter in `llm/`, PDF/document text extractors, image pulse payloads.
* **Affected Boundaries:** LLM provider boundary, Channels, Taint model.
* **Impact:** Expands ingestion types; orthogonal to core execution.

### I. World / Environment State Modeling
* **Prerequisites Present:** `Monitor.TimelineState`.
* **Missing Prerequisites:** Environment diff engine, external state synchronizer (filesystem/git/network snapshotting).
* **Affected Boundaries:** Core Monitor, Space isolation.
* **Impact:** Advanced theoretical capability; premature before basic task dispatch operates.

---

## 9. The Architectural Bottleneck

### The Dependency Chain

```text
Current Capability:
    Orchestrator generates versioned TaskGraph DAGs and emits `task.assigned` Pulses;
    Workers, Sandboxes, Leases, Device Grants, and Approvals are independently verified.
         ↓
Existing Limitation:
    Execution terminates immediately after `task.assigned`.
    Neither the Daemon nor the Core Orchestrator contains a runtime loop
    that drives DAG tasks to worker execution.
         ↓
ARCHITECTURAL BOTTLENECK:
    Autonomous Plan Execution & Task Dispatch Engine
         ↓
Capabilities Blocked by It:
    - Long-Running Autonomous Tasks (cannot execute multi-cycle workflows)
    - Autonomous Software Engineering (cannot edit -> test -> evaluate -> iterate)
    - Autonomous Research (cannot search -> scrape -> evaluate -> synthesize)
    - Closed-Loop Adaptation (cannot collect live outcome metrics from autonomous tasks)
         ↓
Required New Abstraction:
    Space Execution Dispatcher (`core/orchestrator/dispatcher.py` or `runtime/dispatcher.py`)
```

### Empirical Proof from Repository Code

1. In `core/orchestrator/orchestrator.py`:
   `submit_goal()` ends at line 197 by storing `OrchestratorSession(task_graph=kernel_graph, assignments=assignments)`. It provides zero methods to execute the tasks in `kernel_graph`.
2. In `agents/base.py`:
   `step()` lines 305–311 synchronously sequences `WAITING -> EXECUTING -> OBSERVING -> REFLECTING -> COMPLETED` without calling any tool or worker.
3. In `core/orchestrator/monitor.py`:
   `Monitor` records `task.assigned`, `task.started`, `task.completed`, but is explicitly defined as a "Pure observer: executes zero tools, mutates zero plans, issues zero leases."
4. In `harness/cases/orchestrator/test_orchestrator_future.py`:
   Line 65 states: `# 2. Stub Worker Execution: Workers react to task.assigned and emit execution lifecycle`. The test script itself manually loops over the nodes and publishes execution pulses.

**Conclusion:** The missing layer is the **Autonomous Task Dispatcher** that takes an assigned Plan DAG, evaluates dependencies, requests kernel admission, acquires leases, executes workers in sandboxes, handles intermediate outputs, and evaluates goal completion.

---

## 10. Architecture Impact of the Dispatcher Abstraction

### New Interfaces Required
* `TaskDispatcherProtocol`: Interface defining `dispatch_ready_tasks(space_id, plan_version) -> list[TaskId]`.
* `TaskExecutorProtocol`: Interface binding an assigned task node to an admitted worker instance.
* `GoalEvaluatorProtocol`: Semantic evaluator checking if task artifacts satisfy the `GoalSpec`.

### Existing Interfaces Reused
* `SpaceKernel.admit_capability()` (`core/space/admission.py`): Reused for pre-dispatch gating.
* `ResourceManager.acquire()` (`core/resources/manager.py`): Reused for fractional CPU/GPU leases.
* `DeviceGrantManager.create_grant()` (`node/grants.py`): Reused for node device access.
* `BaseWorker.execute()` (`workers/base.py`): Reused for sandboxed capability execution.
* `PlanReconciler.reconcile_task_failure()` (`core/orchestrator/reconciler.py`): Reused for retry/replan.
* `PulseBus.publish()` (`core/pulse_bus/`): Reused for telemetry.

### Contract & Pulse Impact
* Existing pulse types fully support this: `task.started`, `task.completed`, `task.failed`, `task.retried`, `worker.tool.called`, `worker.tool.succeeded`, `worker.tool.failed`, `plan.completed`.
* Zero breaking contract changes required.

### Core Boundary & SCCA Conformity
* **Law 1:** Dispatcher runs strictly within the Space boundary.
* **Law 2:** Dispatcher never owns capabilities; it requests admission from `SpaceKernel`.
* **Law 6:** Failures in worker execution escalate deterministically to `PlanReconciler`.
* **Core Boundary Rule (AGENTS.md §7):** The dispatcher component belongs in `core/orchestrator/` or an execution coordinator layer using dependency injection protocols to invoke workers without importing concrete worker implementations into `core/`.

---

## 11. Recommended Next Architectural Focus

The recommended next architectural priority for RYU AI is:

> **The Space Execution Dispatcher & Convergence Loop (Autonomous Task Execution)**
>
> 1. Formulate formal Architectural Decision Record (**ADR-0041: Autonomous Task Dispatcher & Plan Convergence Engine**).
> 2. Implement dependency-aware DAG traversal (scheduling ready nodes whose upstream dependencies are `completed`).
> 3. Connect `task.assigned` $\rightarrow$ Admission Gating $\rightarrow$ Resource Lease $\rightarrow$ Worker Execution $\rightarrow$ Artifact Generation.
> 4. Wire `worker.tool.failed` directly into `PlanReconciler` for automated retry, reassignment, or CAS plan deltas.
> 5. Introduce a deterministic `GoalEvaluator` to transition plans to `plan.completed`.

---

## 12. Explicit Non-Goals (What Should NOT Be Built Yet)

1. **DO NOT build Voice / Audio (STT/TTS):** Adding speech input when the backend cannot execute plan DAGs autonomously merely creates a voice interface to an incomplete execution loop.
2. **DO NOT build a Knowledge Graph (Neo4j):** Adding relational graph storage is an optimization of memory retrieval, not an execution engine.
3. **DO NOT attempt "Full AGI / Self-Evolving Code":** Unbounded self-modification without an automated execution and verification harness violates SCCA Law 5 and Law 6.
4. **DO NOT create a second runtime in the Desktop UI:** The desktop must remain an unprivileged Channel/Client over loopback HTTP.

---

## 13. Evidence References

* [`core/orchestrator/orchestrator.py`](file:///d:/RYU/core/orchestrator/orchestrator.py#L114-L198) — Orchestrator session halts at `task.assigned`.
* [`agents/base.py`](file:///d:/RYU/agents/base.py#L305-L311) — Agent step synchronous state transitions without execution.
* [`core/orchestrator/monitor.py`](file:///d:/RYU/core/orchestrator/monitor.py#L32-L40) — Monitor pure-observer invariant.
* [`core/orchestrator/reconciler.py`](file:///d:/RYU/core/orchestrator/reconciler.py#L70-L120) — Reconciler failure handling logic.
* [`core/memory/adaptation.py`](file:///d:/RYU/core/memory/adaptation.py#L46-L105) — Read-only experience hints.
* [`core/orchestrator/planner.py`](file:///d:/RYU/core/orchestrator/planner.py#L40-L50) — Planner experience hint avoidance.
* [`channels/daemon/server.py`](file:///d:/RYU/channels/daemon/server.py#L413-L455) — Daemon prompt handling without plan dispatch.
* [`harness/cases/orchestrator/test_orchestrator_future.py`](file:///d:/RYU/harness/cases/orchestrator/test_orchestrator_future.py#L65-L115) — Manual harness execution of assigned tasks.
* [`scripts/v1_run_vertical_slice.py`](file:///d:/RYU/scripts/v1_run_vertical_slice.py#L205-L355) — Manual procedural orchestration of vertical slice.
