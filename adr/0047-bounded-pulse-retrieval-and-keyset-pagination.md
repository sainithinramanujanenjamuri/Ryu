# ADR-0047 — Bounded Pulse Retrieval and Keyset Pagination

**Status:** Accepted  
**Date:** 2026-10-05  
**Phase:** 15.3 — Bounded Pulse Retrieval (Finding F-03 — P1)  
**Supersedes:** (none — refines ADR-0002, ADR-0045, ADR-0046)  
**See also:** ADR-0001 (Monorepo Structure), ADR-0002 (Transport vs Record), ADR-0045 (Durable PostgreSQL Plan Store), ADR-0046 (Space-Safe Artifact Namespace Isolation)

---

## 1. Context / Problem

The Phase 15 Architectural Audit (`docs/PHASE_15_ARCHITECTURE_AUDIT.md`) and Phase 15.3 Architecture Audit (`docs/PHASE_15_3_ARCHITECTURE_AUDIT.md`) identified an unbounded state-retrieval bottleneck in the RYU runtime, classified as **Finding F-03 (Bounded Pulse Retrieval — Priority: P1)**.

### Deficiencies in Prior Architecture:
1. **Unbounded Database Materialization:** `PostgresPulseStore.read_by_space()` issued unbounded `SELECT * FROM pulses WHERE space_id = %s ORDER BY position ASC` queries without a `LIMIT` clause. Client drivers (`psycopg2`) buffered the entire result set in memory, risking Out-Of-Memory (OOM) process crashes on long-lived Spaces accumulating thousands of pulses.
2. **Global & Unbounded Secondary Read Paths:** Four additional read paths exhibited unbounded materialization: `read(from_position)` (unbounded global cross-space read), `read_by_correlation()`, `read_by_parent()`, and `PulseReplayer.replay_from()`.
3. **Hot-Path Taint Lookup Waste:** `TaintResolver.is_taint_cleared()` executed `read_by_correlation()` during `publish()` on every pulse with a tainted parent, fetching all historical correlation pulses merely to check for the presence of a single clearance pulse.
4. **Protocol Lack of Bounded Primitives:** The `PulseStore` protocol had no bounded retrieval primitives other than `get_unpublished(limit)`. Furthermore, `Pulse` objects do not expose their internal database `position`, preventing callers from constructing keyset cursors from plain `Pulse` instances.
5. **Missing Composite Index:** The PostgreSQL schema contained `idx_pulses_space_id (space_id)` but lacked a composite `(space_id, position)` index, forcing the query planner to sort in memory or scan across primary key boundaries.

---

## 2. Decision

We establish strict bounded pulse retrieval across all `PulseStore` implementations and callers using deterministic keyset pagination, a streaming single-snapshot iterator, and a fail-closed legacy envelope.

### 2.1 Bounded Retrieval Primitives

We introduce typed bounded abstractions in `core/pulse_bus/store.py`:
- `StoredPulse`: Wraps a `Pulse` with its authoritative monotonic database `position: int`.
- `PulsePage`: Represents a discrete page containing `entries: tuple[StoredPulse, ...]`, `next_after_position: int | None`, `next_before_position: int | None`, and `has_more: bool`.
- `PulseRetrievalBoundExceeded`: Exception raised when an unpaged legacy retrieval exceeds the safe limit ceiling.

### 2.2 Keyset Pagination & Tail APIs

`PulseStore` protocol is extended with:
1. `read_space_page(space_id, *, after_position=0, limit=100, pulse_types=None) -> PulsePage`:
   Ascending keyset query:
   ```sql
   SELECT position, id, space_id, type, severity, source, timestamp, payload, taint, correlation_id, parent_pulse_id
   FROM pulses
   WHERE space_id = %s AND position > %s [AND type = ANY(%s)]
   ORDER BY position ASC
   LIMIT %s;
   ```
   Fetches `limit + 1` rows to calculate `has_more` without issuing a costly `COUNT(*)`.
2. `read_space_tail(space_id, *, limit=100, before_position=None, pulse_types=None, type_prefix=None) -> PulsePage`:
   Descending index scan fetching the latest $N$ pulses, returned in ascending order for consistent display.
3. `iter_space(space_id, *, after_position=0, page_size=100) -> Iterator[StoredPulse]`:
   Single-snapshot complete ordered traversal using a PostgreSQL server-side named cursor under `REPEATABLE READ READ ONLY` transaction isolation.
4. `has_pulse_of_type(space_id, correlation_id, pulse_type) -> bool`:
   Efficient existence query `SELECT 1 FROM pulses WHERE space_id = %s AND correlation_id = %s AND type = %s LIMIT 1`.
5. `read_page(*, after_position=0, limit=100) -> PulsePage`:
   Explicitly bounded operator-scope global read.

### 2.3 Limits & Fail-Closed Validation

- `DEFAULT_PULSE_PAGE_SIZE = 100`
- `MAX_PULSE_PAGE_SIZE = 1000` (configurable via `RYU_PULSE_PAGE_MAX`, clamped to `[1, 10_000]`).
- Over-limit, negative, zero, or type-confused (`bool`) limits are rejected with `ValueError` before issuing database I/O.
- Empty or whitespace `space_id` values are strictly rejected.

### 2.4 Fail-Closed Legacy Envelope (No Silent Truncation)

Legacy methods (`read_by_space`, `read`, `read_by_correlation`, `read_by_parent`) maintain their signatures. They fetch at most `MAX_PULSE_PAGE_SIZE + 1` rows. If the number of matching records exceeds the maximum safe threshold, they raise `PulseRetrievalBoundExceeded` rather than silently truncating history.

### 2.5 Database Schema Migration (Migration 008)

Create composite indexes on `pulses`:
- `idx_pulses_space_position (space_id, position)`: Enables $O(\log N + k)$ keyset scans.
- `idx_pulses_space_type_position (space_id, type, position)`: Accelerates typed tail queries.
- `idx_pulses_space_corr_type (space_id, correlation_id, type)`: Accelerates taint clearance existence lookups.

---

## 3. Consequences

### Positive Consequences
- **Memory Boundedness:** Maximum memory consumption per retrieval query is bounded to $O(\text{page\_size})$ rather than $O(N_{\text{space}})$.
- **Space Isolation Preserved:** All queries enforce `space_id = %s`; cursors from other Spaces cannot leak cross-space records.
- **Taint Hot-Path Optimized:** `TaintResolver` executes an index-backed `EXISTS` check instead of loading entire correlation logs.
- **Deterministic Replay:** `PulseReplayer` and streaming consumers use `iter_space` with single-snapshot isolation, eliminating late-commit skips during full traversals.

### Negative / Operational Consequences
- **Legacy Call Migration:** Callers expecting full history on Spaces exceeding 1,000 pulses must migrate to `read_space_tail` or `iter_space`.
