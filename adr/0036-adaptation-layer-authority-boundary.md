# ADR-0036: Adaptation Layer Authority Boundary and Hint Generation

## Status
Accepted

## Context
Under SCCA §7 (Adaptation Layer), RYU AI incorporates learning from past execution failures. However, under SCCA Law 2 ("Capabilities Are Requested, Never Owned") and Law 5 ("Humans Define Goals; Ryu Organizes Execution"), the cognitive decomposition must remain strictly partitioned. 

Components must not blur their functional responsibilities: the Adaptation Layer must not become a second Orchestrator, Planner, or Space Kernel.

## Problem
1. Where does `AdaptationLayer` reside in the package hierarchy?
2. What operations is `AdaptationLayer` permitted to perform?
3. What operations is `AdaptationLayer` explicitly prohibited from performing?
4. How do adaptation insights influence planning without violating CAS plan commitment?

## Decision
1. **Package Placement (`core/memory/adaptation.py`):**
   - `AdaptationLayer` resides in `core/memory/` and imports solely from `core/space/memory_protocol.py`.
   - It has zero imports from concrete memory implementations, `core.orchestrator`, `core.space.kernel`, `core.capabilities`, or `core.resources`.
2. **Read-Only Hint Generation:**
   - `AdaptationLayer.generate_hints(space_id, situation_hint, limit)` is strictly a read-only query function.
   - It queries `SpaceMemoryProtocol.query_similar_experiences()` and transforms relevant negative experiences into immutable `ExperienceHint` dataclasses.
   - It injects hints into `GoalSpec.metadata["experience_hints"]` via the Orchestrator.
3. **Explicit Prohibitions:**
   - `AdaptationLayer` CANNOT mutate plans or create `PlanDelta`.
   - `AdaptationLayer` CANNOT commit plans (Plan CAS is owned solely by `SpaceKernel.plan_store`).
   - `AdaptationLayer` CANNOT emit Pulses directly.
   - `AdaptationLayer` CANNOT invoke Admission Control or acquire hardware/lease resources.
   - `AdaptationLayer` CANNOT write to or promote global knowledge.
4. **Planning Sovereignty:**
   - The `Planner` receives `ExperienceHint` objects in `GoalSpec.metadata` and uses them to propose alternative capability choices or compensation nodes.
   - The proposed plan remains unauthoritative until committed by `SpaceKernel` via CAS (`ORCH-003`).

## Alternatives Considered
- **Direct Plan Mutation by AdaptationLayer:** Rejected. Violates deterministic orchestration hierarchy and bypasses Plan CAS.
- **Placing AdaptationLayer in `memory/`:** Rejected. Forces circular dependency or orchestrator coupling. Placing it in `core/memory/adaptation.py` respecting `SpaceMemoryProtocol` keeps `core/` self-contained and clean.

## Consequences
- The Orchestrator retains clear sequencing authority without cognitive boundary leaks.
- Adaptation decisions are auditable, deterministic, and subject to Space Kernel CAS invariants.

## Date
2026-09-23

