# ADR-0043: Closed-Loop Experiential Adaptation and Memory-Guided Plan Convergence

**Status:** Accepted  
**Date:** 2026-09-30  
**Authors:** Antigravity Engineering Agent & Human Engineer  
**Supersedes:** None  
**Related ADRs:** ADR-0033 (Memory Inversion), ADR-0034 (Experience Reflection), ADR-0035 (Promotion Gate), ADR-0036 (Adaptation Boundary), ADR-0041 (Execution Engine), ADR-0042 (Crash Recovery)  
**Governing Laws:** Six Immutable SCCA Laws, `AGENTS.md §7` (Deterministic Core Independence)  

---

## 1. Context and Problem Statement

The RYU AI framework possesses a verified autonomous execution engine (Phases 12.1–12.7) with durable crash recovery (Phase 12.8) and long-term memory primitives (Phase 10: `Reflector`, `SpaceMemoryProtocol`, `AdaptationLayer`, `PromotionPipeline`).

However, as formally audited in `docs/PHASE_13_POST_EXECUTION_ENGINE_ARCHITECTURE_AUDIT.md`, a critical structural disconnect exists (DEBT-01):
1. **The execution engine is isolated from memory:** When `DeterministicDispatcher` completes or fails a task, it never calls `Reflector.reflect()` to create structured experiences.
2. **The convergence engine is isolated from adaptation:** When `ConvergenceEngine` replans a failed task or unsatisfied goal, it never queries `AdaptationLayer.generate_hints()`.
3. **Execution is amnesic regarding behavioral improvement:** The system retries and replans blindly using structural heuristics rather than learning from its own verified historical execution outcomes.

To close this loop, the execution engine must inform memory of verified execution outcomes, and the convergence engine must consume advisory adaptation hints. However, this must be achieved **without violating core independence (`AGENTS.md §7`)** and **without allowing memory to usurp execution authority (SCCA Law 5)**.

---

## 2. Decision

We establish a closed-loop experiential adaptation architecture governed by the following architectural invariants:

### 2.1 Protocol Inversion & Dependency Direction (`AGENTS.md §7`)
The deterministic core (`core/`) defines the abstract interaction contracts. The concrete memory subsystem (`memory/`) implements them. Concrete memory classes are injected at runtime via composition root.
- `core/space/memory_protocol.py` defines:
  - `TaskExecutionOutcome`: A core-neutral, verified outcome dataclass reported by the Dispatcher.
  - `ExperienceObserverProtocol`: The abstract observer interface receiving task outcomes.
  - `AdaptationLayerProtocol`: The abstract query interface providing advisory experience hints.
- `DeterministicDispatcher` and `ConvergenceEngine` in `core/orchestrator/` **MUST NOT** directly import `memory.reflector`, `memory.promotion`, `memory.adapters`, or any concrete memory module.
- `scripts/dep_guard.py` enforces zero forbidden imports into `core/`.

### 2.2 Core-Neutral Experience Capture
The Dispatcher does **not** construct memory-domain `ExperienceRecord`s. It reports what happened:
```text
Dispatcher ──> TaskExecutionOutcome ──> ExperienceObserverProtocol ──> Reflector ──> ExperienceRecord
```
The Dispatcher is responsible for execution verification. The memory subsystem (`ExecutionExperienceObserver` / `Reflector`) is responsible for reflection, secret sanitization, counterfactual synthesis, and persistence.

### 2.3 Memory is Advisory, Never Authority
Memory recommendations, historical outcomes, and adaptation hints are strictly advisory:
- An adaptation hint **CANNOT** directly mutate a `TaskGraph`, commit a `Plan`, execute a worker, or modify permissions.
- The mutation pathway remains:
  ```text
  Adaptation Hint ──> ConvergenceProposal ──> PlanDelta ──> SpaceKernel CAS ──> Plan v+1 ──> Execution
  ```
- If a proposal fails SpaceKernel CAS or Admission Control, it is deterministically rejected.

### 2.4 Unbreakable Anti-Runaway Budgets & Loop Guards
- Adaptation hints cannot reset, increase, or bypass `MAX_RETRY_BUDGET=3` or `MAX_REPLAN_BUDGET=3`.
- Failure fingerprinting (`_compute_failure_fingerprint`) remains authoritative: if the same failure fingerprint repeats, the engine immediately transitions to `ConvergenceDecision.ESCALATE` to prevent infinite loops, regardless of memory recommendations.

### 2.5 Strict Space Isolation & Controlled Promotion (SCCA Law 1 & Law 4)
- Memory queries are Space-local by default. Space A cannot query Space B's experiences.
- Cross-space knowledge is accessible only if formally promoted via `PromotionPipeline` into `global_knowledge` with human approval (`MEM-005`, `MEM-006`). Even promoted knowledge remains advisory.

### 2.6 Bounded Counterfactual Reasoning
When strategy A fails, the `AdaptationLayer` queries past experiences where strategy B succeeded under matching situational constraints. The resulting hint carries:
- `source_experience_id`: Provenance tracing back to the original experience.
- `counterfactual_summary`: Actionable lesson learned.
- `relevance_score`: Bounded confidence metric [0.0..1.0].
The proposal makes clear that this is an inferred recommendation, not an authoritative guarantee.

### 2.7 Replay & Durability Compatibility
- When replaying pulse streams (`replay_mode=True`), the observer avoids duplicate experience persistence.
- Memory queries sort results deterministically by `(relevance_score, stored_at)` to preserve replay equivalence (`V1-006`).
- All experiences are persisted to PostgreSQL `space_experiences` before pulses are emitted, guaranteeing restart safety.

---

## 3. Consequences

### Positive Consequences
1. **Closed-Loop Adaptation:** RYU systematically learns from real execution outcomes without model fine-tuning or weight modification.
2. **Preserved Core Independence:** Zero concrete memory imports in `core/`. Core functions fully even if `experience_observer=None` or `adaptation_layer=None`.
3. **Provable Safety:** Memory cannot become a rogue authority layer. All changes require formal `PlanDelta` committed via `SpaceKernel` atomic CAS.
4. **Inspectable Provenance:** Every adaptation proposal retains complete traceability to the historical experiences that informed it.

### Negative Consequences / Trade-Offs
1. **Indirection:** Adding protocol layers (`ExperienceObserverProtocol`) requires composition wiring at runtime.
2. **Advisory Latency:** Querying historical experiences during replan adds a minor lookup overhead (bounded by `limit=5`).

---

## 4. Rejected Alternatives

1. **Direct Core-to-Memory Coupling:** Allowing `DeterministicDispatcher` to import `Reflector` directly. Rejected: Violates `AGENTS.md §7` and breaks deterministic core independence.
2. **Autonomous Self-Modification:** Allowing adaptation hints to directly append nodes to `TaskGraph`. Rejected: Violates SCCA Law 1, Law 5, and Plan CAS authority.
3. **Unbounded Retries Based on Memory:** Allowing memory to suggest "Try 10 more times". Rejected: Violates anti-runaway bounding contracts (`CONV-002`).
4. **Global Shared Memory Without Gates:** Making all Space experiences immediately visible across all Spaces. Rejected: Violates SCCA Law 1 & Law 4 (Space isolation).

---

## 5. Non-Goals

- Unrestricted self-modifying code or architecture rewriting.
- Autonomous permission escalation or secret exposure.
- Direct memory-to-kernel state mutation.
- Live autonomous web research or multimodal learning.
- Changing `PromotionPipeline` HMAC authorization semantics.
