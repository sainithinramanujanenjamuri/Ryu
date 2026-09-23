# ADR-0034: Experience Reflection, Validation, and Failure Semantics

## Status
Accepted

## Context
Under SCCA §4 (Space Memory) and §16 (Component Contracts), task outcomes must be captured as structured `ExperienceRecord` entities to enable learning from failures. The architecture explicitly mandates a `counterfactual` field ("what would have worked better") to make past experiences actionable rather than purely archival. 

Furthermore, SCCA Law 6 dictates that "Failures are contained, escalated, and never silent." Memory operations cannot fail quietly or convert storage errors into synthetic empty results.

## Problem
1. How is the mandatory `counterfactual` field enforced across the stack?
2. What authority does the Reflector possess, and can it alter authoritative execution plans?
3. What is the scope of learning in Phase 10 (behavioral adaptation vs. model-weight training)?
4. What failure semantics govern memory persistence and query operations?

## Decision
1. **Three-Layer Counterfactual Enforcement:**
   - Layer 1 (Dataclass): `ExperienceRecord.__post_init__` raises `ValueError` if `counterfactual` is missing, empty, or whitespace-only (`MEM-002`).
   - Layer 2 (Pulse Validator): The JSON Schema for `experience.stored` enforces `required: ["counterfactual", ...]` and rejects malformed payloads with `PulseRejectedError`.
   - Layer 3 (Relational Constraint): Database migration `005_create_memory_tables.sql` enforces `CHECK (counterfactual <> '')` at rest.
2. **Reflector Authority Boundary:**
   - The `Reflector` (and `Adapter.record_experience()`) persists experiences to Space-local memory and emits `experience.stored` and `memory.updated` Pulses.
   - The `Reflector` CANNOT create `PlanDelta`, CANNOT commit plans, CANNOT invoke Admission Control, CANNOT acquire resources, and CANNOT write global knowledge.
3. **Behavioral Adaptation, Not Model-Weight Training:**
   - Phase 10 provides experience-driven behavioral adaptation.
   - It persists and evaluates experiences to generate contextual hints that guide the Planner's capability choices.
   - Phase 10 does NOT fine-tune models, update neural weights, or perform autonomous self-modification.
4. **Non-Silent Failure Containment:**
   - Memory storage and retrieval failures raise typed `MemoryFailure` exceptions with operation context.
   - Failures are never silently swallowed as empty lists (`[]`) or `None` unless explicitly permitted by an observable contract.
   - If persistence fails during reflection, the failure is raised and no `experience.stored` Pulse is emitted.

## Alternatives Considered
- **Optional counterfactual field:** Rejected. Un-actionable telemetry without counterfactual explanations prevents deterministic adaptation.
- **Swallowing memory errors to avoid crashing workers:** Rejected. Violates Law 6 (silent failures are defects).

## Consequences
- Every stored experience is guaranteed to contain a counterfactual hypothesis.
- Memory failures are immediately observable and auditable.
- Architectural boundaries between reflection and plan commitment remain inviolable.

## Date
2026-09-23

