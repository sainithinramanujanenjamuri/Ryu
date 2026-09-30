# RYU AI — Phase 13 Architecture & Implementation Plan
**Milestone:** Phase 13 — Closed-Loop Experiential Adaptation & Memory-Guided Execution  
**Predecessor Baseline:** Phase 12.8 Crash Recovery & Durable Execution State (`af69667`)  
**Governing Architecture:** Space-Centric Cognitive Architecture (SCCA)  
**Governing Rules:** SCCA Laws 1–6, `AGENTS.md §7` (Deterministic Core Independence)  

---

## 1. Current Memory Interfaces

### 1.1 Core Memory Protocol (`core/space/memory_protocol.py`)
- **`SpaceMemoryProtocol`**: Defines read/write contracts (`store_experience`, `get_experience`, `list_experiences`, `query_similar_experiences`, `store_knowledge`, `get_global_knowledge`).
- **`ExperienceRecord`**: Immutable record of past execution (`experience_id`, `space_id`, `situation`, `action`, `outcome`, `counterfactual`, `applicable_context`, `stored_at`). Enforces mandatory `counterfactual` via `__post_init__` (`MEM-002`).
- **`KnowledgeEntry`**: Promoted global knowledge record (`knowledge_id`, `source_space_id`, `content`, `promoted_by`, `promotion_pulse_id`, `global_version`, `promoted_at`).
- **`PromotionAuthorization`**: Cryptographic HMAC-SHA256 capability token (`promotion_id`, `knowledge_id`, `source_space_id`, `approver_id`, `approval_request_id`, `signature`, `issued_at`). Verified via `verify_promotion_authorization`.

### 1.2 Memory Subsystem Implementation (`memory/`)
- **`Reflector` (`memory/reflector.py`)**: Accepts raw task execution metrics, constructs `ExperienceRecord`, validates `counterfactual`, persists to `SpaceMemoryProtocol`, and emits `experience.stored` and `memory.updated` pulses.
- **`PromotionPipeline` (`memory/promotion.py`)**: Enforces cryptographic promotion gate (`MEM-005`, `MEM-006`) integrating `EvaluationModule` frozen-trace benchmarks and `ApprovalManager` human gate.
- **Adapters (`memory/adapters/`)**:
  - `PostgreSQLMemoryAdapter`: Production storage backing `space_experiences`, `global_knowledge`, and `promotion_audit` tables.
  - `InMemoryMemoryAdapter`: Thread-safe, hermetic storage for unit testing.
  - `Neo4jAdapterStub` & `QdrantAdapterStub`: Stubs for Phase 11+ graph/vector capabilities.

### 1.3 Adaptation Layer (`core/memory/adaptation.py`)
- **`AdaptationLayer`**: Read-only query layer (`MEM-004`, `ADR-0036`). Accepts `space_id` and `situation_hint`, queries `SpaceMemoryProtocol`, and filters past experiences to produce advisory `ExperienceHint`s.
- **`ExperienceHint`**: Advisory dataclass (`experience_id`, `failed_capability`, `suggested_avoidance`, `outcome_summary`, `counterfactual_summary`, `relevance_score`).

---

## 2. Current Execution Interfaces (`core/orchestrator/dispatch_model.py`)

- **`DeterministicDispatcher`**: Evaluates DAG topological readiness, coordinates admission control, resource leases, and worker execution. Records execution attempts in `ExecutionAttemptStore`.
- **`observe_and_evaluate_task()`**: Observes worker results, verifies cryptographic evidence (`EvidenceVerifier`), transitions task state via `SpaceKernel.propose_task_transition()` CAS (`OBSERVING -> EVALUATING -> COMPLETED / FAILED`), unblocks/blocks downstream DAG tasks, and returns `TaskCompletionResult`.
- **`DeterministicGoalEvaluator`**: Evaluates verified evidence against `GoalSpec` constraints, returning `GoalEvaluationResult` (`SATISFIED`, `UNSATISFIED`, `INCONCLUSIVE`).
- **`ConvergenceEngine`**: Evaluates goal verdicts and task failures, enforcing `MAX_RETRY_BUDGET=3`, `MAX_REPLAN_BUDGET=3`, and SHA-256 failure fingerprint loop guards. Produces immutable `ConvergenceProposal` (`CONTINUE`, `RETRY`, `REPLAN`, `ESCALATE`, `ABORT`).
- **`PlanReconciler`**: Reconciles replan proposals via bounded rebase (`commit_delta_with_rebase()`) and commits `PlanDelta` to `SpaceKernel` via CAS.

---

## 3. Dependency Graph: Current vs Target

### Current Disconnected Architecture (DEBT-01)
```text
[DeterministicDispatcher] ──> [Task Completion] ──> [Unblocks DAG]
                                                         │
                                                  (NO CONNECTION)
                                                         ▼
[Reflector] ──> [SpaceMemory] ──> [AdaptationLayer] ──> (UNUSED) ──> [ConvergenceEngine]
```

### Target Architecture (Phase 13 Dependency-Inverted Composition)
```text
┌─────────────────────────────────────── core/ ────────────────────────────────────────┐
│                                                                                      │
│   DeterministicDispatcher                                                            │
│             │                                                                        │
│             │ reports TaskExecutionOutcome                                           │
│             ▼                                                                        │
│   ExperienceObserverProtocol (core protocol) ◄──────┐                                │
│                                                     │                                │
│   ConvergenceEngine                                 │                                │
│             ▲                                       │                                │
│             │ queries advisory hints                │                                │
│             │                                       │                                │
│   AdaptationLayerProtocol (core protocol) ◄────┐    │                                │
│                                                │    │                                │
└────────────────────────────────────────────────┼────┼────────────────────────────────┘
                                                 │    │
┌─────────────────────────────── runtime/ ───────┼────┼────────────────────────────────┐
│   Runtime Composition Root                     │    │                                │
│   (wires protocols to concrete components)     │    │                                │
└────────────────────────────────────────────────┼────┼────────────────────────────────┘
                                                 │    │
┌────────────────────────────────────── memory/ ─┼────┼────────────────────────────────┐
│                                                │    │                                │
│   ExecutionExperienceObserver ─────────────────┼────┘ (implements observer)          │
│             │                                  │                                     │
│             ▼                                  │                                     │
│         Reflector                              │                                     │
│             │                                  │                                     │
│             ▼                                  │                                     │
│     SpaceMemoryStore                           │                                     │
│             │                                  │                                     │
│             ▼                                  │                                     │
│      AdaptationLayer ──────────────────────────┘ (implements adaptation protocol)    │
│                                                                                      │
└──────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 4. Proposed Protocol Boundaries (`core/space/memory_protocol.py`)

### 4.1 `TaskExecutionOutcome` (Core-Neutral Outcome Dataclass)
The Dispatcher does **not** construct memory-domain `ExperienceRecord`s. It simply reports the verified execution outcome:

```python
@dataclass(frozen=True)
class TaskExecutionOutcome:
    """Core-neutral, verified execution outcome reported by the Dispatcher (ADAPT-001)."""
    task_id: str
    space_id: str
    plan_version: int
    capability: str
    params: dict[str, Any]
    status: str                          # 'completed' | 'failed'
    exit_code: int | None
    duration_seconds: float
    error_class: str | None
    error_message: str | None
    failure_fingerprint: str | None
    result_ref: str | None
    artifact_refs: tuple[str, ...]
    dependencies: tuple[str, ...]
    taint: bool
    completed_at: datetime
```

### 4.2 `ExperienceObserverProtocol` (Core Protocol)
```python
@runtime_checkable
class ExperienceObserverProtocol(Protocol):
    """Protocol for recording execution outcomes into experience without core-to-memory coupling."""
    def observe_task_outcome(self, outcome: TaskExecutionOutcome) -> str | None:
        """Observe task outcome, reflect, persist, and return experience_id or None."""
        ...
```

### 4.3 `AdaptationLayerProtocol` (Core Protocol)
```python
@runtime_checkable
class AdaptationLayerProtocol(Protocol):
    """Protocol for querying advisory adaptation hints without coupling core to memory internals."""
    def generate_hints(
        self,
        space_id: str,
        situation_hint: dict[str, Any],
        limit: int = 5,
    ) -> list[ExperienceHint]:
        """Generate advisory experience hints strictly within the specified space."""
        ...
```

---

## 5. Composition Root & Concrete Bridges (`memory/experience_observer.py`)

To keep `core/` completely decoupled from `memory/`:
- **`ExecutionExperienceObserver`** lives in `memory/experience_observer.py`.
- It implements `ExperienceObserverProtocol`.
- It receives `TaskExecutionOutcome`, applies secret sanitization, synthesizes a structured `counterfactual`, calls `Reflector.reflect()`, and persists the experience.
- The runtime / orchestrator / test harness injects `ExecutionExperienceObserver` into `DeterministicDispatcher(experience_observer=...)`.
- `DeterministicDispatcher` has **zero** imports from `memory/`.

---

## 6. Data Flow

```text
1. Worker completes execution
       ↓
2. Dispatcher verifies cryptographic evidence & exit code
       ↓
3. Dispatcher transitions task state via SpaceKernel CAS
       ↓
4. Dispatcher constructs TaskExecutionOutcome (core-neutral)
       ↓
5. Dispatcher calls experience_observer.observe_task_outcome(outcome)
       ↓
6. ExecutionExperienceObserver sanitizes secrets & constructs counterfactual
       ↓
7. Reflector.reflect() commits ExperienceRecord to PostgreSQL space_experiences
       ↓
8. Reflector emits experience.stored & memory.updated pulses
       ↓
9. Subsequent failure or replan occurs in ConvergenceEngine
       ↓
10. ConvergenceEngine calls adaptation_layer.generate_hints(space_id, hint_query)
       ↓
11. AdaptationLayer queries Space-local experiences (provenance-preserved)
       ↓
12. ConvergenceEngine inspects advisory hints for counterfactual recommendations
       ↓
13. ConvergenceEngine constructs ConvergenceProposal (carrying PlanDelta)
       ↓
14. SpaceKernel commits PlanDelta via CAS (Plan v+1)
       ↓
15. Revised task DAG dispatched with adapted capability / parameters
```

---

## 7. Authority Flow & Invariants

```text
    ADVISORY                                              AUTHORITATIVE
┌──────────────────────┐                             ┌──────────────────────┐
│  AdaptationLayer     │ ──── advisory hints ─────>  │  ConvergenceEngine   │
│  (Read-only query)   │                             │  (Decision logic)    │
└──────────────────────┘                             └──────────┬───────────┘
                                                                │ Proposal
                                                                ▼
                                                     ┌──────────────────────┐
                                                     │     PlanDelta        │
                                                     └──────────┬───────────┘
                                                                │ CAS commit
                                                                ▼
                                                     ┌──────────────────────┐
                                                     │    SpaceKernel       │
                                                     │ (Sole Plan Authority)│
                                                     └──────────────────────┘
```

1. **Memory is Advisory, Never Authority:** A retrieved memory or hint cannot mutate plans, grant capabilities, or execute workers.
2. **Authority Remains in SpaceKernel:** Only `SpaceKernel.commit_plan_delta()` commits a new plan version via atomic CAS.
3. **Budget Caps are Unbreakable:** `MAX_RETRY_BUDGET=3` and `MAX_REPLAN_BUDGET=3` are strictly enforced. A memory hint saying "Retry 5 times" is ignored.
4. **Loop Guards are Unbreakable:** Repeated failures matching SHA-256 fingerprints trigger deterministic `ESCALATE` regardless of memory recommendations.

---

## 8. Persistence Flow

- **Authoritative Database**: PostgreSQL remains the single source of truth.
- **Table `space_experiences`**: Stores structured `ExperienceRecord`s (Migration 005).
- **Table `global_knowledge`**: Stores promoted cross-space knowledge (Migration 005).
- **Table `execution_attempts`**: Stores execution attempts (Migration 006).
- **Table `convergence_state`**: Stores retry/replan counters and failure fingerprints (Migration 006).
- **No Duplicate Databases**: Phase 13 creates zero duplicate storage systems.

---

## 9. Security Boundaries

- **Space Isolation**: All memory queries are filtered by `space_id`. Cross-space queries raise `SpaceIsolationViolation`.
- **Promotion Gate**: Cross-space knowledge requires `PromotionAuthorization` signed by the Human Gate.
- **Secret Sanitization**: Secrets and credentials are stripped from `TaskExecutionOutcome` before reflection.
- **Taint Containment**: Tainted execution produces tainted experiences (`taint=True`), preventing them from un-tainting downstream plans.

---

## 10. Replay Implications

- Replay mode (`replay_mode=True`):
  - When replaying historical pulse streams, `observe_task_outcome` degrades to a no-op or verification-only mode to prevent duplicate experience insertion.
  - `AdaptationLayer` queries return deterministic results by sorting by `(relevance_score, stored_at)`.
  - Replay equivalence is preserved (`V1-006`).

---

## 11. Crash / Restart Implications

- Builds directly upon Phase 12.8 crash recovery:
  - If a process crashes after execution but before reflection, `StartupRecoveryEngine` detects the interrupted attempt and recovers cleanly.
  - If a process crashes after experience persistence, the record is durable in PostgreSQL and survives restart.
  - If a process crashes during replan, durable `convergence_state` prevents retry/replan counter resets.

---

## 12. Required Contracts

| ID | Title | Summary Invariant |
|:---|:---|:---|
| **ADAPT-001** | Real-Time Experience Observation | Dispatcher reports verified task outcomes through protocol without core-to-memory coupling; Reflector persists structured experiences. |
| **ADAPT-002** | Read-Only Contextual Adaptation Hints | AdaptationLayer produces advisory `ExperienceHint`s filtered by Space isolation; cannot directly mutate plans. |
| **ADAPT-003** | Memory-Guided Bounded Convergence | ConvergenceEngine consumes adaptation hints to formulate bounded replan proposals; budgets and loop guards remain authoritative. |
| **ADAPT-004** | Experience Provenance & Anti-Tampering | Every adaptation hint and proposal preserves provenance back to source experience ID, space ID, and evidence refs. |
| **ADAPT-005** | Controlled Cross-Space Adaptation | Cross-space hints require authenticated promotion through `PromotionPipeline` and Human Gate. |

---

## 13. Required Pulses

No new pulse types are strictly necessary because existing pulses fully represent the lifecycle:
- `experience.stored`: Emitted when an experience is persisted.
- `memory.updated`: Emitted when space memory is updated.
- `plan.delta`: Emitted when a replan is committed.
- `task.completed` / `task.failed`: Emitted on task lifecycle changes.

However, to provide full runtime inspectability of adaptation queries without violating core independence, we register:
- **`adaptation.hint_generated`** (optional telemetry pulse in `contracts/registry/pulse-types.json`).

---

## 14. Required Tests (`core/orchestrator/tests/test_phase13_experiential_adaptation.py`)

- **Group A (Core Independence)**:
  - Dispatcher executes cleanly with `experience_observer=None`.
  - ConvergenceEngine evaluates cleanly with `adaptation_layer=None`.
  - Fake observer receives `TaskExecutionOutcome` without importing `memory/`.
- **Group B (Experience Capture)**:
  - Successful task generates structured experience.
  - Failed task generates structured failure experience with counterfactual.
  - Secrets are sanitized from experiences.
- **Group C (Adaptation Guidance)**:
  - Failed strategy avoided; successful alternative recommended.
  - Counterfactual recommendation injected into proposal.
  - Irrelevant and stale experiences filtered out.
- **Group D (Convergence Integration)**:
  - Hint incorporated into `ConvergenceProposal`.
  - Proposal applied via `SpaceKernel` CAS.
  - Budget ceilings (`MAX_RETRY_BUDGET=3`) enforced despite hints.
  - SHA-256 fingerprint loop detection triggers `ESCALATE` despite hints.
- **Group E (Adversarial Security — MEM-ADV-01..10)**:
  - MEM-ADV-01: Malicious memory claims task success when evidence fails.
  - MEM-ADV-02: Memory proposes unauthorized capability (rejected by Admission Control).
  - MEM-ADV-03: Memory contains prompt injection (treated as untrusted data).
  - MEM-ADV-04: Memory references another Space (rejected by Space isolation).
  - MEM-ADV-05: Memory attempts to bypass human approval (approval boundary intact).
  - MEM-ADV-06: Memory attempts to reset retry budget (ignored).
  - MEM-ADV-07: Memory attempts to reset replan budget (ignored).
  - MEM-ADV-08: Memory attempts direct kernel mutation (rejected by protocol).
  - MEM-ADV-09: Memory contains secret material (sanitized/blocked).
  - MEM-ADV-10: Memory proposes strategy contradicted by verified evidence (evidence wins).
- **Group F (End-to-End Vertical Slices)**:
  - Success slice: Strategy A fails $\rightarrow$ Reflection $\rightarrow$ Hint recommends Strategy B $\rightarrow$ Replan CAS $\rightarrow$ Strategy B executes and succeeds $\rightarrow$ Goal SATISFIED.
  - Rejection slice: Advisory proposal rejected by SpaceKernel CAS $\rightarrow$ Escalates to human.
  - Cross-space slice: Space A experience $\rightarrow$ PromotionPipeline $\rightarrow$ Space B uses promoted hint.

---

## 15. Non-Goals

1. **No Unrestricted Self-Modification**: No autonomous code editing of core components.
2. **No Memory-to-Kernel Authority**: Memory remains 100% advisory.
3. **No Direct Core Imports of Memory**: `core/` remains strictly independent.
4. **No LLM in Core**: Core remains 100% deterministic.
5. **No Second Architecture**: No bypass channels or duplicate dispatchers.
