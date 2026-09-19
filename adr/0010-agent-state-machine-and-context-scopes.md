# ADR-0010: Agent State Machine, Proposal Validation, and Context Scope Hierarchy

## Status
Accepted (Phase 5)

## Context
In RYU AI, Agents provide stochastic reasoning while deterministic infrastructure (`SpaceKernel`, `ResourceManager`, `PulseBus`) enforces authority, budgets, plans, and isolation. If an Agent or LLM were able to mutate the TaskGraph, allocate resources, or approve actions directly, the architecture's security and deterministic guarantees would collapse. Furthermore, multi-turn tasks require strict context scoping and bounded memory growth to prevent context window overflow (Architecture §7, §12, §18).

## Decision
1. **Deterministic Agent Lifecycle States:**
   - Agent lifecycle is governed by an explicit state machine:
     `IDLE -> THINKING -> PROPOSING -> WAITING -> EXECUTING -> OBSERVING -> REFLECTING -> COMPLETED / FAILED`.
   - The LLM acts purely as the transition decision function during `THINKING`; it NEVER mutates state directly.
   - Transition decisions are emitted as typed `AgentProposal` objects.

2. **Structured Agent Proposal & Constitutional Non-Authority:**
   - Schema: `AgentProposal(intent, reasoning, requested_action, parameters, confidence, required_capabilities)`.
   - The deterministic Agent runtime runs `ProposalValidator` before any state transition or action dispatch.
   - Any proposal attempting direct plan mutation, resource allocation, lease minting, budget bypass, human gate self-approval, secret resolution, or cross-space mutation is rejected deterministically.

3. **Context Scope Hierarchy & Mutation Authority (Correction 5):**
   - Three nested context scopes:
     `SpaceContext` (broadest, shared workspace)  
       ↓  
     `AgentContext` (agent working session)  
       ↓  
     `TaskContext` (narrowest, active plan node)
   - **Separation of Visibility vs Mutation:**
     - Children may read parent context (visibility).
     - Children CANNOT mutate parent context without explicit authority:
       - `Task -> Agent` context mutation is REJECTED with `PermissionError`.
       - `Agent -> Space` context mutation is REJECTED with `PermissionError`.
       - `Space A -> Space B` context access is REJECTED with `PermissionError`.

4. **Context Compaction & Pinned Survival (Correction 6):**
   - When context utilization crosses the configured threshold (default 80% or configured turn count), the `ContextManager` triggers compaction:
     - Evictable turns are summarized and evicted via LRU.
     - Pinned content (`pinned=True`) is NEVER evicted.
     - A structured `HandoffNote` is generated containing:
       `{goal, current_task_id, plan_version, last_3_decisions, open_questions}`.
     - Compaction decreases active context size, bounds growth across 500+ turns, and retains all pinned fields by construction.

## Consequences
- The LLM can never gain direct authority over RYU infrastructure.
- Unauthorized upward context leakage and mutations are blocked.
- Long-running execution chains remain stable without context overflow.

