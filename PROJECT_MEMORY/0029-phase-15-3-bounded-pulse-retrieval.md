# Project Memory: 0029 — Phase 15.3 Bounded Pulse Retrieval

**Date:** 2026-10-04  
**Author:** Ryu Autonomous Core Agent  
**Baseline:** Phase 15.2 Hardened (`e064d46`)  
**Status:** COMPLETE (GATE-15.3: VERIFIED WITH EXPLICIT LIMITATIONS)  
**Governing ADR:** ADR-0047 (Bounded Pulse Retrieval and Keyset Pagination)  
**Governing Contracts:** PULSE-013, PULSE-014, PULSE-015, PULSE-016, PULSE-017  
**Governing Architecture:** Space-Centric Cognitive Architecture (SCCA)  

---

## 1. Context & Purpose

The Phase 15 Architecture Audit (`docs/PHASE_15_ARCHITECTURE_AUDIT.md`) identified **Finding F-03 (Bounded Pulse Retrieval — P1)** as a critical operational reliability risk:
- Prior to Phase 15.3, `PostgresPulseStore.read_by_space()` and related retrieval methods performed unbounded queries issuing `SELECT * FROM pulses WHERE space_id = %s` without a `LIMIT`.
- In long-lived Spaces or during heavy execution bursts, this created memory exhaustion (OOM), high query latency, unbounded network payload transfers, and thread starvation.
- Consumers such as Channel Daemon audit endpoints, history rehydration, and CLI audit stream relied on unconstrained scans or full-table memory loading.
- Taint clearance checking (`is_taint_cleared`) scanned full Space histories to locate a single `security.taint.cleared` pulse.

Phase 15.3 strictly resolves Finding F-03 without altering the frozen Phase 0 `Pulse` dataclass or publish validation contracts, introducing monotonic keyset pagination, tail retrieval, memory-bounded generator streaming, existence probes, and fail-closed caps on legacy methods.

---

## 2. What Changed

1. **Pulse Page Configuration (`core/pulse_bus/src/ryu/pulse_bus/config.py`):**
   - Defined `DEFAULT_PULSE_PAGE_SIZE = 100`, `MAX_PULSE_PAGE_SIZE = 1000`, `MIN_PULSE_PAGE_SIZE = 1`.
   - Added `get_max_pulse_page_size()` reading environment variable `RYU_PULSE_PAGE_MAX` with dynamic clamping to `[1, 10_000]`.

2. **Durable Keyset Pagination & Bounded Retrieval Models (`core/pulse_bus/src/ryu/pulse_bus/store.py`):**
   - Defined `StoredPulse` dataclass binding sequence `position: int` to frozen `pulse: Pulse`.
   - Defined `PulsePage` immutable dataclass with `space_id`, `entries: tuple[StoredPulse, ...]`, `next_after_position: int | None`, `next_before_position: int | None`, `has_more: bool`.
   - Defined `PulseRetrievalBoundExceeded(RuntimeError)` exception adhering to SCCA Law 6 (*Failures are contained, escalated, and never silent*).
   - Implemented pre-execution input validator `validate_page_bounds()`.
   - Extended `PulseStore` protocol and implemented on both `InMemoryPulseStore` and `PostgresPulseStore`:
     - `read_space_page(space_id, after_position=0, limit=100, pulse_types=None)`
     - `read_space_tail(space_id, limit=100, before_position=None, pulse_types=None, type_prefix=None)`
     - `iter_space(space_id, batch_size=100, pulse_types=None) -> Iterator[StoredPulse]`
     - `has_pulse_of_type(space_id, correlation_id, pulse_type) -> bool`
     - `read_page(after_position=0, limit=100)`
   - Wrapped legacy methods (`read_by_space`, `read`, `read_by_correlation`, `read_by_parent`) in fail-closed retrieval envelopes that query up to `cap + 1` and raise `PulseRetrievalBoundExceeded` if exceeded.

3. **Composite Database Indexing (`deploy/migrations/008_pulses_space_position_index.sql`):**
   - Created composite B-tree indexes supporting $O(\log N)$ keyset lookups:
     - `idx_pulses_space_position` on `(space_id, position ASC)`
     - `idx_pulses_space_type_position` on `(space_id, type, position ASC)`
     - `idx_pulses_space_corr_type` on `(space_id, correlation_id, type)`

4. **Taint Clearance Existence Optimization (`core/pulse_bus/src/ryu/pulse_bus/taint.py`):**
   - Replaced linear scan in `is_taint_cleared` with `store.has_pulse_of_type(space_id, correlation_id, "security.taint.cleared")`, avoiding full Space pulse retrieval.

5. **Memory-Safe Replay Engine (`core/pulse_bus/src/ryu/pulse_bus/replay.py`):**
   - Backed `replay_from()` with paginated generator over `store.read_page()` avoiding full-table loading.
   - Backed `replay_by_space()` with `store.iter_space()` streaming.

6. **Channel Daemon & CLI Migration (`channels/daemon/server.py`, `channels/daemon/history.py`, `channels/cli/commands/audit.py`):**
   - Migrated Channel Daemon `/api/v1/spaces/{space_id}/audit` to `store.read_space_tail()`.
   - Migrated SpaceHistoryStore goal rehydration to `store.read_space_tail(pulse_types=frozenset(["goal.defined"]))`.
   - Migrated CLI `audit stream` to `read_space_tail()` for initial history and keyset `read_page()` during polling.

7. **Contract Matrix & Spec Mapping:**
   - Registered contracts `PULSE-013`, `PULSE-014`, `PULSE-015`, `PULSE-016`, `PULSE-017` in `docs/CONTRACT_MATRIX.md`.
   - Mapped all 5 contracts to executable test suite in `harness/spec_map.yaml`.

---

## 3. What Was Verified

1. **Phase 15.3 Dedicated Test Suite (`core/pulse_bus/tests/test_phase15_3_bounded_pulse_retrieval.py`):**
   - **33 tests passed** (100% pass rate).
   - Covers PBR-001 through PBR-020 and Adversarial Matrix ADV-PULSE-01 through ADV-PULSE-14:
     - `PBR-001`: Keyset page forward pagination with stable cursor progression.
     - `PBR-002`: Keyset page limit clamping to `MAX_PULSE_PAGE_SIZE`.
     - `PBR-003`: Keyset page boundary validation (rejects invalid space_id, negative position, negative limit).
     - `PBR-004`: Keyset page filtering by pulse type set.
     - `PBR-005`: Tail retrieval returns latest $N$ pulses in chronological ascending order.
     - `PBR-006`: Tail retrieval with `before_position` cursor.
     - `PBR-007`: Tail retrieval filtering by pulse type set.
     - `PBR-008`: Tail retrieval filtering by pulse type prefix.
     - `PBR-009`: `iter_space` generator streams across pages with bounded memory.
     - `PBR-010`: `iter_space` generator respects type filter.
     - `PBR-011`: `has_pulse_of_type` returns `True` when matching pulse exists.
     - `PBR-012`: `has_pulse_of_type` returns `False` when matching pulse is absent.
     - `PBR-013`: `has_pulse_of_type` isolates by `correlation_id` and `space_id`.
     - `PBR-014`: TaintResolver fast lookup via `has_pulse_of_type`.
     - `PBR-015`: Duplicate append idempotence in pagination.
     - `PBR-016`: Legacy `read_by_space` fails closed with `PulseRetrievalBoundExceeded` when exceeding cap.
     - `PBR-017`: Legacy `read` fails closed when exceeding cap.
     - `PBR-018`: Legacy `read_by_correlation` fails closed when exceeding cap.
     - `PBR-019`: Legacy `read_by_parent` fails closed when exceeding cap.
     - `PBR-020`: Replay engine uses paginated streaming.
     - `ADV-PULSE-01` .. `ADV-PULSE-06`: Keyset monotonicity, zero-limit/negative cursor rejection, oversized limit clamping, SQL injection immunity.
     - `ADV-PULSE-07` .. `ADV-PULSE-14`: Tail empty space handling, tail cursor boundaries, generator interruption safety, mock Postgres query generation, mock tail DESC-to-ASC reversal, mock Postgres legacy fail-closed cap.

2. **Full Regression Test Suite:**
   - `core/pulse_bus/tests` + `channels/tests`: **144 passed, 1 skipped** (integration test requiring active Postgres skipped as expected).
   - Zero regressions across existing bus validation, registry, replay, taint, approval, and daemon tests.

3. **Core Boundary & Governance Audits:**
   - `python scripts/dep_guard.py`: **PASS** (0 forbidden imports in `core/`).
   - `ruff check core/pulse_bus channels/cli channels/daemon`: **PASS** (0 errors).
   - `mypy`: **PASS** (0 errors across `store.py`, `config.py`, `taint.py`, `replay.py`).
   - `python scripts/contract_sync.py`: **PASS** (38/38 architecture types registered).
   - `python scripts/v1_audit_spec_coverage.py`: **PASS** (V1-001 PASS: 177 criteria, 240 contract IDs, 198 spec-map entries).
   - `python scripts/v1_audit_governance.py`: **PASS** (V1-005 PASS: ADR Inventory 0001..0047 PASS).

---

## 4. What Remains / Deferred Scope

Finding F-03 is strictly completed. In accordance with the Phase 15 Architecture Audit, the following findings remain deferred to subsequent phases:
- **Phase 15.4:** Finding F-04 (Concurrent DAG Scheduler — Priority: P1)
- **Phase 15.5:** Finding F-05 (Semantic Memory / Experience Retrieval — Priority: P1)
- **Phase 15.6:** Finding F-06 (Durable Convergence State — Priority: P1)
- **Phase 15.7:** Finding F-07 (Agent Hierarchy Integration — Priority: P2)
- **Phases 15.8–15.13:** Findings F-08 through F-13 (P2 / P3 items).
