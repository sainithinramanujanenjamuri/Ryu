# ADR 0003: Plan CAS Livelock Bound and Rebase Escalation Policy

## Status
Accepted

## Context
In RYU AI Space-Centric Cognitive Architecture (SCCA), the Space Kernel acts as the single-writer authority for `plan_version` via Compare-And-Swap (CAS). Multiple planners or adaptive agents may concurrently propose `PlanDelta` updates against a shared `base_version`. When two or more Plan Deltas race, the first delta accepted by the Kernel wins, bumping `plan_version`. Losing deltas are rejected because their `base_version != current_version` and are notified via `plan.version.superseded`.

Under rapid re-planning storms or high-contention scenarios, competing writers could repeatedly attempt to rebase their deltas against the latest version and continuously collide, resulting in CAS livelock and CPU starvation. Law 6 dictates: *Failures Are Contained, Escalated, and Never Silent*.

## Decision

1. **Single-Writer Authority:** The Space Kernel's `PlanStore` is the sole authority committing `PlanDelta` objects and incrementing `plan_version`.
2. **Optimistic CAS Protocol:**
   - Commit condition: `delta.base_version == current_plan_version`.
   - On match: delta is applied, `plan_version` increments by 1, and `plan.delta` (or authorized equivalent) is published to the Pulse Bus.
   - On mismatch: the commit is rejected, `plan.version.superseded` is published containing `superseded_version`, `current_version`, and `winning_delta_id`.
3. **Bounded Rebase Retries (Locked Decision):**
   - The maximum number of consecutive automatic rebase attempts for a plan modification is **3**.
4. **Livelock Escalation (Law 6):**
   - If a proposed plan update fails CAS three consecutive times, the system **MUST NOT** spin indefinitely.
   - The rebase loop terminates immediately and publishes:
     - Pulse: `task.failed`
     - Payload:
       - `task_id`: the affected task or delta identifier
       - `error_class`: `terminal.plan_livelock`
       - `message`: `Plan CAS livelock: exceeded maximum automatic rebase attempts (3)`
       - `plan_version`: current authoritative `plan_version`
       - `severity`: `error`
5. **In-Flight Task Resolution:**
   - When a winning delta commits, in-flight tasks whose `plan_version` was superseded are deterministically resolved by node operation:
     - `finish`: Unaffected nodes continue execution to completion on their original version.
     - `checkpoint`: Nodes modified by the winning delta are paused and state is checkpointed.
     - `cancel`: Nodes removed by the winning delta are cancelled immediately.

## Consequences
- Prevents unbounded CPU loops and livelocks during concurrent re-planning.
- Concurrency conflicts escalate to the Orchestrator/Human per Law 6 rather than silently stalling.
- Re-plans remain deterministic and auditable on the Pulse Bus.

