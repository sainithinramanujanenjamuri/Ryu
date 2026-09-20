# ADR-0025: Human Attention Budget and Prioritized Queuing

## Context
Concurrent autonomous agents and workers can generate multiple simultaneous capability requests. Flooding human operators with concurrent notification prompts causes fatigue, operational errors, and compromised security vigilance.

## Decision
1. **Configurable Concurrency Bound:**
   - Each Space enforces a strict maximum limit $N$ (default $N=3$) of concurrently active approval requests in the human operator's attention window.
2. **Prioritized Queuing Discipline:**
   - Excess approval requests exceeding $N$ are held in a prioritized FIFO queue (`AttentionQueueState = QUEUED`).
   - Priority Classes:
     - **Class 1 (High/Urgent):** Budget replenishment and held requests awaiting resumption.
     - **Class 2 (Standard):** Normal capability execution gates.
     - **Class 3 (Low):** Background / speculative tasks.
3. **Orchestrator Coordination:**
   - When the attention budget is saturated (`active_count >= limit`), the Space Orchestrator is notified via `is_attention_saturated()` and halts further dispatch of gated tasks until active approvals are resolved.
4. **Automatic Dequeuing:**
   - Upon resolution of an active approval, the Attention Budget immediately pops the highest-priority queued request and activates it (`AttentionQueueState = ACTIVE`).

## Alternatives Considered
- *Rejecting excess requests immediately:* Rejected because autonomous plan steps would fail spuriously during momentary human unavailability.
- *Unbounded queuing without orchestrator backpressure:* Rejected because agents would continue generating endless tasks while humans are blocked, causing unbounded memory growth.

## Consequences
- Human operators receive at most $N$ concurrent approval prompts.
- Dispatch pauses cleanly during attention saturation without dropping requests.

## Date
2026-09-20

