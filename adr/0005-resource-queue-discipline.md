# ADR 0005: Resource Queue Discipline, Fairness, and Starvation Prevention

## Status
Accepted

## Context
In the RYU AI Space-Centric Cognitive Architecture (SCCA) §9 and §16, contested resources (such as GPU instances, execution slots, or specialized hardware nodes) are arbitrated solely by the `ResourceManager`. Unlike first-writer-wins or uncoordinated distributed locks, Ryu uses a **queue with lease** model.

When a requested resource is unavailable (already leased to capacity), incoming requests must be queued rather than dropped or silently ignored. Law 6 dictates: *Failures Are Contained, Escalated, and Never Silent*. The roadmap also explicitly notes the starvation trade-off between strict FIFO and greedy requesters. This ADR resolves Contract Matrix item **OPEN-010**.

## Decision

1. **Default Queue Discipline — Strict FIFO:**
   - The default queue discipline for all resources is strict **FIFO (First-In, First-Out)** based on arrival timestamp at the `ResourceManager` (with an atomic monotonic counter as tie-breaker).
   - This ensures simple, predictable, and fair ordering by default without hidden scheduling bias.

2. **Priority Opt-In (`PRIORITY_FIFO`):**
   - Spaces or individual resource registrations may opt into priority-aware queueing.
   - Priority levels are integer values (higher value = higher priority).
   - Higher priority requests are dequeued before lower priority requests.
   - Within the same priority level, strict FIFO applies.

3. **Starvation Prevention (Aging / Max-Bypasses):**
   - Under priority scheduling, a continuous stream of high-priority requests could theoretically starve lower-priority requests indefinitely.
   - To prevent starvation, priority queues implement an **anti-starvation aging threshold**:
     - Each time a waiting lower-priority request is bypassed by a higher-priority request, its bypass counter increments.
     - If the bypass count reaches `max_bypasses` (default: 5), the waiting request is elevated to top priority for the next available allocation slot.
     - Once allocated, its counter is reset.

4. **Contention Notification Contract (Law 6 & Contract RESOURCE-006):**
   - Whenever an acquisition cannot be immediately granted and is queued, the `ResourceManager` **MUST** publish a `resource.conflict` Pulse:
     - `resource_id`: serialized canonical string handle `(resource_type/provider_id/instance_id)`
     - `queue_position`: 1-based integer position of the request in the queue
     - `severity`: `warning`
   - Losers in a race are never met with silence or empty returns; their position in the queue is confirmed via typed event.

5. **Cancellation Semantics:**
   - Any request currently waiting in queue may be explicitly cancelled by its `requester_id` or Space authority prior to grant.
   - A cancelled request is pruned from the queue immediately; its cancellation does not disrupt remaining queue positions.
   - A cancelled request can never subsequently be allocated the resource upon release.

6. **Capacity Accounting:**
   - For capacity resources (total capacity $C > 1$), requests may ask for $k$ units ($1 \le k \le C$).
   - A request is granted if and only if $allocated + k \le total$. Otherwise, it is queued.
   - Release of capacity wakes queued requests in queue discipline order.

## Consequences
- Contested resource arbitration is 100% deterministic and auditable.
- Clients know their exact queue position via `resource.conflict`.
- Greedy requesters cannot permanently starve pending requests.
- Space boundaries remain enforced; queues are Space-scoped or strictly arbitrated per resource.

