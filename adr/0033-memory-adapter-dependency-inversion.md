# ADR-0033: Memory Adapter Dependency Inversion and Storage Boundaries

## Status
Accepted

## Context
Phase 10 introduces the long-term cognitive substrate of RYU AI under Space-Centric Cognitive Architecture (SCCA). Space Memory must accommodate different physical storage backends (in-memory for deterministic testing, PostgreSQL for durable timeline and relational state, and future vector or graph databases). 

SCCA Law 4 dictates that "Knowledge belongs to the Space first." In accordance with AGENTS.md §4 (Dependency Direction), the deterministic `core/` package must remain strictly independent of higher-level packages and concrete external storage implementations. Furthermore, the repository must not introduce unneeded external infrastructure (such as running Qdrant or Neo4j clusters) merely to satisfy roadmap labels when core architectural requirements can be verified deterministically.

## Problem
1. Where should the Space Memory interface reside to maintain strict one-way dependency flow?
2. Which storage adapters are authoritative for Phase 10, and which are extension boundaries?
3. How are vector (Qdrant) and graph (Neo4j) backends handled without compromising repository determinism or Phase 10 gate verification?
4. How do concrete adapters receive authorization to store global knowledge without leaking private adapter methods to higher-level orchestrators?

## Decision
1. **Interface in Core (`SpaceMemoryProtocol`):** The primary abstraction is defined as `SpaceMemoryProtocol` in `core/space/memory_protocol.py`. The `core/` package defines only abstract protocols, dataclasses (`ExperienceRecord`, `KnowledgeEntry`, `PromotionAuthorization`), and typed exceptions. It contains zero imports from `memory/`.
2. **Authoritative Implementations:**
   - `InMemoryMemoryAdapter` is the authoritative thread-safe implementation for hermetic unit testing and fast development.
   - `PostgreSQLMemoryAdapter` is the authoritative durable implementation for production persistence across process restarts.
3. **Extension Boundaries for Qdrant and Neo4j:**
   - `QdrantAdapterStub` and `Neo4jAdapterStub` are provided in `memory/adapters/` as typed stubs raising `NotImplementedError("spec §4 — Phase 11+")`.
   - Running Qdrant or Neo4j instances are NOT required for the Phase 10 gate. No Docker containers or external service dependencies are introduced for them.
   - Future vector and graph capabilities can be introduced in Phase 11+ without altering `SpaceMemoryProtocol`.
4. **No Competing Sources of Truth:** PostgreSQL is the single durable source of truth when configured. InMemory is used for unit tests. No synchronization split exists.

## Alternatives Considered
- **Direct import of memory adapters into core:** Rejected. Violates AGENTS.md §4 (core must not import from higher layers).
- **Mandating live Qdrant and Neo4j in Phase 10:** Rejected. Adds unnecessary infrastructure complexity and external service fragility without contributing to the Phase 10 exit gate invariants (experience persistence, forgery resistance, and Space isolation).

## Consequences
- The dependency direction `core -> Protocol; memory -> implements Protocol` is preserved and enforced by `scripts/dep_guard.py`.
- Phase 10 gate verification remains hermetic, fast, and deterministic.

## Date
2026-09-23

