# ADR-0008: Orchestrator Decomposition, Plan Reconciler Loop, and Non-Authority Invariants

- **Status:** Accepted
- **Date:** 2026-09-19
- **Scope:** Space Orchestrator, Plan Reconciler, Monitor, Adapter, Kernel Authority
- **Affects:** `docs/Architecture` §4, §10, §16; `docs/CONTRACT_MATRIX.md` (ORCH-001..007, OPEN-008)

---

## 1. Context and Problem Statement

`docs/Architecture` §4 specifies that the Space Orchestrator is the "Brain of the Space", decomposed into five sub-modules:
1. `Goal Analyzer`
2. `Planner`
3. `Team Builder`
4. `Monitor`
5. `Adapter / Reflector`

`ROADMAP.md` Phase 4 introduces `reconciler.py` as:
> *"one loop: subscribe Pulses → compare desired vs. actual → emit Plan Deltas / retries / escalations (merged Monitor+Adapter per the reconciler decision)"*

In `docs/CONTRACT_MATRIX.md`, item `OPEN-008` states:
> *"Exact Reconciler responsibility relative to Monitor and Adapter/Reflector — Phase 4"*.

Furthermore, the central architectural invariant of Phase 4 requires that the Orchestrator coordinate the Space without becoming a second authority over:
- Plan versions (owned by Space Kernel Plan CAS).
- Admission & budgets (owned by Space Kernel Admission Control).
- Human approvals (owned by Space Kernel Approver).
- Resource capacity & leases (owned by Resource Manager).
- Space isolation (owned by Space Kernel).

We must formalize the Reconciler architecture and explicitly document the non-authority invariants.

---

## 2. Decision

### 2.1 Resolution of OPEN-008: Reconciler vs Monitor vs Adapter

We define clear, separated responsibilities:

1. **`Monitor` (The State Observer):**
   - Pure observer subscribing to typed Pulses from the `PulseBus` (`task.*`, `resource.*`, `plan.*`, `space.*`).
   - Tracks actual runtime state: active tasks, completed tasks, failed tasks, held leases, and the authoritative `plan_version`.
   - Never mutates state or emits plan deltas; produces clean `StatusEvent` and `TimelineSnapshot` representations.

2. **`Adapter / Reflector` (The Proposal Generator):**
   - Pure strategy component.
   - Given an actual failure, resource contention, or plan drift, computes the necessary repair operations (`add`, `remove`, `reassign`, `rollback`).
   - Produces a proposed `PlanDelta` with `base_version` and `resulting_version = base_version + 1`.
   - On goal completion, formats `experience.stored` records with actionable `counterfactual` fields.

3. **`PlanReconciler` (The Convergence Loop):**
   - Merges Monitor observation and Adapter generation into a deterministic reconciliation loop:
     $$\text{Pulses} \to \text{Monitor} \to \text{Desired vs Actual Comparison} \to \text{Adapter} \to \text{PlanDelta} \to \text{Kernel CAS}$$
   - When actual state diverges from desired state:
     - If `transient.*`: tracks bounded retries with the original `idempotency_key` per failure taxonomy (§10).
     - If `plan drift` or `task failure`: queries `Adapter` for a `PlanDelta` and submits it to the Space Kernel's single-writer CAS engine (`PlanEngine`).
     - If `terminal.*` or `budget exhausted`: escalates immediately to Kernel / Human Gate without retry.

### 2.2 Strict Non-Authority Invariants

The Orchestrator and its sub-modules are constitutionally bound by the following prohibitions:

| Dimension | Orchestrator Capability | Forbidden Action (Constitutional Boundary) |
| :--- | :--- | :--- |
| **Plan Versioning** | Propose `PlanDelta(base_version, ops)` | Cannot mutate `TaskGraph` in place; cannot force plan version; must rebase on `plan.version.superseded`. |
| **Budget & Spend** | Read budget policy (`hard_stop`, `approval_required`, `degraded`) | Cannot execute tools or dispatches when Kernel Admission denies with `space.budget.exceeded`. |
| **Human Approvals** | Emit approval requests for high-risk actions | Cannot approve its own requests; cannot bypass attention budget limit $N$. |
| **Resources & Leases** | Request allocation via `resource.requested` | Cannot allocate GPU/CPU/tokens directly; cannot forge `Lease` tokens; must respect queue positions. |
| **Space Boundaries** | Orchestrate within owning `space_id` | Cannot access, plan, or acquire resources in sibling Spaces (`PermissionError`). |

### 2.3 Mock Agent Contract for Phase 4 (No LLM)

To preserve the deterministic core boundary (AGENTS.md §4, §23):
- Phase 4 contains **zero real LLM calls**.
- Tasks assigned to mock agents/workers communicate strictly over the Pulse Bus.
- Mock execution agents subscribe to `task.assigned`, optionally request resources, and emit `task.started`, `worker.tool.*`, and `task.completed`/`task.failed`.
- The entire control loop is 100% deterministic and reproducible.

---

## 3. Alternatives Considered and Rejected

1. **Monolithic Orchestrator doing planning, monitoring, and CAS directly:**
   - *Rejected:* Violates the thin Orchestrator requirement (§4). Creates an unmaintainable God Object that risks duplicating Kernel authority.
2. **Orchestrator maintaining its own private plan versioning store:**
   - *Rejected:* Violates Plan CAS authority. Two orchestrators racing would desynchronize if the Orchestrator held the plan version. The Space Kernel must remain the single writer for `plan_version`.
3. **Synchronous polling instead of Pulse-driven reconciliation:**
   - *Rejected:* Violates Law 3 (*Components Communicate Through Pulses*). The Reconciler must be event-driven via Pulse subscriptions.

---

## 4. Consequences

- Fully resolves `CONTRACT_MATRIX` `OPEN-008`.
- Guarantees that the Orchestrator remains a proposal-and-coordination engine, while the Space Kernel remains the sole sovereign authority.
- Enables complete, non-flaky testing of concurrent re-plans, stale delta rejections, and orchestrator crash recovery.
