# Phase 13 — Post-Execution-Engine Architecture Audit
## Autonomous Execution Baseline, System Capability Inventory, and Roadmap Definition

**Milestone:** Phase 13 — Architecture Audit  
**Phase Type:** Architecture Discovery / Capability Audit / Roadmap Definition  
**Date:** 2026-09-28  
**Author:** RYU Architecture Agent  
**Baseline Commit:** `224c6c0` (`feat(phase12): implement Phase 12.7 Integrated Autonomous Execution Verification`)  
**Remote Tracking:** `origin/main` (up to date, working tree clean)  
**Governing Architecture:** Space-Centric Cognitive Architecture (SCCA)  
**Governing Contracts:** `AGENTS.md`, `docs/CONTRACT_MATRIX.md`, `adr/0001`..`0041`  
**Audit Gate:** `PHASE_13_AUDIT_COMPLETE`  

---

## 1. Audit Objective

Now that RYU possesses a verified autonomous execution engine (Phases 12.1–12.7, GATE_VERIFIED at commit `224c6c0`), this audit addresses one governing architectural question:

> **Now that RYU possesses a verified autonomous execution engine, what is the next architectural capability required to move RYU toward its intended long-term system architecture?**

This question is answered strictly from repository evidence, execution traces, code inspection, and AST dependency verification. In accordance with the Phase 13 mandate:
- No speculative capabilities are selected because they sound impressive.
- No implementation of Phase 14 or subsequent subsystems occurs during this audit.
- Existing verification gates must pass completely without modification.

---

## 2. Baseline

The audit evaluates the repository at commit `224c6c0` on branch `main`:
- **Phase 0–11 Foundation:** Durable Pulse Bus (PostgreSQL + Redis Streams), Space Kernel CAS, Resources & Fractional Leases, Cognitive Orchestrator, Agent State Machine, MCP Skill Registry, Node Runtime (Rust cross-platform), Human Gates (WebCrypto HMAC tokens), Desktop Command Center Channel Daemon, and Experience Memory.
- **Phase 12 Execution Engine:**
  - `cebaaa5` (Phase 12.1): Dispatcher contracts (`DISPATCH-001..005`) and `TaskGraph` Kahn DAG traversal.
  - `1df9eec` (Phase 12.2): SpaceKernel CAS integration and persistent task state transitions.
  - `3783f70` (Phase 12.3): Admission Control risk gating and fractional lease pipeline coordination.
  - `b77653d` (Phase 12.4): Sandboxed `RuntimeWorkerInvoker` process execution and lease binding.
  - `9ec5568` (Phase 12.5): Physical evidence verification (SHA-256) and DAG dependency unblocking.
  - `de41713` (Phase 12.6): Goal evaluation (`DeterministicGoalEvaluator`) and `ConvergenceEngine` reconciliation.
  - `1654725` (Phase 12.6 docs): `CONV-001..005` codified in `CONTRACT_MATRIX.md`.
  - `224c6c0` (Phase 12.7): Integrated closed-loop execution verification (18/18 scenarios pass).

---

## 3. Repository State

Prior to conducting this audit, all 7 repository verification scripts were executed without modification:
1. `scripts/dep_guard.py` $\rightarrow$ **PASS** (0 forbidden imports in `core/`, adhering to `AGENTS.md §7`).
2. `scripts/contract_sync.py` $\rightarrow$ **PASS** (38/38 pulse types registered and synchronized).
3. `scripts/v1_verify_core_independence.py` $\rightarrow$ **PASS** (Runtime isolation & zero-LLM control loop verified).
4. `scripts/v1_audit_spec_coverage.py` $\rightarrow$ **PASS** (141 criteria, 192 contracts, 162 mappings, 0 orphaned).
5. `scripts/v1_audit_governance.py` $\rightarrow$ **PASS** (ADR inventory 0001..0041, registry, schemas verified).
6. `scripts/v1_verify_replay.py` $\rightarrow$ **PASS** (Bitwise artifact SHA-256 and plan state replay verified).
7. `scripts/v1_run_security_regression.py` $\rightarrow$ **PASS** (12/12 security checks passed: SEC-01 through SEC-12).

Total unit and integration battery: **355 passed, 0 failed** in `pytest core workers`.  
Full test harness: **319 passed, 10 skipped** (all skips guarded by `RYU_INTEGRATION_TESTS=1`).  
Git working tree: Clean. HEAD equals `origin/main`.

---

## 4. Phase 12 Capability Baseline

Phase 12 established the following closed-loop autonomous execution capability:

```text
Human Goal (GoalSpec)
   ↓
Plan v1 Proposed (Planner)
   ↓
SpaceKernel Plan CAS (plan_version = 1)
   ↓
TaskGraph DAG (Topological in-degree sort)
   ↓
DeterministicDispatcher (Ready tasks identified)
   ↓
SpaceKernel AdmissionController (Tier-0 / Tier-1 capability authorized)
   ↓
ResourceManager & LeaseManager (Fractional hardware lease acquired)
   ↓
RuntimeWorkerInvoker (Sandboxed subprocess in dedicated space directory)
   ↓
Physical Artifact Written (calc_summary.txt, output.txt)
   ↓
Evidence Collected & Verified (Cryptographic SHA-256 match, exit_code = 0)
   ↓
Task Completed (CAS transition to 'completed', downstream unblocked)
   ↓
DeterministicGoalEvaluator (Inspects verified evidence against constraints)
   ↓
UNSATISFIED (Constraint require_artifact:marker.txt not met)
   ↓
ConvergenceEngine (Bounded retry <= 3, proposes REPLAN, fingerprint loop guard)
   ↓
ConvergenceProposal (Immutable proposal carrying rollback PlanDelta)
   ↓
PlanReconciler & SpaceKernel CAS (plan_version = 2 committed with rebase)
   ↓
TaskGraph v2 Executed (Revised task executed via full pipeline)
   ↓
Goal Evaluated: SATISFIED
   ↓
Convergence Complete (CONTINUE, is_plan_succeeded = True)
```

This pipeline is deterministic, non-speculative, and backed by passing tests in `workers/tests/test_phase12_integrated_execution.py`.

---

## 5. Runtime Capability Map

Tracing actual runtime execution from user input through the system components reveals the exact boundary between operational and non-operational pathways:

```text
User / CLI / Desktop Channel
   │
   ▼
Channel Daemon (channels/daemon/server.py) [OPERATIONAL over loopback HTTP 127.0.0.1:8420]
   │
   ▼
Space Orchestrator (core/orchestrator/orchestrator.py) [OPERATIONAL]
   ├── GoalAnalyzer (core/orchestrator/goal_analyzer.py) [OPERATIONAL: Command -> GoalSpec]
   ├── Planner (core/orchestrator/planner.py) [OPERATIONAL: GoalSpec -> TaskGraph]
   ├── SpaceKernel (core/space/kernel.py) [OPERATIONAL: Space isolation, CAS, Approvals]
   ├── DeterministicDispatcher (core/orchestrator/dispatch_model.py) [OPERATIONAL]
   │     ├── AdmissionController [OPERATIONAL: Budget & risk tier gating]
   │     ├── ResourceManager [OPERATIONAL: Capacity & fractional leasing]
   │     └── RuntimeWorkerInvoker (workers/invoker.py) [OPERATIONAL]
   │           ├── PythonWorker (workers/python/worker.py) [OPERATIONAL: subprocess sandbox]
   │           ├── ShellWorker (workers/shell/worker.py) [OPERATIONAL: CLI subprocess sandbox]
   │           ├── FileWorker (workers/file/worker.py) [OPERATIONAL: sandboxed I/O]
   │           ├── NodeWorker (workers/node/worker.py) [OPERATIONAL: Rust Node bridge]
   │           ├── BrowserWorker (workers/browser/worker.py) [PARTIAL: mock/fetch only, no live DOM]
   │           ├── MCPWorker (workers/mcp/worker.py) [OPERATIONAL: local stdio/SSE MCP client]
   │           ├── SubagentWorker (workers/subagent/worker.py) [PARTIAL: isolated handoff note only]
   │           ├── AutomationWorker (workers/automation/) [STUB: empty __init__.py]
   │           ├── DBWorker (workers/db/) [STUB: empty __init__.py]
   │           ├── GitWorker (workers/git/) [STUB: empty __init__.py]
   │           ├── NetworkWorker (workers/network/) [STUB: empty __init__.py]
   │           └── RetrievalWorker (workers/retrieval/) [STUB: empty __init__.py]
   ├── DeterministicGoalEvaluator [OPERATIONAL: Evidence-bound constraint validation]
   ├── ConvergenceEngine [OPERATIONAL: Bounded retry, REPLAN proposal, loop guard]
   └── PlanReconciler [OPERATIONAL: CAS rebase <= 3]
```

### Operational Boundaries Determined by Trace:
1. **What RYU can do without human intervention:**
   - Decompose a command into a DAG of sandboxed tasks.
   - Automatically admit Tier-0 and Tier-1 capabilities against a funded Space budget.
   - Acquire fractional leases, execute sandboxed Python/shell/node/file tasks.
   - Verify cryptographic file artifacts and process exit codes.
   - Unblock downstream DAG tasks in topological order.
   - Retry transient failures up to 3 times with exponential backoff.
   - Reconcile plan mismatches via atomic CAS PlanDelta up to 3 replans.
   - Halt deterministically when goals are satisfied.

2. **What requires human approval (SCCA Law 5):**
   - Tier-2 and Tier-3 capabilities (out-of-space writes, system commands, device control).
   - Knowledge promotion from Space-local memory to global memory (`PromotionPipeline`).
   - Retries or replans exceeding the hard ceiling of 3 attempts (`ConvergenceDecision.ESCALATE`).
   - Approval requests queue when attention budget saturation exceeds 3 concurrent gates.
   - Approvals require valid `token-hmac-v1` WebCrypto preimages; synthetic flags are rejected.

3. **What terminates execution:**
   - All tasks reaching terminal states and goal reaching `SATISFIED`.
   - Terminal error classes (`terminal.permission_denied`, `terminal.budget_exceeded`, `terminal.security_violation`).
   - Exhaustion of retry budget (3) or replan budget (3).
   - Detection of duplicate failure fingerprints across replan cycles.
   - Explicit `ABORT` from fatal policy or security violations.

4. **What cannot survive restart:**
   - Active worker subprocesses (no process re-attachment after host/daemon reboot).
   - In-memory `ConvergenceEngine` retry/replan counters and seen fingerprint registry.
   - In-flight dispatch attempt cache (`_tracked_attempts`).
   - Uncheckpointed `PlanStore` graphs (graphs exist in memory unless explicitly snapshotted or reconstructed from the PostgreSQL pulse log).

---

## 6. Autonomous Execution Boundary

```text
┌────────────────────────────────────────────────────────────────────────┐
│                        RYU CAN DO (VERIFIED)                           │
│                                                                        │
│  • Single-session DAG execution (Python, Shell, Node, File, MCP)       │
│  • Deterministic admission gating and fractional resource leasing       │
│  • Physical artifact creation with SHA-256 hash verification           │
│  • Atomic plan state transitions via single-writer PlanStore CAS       │
│  • Deterministic topological DAG unblocking (mandatory vs optional)    │
│  • Evidence-bound goal evaluation (SATISFIED / UNSATISFIED)            │
│  • Bounded retry (<=3), bounded replan (<=3), failure loop escalation   │
│  • Forward-only taint containment and secret output sanitization       │
│  • Space checkpoint snapshot and restoration                           │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
                           AUTONOMY BOUNDARY
                                    │
┌───────────────────────────────────▼────────────────────────────────────┐
│                    RYU CANNOT / PARTIAL (DISCONNECTED)                 │
│                                                                        │
│  1. DURABLE CRASH RECOVERY: Cannot auto-recover in-flight tasks across │
│     daemon restart without explicit checkpoint restoration.            │
│  2. EXPERIENTIAL LEARNING: Execution Engine is DISCONNECTED from       │
│     Reflector and AdaptationLayer. Past failures in memory DO NOT      │
│     inform ConvergenceEngine replanning or task dispatch.              │
│  3. LONG-RUNNING ASYNC WORKFLOWS: No background scheduler, daemon      │
│     polling cron, or persistent task queue for multi-hour jobs.        │
│  4. AUTONOMOUS RESEARCH: BrowserWorker is mock HTML fetcher; no live   │
│     web search, no DOM interaction, no citation or synthesis graph.    │
│  5. AUTONOMOUS SOFTWARE ENGINEERING: GitWorker, DBWorker, and          │
│     RetrievalWorker are empty stubs. No repository AST parsing.        │
│  6. MULTI-AGENT COLLABORATION: Agents execute independently; no peer   │
│     negotiation, shared working memory, or subagent team delegation.   │
│  7. SEMANTIC KNOWLEDGE RETRIEVAL: Neo4j and Qdrant adapters are stubs  │
│     raising NotImplementedError. Memory search is linear scan.         │
└────────────────────────────────────────────────────────────────────────┘
```

---

## 7. Long-Running Execution Audit

Evaluation of RYU's architectural ability to run continuous workloads:

| Workload Duration | Architectural Capability | Failure / Blocking Point |
|:---|:---|:---|
| **< 1 Minute** | **FULL** | Operates cleanly within single lease TTL (default 60s). |
| **5 Minutes** | **PARTIAL** | Requires lease renewal; if lease expires without completion, `ResourceManager` reaps lease, causing `terminal.lease_expired`. |
| **30 Minutes** | **PARTIAL** | Supported only if tasks are short and sequential. A single 30m subprocess blocks the thread; worker has no progress heartbeat. |
| **2 Hours** | **BLOCKED** | Attention budgets expire (`default_deny` timeout 30s..5m); human approval windows close; no persistent heartbeat mechanism. |
| **12–24 Hours** | **BLOCKED** | Host sleep, network interruption, or daemon restart terminates the process. In-flight tasks become orphaned; memory state is lost. |

### Architectural Gaps for Long-Running Work:
- **No Background Task Daemon:** Dispatcher executes synchronously within the active thread or runner.
- **No In-Flight Task Re-attachment:** If the runner exits, running OS processes cannot be reconnected to the Dispatcher.
- **No Heartbeat Monitor:** Workers block synchronously on `subprocess.run(timeout=...)`. If a worker hangs, it consumes its lease until timeout without intermediate telemetry.

---

## 8. Memory Architecture Audit

Inspection of `core/memory/`, `memory/`, and `core/space/memory_protocol.py`:

```text
Subsystem               Status          Authority               Persistence
---------------------------------------------------------------------------------
SpaceMemoryProtocol     IMPLEMENTED     Kernel / MemoryStore    Contract definition
InMemoryMemoryAdapter   VERIFIED        Space-local             In-memory dict
PostgresMemoryAdapter   VERIFIED        Space-local             PostgreSQL tables
Neo4jAdapterStub        STUB (Phase 11+) Extension stub         NotImplementedError
QdrantAdapterStub       STUB (Phase 11+) Extension stub         NotImplementedError
Reflector               VERIFIED        Emits experience Pulses PostgreSQL / MemoryStore
AdaptationLayer         VERIFIED        Read-only hints         Queries SpaceMemory
PromotionPipeline       VERIFIED        Human Gate + Kernel     Signed global knowledge
```

### Critical Architectural Finding: The Memory-Execution Disconnect
1. **What is remembered:** Structured `ExperienceRecord`s containing `situation`, `action`, `outcome`, `counterfactual`, and `applicable_context`.
2. **Where stored:** PostgreSQL table `experiences` and `global_knowledge`.
3. **Who can write:** Strictly Space-local callers. Cross-space global knowledge requires `PromotionPipeline` with human approver signature (`token-hmac-v1`).
4. **Who can read:** `AdaptationLayer.generate_hints()` reads past failure experiences within the same Space.
5. **DISCONNECT EVIDENCE:**
   - In `core/orchestrator/orchestrator.py` (line 137), `submit_goal()` queries `adaptation_layer.generate_hints()` during initial goal submission.
   - **HOWEVER**, in `DeterministicDispatcher` (`core/orchestrator/dispatch_model.py`), `execute_task_full_pipeline()` and `observe_and_evaluate_task()` **never call `Reflector.reflect()`** upon task completion or failure!
   - In `ConvergenceEngine.evaluate_and_propose()`, the engine **never calls `AdaptationLayer.generate_hints()`** when proposing a REPLAN!
   - When a task fails, `ConvergenceEngine` computes an SHA-256 fingerprint of `(space_id, task_id, error_class)`, but does **not** query memory to see how similar failures were resolved in prior plans!

The memory subsystem is structurally complete and tested in isolation, but **disconnected from the runtime execution and convergence loop**.

---

## 9. Learning Audit

Taxonomy of experiential learning levels across the repository:

| Level | Definition | Repository Status | Evidence / Location |
|:---|:---|:---|:---|
| **Level 1: Observation** | Telemetry and metrics recorded | **VERIFIED** | `VerifiedExecutionEvidence`, `TaskExecutionResult`, `Monitor` |
| **Level 2: Memory** | Experiences persisted durably | **VERIFIED** | `PostgresMemoryAdapter`, `ExperienceRecord`, `Reflector` |
| **Level 3: Reflection** | Counterfactual reasoning recorded | **VERIFIED** | `Reflector.reflect()` mandates `counterfactual` parameter |
| **Level 4: Evaluation** | Benchmark against frozen traces | **VERIFIED** | `memory/evaluation.py:EvaluationModule` (blake2b hash) |
| **Level 5: Adaptation** | Historical hints alter plan | **PARTIAL** | `core/memory/adaptation.py:AdaptationLayer` works in unit tests, but disconnected from Phase 12 `ConvergenceEngine` |
| **Level 6: Autonomous Learning** | Continuous cross-task optimization | **ABSENT** | System does not update models, prompts, or DAG templates based on accumulated experiences |

**Highest Verified Level:** **Level 4 (Evaluation)** in isolation; **Level 1 (Observation)** in the Phase 12 execution loop.

---

## 10. Cognitive Architecture Audit

### LLM vs. Deterministic Boundary Map

```text
Deterministic Core (Zero-LLM Authority)
   ├── Space Kernel (Isolation, CAS PlanStore, Checkpoints)
   ├── Admission Controller (Risk tier gating, Budget windows)
   ├── Resource Manager (Fractional hardware leases, Token reaper)
   ├── Deterministic Dispatcher (Kahn DAG traversal, Topological sort)
   ├── Evidence Verifier (Physical file SHA-256 calculation, Exit code check)
   ├── Deterministic Goal Evaluator (Deterministic constraint checks)
   ├── Convergence Engine (Bounded retry/replan math, Fingerprint loop guard)
   └── Approval Manager (WebCrypto HMAC verification)
            ▲
            │ Strict Protocol Boundary (AGENTS.md §7, dep_guard.py)
            ▼
Cognitive / Agentic Layer
   ├── GoalAnalyzer (Translates natural language Command -> GoalSpec)
   ├── Planner (Translates GoalSpec -> Proposed TaskGraph)
   ├── BaseAgent (State machine: IDLE -> THINKING -> PROPOSING)
   ├── ProposalValidator (Strips forbidden actions from Agent proposals)
   └── LLMProvider / LiveHTTPLLMProvider (Urllib HTTP client to Ollama/OpenAI)
```

### Critical Invariants Verified:
- **Zero LLM in Core:** Core modules import zero LLM packages (`dep_guard.py` PASS).
- **LLM Output is Proposal Only:** Proposals emitted by agents or LLMs cannot mutate state directly; they must pass `ProposalValidator`, `AdmissionController`, and `SpaceKernel.commit_plan_delta()`.
- **Adversarial Evaluator Rejection:** Verified in INT-14: an LLM evaluator claiming `confidence=1.0, status=SATISFIED` cannot suppress a terminal error escalation.

---

## 11. Agent Audit

Inspection of `agents/base.py` and `agents/context.py`:
- **State Machine:** Explicit states: `IDLE`, `THINKING`, `PROPOSING`, `WAITING`, `EXECUTING`, `OBSERVING`, `REFLECTING`, `COMPLETED`, `FAILED`.
- **Proposal Constraint:** `ProposalValidator` actively intercepts and rejects proposals attempting to:
  - `modify_authoritative_plan`, `edit_task_graph`
  - `allocate_gpu`, `mint_lease`, `bypass_budget`
  - `approve_human_gate`, `self_approve`
  - `read_secret`, `cross_space_action`
- **Agent Lifecycle Limitation:**
  - Agents are **single-turn proposal generators**.
  - An agent does not autonomously step through a multi-hour plan. The `DeterministicDispatcher` steps through the plan, invoking worker tools. Agents are invoked only to generate proposals or decompose goals.
  - Agents cannot recruit or spawn other agents autonomously.

---

## 12. Multi-Agent Audit

Inspection of `core/orchestrator/team_builder.py` and multi-agent roles:
- `TeamBuilder` maps TaskGraph capabilities to role names (`researcher`, `coder`, `auditor`, `fallback`).
- **Capability Status:**
  - Parallel agents: **SCAFFOLD** (TeamBuilder generates assignments table, but Dispatcher executes worker tasks, not autonomous agent loops).
  - Agent-to-agent delegation: **PARTIAL** (`SubagentWorker` passes an isolated handoff note and plan node ID; subagents cannot communicate back interactively).
  - Shared working context: **ABSENT** (Agents do not share a live blackboard; communication is strictly through pulse bus events).
  - Peer negotiation: **ABSENT**.

---

## 13. Research Capability Audit

Inspection of research capabilities across `agents/roles/researcher.py`, `workers/browser/worker.py`, and `skills/mcp/`:
- **Current State:**
  - `ResearcherRole` is a 50-line class that calls `self.step()` with a prompt string.
  - `BrowserWorker` is a 75-line passive content fetcher with regex URL parsing; it validates network policies and returns mock HTML with `taint: True`.
  - MCP client (`skills/mcp/client.py`) can discover and execute external tools via stdio/SSE.
- **What is Missing for Autonomous Research:**
  - No web search engine provider (Brave, Google, DuckDuckGo).
  - No headless browser DOM interaction (Playwright / Chromium).
  - No HTML-to-markdown content extraction or document parser (PDF, DOCX).
  - No source credibility tracking, citation graph, or contradiction detection.
  - No multi-step evidence synthesis pipeline.

---

## 14. Software Engineering Capability Audit

Inspection of software development capabilities across `workers/python/`, `workers/shell/`, `workers/file/`, and `workers/git/`:
- **Current State:**
  - Sandboxed Python execution (`PythonWorker`): executes isolated Python scripts in space-local temp directories, captures stdout/stderr, measures duration, enforces timeouts.
  - Sandboxed Shell execution (`ShellWorker`): executes allowed commands (`python -c`, basic utilities) with argument sanitization.
  - Sandboxed File I/O (`FileWorker`): reads/writes files within space root directory; rejects path traversal (`../`).
- **What is Missing for Autonomous Software Engineering:**
  - `workers/git/` is an empty stub (`__init__.py` only). No automated git worktree branching, committing, diffing, or PR creation.
  - `workers/db/` is an empty stub (`__init__.py` only).
  - No AST-aware code editing or refactoring tools.
  - No test-runner integration (cannot automatically run repository pytest and parse structured failure reports for iterative debugging).
  - No multi-file workspace indexing.

---

## 15. Artifact Architecture Audit

Inspection of `channels/daemon/artifacts.py`, `workers/file/worker.py`, and `core/orchestrator/dispatch_model.py`:
- **Artifact Types Supported:** Arbitrary file bytes (text, JSON, binary) written within the space root directory.
- **Cryptographic Provenance:** Artifacts are digested with SHA-256; `VerifiedExecutionEvidence` binds `path`, `sha256`, `task_id`, `space_id`, `plan_version`, and `attempt`.
- **Cross-Task Consumption:** Downstream tasks can read upstream files if they know the relative path within the space directory.
- **Cross-Space Isolation:** Strict boundary: Space A cannot access Space B artifacts (`PermissionError`).
- **Desktop Exposure:** Channel Daemon exposes `/api/v1/spaces/{id}/artifacts` for listing and downloading artifacts with path traversal guards (`test_v101_hardening.py`).
- **Limitation:** Artifacts are unversioned on disk (overwriting a path replaces file content; historical versions exist only as SHA-256 records in pulses, not as a content-addressed blob store).

---

## 16. Context Architecture Audit

Inspection of `agents/context.py` and token management:
- `ContextManager` maintains token-bounded context windows with `ContextEntry` priority levels (`CRITICAL`, `HIGH`, `NORMAL`, `LOW`).
- Compaction: FIFO truncation of `LOW` and `NORMAL` entries when token count exceeds budget.
- **Context Boundaries:**
  - Context does **not** cross Space boundaries.
  - Context does **not** automatically persist across daemon restart.
  - Subagents receive an isolated `handoff_note` rather than the parent context history (`WORKER-005`).

---

## 17. Security Boundary Audit

The existing security architecture remains intact across all verified milestones:
- **Space Isolation (SCCA Law 1):** `verify_space_identity()` enforced at every entry point. Cross-space dispatch, CAS, proposals, and memory queries raise `PermissionError`.
- **Capability Gating (SCCA Law 2):** Tasks cannot execute without admission from `AdmissionController` and a fractional lease from `ResourceManager`.
- **Forward-Only Taint (TAINT-001..005):** Ingested external data is marked `taint=True`. Child tasks inherit taint. Clearance requires explicit `security.taint.cleared` pulse. Evaluator rejects tainted evidence unless goal specifies `allow_taint`.
- **Secret Sanitization (SEC-04):** Text outputs, errors, and traces are sanitized against `SecretStore` prior to pulse publication or worker result return.
- **Device Grants (NODE-001..013):** Physical hardware access is mediated by `DeviceGrantManager` with lease binding and platform MDM allow-lists.
- **Human Gates (HMAC Preimages):** Human approvals require client-side HMAC signatures (`token-hmac-v1`). Synthetic approvals are rejected.

---

## 18. Governance Audit

- **ADR Monotonicity:** Continuous from `0001` through `0041`. Zero resets. Zero versioned directories (`adr/v1.0/`).
- **Contract Numbering:** Permanent IDs (`DISPATCH-001..005`, `CONV-001..005`, `SPACE-001..006`, etc.).
- **Spec Coverage:** 141 architecture criteria, 192 contract IDs, 162 mappings in `harness/spec_map.yaml`. Zero orphans in either direction (`v1_audit_spec_coverage.py` PASS).
- **Documentation Hygiene:** `AGENTS.md`, `CONTRACT_MATRIX.md`, and `RELEASES.md` are synchronized (`v1_audit_governance.py` PASS).

---

## 19. Technical Debt Audit

The following technical debt items were identified in the repository:

| ID | Finding | Subsystem | Severity | Risk | Current Workaround | Recommended Resolution |
|:---|:---|:---|:---|:---|:---|:---|
| **DEBT-01** | **Memory-Execution Disconnect:** `DeterministicDispatcher` does not invoke `Reflector.reflect()` on task completion; `ConvergenceEngine` does not consult `AdaptationLayer` during replan. | `core/orchestrator/`, `memory/` | **HIGH** | The system is amnesic: past failure lessons in PostgreSQL are never used by the autonomous convergence engine. | Handcrafted goal hints in tests. | Formally connect `Reflector` to `Dispatcher` completion and wire `AdaptationLayer` into `ConvergenceEngine`. |
| **DEBT-02** | **In-Memory Convergence State:** `ConvergenceEngine._retry_counts`, `_replan_counts`, and `_seen_fingerprints` are stored in python dicts/sets and lost on process restart. | `core/orchestrator/dispatch_model.py` | **HIGH** | A daemon restart resets retry counters, allowing tasks to exceed the hard ceiling of 3 retries. | Checkpoint serializes plan version, but not engine retry budgets. | Persist task attempt and replan counters in PostgreSQL or PlanStore task metadata. |
| **DEBT-03** | **Synchronous Worker Execution:** Workers block execution threads via `subprocess.run()`. No asynchronous process tracking or heartbeat. | `workers/sandbox/process.py` | **MEDIUM** | Long-running jobs block the runner thread and cannot report intermediate progress. | Execution timeout limits. | Transition worker execution to async process handles with periodic heartbeat pulses. |
| **DEBT-04** | **Empty Worker Stubs:** `workers/automation/`, `workers/db/`, `workers/git/`, `workers/network/`, and `workers/retrieval/` contain only empty `__init__.py` files. | `workers/` | **MEDIUM** | Capabilities appear in architecture taxonomy but raise errors if dispatched. | Fallback to Python/Shell workers. | Either implement concrete sandboxed workers or remove dead directory stubs. |
| **DEBT-05** | **Graph Store Stubs:** `Neo4jAdapterStub` and `QdrantAdapterStub` raise `NotImplementedError`. | `memory/adapters/` | **LOW** | Knowledge graph and semantic vector retrieval are not operational. | In-memory and PostgreSQL linear scans. | Complete vector/graph adapters when semantic memory is prioritized. |

---

## 20. Capability Bottleneck Analysis

To determine the true next architectural phase, we evaluate which limitation serves as the primary blocker across multiple downstream capabilities:

```text
                               ┌────────────────────────────────┐
                               │   AUTONOMOUS SOFTWARE ENG.     │
                               └───────────────▲────────────────┘
                                               │
                               ┌───────────────┴────────────────┐
                               │      AUTONOMOUS RESEARCH       │
                               └───────────────▲────────────────┘
                                               │ requires learning & recovery
                               ┌───────────────┴────────────────┐
                               │  CLOSED-LOOP ADAPTIVE LEARNING │
                               └───────────────▲────────────────┘
                                               │ requires durable persistence
                               ┌───────────────┴────────────────┐
                               │  DURABLE EXECUTION & RECOVERY  │
                               │   (Phase 12.8 Crash Recovery)  │
                               └───────────────▲────────────────┘
                                               │
                               ┌───────────────┴────────────────┐
                               │  PHASE 12.7 EXECUTION ENGINE   │
                               │         (GATE_VERIFIED)        │
                               └────────────────────────────────┘
```

### Bottleneck Identification:
1. **Could we jump directly to Autonomous Research or Autonomous Software Engineering?**
   - **No.** Building research tools (web search, browser DOM) or software engineering tools (git branching, AST parsing) on top of an execution engine that loses its retry state on daemon reboot and cannot learn from past task failures will produce brittle, amnesic execution loops that repeat errors indefinitely.
2. **The Dual Bottleneck:**
   - **Bottleneck 1 (Durability):** The execution engine cannot survive process crashes without losing in-flight task tracking and retry ceilings (`DEBT-02`). This is already scheduled as **Milestone 12.8 (Crash Recovery & Persistence)**.
   - **Bottleneck 2 (Cognitive Continuity & Adaptive Memory):** The execution engine is completely disconnected from the memory subsystem (`DEBT-01`). The system executes tasks, but neither captures experiences into memory automatically nor uses past experiences to guide replanning.

---

## 21. Candidate Architecture Analysis

| Candidate Architectural Phase | Problem Solved | Infrastructure Reused | New Infrastructure Required | Architectural Tradeoffs |
|:---|:---|:---|:---|:---|
| **Candidate 1: Durable Crash Recovery & Pulse Scan (Phase 12.8)** | In-flight task recovery across host reboot; persistent retry counters; pulse outbox reconciliation. | `PostgresPulseStore`, `PulseReplayer`, `SpaceKernel`, `DeterministicDispatcher`. | Startup scan daemon; orphan task reaper; persistent attempt table. | **Essential operational baseline**, but already codified as Milestone 12.8 of Phase 12. |
| **Candidate 2: Closed-Loop Experiential Learning & Memory-Driven Adaptation** | Closes the loop between Phase 10 (Memory) and Phase 12 (Execution). Tasks automatically record experiences; ConvergenceEngine consults past failure hints before replanning. | `Reflector`, `AdaptationLayer`, `PostgresMemoryAdapter`, `ConvergenceEngine`, `DeterministicDispatcher`. | Auto-reflection pipeline on task completion; memory-informed replan generator; experience-augmented GoalSpec. | **Highest cognitive leverage.** Converts RYU from an amnesic task executor into an adaptive learning system. |
| **Candidate 3: Autonomous Research & Synthesis Pipeline** | Web search, document retrieval, and evidence synthesis. | `BrowserWorker`, `MCPWorker`, `RuntimeWorkerInvoker`, `TaskGraph`. | Live search provider integration (Brave/Google API); Playwright DOM worker; PDF/Markdown parsers; citation graph. | High external dependency footprint; premature without durable memory and adaptive replanning. |
| **Candidate 4: Autonomous Software Engineering Engine** | Git worktree management, multi-file code editing, test-driven iteration. | `PythonWorker`, `ShellWorker`, `FileWorker`, `ConvergenceEngine`. | Git worker implementation; AST patch generator; test report parser; workspace indexing. | Highly complex; requires adaptive memory to avoid repeating identical compilation/test errors. |

---

## 22. Architectural Dependency Graph

```text
Phase 12.7: Integrated Execution Engine (COMPLETE)
   │
   ▼
Phase 12.8: Crash Recovery & Durable Persistence (IMMEDIATE HARDENING)
   ├── PostgreSQL startup pulse scan
   ├── Abandoned task detection & transient.worker_crash transition
   └── Durable attempt and retry budget persistence
   │
   ▼
Phase 13 (Proposed): Closed-Loop Experiential Adaptation (NEXT MAJOR ARCHITECTURE)
   ├── Dispatcher-to-Reflector automatic experience capture
   ├── ConvergenceEngine-to-AdaptationLayer failure hint integration
   ├── Counterfactual-guided replan DAG generation
   └── Cross-Space knowledge promotion integration
   │
   ▼
Phase 14: Autonomous Research & Software Engineering
   ├── Concrete GitWorker, DBWorker, and RetrievalWorker
   ├── Live Web Search & Headless Browser DOM interaction
   └── Iterative Test-Driven Software Repair Loops
```

---

## 23. Architectural Readiness Matrix

| Capability Subsystem | Foundation Implemented | Missing / Disconnected | Security Impact | Persistence Impact | Architectural Readiness |
|:---|:---|:---|:---|:---|:---|
| **Durable Crash Recovery** | `PostgresPulseStore`, `PulseReplayer`, Checkpoints | Startup pulse scanner, abandoned task reaper | Low (SCCA Law 6) | PostgreSQL `pulses` table | **READY FOR IMPLEMENTATION (Phase 12.8)** |
| **Experiential Learning** | `Reflector`, `AdaptationLayer`, `ExperienceRecord` | Wired into `Dispatcher` and `ConvergenceEngine` | Low (Law 4 Space-local) | PostgreSQL `experiences` table | **ARCHITECTURALLY READY (Phase 13)** |
| **Autonomous Research** | `BrowserWorker` (mock), `MCPWorker` | Search provider, Playwright, citation synthesis | Medium (external web egress) | Filesystem artifacts | **BLOCKED on Adaptive Memory** |
| **Software Engineering** | `PythonWorker`, `ShellWorker`, `FileWorker` | `GitWorker` stub, AST editor, test parser | High (code execution risk) | Workspace git repository | **BLOCKED on Adaptive Memory & GitWorker** |
| **Multi-Agent Runtime** | `TeamBuilder`, `SubagentWorker` | Live agent coordination, shared context blackboard | Medium (agent delegation) | Agent state machines | **BLOCKED on Async Execution** |
| **Multimodal / Voice** | None (Explicit non-goal) | STT, TTS, WebRTC, frame processors | High (device grants, data egress) | Media streams | **EXCLUDED BY SPECIFICATION** |

---

## 24. Proposed Next Phase Definition

Based on the evidence uncovered in this audit, the transition trajectory divides cleanly into **two distinct steps**:

### Step 1: Complete the Phase 12 Boundary (Milestone 12.8)
Before leaving Phase 12, the repository must finish the already-specified **Milestone 12.8 (Crash Recovery & Persistence)**:
- Implement startup pulse scan from PostgreSQL position 0.
- Clean up abandoned tasks across daemon restart.
- Persist retry and replan attempt budgets durably.

### Step 2: Next Architectural Phase — Phase 13: Closed-Loop Experiential Adaptation

```text
Phase Number:           Phase 13
Phase Title:            Closed-Loop Experiential Adaptation & Memory-Guided Execution
Architectural Objective: Connect the Phase 10 Memory & Adaptation subsystem to the
                        Phase 12 Autonomous Execution Engine, eliminating amnesic execution.
Problem Solved:         Currently, the Dispatcher never records completed tasks into
                        Experience memory, and the ConvergenceEngine never consults past
                        failure experiences when generating replan proposals. Phase 13 closes
                        this loop, making RYU adaptive across execution cycles.
Existing Subsystems:    Reflector, AdaptationLayer, PostgresMemoryAdapter, SpaceKernel,
                        DeterministicDispatcher, ConvergenceEngine, PlanReconciler.
New Mechanisms:         1. Automatic task completion reflection hook in Dispatcher.
                        2. Experience-augmented GoalSpec generation in GoalAnalyzer.
                        3. Memory-informed ReplanDelta generator in PlanReconciler.
                        4. Historical avoidance constraints applied during DAG scheduling.
Required Contracts:     ADAPT-001..004, MEM-007..008.
Required ADRs:          ADR-0042 (Closed-Loop Reflection & Convergence Memory Integration).
Required Security:      SCCA Law 4 enforcement: experiences remain strictly Space-local;
                        cross-Space adaptation requires verified PromotionPipeline tokens.
Verification Strategy:  Re-run failure scenario; verify that on second run, the system
                        actively avoids the failed capability based on persisted experience hints.
```

---

## 25. Non-Goals for the Next Phase

The following remain **explicit non-goals** for Phase 13:
1. **No Speculative Multimodality:** No voice (STT/TTS), audio streaming, or video processing.
2. **No Unrestricted Code Self-Modification:** Agents cannot alter their own prompts, system architecture, or core rules.
3. **No External Graph DB Dependency:** Neo4j and Qdrant remain optional extension stubs; PostgreSQL remains the authoritative memory store.
4. **No Direct Desktop Authority:** Desktop UI remains an unprivileged loopback HTTP client.
5. **No Bypassing Human Gates:** Promotion to global memory and high-risk capability access strictly require human HMAC tokens.

---

## 26. Required Contracts for Phase 13

| Contract ID | Name | Architectural Invariant | Boundary |
|:---|:---|:---|:---|
| **ADAPT-001** | Automatic Task Completion Reflection | Every terminal task execution (`completed` or `failed`) automatically persists an `ExperienceRecord` with counterfactual reasoning prior to task pulse emission. | `core/orchestrator/dispatch_model.py`, `memory/reflector.py` |
| **ADAPT-002** | Memory-Guided Replan Synthesis | When `ConvergenceEngine` produces a `REPLAN` proposal, it must query `AdaptationLayer.generate_hints()` and include avoidance constraints in the proposed `PlanDelta`. | `core/orchestrator/dispatch_model.py`, `core/memory/adaptation.py` |
| **ADAPT-003** | Space-Local Memory Isolation in Planning | `AdaptationLayer` hints must never incorporate unpromoted experiences from outside the target Space (`SPACE-001`, `SCCA Law 4`). | `core/memory/adaptation.py`, `core/space/kernel.py` |
| **ADAPT-004** | Deterministic Hint Replay | During `REPLAY_MODE`, `AdaptationLayer` produces bitwise identical hints from recorded experience state; zero live database queries. | `core/memory/adaptation.py`, `llm/replay.py` |

---

## 27. Required ADR Decisions

- **ADR-0042:** *Closed-Loop Reflection & Memory-Guided Plan Convergence*
  - **Context:** Phase 10 implemented `Reflector` and `AdaptationLayer`, while Phase 12 implemented `DeterministicDispatcher` and `ConvergenceEngine`. However, they operate in isolation: the Dispatcher does not record experiences, and the ConvergenceEngine does not consult past experiences when replanning.
  - **Decision:** Wire `Reflector.reflect()` directly into the Dispatcher's task completion lifecycle, and require `ConvergenceEngine._propose_replan()` to query `AdaptationLayer.generate_hints()` to construct informed recovery deltas.
  - **Consequences:** Eliminates amnesic trial-and-error; preserves `AGENTS.md §7` (Core Independence) via dependency inversion protocols; enforces SCCA Law 4 (Space-local knowledge).

---

## 28. Verification Strategy

Phase 13 verification must be demonstrated through executable proofs:
1. **The Amnesia Test:** Execute a task with a deliberate defect; let it fail; verify an `ExperienceRecord` is durably persisted with a valid `counterfactual`.
2. **The Adaptation Proof:** Re-execute the same goal; verify that `AdaptationLayer` generates an `ExperienceHint`; verify that `Planner` / `PlanReconciler` selects an alternative capability avoiding the known defect.
3. **The Isolation Proof:** Verify that Space B executing the same goal does **not** receive Space A's unpromoted experience hints.
4. **Replay Equivalence:** Verify that replaying the adaptive run reproduces identical hints and CAS transitions in `REPLAY_MODE`.

---

## 29. Final Findings

1. **Phase 12 is Complete & Verified:** The execution engine reliably sequences goals, plans, admission, leases, sandboxed workers, evidence collection, verification, evaluation, and bounded CAS replanning.
2. **The Architecture Has Zero Speculative Bloat:** Core independence (`AGENTS.md §7`) is 100% intact (0 forbidden imports). No unused multimodal or voice dependencies exist.
3. **The Primary Next Bottleneck is Cognitive Continuity:** RYU has mastered *execution*, but remains *amnesic*. Connecting the existing Phase 10 Memory/Adaptation subsystem to the Phase 12 Execution Engine is the highest-leverage architectural progression before building complex research or software engineering tools.

---

## 30. Audit Conclusion

$$\mathbf{PHASE\_13\_AUDIT\_COMPLETE = TRUE}$$

- **Repository Capability Inventory:** Complete across all 12 subsystems.
- **Runtime Execution Path:** Formally traced and verified.
- **Autonomy Boundary:** Explicitly defined between single-session execution and lifelong adaptation.
- **Technical Debt:** 5 concrete items cataloged with recommended resolutions.
- **Next Phase Defined:** **Phase 13 (Closed-Loop Experiential Adaptation)** preceded by the completion of **Milestone 12.8 (Crash Recovery & Persistence)**.
- **Non-Goals Preserved:** Zero premature implementation of Phase 13/14 occurred.
- **Repository State:** Clean working tree, all baseline verification scripts pass 100%.
