# PHASE 15 ARCHITECTURE AUDIT

**RYU AI Framework — Architecture Audit & Next-Generation Runtime Hardening**

**Date:** 2026-10-03
**Prepared by:** Architecture Auditor & Verification Engineer
**Governing ADR:** ADR-0044
**Architecture:** Space-Centric Cognitive Architecture (SCCA)

---

## 1. Baseline

### Current Commit
- **HEAD:** `7d4198a` — `docs(phase14.8): replace absolute defect-free wording`
- **Phase 14.8 implementation:** `bd3caa7`
- **Phase 14.7 hardened:** `6a50de3`
- **Phase 14.6 (repair loop):** `5452677`
- **Phase 14.5 baseline:** `27520a6`
- **Phase 14.4 baseline:** `acc236b`

### Repository State
- Working tree: **clean** (no uncommitted changes)
- Branch: `main` synchronized with `origin/main`
- Python: 3.11.9 (pyproject.toml requires `>=3.12`; environment discrepancy)
- ADRs: 44 (0001..0044)
- Project Memory: 26 entries (0001..0026)
- Contracts: 50 pulse types in `contracts/registry/pulse-types.json`
- Spec map: 182 executable mappings (V1-001 PASS)
- Total test suite: **1,003 unit/component tests** collected

### Phase 14.8 Status
- **GATE-14.8: PASS** (governance, dep-guard, contract-sync all PASS)
- `dep_guard.py`: PASS — no forbidden imports in `core/`
- `contract_sync.py`: PASS — all 38 architecture types in registry (12 additional registry types exist beyond the architecture spec, noted below)
- `v1_audit_spec_coverage.py`: PASS — V1-001 PASS, 182 mappings
- `v1_audit_governance.py`: PASS — V1-005 PASS

### Governance Discrepancy (Pre-existing)
`contract_sync.py` reports 12 pulse types in the registry that are **not** documented in the Architecture Sec 16:
`lease.reconciled`, `recovery.completed`, `recovery.scan_completed`, `recovery.started`, `repair.loop_iterated`, `repo.patch_applied`, `repo.patch_reverted`, `research.conflict_detected`, `research.retrieved`, `task.interrupted_detected`, `task.worker_crash`, `test.executed`.
These are implemented (Phases 12–14) but architecture documentation was not updated. Classification: **PARTIAL** (implemented, not architecture-documented).

---

## 2. Existing Capability Map

The following table reflects *actual verified* implementation state as of Phase 14.8.

| Capability | Component | Status | Evidence |
|:---|:---|:---|:---|
| Space Lifecycle | `core/space/kernel.py` | INTEGRATION_VERIFIED | test_phase12_kernel_cas.py (16 tests) |
| Pulse Bus (In-memory) | `core/pulse_bus/bus.py` | UNIT_VERIFIED | test_bus_validation.py (7), test_taint.py (4) |
| Pulse Bus (Durable) | `core/pulse_bus/durable_bus.py` | UNIT_VERIFIED | test_durable_bus_unit.py (6) |
| PostgreSQL Pulse Store | `core/pulse_bus/store.py` | UNIT_VERIFIED | test_store_interface.py (7) |
| Redis Stream Transport | `core/pulse_bus/transport.py` | UNIT_VERIFIED | test_durable_bus_unit.py |
| PlanStore / TaskGraph | `core/plans/plan_store.py` | UNIT_VERIFIED | test_plan_engine.py (7) |
| PlanDelta CAS | `core/plans/plan_store.py` | INTEGRATION_VERIFIED | test_phase12_kernel_cas.py (16) |
| Admission Control | `core/capabilities/admission.py` | UNIT_VERIFIED | test_admission.py (4) |
| Resource Manager | `core/resources/manager.py` | UNIT_VERIFIED | test_manager.py (8) |
| Deterministic Dispatcher | `core/orchestrator/dispatch_model.py` | INTEGRATION_VERIFIED | test_phase12_worker_invocation.py (24), test_phase12_integrated_execution.py (18) |
| ConvergenceEngine | `core/orchestrator/dispatch_model.py` | UNIT_VERIFIED | test_phase12_convergence_engine.py (34) |
| DeterministicGoalEvaluator | `core/orchestrator/dispatch_model.py` | UNIT_VERIFIED | test_phase12_convergence_engine.py |
| StartupRecoveryEngine | `core/orchestrator/startup_recovery.py` | UNIT_VERIFIED | test_phase12_8_crash_recovery.py (32) |
| RepositoryWorker | `workers/repository/worker.py` | UNIT_VERIFIED | test_phase14_3_repository_worker.py (19) |
| AtomicPatcher | `workers/repository/worker.py` | UNIT_VERIFIED | test_phase14_4_patcher.py (62) |
| TestRunnerWorker | `workers/test_runner/worker.py` | UNIT_VERIFIED | test_phase14_5_test_runner.py (43) |
| BoundedRepairLoop | `core/space/repair_protocol.py` | UNIT_VERIFIED | test_repair_protocol.py (32), test_phase14_6_repair_loop.py (46) |
| ResearchWorker | `workers/research/worker.py` | UNIT_VERIFIED | test_phase14_2_research_worker.py (34) |
| ResearchSynthesizer | `workers/research/synthesis.py` | UNIT_VERIFIED | test_phase14_7_research_synthesis.py (36) |
| Memory Adapters | `memory/adapters/` | PARTIAL | test_in_memory_adapter.py (5), postgres.py STUB for embeddings |
| AdaptationLayer | `core/memory/adaptation.py` | UNIT_VERIFIED | test_adaptation.py (5), test_phase13_experiential_adaptation.py (27) |
| ExperienceObserver | `memory/experience_observer.py` | UNIT_VERIFIED | test_phase13_experiential_adaptation.py |
| KnowledgePromotion | `memory/promotion.py` | UNIT_VERIFIED | test_promotion.py (12) |
| MCPWorker | `workers/mcp/worker.py` | UNIT_VERIFIED | test_mcp_worker.py (3) |
| Agent State Machine | `agents/base.py` | UNIT_VERIFIED | test_agent_state_machine.py (3) |
| ContextManager | `agents/context.py` | UNIT_VERIFIED | test_context.py (3) |
| SoftwareEngineeringWorkflow | `workflows/software_engineering.py` | INTEGRATION_VERIFIED | test_phase14_8_software_engineering_workflow.py (39) |
| Channel Daemon | `channels/` | UNIT_VERIFIED | test_daemon.py (9), test_daemon_v101.py (16) |
| NodeRuntime (Rust) | `node_runtime/` | UNIT_VERIFIED | Cargo tests PASS |
| Secret Management | `core/security/` | UNIT_VERIFIED | test_secrets.py (3) |

---

## 3. Execution Lifecycle Audit

### Complete Lifecycle Trace (Phase 14.8 Actual Path)

```
Human
  │
  ▼ CLI / Channel Daemon (channels/)
  │   Authority: HTTP loopback only, HMAC-signed approvals
  │   State owner: in-memory session + PostgreSQL approval_requests table
  │   Recovery: restart loses in-flight channel sessions
  │
  ▼ GoalSpec (core/orchestrator/goal_analyzer.py)
  │   Authority: deterministic parser — goal structure validated
  │   Persistence: NOT durable — held in-memory by SoftwareEngineeringWorkflow
  │
  ▼ SpaceKernel (core/space/kernel.py)
  │   Authority: ONLY authority for space state, plan CAS
  │   State: budget, plan_version, approval_store (IN-MEMORY)
  │   Persistence: PlanStore is purely in-memory (P0 gap)
  │   Recovery: create_checkpoint() returns dict; restore_checkpoint() re-populates in-memory state
  │
  ▼ PlanDelta CAS (core/plans/plan_store.py)
  │   Authority: versioned CAS with bounded rebase (ADR-0003)
  │   State: _graphs, _history, _last_winning_delta — all in-memory dict
  │   Persistence: NONE — process restart loses all plan history
  │
  ▼ TaskGraph (core/plans/task_graph.py)
  │   Authority: SpaceKernel owns TaskGraph
  │   Concurrency: sequential modification via kernel.commit_plan_delta()
  │
  ▼ DeterministicDispatcher (core/orchestrator/dispatch_model.py)
  │   execute_task_full_pipeline() — blocking, single-task
  │   No concurrent DAG scheduling — one task executes at a time
  │
  ▼ AdmissionController (core/capabilities/admission.py)
  │   Authority: checks TIER_0..TIER_3 risk, budget
  │
  ▼ ResourceManager (core/resources/manager.py)
  │   Authority: leases, quotas, FIFO priority queue
  │   State: InMemoryResourceStore — LOST on restart
  │
  ▼ Workers (workers/repository/, workers/test_runner/, workers/research/)
  │   Artifact paths: base_working_dir/artifacts/{worker_type}/{task_id}_*
  │   Space isolation: NOT enforced in path — MISSING space_id namespace (P0 gap)
  │
  ▼ ExecutionAttemptStore (core/orchestrator/execution_state.py)
  │   PostgreSQL-backed: DURABLE
  │   Recovery: StartupRecoveryEngine scans for interrupted attempts at startup
  │
  ▼ PulseBus → DurablePulseBus
  │   In-memory bus: PulseBus (default, non-durable)
  │   Durable bus: DurablePulseBus (PostgreSQL + Redis Streams)
  │   SoftwareEngineeringWorkflow uses in-memory PulseBus
  │
  ▼ Evidence (VerifiedExecutionEvidence)
  │   Authority: DeterministicGoalEvaluator
  │   State: in-memory list in SoftwareEngineeringWorkflow.evidence_collected
  │   Persistence: NONE — replay would require re-execution
  │
  ▼ Memory / Experience (memory/adapters/)
  │   SpaceMemory: InMemoryMemoryAdapter (default) or PostgreSQLMemoryAdapter
  │   Retrieval: ORDER BY stored_at DESC LIMIT N + in-memory keyword scan
  │   Semantic search: NOT IMPLEMENTED (Neo4j, Qdrant = explicit stubs raising NotImplementedError)
  │
  ▼ AdaptationLayer (core/memory/adaptation.py)
  │   Authority: adapts goal/task parameters from ExperienceHint
  │   State: in-memory convergence history (ConvergenceStateStore)
  │
  ▼ ConvergenceEngine (core/orchestrator/dispatch_model.py)
  │   Decisions: CONTINUE / RETRY / REPLAN / ESCALATE / ABORT
  │   State: InMemoryConvergenceStateStore — LOST on restart
  │
  ▼ Goal Completion
      Evidence verified → GoalEvaluationStatus.SATISFIED → WorkflowStatus.COMPLETED
```

### Known Lifecycle Gaps
1. GoalSpec is never persisted; goal cannot be reconstructed after process restart
2. ConvergenceState (retry counts, fingerprints) is in-memory; restarts could permit infinite retry
3. ResourceStore is in-memory; orphaned leases cannot be detected after restart
4. Artifact paths are not space-namespaced; concurrent space runs collide

---

## 4. Persistence Audit

### Durable State (PostgreSQL-backed)

| State | Table / Mechanism | Retention Policy |
|:---|:---|:---|
| Pulses | `pulses` table | **NONE** — unbounded growth |
| Execution Attempts | `execution_attempts` table (InMemoryExecutionAttemptStore has a Postgres variant) | Unknown — no archival policy |
| Memory Experiences | `experiences` table (PostgreSQLMemoryAdapter) | **NONE** — no decay, deduplication, or archival |
| Knowledge Promotions | `promotion_audit` table | **NONE** — append-only, no archival |
| Channel Approvals | `approval_requests` table | **NONE** |

### Non-Durable State (In-Memory Only, LOST on restart)

| State | Location | Recovery Mechanism | Status |
|:---|:---|:---|:---|
| PlanStore / TaskGraph | `core/plans/plan_store.py:43` `_graphs: dict[str, TaskGraph]` | Checkpoint (dict in RAM) | **MISSING durable backing** |
| Plan history | `plan_store.py:44` `_history: dict` | None | **MISSING** |
| ConvergenceState | `InMemoryConvergenceStateStore` | None — retry counts reset | **MISSING** |
| ResourceStore (leases) | `InMemoryResourceStore` | Lease reconcile at startup | PARTIAL |
| GoalSpec | SoftwareEngineeringWorkflow instance | None | **MISSING** |
| Evidence collected | `workflow.evidence_collected` list | None | **MISSING** |
| Checkpoint data | `kernel.create_checkpoint()` → dict | `restore_checkpoint()` from dict | RAM-only |
| Workflow provenance chain | `_provenance_chain` list | None | **MISSING** |

### The Cold-Boot Cliff Edge

`StartupRecoveryEngine` (`core/orchestrator/startup_recovery.py`) requires `kernel.get_task_graph()` to already be present in the in-memory `PlanStore`. On cold process restart, `PlanStore._graphs = {}`. The recovery engine scans `ExecutionAttemptStore` for interrupted tasks and attempts `kernel.propose_task_transition()`, but the kernel has no graph to operate on. This means **recovery is possible only if the same Python process handles both the original and the recovery execution** — a condition that is never guaranteed after a crash.

**Evidence:**
- `core/plans/plan_store.py:43`: `self._graphs: dict[str, TaskGraph] = {}`
- `core/space/kernel.py:253`: `create_checkpoint` returns a `dict` in RAM
- `workflows/software_engineering.py:806`: `chk_data` is created and immediately discarded

---

## 5. Concurrency Audit

### Same-Space Concurrent Task Execution

**Current behavior:** `execute_task_full_pipeline()` is a **single-threaded blocking call**. There is no concurrent DAG scheduler. The SoftwareEngineeringWorkflow executes 4 hardcoded sequential stages. Independent DAG branches cannot run in parallel.

**Evidence:** `core/orchestrator/dispatch_model.py:2401` — `execute_task_full_pipeline` has no thread pool, executor, or async runtime.

**Impact:** A 10-task DAG with 8 independent branches will execute serially as if it were a 10-step chain.

### Same-Space Resource Races

`ResourceManager` uses a `PriorityQueue` with a threading lock for the ready queue. However:
- Multiple threads calling `acquire()` simultaneously for the same `ResourceIdentity` would compete correctly (lock-protected)
- However, the `PlanStore` has **no locking**: concurrent `commit_plan_delta()` calls from separate threads would race on `_graphs`, `_history`, and `_last_winning_delta` (all plain `dict`s)

**Evidence:** `core/plans/plan_store.py` has no `threading.Lock` wrapping `_graphs` mutations.

### Multi-Space Artifact Collision

Workers write artifacts to:
```
{base_working_dir}/artifacts/repository/{task_id}_*
{base_working_dir}/artifacts/test_runner/{task_id}_*
{base_working_dir}/artifacts/research/{task_id}_*
```

The `task_id` is typically `task-test`, `task-patch`, etc. — short, predictable strings. **Two concurrent spaces with the same base working directory and the same `task_id` will overwrite each other's artifacts.**

**Evidence:**
- `workers/repository/worker.py:349`: `art_dir = self.base_working_dir / "artifacts" / "repository"`
- `workers/test_runner/worker.py:245`: `art_dir = self.base_working_dir / "artifacts" / "test_runner"`
- `workers/research/worker.py:405`: `artifact_dir = self.base_working_dir / "artifacts" / "research"`

None of these paths incorporate `space_id`.

### Multi-Space Plan Isolation

SpaceKernel uses `space_id` as a key in `PlanStore._graphs`. Multiple in-memory spaces are isolated by `space_id`. However, there is no enforcement preventing a process from constructing two `SpaceKernel` instances with the same `space_id`.

### Checkpoint Races

`create_checkpoint()` and `restore_checkpoint()` are `threading.Lock`-protected within `SpaceKernel`. A concurrent checkpoint creation during an active plan mutation is protected.

---

## 6. Memory Audit

### Current Implementation State

| Feature | Status | Evidence |
|:---|:---|:---|
| Space-local experience storage | IMPLEMENTED | `InMemoryMemoryAdapter` + `PostgreSQLMemoryAdapter` |
| Recency-based retrieval | IMPLEMENTED | `ORDER BY stored_at DESC LIMIT N` |
| In-memory keyword scoring | IMPLEMENTED | `memory/adapters/postgres.py:173` |
| Semantic / vector retrieval | MISSING | `Neo4jAdapterStub`, `QdrantAdapterStub` raise `NotImplementedError` |
| Experience deduplication | MISSING | No deduplication in any adapter |
| Experience decay / aging | MISSING | No TTL, no weight decay |
| Retention / archival policy | MISSING | No retention configuration |
| Knowledge promotion pipeline | IMPLEMENTED | `memory/promotion.py`, `test_promotion.py (12)` |
| Cross-space promotion | IMPLEMENTED (governed) | HMAC-signed, human authorization required |
| Contradiction detection | PARTIAL | ResearchSynthesizer in Phase 14.7 (workflow-level only) |
| Confidence scoring | IMPLEMENTED | `ExperienceRecord.confidence` field |
| Temporal context | PARTIAL | `stored_at` timestamp stored, not used for decay |

### Critical Gap: Recency-Only Retrieval

`PostgreSQLMemoryAdapter.query_similar_experiences()` (line 154–173):
```sql
SELECT ... FROM experiences WHERE space_id = %s
ORDER BY stored_at DESC
LIMIT %s;
```
This retrieves only the **most recent** N experiences, then performs **in-memory keyword matching** on that limited set. If a highly relevant historical failure occurred more than N experiences ago, it will never be surfaced for adaptation.

**Architectural consequence:** The adaptation system silently degrades as a space accumulates experience. Long-lived spaces become progressively worse at learning from distant history.

### Neo4j and Qdrant Stubs

`memory/adapters/neo4j_stub.py` and `memory/adapters/qdrant_stub.py` raise `NotImplementedError` on all methods. These are placeholders from Phase 10's specification; the actual adapters are unimplemented.

---

## 7. Agent / Worker / Skill Audit

### Current Hierarchy

**SCCA Specified Hierarchy:**
```
Space → Plan → Team → Agent → Worker → Skill
```

**Phase 14.8 Actual Execution Path:**
```
SoftwareEngineeringWorkflow → Worker (Repository / TestRunner / Research)
```

### Agent State Machine

`agents/base.py` defines `AgentState` (IDLE, THINKING, PROPOSING, WAITING, EXECUTING, OBSERVING, REFLECTING, COMPLETED, FAILED) and `AgentProposal`. It is **IMPLEMENTED** and **UNIT_VERIFIED** (3 tests).

### Disconnection Evidence

`workflows/software_engineering.py` imports list (complete):
```python
from ryu.pulse_bus.bus import PulseBus
from ryu.pulse_bus.pulse import Pulse, Severity
from core.orchestrator.dispatch_model import (...)
from core.orchestrator.goal_analyzer import GoalSpec
from core.plans.delta import PlanDelta
from core.space.kernel import SpaceKernel
from core.space.repair_protocol import (...)
from core.space.research_protocol import (...)
from workers.contract import ExecutionRequest, WorkerIdentity
from workers.repository.worker import RepositoryWorker
from workers.research.synthesis import ResearchSynthesizer
from workers.research.worker import ResearchWorker
from workers.test_runner.worker import TestRunnerWorker
```

**`agents/` is not imported anywhere in `workflows/`.** The Agent state machine and ContextManager are **not used** in the Phase 14.8 execution path.

### ContextManager

`agents/context.py` implements `ContextManager` with UNIT_VERIFIED tests (3 tests). However, `ContextManager` is never instantiated in `SoftwareEngineeringWorkflow`. Context accumulates implicitly in Python class attributes rather than through the formal context management protocol.

### Team Builder

`core/orchestrator/tests/test_team_builder.py` (2 tests) — `TeamBuilder` exists but is not invoked during workflow execution.

### Assessment

| Component | Contracts | Tests | Used in Workflow | Status |
|:---|:---|:---|:---|:---|
| Agent State Machine | AGENT-001 | 3 | No | DISCONNECTED |
| ContextManager | (implicit) | 3 | No | DISCONNECTED |
| TeamBuilder | TEAM-001 | 2 | No | DISCONNECTED |
| Workers | WORKER-001..005 | 259 | Yes | IMPLEMENTED |
| Skills / MCP | SKILL-001, REG-006 | 23 | Via MCPWorker only | PARTIAL |

---

## 8. Context Audit

### Context Ownership

There is no single authoritative context object in the Phase 14.8 execution path. Context is distributed across:

| Context Aspect | Owner | Lifetime | Persistence |
|:---|:---|:---|:---|
| Goal parameters | `GoalSpec.metadata` dict | Workflow lifetime | None |
| Execution context | `workflow._ctx` dict | Workflow lifetime | None |
| Provenance chain | `workflow._provenance_chain` list | Workflow lifetime | None |
| Research results | In-memory list | Workflow lifetime | Partial (artifacts to disk) |
| Synthesis results | In-memory object | Workflow lifetime | Partial (artifacts to disk) |
| Repair history | `RepairIterationHistory` | Workflow lifetime | None |
| Evidence collected | `workflow.evidence_collected` list | Workflow lifetime | None |
| Agent context | `ContextManager` (disconnected) | Not used | N/A |

### Context Risks

- **Stale context:** After repair, the workflow may still reference pre-repair research results without re-querying.
- **Cross-task contamination:** Evidence from task A may incorrectly influence goal evaluation for task B in the same workflow instance.
- **No context compaction:** The `_provenance_chain` and `evidence_collected` grow unboundedly during a long workflow.
- **No context reconstruction:** After a process crash, the entire context is lost. There is no mechanism to resume mid-workflow.

---

## 9. Recovery Audit

### Process Crash Scenarios

| Failure Scenario | State Surviving | State Lost | Recovery Mechanism |
|:---|:---|:---|:---|
| Process crash during task execution | `execution_attempts` row (PostgreSQL) | PlanStore, ConvergenceState, Evidence, Context | StartupRecoveryEngine scans execution_attempts |
| Process crash after plan commit | Pulse in `pulses` table | PlanStore graph | **None — PlanStore not reconstructable** |
| Worker crash (subprocess) | Attempt record | Worker internal state | Attempt marked CRASHED, recovery proposed |
| Database interruption | Redis publishes buffered | PostgreSQL row | Reconcile_unpublished() re-publishes |
| Redis interruption | PostgreSQL commit | Redis stream entry | Pulse durable; Redis failure logged, not silent |
| Filesystem failure | Pulses, execution_attempts | Artifacts | Artifact re-creation requires re-execution |
| Lease expiration | Lease record expires | None (resource freed) | Resource auto-freed |

### The Cold-Boot Cliff Edge (P0 finding)

`StartupRecoveryEngine.run()` calls `kernel.propose_task_transition()`, which requires `kernel.get_task_graph()` to return a valid TaskGraph. After process restart, `PlanStore._graphs = {}`. The recovery engine scans PostgreSQL for interrupted `execution_attempts`, but cannot reconstruct which TaskGraph they belonged to.

**Result:** Recovery engine will attempt to transition tasks, but the kernel has no plan graph → `KeyError` or `AssertionError` on missing space key → recovery silently fails or crashes.

**Evidence:** `core/plans/plan_store.py:88`: `if space_id not in self._graphs: return 0` (returns 0 version without error).

### Checkpoint Limitations

`SpaceKernel.create_checkpoint()` returns a Python dict. `SoftwareEngineeringWorkflow._save_checkpoint()` stores only the `checkpoint_id` string. The checkpoint dict is **discarded after creation**. There is no persistence layer for checkpoint data.

---

## 10. Replay Audit

### Determinism Analysis

| Component | Deterministic | Basis |
|:---|:---|:---|
| PlanDelta CAS | Yes | Versioned, append-only |
| DeterministicGoalEvaluator | Yes | Same inputs → same output (10x verified in INT-11) |
| ConvergenceEngine decisions | Yes | Same fingerprint history → same decision |
| Worker execution (subprocess) | **No** | Depends on filesystem state, clocks, external tools |
| Research retrieval | **No** | Depends on external URLs, network |
| LLM calls | **No** | Stochastic; recorded via LLMRecorder |
| Pulse ordering | Yes (position-based) | PostgreSQL BIGSERIAL |

### Replay Practical Limits

- **Short workflows (Phase 14.8 scope):** Replay is possible by replaying the pulse log and re-running from checkpoints. However, worker execution (pytest, git operations) is non-deterministic.
- **Long workflows (100+ tasks):** The pulse log grows unboundedly. `PostgresPulseStore.read_by_space()` issues an unbounded `SELECT *` query with no pagination or limit, causing OOM risk.
- **Crashed workflows:** Cannot replay from after crash because PlanStore state is not durable.

### LLM Replay

`LLMRecorder` (Phase 5) records all LLM interactions with deterministic hashes. Replay uses recorded responses. This is UNIT_VERIFIED (test_replay.py, 1 test) but not exercised in Phase 14.8 workflow tests.

---

## 11. Resource Audit

### Current Resource Architecture

| Feature | Status | Evidence |
|:---|:---|:---|
| CPU/GPU/memory quotas | UNIT_VERIFIED | test_manager.py (8) |
| FIFO priority queue | UNIT_VERIFIED | test_queue.py (5) |
| Rate limiting | UNIT_VERIFIED | test_rate_limit.py (2) |
| Lease enforcement | UNIT_VERIFIED | test_lease_enforcement.py (5) |
| Multi-resource scheduling | PARTIAL | Single `ResourceIdentity` per task |
| Multi-space fair sharing | MISSING | No fair-share scheduler across spaces |
| Device grants | UNIT_VERIFIED | test_device_binding.py (4) |
| Starvation detection | MISSING | No starvation detection |
| Deadlock detection | MISSING | No deadlock detection |
| Orphan lease recovery | PARTIAL | StartupRecoveryEngine reconciles leases |

### Concurrency Gaps

`ResourceManager` uses a `threading.RLock` wrapping all resource store operations — correct for multi-threaded access. However, since `execute_task_full_pipeline()` is sequential, the lock is never meaningfully contested. When concurrent task execution is introduced, the `InMemoryResourceStore` locking will be tested.

### Priority Inversion Risk

`ResourcePriorityQueue` assigns priority values. If a low-priority space holds a lease required by a high-priority space, no priority inheritance mechanism exists. This is a latent starvation risk.

---

## 12. Tool Runtime Audit

### MCP Architecture

| Feature | Status | Evidence |
|:---|:---|:---|
| MCP tool registration | IMPLEMENTED | `skills/mcp/server_registry.py` |
| MCP tool invocation | IMPLEMENTED | `workers/mcp/worker.py` |
| MCP sandbox (stdio subprocess) | IMPLEMENTED | ADR-0031 |
| MCP taint marking | IMPLEMENTED | MCP outputs taint=True by default |
| MCP secret handling | PARTIAL | Secrets passed via env, not injected into output |
| MCP replay | MISSING | MCP calls are not recorded for replay |
| MCP cross-space isolation | PARTIAL | Worker is space-scoped but server process is shared |
| Capability authority path | PARTIAL | MCPWorker checks `worker.can_execute()` but no formal admission |

### Tool Runtime Authority Gap

The SCCA Law 2 states: *"Capabilities are requested, never owned."* MCP servers are registered globally in `MCPServerRegistry` and are not scoped to a specific Space. A malicious or failing MCP server could be invoked from any Space. There is no per-Space MCP authorization policy.

---

## 13. Security Composition Audit

### Individual Component Security

All individual components pass their unit-level security tests. Key security mechanisms verified:
- Taint propagation: forward-only, UNIT_VERIFIED
- HMAC approval signatures: INTEGRATION_VERIFIED (channels/tests)
- Secret sanitization: UNIT_VERIFIED (test_secret_sanitization.py)
- Cross-space permission enforcement: UNIT_VERIFIED (INT-12 in Phase 12.7)
- LLM output isolation: UNIT_VERIFIED (test_sanitizer.py, 11 tests)

### Attack Chain Analysis

**Chain 1: Research → Synthesis → Repair → Patch**
```
Tainted research result
  → ResearchSynthesizer (CONFLICT_DETECTED preserved, not fabricated)
  → RepairProposal (taint=True propagated)
  → AtomicPatcher (taint flag on patch_applied pulse)
  → Repository mutation
  → test.executed pulse (taint=True)
  → GoalEvaluator (taint=True; only SATISFIED if allow_taint constraint)
```
**Assessment:** Taint propagates correctly through the chain. A tainted patch CAN reach the repository. The `allow_taint` constraint must be explicitly set to accept tainted evidence as proof of goal satisfaction. **Mitigation is correct but optional** — a misconfigured goal spec without `allow_taint` but with tainted research could produce UNSATISFIED (correct) but the repository change would still be committed.

**Chain 2: Forged Evidence → Goal Satisfaction**
- `VerifiedExecutionEvidence` is a frozen dataclass — immutable after creation
- `DeterministicGoalEvaluator` verifies exit_code, artifact paths from actual evidence
- LLM advisory claims are recorded as passive telemetry, never override deterministic evidence
- **Assessment:** SECURE — verified in test_slice_g_llm_authority_rejection.py

**Chain 3: Artifact Collision → Cross-Space Data Leak**
- Space A writes `artifacts/test_runner/task-test_report.json`
- Space B (concurrent, same base_dir) writes to the same path
- Space B's test runner reads a file and attributes it to its own space
- **Assessment:** VULNERABLE (P0) — no `space_id` in artifact paths

**Chain 4: ConvergenceState Reset After Crash → Infinite Retry**
- Before crash: ConvergenceEngine has tracked 3 RETRY decisions for fingerprint F1
- Process crashes
- After restart: `InMemoryConvergenceStateStore` is empty (retry count = 0)
- Same task fails again → engine proposes RETRY (count = 0, limit not reached)
- **Assessment:** VULNERABLE — in-memory convergence state enables retry bypass after crash

**Chain 5: Unbounded Pulse Load → Memory Exhaustion**
- Long-lived space with 10,000 tasks emits ~5 pulses per task = 50,000 pulses
- `PostgresPulseStore.read_by_space()` issues `SELECT * FROM pulses WHERE space_id = %s`
- 50,000 rows loaded into memory for each `read_by_space()` call
- **Assessment:** VULNERABLE (P1) — no LIMIT, no pagination

---

## 14. Governance Audit

### ADR Synchronization

| Document | State |
|:---|:---|
| ADRs 0001..0044 | PASS — monotonic, no gaps |
| Architecture Sec 16 vs registry | 12 pulse types in registry not documented in Sec 16 |
| contracts/registry/pulse-types.json | 50 types total, 38 in architecture |
| payload-schemas/ | 1:1 coverage PASS (V1-005) |
| CONTRACT_MATRIX.md | Current, 224 contract IDs |
| spec_map.yaml | 182 mappings, V1-001 PASS |
| PROJECT_MEMORY | 26 entries, current through Phase 14.8 |
| RELEASES.md | Current through v1.0.1 |

### Undocumented Behavior

The following behaviors are implemented but not documented in any ADR or architecture section:
1. `repair.loop_iterated` pulse type — emitted but not in Architecture Sec 16
2. `research.conflict_detected` pulse — emitted but not in Architecture Sec 16
3. `task.interrupted_detected` — emitted during recovery but not in Architecture Sec 16
4. `lease.reconciled` — emitted during startup recovery but not documented

These are additions from Phases 12–14 that predate formal Architecture Sec 16 updates.

### Lint Debt (Test Files)

`ruff check` reports 22 errors in `memory/tests/` (unused imports, unused variables in test files). These are **test-only** issues that do not affect production behavior. All are auto-fixable. Classification: P3 — documentation debt.

### Test Flakiness

`test_slice_c_failure_then_repair` and `test_slice_d_repair_ceiling` exhibit **order-dependent flakiness** when run as a full suite (test_slice_c fails when run with test_slice_d before it, passes alone). This indicates shared mutable state or filesystem interaction between tests.

**Evidence:** Running `test_slice_c` alone: PASS. Running full suite: `test_slice_c` FAILS (`ESCALATED` instead of `COMPLETED`). Running `test_slice_d` alone: PASS.

**Root cause hypothesis:** `test_slice_d` leaves a `calc.py` file in a modified state in the shared `tmp_path` root, or the repair history state from `test_slice_d` bleeds into `test_slice_c` if they share a kernel/space_id. Classification: P2 — may mask genuine repair loop bugs.

---

## 15. Findings

| ID | Severity | Evidence | Current Behavior | Required Behavior | Architectural Consequence |
|:---|:---|:---|:---|:---|:---|
| **F-01** | **P0** | `core/plans/plan_store.py:43` `_graphs: dict` | PlanStore is purely in-memory; all plan history lost on restart | PlanStore must have a durable backend (PostgreSQL or equivalent) allowing cold-boot plan reconstruction | StartupRecoveryEngine cannot recover tasks after process restart; crash recovery is effectively non-functional for plan-level state |
| **F-02** | **P0** | `workers/repository/worker.py:349`, `workers/test_runner/worker.py:245`, `workers/research/worker.py:405` | Artifact paths omit `space_id`; concurrent spaces with shared base_dir collide | Artifact paths must include `space_id` namespace | Cross-space data leakage and artifact corruption under any concurrent workload |
| **F-03** | **P1** | `core/pulse_bus/store.py:136` | `read_by_space()` issues `SELECT * FROM pulses WHERE space_id = %s` with no LIMIT | Paginated, bounded retrieval with configurable LIMIT | Memory exhaustion on long-lived spaces with many pulses |
| **F-04** | **P1** | `core/orchestrator/dispatch_model.py:2401` | `execute_task_full_pipeline()` is a blocking, single-task call; no concurrent DAG scheduling | Independent DAG branches should execute concurrently | System executes as a single-threaded workflow executor rather than a concurrent runtime |
| **F-05** | **P1** | `memory/adapters/postgres.py:163` | `query_similar_experiences()` retrieves only the N most recent records, then applies in-memory keyword filter | Full-history relevance retrieval (embedding-based or indexed full-text search) | Adaptation degrades silently as experience history grows; old relevant failures are ignored |
| **F-06** | **P1** | `InMemoryConvergenceStateStore` | Convergence state (retry counts, fingerprint history) is in-memory | Convergence state must be durable or reconstructable from pulse/execution history | Process crash resets retry counts; same failing task can be retried infinitely across restarts, bypassing repair ceiling |
| **F-07** | **P2** | `agents/base.py`, `agents/context.py`, `core/orchestrator/team_builder.py` | Agent, ContextManager, TeamBuilder are implemented but not connected to the execution path | SoftwareEngineeringWorkflow should route through Agent → Worker, not Workflow → Worker directly | SCCA execution hierarchy (Space → Plan → Team → Agent → Worker → Skill) is not enforced; architecture is incomplete |
| **F-08** | **P2** | 12 pulse types in registry not in Architecture Sec 16 | Pulse types emitted in Phases 12–14 were not backfilled into the architecture document | Architecture Sec 16 must enumerate all 50 pulse types | Governance drift: `contract_sync.py` issues warnings that are not treated as failures |
| **F-09** | **P2** | `test_slice_c_failure_then_repair` | Test passes in isolation, fails in full suite (ESCALATED instead of COMPLETED) | Tests must be fully isolated with no inter-test state leakage | Potential masking of genuine repair ceiling bugs; test suite is unreliable under full execution |
| **F-10** | **P2** | `skills/mcp/server_registry.py` | MCP servers registered globally, not per-Space | MCP server access must be Space-scoped with per-Space authorization | Violates SCCA Law 2 (capabilities are requested, never owned); cross-space MCP invocation is architecturally possible |
| **F-11** | **P3** | `memory/adapters/neo4j_stub.py`, `memory/adapters/qdrant_stub.py` | Both raise `NotImplementedError` on all methods | Implement or formally remove stubs | Dead code that creates false impression of semantic memory capability |
| **F-12** | **P3** | `ruff check` — 22 errors in `memory/tests/` | Unused imports and variables in test files | Clean up unused imports | Minor code quality issue in test files only |
| **F-13** | **P3** | pyproject.toml `requires-python = ">=3.12"` vs runtime Python 3.11.9 | Python 3.11.9 is being used despite `>=3.12` requirement | Align runtime Python version with declared requirement | Silent compatibility risk; some Python 3.12+ features may not be available |

---

## 16. Bottleneck Analysis

### The Core Bottleneck: **Durable State & Space-Safe Concurrent Execution**

After exhaustive audit, the primary architectural bottleneck preventing RYU from operating as a **durable, multi-task, multi-agent cognitive runtime** is the combination of:

**1. Non-Durable Plan State (F-01 — P0)**
RYU currently cannot survive a process restart while preserving plan execution context. The PlanStore, ConvergenceState, GoalSpec, and Evidence are all in-memory. This makes RYU a **bounded, single-process, single-workflow system** rather than a durable runtime. No amount of feature expansion (multi-agent, vector memory, MCP hardening) makes architectural sense until the system can survive process failure.

**2. Unpartitioned Artifact Namespace (F-02 — P0)**
Multi-space concurrent execution is architecturally impossible without space-scoped artifact isolation. Any attempt to run two Spaces concurrently will produce corrupted evidence and incorrect goal evaluation.

**3. Unbounded State Growth (F-03, F-05, F-06 — P1)**
Three subsystems grow without bounds: the Pulse store (no pagination), the experience retrieval (recency-only), and the convergence state (in-memory, reset on crash). These prevent long-lived execution even within a single process.

**4. Sequential Execution Only (F-04 — P1)**
The dispatcher is single-task. This is not an optimization gap — it is an architectural constraint that must be resolved before multi-agent coordination (F-07) can be meaningfully implemented.

### Why NOT the Agent Hierarchy First?

F-07 (disconnected Agent hierarchy) is P2, not P0. The Agent/Worker/Skill hierarchy is contracted, implemented, and unit-verified. The reason it is not the primary bottleneck is: **connecting agents to the workflow before making the runtime durable would produce a durable agent that cannot survive crashes, cannot run concurrently, and cannot isolate artifacts between spaces.** The foundation must be fixed first.

### Why NOT Semantic Memory First?

Semantic memory (vector/graph retrieval) requires a durable experience store to be meaningful. If experiences are lost after a process restart, investing in semantic retrieval infrastructure provides no durable benefit.

### Bottleneck Priority Ordering

```
1. Durable PlanStore (F-01) — prerequisite for everything else
2. Space-scoped artifact isolation (F-02) — prerequisite for concurrent execution
3. Bounded Pulse retrieval (F-03) — prerequisite for long-lived Spaces
4. Durable ConvergenceState (F-06) — prerequisite for reliable repair ceiling
5. Concurrent DAG Scheduler (F-04) — prerequisite for multi-task efficiency
6. Full-history experience retrieval (F-05) — prerequisite for meaningful adaptation
7. Agent hierarchy integration (F-07) — enabled by all of the above
```

---

## 17. Candidate Phase 15 Implementation Scope

Based on the audit, Phase 15 implementation should address **exactly the P0 and critical P1 findings** in priority order.

### Phase 15 Implementation Target

**Title:** Durable Runtime State & Space-Safe Concurrent Execution Foundation

**Scope (in order of execution):**

**15.1 — Durable PlanStore (P0)**
- Implement `PostgresPlanStore` as a durable alternative to the in-memory `PlanStore`
- Schema: `plans` table (space_id, plan_version, graph_json, created_at) and `plan_history` table
- `PlanStore.commit_delta()` persists the graph snapshot to PostgreSQL before returning
- `StartupRecoveryEngine` can reconstruct TaskGraph from `plans` table on cold boot
- New ADR required: `0045-durable-plan-store.md`

**15.2 — Space-Scoped Artifact Isolation (P0)**
- Modify `workers/repository/worker.py`, `workers/test_runner/worker.py`, `workers/research/worker.py`
- Change artifact base path to: `{base_working_dir}/artifacts/{space_id}/{worker_type}/{task_id}_*`
- No contract changes required; purely implementation fix
- Update Phase 14 tests that construct expected artifact paths

**15.3 — Bounded Pulse Retrieval (P1)**
- Add `limit: int = 1000` and `from_position: int = 0` parameters to `PostgresPulseStore.read_by_space()`
- Add a pulse retention/archival policy configuration (default: no archival, configurable)
- Update `PulseStore` Protocol and `InMemoryPulseStore` to match

**15.4 — Durable ConvergenceState (P1)**
- Implement `PostgresConvergenceStateStore`
- Schema: `convergence_state` table (space_id, fingerprint, retry_count, last_decision, updated_at)
- `ConvergenceEngine` reads/writes convergence state durably
- New ADR: covered in `0045-durable-plan-store.md` or separate `0046`

**15.5 — Architecture Sec 16 Backfill (P2/Governance)**
- Document all 50 pulse types in Architecture Sec 16
- Update `contract_sync.py` to treat undocumented pulse types as a WARNING (not silent pass)

**Non-scope for Phase 15:**
- Concurrent DAG Scheduler (requires durable PlanStore as foundation — Phase 16)
- Agent hierarchy integration (requires durable runtime — Phase 16+)
- Semantic memory / embeddings (requires durable experience foundation — Phase 16+)

---

## 18. Explicit Non-Goals

The following are explicitly **not** in scope for Phase 15:

1. **Concurrent DAG Scheduler** — requires durable PlanStore as prerequisite
2. **Multi-Agent Coordination** — requires durable runtime and concurrent scheduler
3. **Semantic Memory / Vector Embeddings** — Neo4j, Qdrant implementation deferred
4. **Distributed / Multi-Node Execution** — requires concurrent scheduler first
5. **LLM Integration Expansion** — no new LLM features until runtime is durable
6. **Browser Automation or Voice** — not architectural priorities
7. **Channel Daemon UI Enhancements** — not a runtime bottleneck
8. **Kafka / external message queues** — Redis Streams already provides durable transport
9. **Kubernetes or cloud infrastructure** — single-node execution is sufficient for current phase
10. **Redesigning the Authority Model** — existing authority boundaries are correct; only persistence needs to change
11. **New Pulse Types** — no new contracts until existing pulse types are architecture-documented

---

## Verification Evidence Summary

| Check | Result | Command |
|:---|:---|:---|
| Core boundary (dep_guard) | **PASS** | `scripts/dep_guard.py` |
| Contract sync | **PASS** (38/38 arch types in registry; 12 extra noted) | `scripts/contract_sync.py` |
| Spec coverage (V1-001) | **PASS** (182 mappings) | `scripts/v1_audit_spec_coverage.py` |
| Governance hygiene (V1-005) | **PASS** | `scripts/v1_audit_governance.py` |
| Core unit tests | **PASS** (all core/ tests) | `pytest core -q` |
| Workers unit tests | **PASS** (359 tests, 1 skipped) | `pytest workers -q` |
| Agents / memory / harness | **PASS** (86 tests) | `pytest agents memory harness/cases/pulse_bus harness/cases/space -q` |
| Phase 14 harness | **PASS** (8 tests) | `pytest harness/cases/phase14 -q` |
| Phase 14.8 workflow | **38/39 PASS** (1 order-dependent flake, passes in isolation) | `pytest workflows/tests/ -q` |
| Ruff lint | 22 warnings in memory/tests/ only (test files) | `ruff check memory/tests/` |
| Total tests collected | **1,003 tests** | `pytest --collect-only -q` |

---

## Final Decision

> **PHASE 15 AUDIT REQUIRES ARCHITECTURAL REMEDIATION**

The audit identifies two P0 architectural blockers that must be resolved before Phase 15 feature expansion:

1. **F-01 (P0): Non-Durable PlanStore** — process restart loses all plan execution context, making crash recovery non-functional for plan-level state
2. **F-02 (P0): Unpartitioned Artifact Namespace** — concurrent multi-space execution produces artifact collisions and cross-space data leakage

Additionally, three P1 gaps (unbounded Pulse retrieval, sequential-only execution, in-memory ConvergenceState) must be resolved to support long-lived, multi-task operation.

Phase 15 implementation must begin with **15.1 Durable PlanStore** and **15.2 Space-Scoped Artifact Isolation** before any other capability expansion is started.

**Phase 15 implementation is conditionally ready to begin upon confirmation of this audit's scope and ADR authorization.**
