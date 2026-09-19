# ADR-0007: Goal Submission, Orchestration Idempotency, and Command Lifecycle

- **Status:** Accepted
- **Date:** 2026-09-19
- **Scope:** Space Orchestrator, Goal Analyzer, Space Kernel, Pulse Bus
- **Affects:** `docs/Architecture` §4, §10, §16; `docs/CONTRACT_MATRIX.md` (ORCH-001, ORCH-002, IDEM-001..004)

---

## 1. Context and Problem Statement

When a human user or upstream channel (chat, CLI, API, webhook) submits a goal to a Space, network drops, client retries, or user re-clicks may result in the exact same `Command` being submitted multiple times.

If duplicate command submissions are not handled idempotently at the orchestration boundary:
1. The `GoalAnalyzer` could generate duplicate `GoalSpec` instances.
2. The `Planner` could generate duplicate `TaskGraph` instances.
3. The Space Kernel could receive duplicate `plan.created` proposals.
4. The `TeamBuilder` could spawn duplicate worker assignments.
5. The `ResourceManager` could receive redundant lease requests, polluting the contention queue.
6. Downstream workers could execute duplicate side-effecting operations.

We must define unambiguous, deterministic idempotency and lifecycle semantics for goal submission and orchestration.

---

## 2. Decision

### 2.1 Command Identity and Idempotency Scope

Every human goal enters the Space as a `Command`:
```python
@dataclass(frozen=True)
class Command:
    command_id: str
    space_id: str
    objective: str
    params: dict[str, Any] = field(default_factory=dict)
    constraints: list[str] = field(default_factory=list)
    correlation_id: str | None = None
```

- Idempotency is strictly scoped to the 2-tuple: `(space_id, command_id)`.
- Re-submitting the same `command_id` to a different `space_id` is a cross-space boundary violation (`PermissionError`).

### 2.2 Orchestrator Idempotency Lifecycle

When `SpaceOrchestrator.submit_goal(command)` is called:

1. **Space Boundary Validation:** Validate that `command.space_id` matches the Orchestrator's owning Space (Law 1).
2. **Idempotency Lookup:** Check if `(space_id, command_id)` has already been analyzed or executed.
   - If an active orchestration session exists for `command_id`:
     - Return the existing active `GoalSpec` and current `plan_version`.
     - Do NOT re-run `GoalAnalyzer`.
     - Do NOT re-run `Planner`.
     - Do NOT publish duplicate `goal.defined` or `plan.created` Pulses.
   - If the command has already completed (`space.completed` / outcome reached):
     - Return the cached terminal outcome and artifact references.
   - If the command is new:
     - Record `command_id` as active.
     - Analyze goal $\to$ `GoalSpec`.
     - Publish `goal.defined`.
     - Plan initial DAG $\to$ `TaskGraph` (`plan_version = 1`).
     - Submit initial plan to Space Kernel CAS $\to$ publish `plan.created`.

### 2.3 Single Execution Invariant

At no point may a duplicate command submission trigger:
- A secondary `TaskGraph` for the same command.
- Additional resource lease allocations.
- Additional task assignments.

The Orchestrator owns session deduplication for goals, consistent with ADR-0006 (where the boundary receiving the request owns idempotency).

---

## 3. Alternatives Considered and Rejected

1. **First-writer-wins with exception on duplicate:**
   - *Rejected:* Returning an error to a client on a network retry forces client-side error handling when the operation was actually successful. Returning the existing active session is standard distributed systems practice.
2. **Global command registry across all Spaces:**
   - *Rejected:* Violates Law 1 (*Everything Happens Inside a Space*). Commands belong to their owning Space. Cross-space command deduplication would introduce global mutable state.
3. **Stateless orchestrator with pure Pulse replay deduplication:**
   - *Rejected:* While the event log in PostgreSQL is authoritative, caching the active command mapping in the Orchestrator avoids re-scanning the entire event stream on every concurrent RPC. Replay from the store reconstructs the active command set on restart.

---

## 4. Consequences

- Guarantees zero duplicate plans, teams, or resource requests from duplicate command submissions.
- Makes goal submission deterministic and safe for retrying network clients.
- Fully auditable via `goal.defined` Pulses.
