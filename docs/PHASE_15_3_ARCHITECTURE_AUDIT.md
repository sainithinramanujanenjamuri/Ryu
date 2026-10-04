# Phase 15.3 Architecture Audit — Bounded Pulse Retrieval (Finding F-03)

---

## 1. Audit Metadata

| Field | Value |
|:---|:---|
| Document | `docs/PHASE_15_3_ARCHITECTURE_AUDIT.md` |
| Audit type | Architecture audit — **no implementation** |
| Date | 2026-10-05 |
| Governing architecture | Space-Centric Cognitive Architecture (SCCA) |
| Authoritative finding source | `docs/PHASE_15_ARCHITECTURE_AUDIT.md` §15 (finding table), §16 |
| Target finding | **F-03 — Bounded Pulse Retrieval — P1** |
| Related ADRs | ADR-0002 (Transport vs Record), ADR-0045 (Durable PlanStore), ADR-0046 (Artifact Isolation) |
| Related contracts | PULSE-004, PULSE-005, PULSE-006, PULSE-007, PULSE-008, PULSE-009, SPACE-005, REC-002 |

## 2. Current Baseline Commit

```text
e064d46  docs(phase15.2): correct deferred finding references   (HEAD, origin/main)
c28d6f2  feat(phase15.2): implement space-safe artifact namespace isolation (Finding F-02)
75afd6b  docs(phase15.1): align verification report with architecture audit
```

Working tree was clean at audit start.

## 3. Phase 15.2 Dependency

Phase 15.2 (F-02, GATE-15.2: PASS) is complete and pushed. F-03 has **no code dependency** on F-02. The two findings share one principle: retrieval and storage must stay inside a Space (SCCA Law 1). Phase 15.3 must not modify `core/space/artifact_paths.py`, Dispatcher containment, or any worker artifact path.

## 4. F-03 Definition (Authoritative, Unchanged)

From `docs/PHASE_15_ARCHITECTURE_AUDIT.md` §15:

> **F-03 | P1 |** `core/pulse_bus/store.py:136` | `read_by_space()` issues `SELECT * FROM pulses WHERE space_id = %s` with no LIMIT | Paginated, bounded retrieval with configurable LIMIT | Memory exhaustion on long-lived spaces with many pulses

This audit keeps F-03's identity, name, and priority (P1). It does not merge F-03 with F-04, F-05, or F-06.

## 5. Executive Summary

**F-03 still exists**, and its real extent is wider than the single method named in the Phase 15 audit.

1. `PostgresPulseStore.read_by_space()` (`store.py:133-138`) is still unbounded. It uses `SELECT * ... ORDER BY position ASC` with no `LIMIT`, followed by `fetchall()`.
2. Four more pulse-retrieval paths are unbounded in the same way: `read()` (global and cross-Space), `read_by_correlation()`, `read_by_parent()`, and `PulseReplayer.replay_from()` / `replay_by_space()`.
3. **Hidden hot path:** `TaintResolver.is_taint_cleared()` calls `read_by_correlation()` on **every publish whose parent is tainted**. It only needs to know whether one row exists, but it loads every pulse in the correlation.
4. The `PulseStore` protocol has **no way to express bounded retrieval** today. Besides `get_unpublished(limit)`, no method takes a limit or a cursor.
5. The `Pulse` dataclass does **not expose `position`**. Callers therefore cannot build a keyset cursor from the results they receive, so a page type is required.
6. **Recovery does not read pulse history.** `StartupRecoveryEngine` reads `ExecutionAttemptStore`. Plan reconstruction reads the `plans` table (ADR-0045). `Monitor` builds its state from live subscriptions. Bounding pulse retrieval therefore cannot break crash recovery.
7. Callers need **three different access patterns**: latest-N tail (daemon audit, daemon history, CLI audit), complete ordered traversal (`replay_by_space`, verification scripts), and existence checks (taint). A single `LIMIT 1000` would silently cut off the complete-traversal consumers, so it is **not** an acceptable fix.
8. The schema has `idx_pulses_space_id (space_id)` but **no composite `(space_id, position)` index**, so ordered keyset reads within a Space cannot be served by an index range scan.

**Recommended remediation:** keyset (cursor) pagination on the authoritative `position`, a separate descending tail API, a streaming complete-traversal iterator that runs in a single snapshot, fail-closed limit validation, a fail-closed (never silently truncating) envelope on the legacy unpaged methods, an `EXISTS`-style taint query, and one composite index.

---

## 6. Current Pulse Architecture

### 6.1 Reconstructed Lifecycle

```text
Pulse emission (kernel / workers / orchestrator / channels)
      │
      ▼
PulseBus.publish()            ← in-memory bus (core/pulse_bus/bus.py) — default, non-durable
  or DurablePulseBus.publish() ← durable bus (durable_bus.py)
      │  1. validate type + payload (PULSE-001/002/003)
      │  2. TaintResolver.resolve_taint()  → store.get_by_id(parent) → store.read_by_correlation()  ⚠ unbounded
      │  3. store.append()                 → PostgreSQL INSERT ... ON CONFLICT DO NOTHING (AUTHORITATIVE)
      │  4. transport.publish()            → Redis XADD ryu:pulses:<space_id>            (TRANSPORT)
      │  5. store.mark_published()
      │  6. local subscriber dispatch      → Monitor / CLI follow / daemon            (LIVE VIEW)
      ▼
Read consumers
  ├─ channels/daemon/server.py:get_audit()      → read_by_space()  ⚠
  ├─ channels/daemon/history.py:get_history()   → read_by_space()  ⚠
  ├─ channels/cli/commands/audit.py              → read_by_space() / read(0)  ⚠ (read(0) is cross-Space)
  ├─ PulseReplayer.replay_by_space / replay_from → read_by_space() / read()  ⚠
  ├─ PulseReplayer.replay_causal_chain           → get_by_id() per hop (bounded by depth)
  └─ scripts/v1_run_vertical_slice.py            → read_by_space()  ⚠ (verification script)
```

### 6.2 Layer Classification

| Layer | Component | Role | Can serve history independently? |
|:---|:---|:---|:---|
| PostgreSQL `pulses` | `PostgresPulseStore` | **Authoritative record** (ADR-0002) | Yes — sole source of truth |
| Redis Streams | `RedisStreamTransport` | **Transport only** (at-least-once delivery) | No. Stream entries drop `source`, `timestamp`, and `parent_pulse_id` (`transport.py:65-79`), and `consume()` substitutes an epoch timestamp. Redis cannot reconstruct history. |
| `PulseBus._log` | in-memory bus | Process-local log, non-durable | In-process only; lost on restart |
| `InMemoryPulseStore` | test / fallback store | Non-durable mirror of the protocol | In-process only |
| `Monitor` | orchestrator | Derived live view | No; it is rebuilt from subscriptions |

**PostgreSQL remains the only source of truth.** Redis duplicates are possible (at-least-once delivery, ADR-0002), but Redis is never read for history, so it cannot cause duplicate or incomplete *history*.

---

## 7. PulseStore Implementation Audit

`core/pulse_bus/src/ryu/pulse_bus/store.py`

| Method | Protocol | `InMemoryPulseStore` | `PostgresPulseStore` | Bounded? |
|:---|:---|:---|:---|:---|
| `append` | ✓ | O(n) duplicate scan via `exists()` | INSERT … RETURNING position | n/a |
| `read(from_position)` | ✓ | `self._log[from_position:]` | `SELECT * WHERE position > %s ORDER BY position` + `fetchall()` | **No — global, cross-Space** |
| `read_by_correlation` | ✓ | list comprehension | `SELECT * WHERE correlation_id = %s ORDER BY position` | **No** |
| `read_by_space` | ✓ | list comprehension | `SELECT * WHERE space_id = %s ORDER BY position` (`:136`) | **No (F-03)** |
| `read_by_parent` | ✓ | list comprehension | `SELECT * WHERE parent_pulse_id = %s ORDER BY position` | **No** |
| `get_by_id` | ✓ | linear scan | `SELECT * WHERE id = %s` | Yes (≤1 row) |
| `get_unpublished(limit)` | ✓ | slice `[:limit]` | `… ORDER BY position LIMIT %s` | **Yes** (only bounded method) |

### 7.1 Semantic Parity Findings

| Aspect | InMemory | PostgreSQL | Parity |
|:---|:---|:---|:---|
| Position origin | `index + 1` (1-based) | `BIGSERIAL` from 1 | ✓ |
| `read(k)` | `_log[k:]` = positions > k | `position > k` | ✓ |
| Ordering | insertion order | `ORDER BY position ASC` | ✓ (single writer) |
| Position gaps | never | possible (sequence gaps on rollback / `ON CONFLICT`) | ✗. Cursors must use `position > after`, never offset arithmetic. |
| Concurrent-commit order | serialized | position assigned at INSERT, visible at COMMIT; commit order can differ from position order | ✗. See §14. |
| Limit validation | none (`[:limit]` accepts negatives) | `LIMIT -1` raises a PostgreSQL error | ✗ |

### 7.2 `position` Not Exposed

`_row_to_pulse()` drops `row[0]` (position). `Pulse` (`pulse.py`, a frozen Phase 0 contract) has no position field. **Consequence:** a caller cannot continue paging from the last pulse it received. The page result must therefore carry positions, and `Pulse` must not be modified (Phase 0 is frozen).

### 7.3 `SELECT *` and Row Shape

Every read uses `SELECT *`. That transfers `payload JSONB` (no store-level size bound was found in `store.py`, `validator.py`, or the payload schemas; search for `maxLength`, `max_payload`, `MAX_PULSE` returned nothing), plus `redis_published` and `created_at`, neither of which `Pulse` uses. `_row_to_pulse` also depends on column *order*, which is fragile.

### 7.4 Connection Model

`_get_conn()` opens a new psycopg2 connection per call, with no pooling. psycopg2's default **client-side cursor buffers the entire result set in client memory during `execute()`**, before `fetchall()` runs. Iterating the cursor would not reduce peak memory. Only a server-side (named) cursor streams rows.

---

## 8. PostgreSQL Schema / Index Audit

`deploy/migrations/001_create_pulses_table.sql` (no later migration touches `pulses`):

| Column | Type | Notes |
|:---|:---|:---|
| `position` | `BIGSERIAL PRIMARY KEY` | Canonical global order. Monotonic allocation; gaps possible. |
| `id` | `VARCHAR(255) UNIQUE` | Idempotency key |
| `space_id` | `VARCHAR(255) NOT NULL` | Isolation key |
| `type`, `severity`, `source` | VARCHAR | |
| `timestamp` | `TIMESTAMPTZ` | Producer clock; **not** an ordering authority |
| `payload` | `JSONB NOT NULL` | Unbounded size |
| `taint` | BOOLEAN | |
| `correlation_id`, `parent_pulse_id` | VARCHAR | |
| `redis_published` | BOOLEAN | Reconciliation flag |
| `created_at` | `TIMESTAMPTZ DEFAULT NOW()` | DB clock |

Indexes: `idx_pulses_space_id (space_id)`, `idx_pulses_correlation_id`, `idx_pulses_parent_pulse_id`, `idx_pulses_type`, partial `idx_pulses_redis_published WHERE redis_published = FALSE`.

**Retention / partitioning / archival:** none.

**Index efficiency for `WHERE space_id = ? AND position > ? ORDER BY position LIMIT ?`:**
- With `(space_id)` alone, the planner must either (a) read every matching heap row for the Space and sort, which is O(N_space) work even when the LIMIT is small, or (b) walk the primary key in position order and filter on `space_id`, which can scan rows from many other Spaces.
- **Architectural concern:** a composite `(space_id, position)` B-tree is required for O(log N + k) keyset reads and for backward (tail) scans. It is not created in this audit.
- Exact-type filtered tails (`goal.defined` in daemon history; `pulse_type` in daemon audit) would benefit from `(space_id, type, position)`. This is recommended as optional (see §23).

---

## 9. Every Unbounded Retrieval Path

| ID | Location | Query / Operation | Scope | Production reachable? |
|:---|:---|:---|:---|:---|
| **U-1** | `store.py:133` `PostgresPulseStore.read_by_space` | `SELECT * WHERE space_id=%s ORDER BY position` + `fetchall` | Space | **Yes** (daemon, CLI) |
| **U-2** | `store.py:116` `PostgresPulseStore.read` | `SELECT * WHERE position > %s ORDER BY position` + `fetchall` | **Global (all Spaces)** | **Yes** (CLI `audit stream` without `--space-id` → `read(0)`) |
| **U-3** | `store.py:123` `PostgresPulseStore.read_by_correlation` | `SELECT * WHERE correlation_id=%s …` | Correlation (not Space-filtered) | **Yes, hot path** via `TaintResolver` on tainted-parent publishes |
| **U-4** | `store.py:140` `PostgresPulseStore.read_by_parent` | `SELECT * WHERE parent_pulse_id=%s …` | Children of one pulse | Tests only |
| **U-5** | `replay.py:11` `PulseReplayer.replay_from` | `yield from store.read(from_position)`. **Looks like a generator but materializes the full list first.** | Global | Tests only |
| **U-6** | `replay.py:18` `PulseReplayer.replay_by_space` | `store.read_by_space(space_id)` | Space | Tests only |
| **U-7** | `store.py:34-44` `InMemoryPulseStore.read*` | Full list copies / scans | Process | Tests and CLI fallback |

**Bounded / non-issues:**
- `get_unpublished(limit)` is bounded. `DurablePulseBus.reconcile_unpublished(limit=100)` uses it.
- `get_by_id` returns at most one row.
- `replay_causal_chain` is bounded by causal depth: one `get_by_id` per hop, with cycle detection.
- Redis `consume()` (`count=`) and `reclaim_stale()` (`XAUTOCLAIM`, whose server default COUNT is 100) are bounded.

**Not F-03, but recorded:** see §27, "Newly identified concerns."

## 10. Every Caller of the Unbounded Paths

| Caller | Path | What it actually needs | Current behavior |
|:---|:---|:---|:---|
| `channels/daemon/server.py:1011` `get_audit(space_id, limit=50, pulse_type)` | U-1 | **Latest N** (descending), optional exact type | Loads the whole Space, reverses in Python, takes N |
| `channels/daemon/history.py:107` `get_history(space_id, limit=100)` | U-1 | **Latest N `goal.defined`** pulses (fallback rehydration) | Loads the whole Space, filters by type, takes the last N |
| `channels/cli/commands/audit.py:39` | U-1 | **Latest N** (`--limit 50`), optional type **prefix** | Loads the whole Space, filters, slices `[-limit:]` |
| `channels/cli/commands/audit.py:41` | U-2 | Latest N across **all Spaces** (operator view) | Loads the **entire pulse table** |
| `taint.py:20` `TaintResolver.is_taint_cleared` | U-3 | **Existence** of a `security.taint.cleared` pulse for a correlation | Loads every pulse in the correlation and filters in Python |
| `PulseReplayer.replay_by_space` / `replay_from` | U-5/U-6 | **Complete ordered traversal** | Materializes everything |
| `scripts/v1_run_vertical_slice.py:341,416` | U-1 | Latest `task.assigned`; count ≥ 4 | Small verification Space |
| Tests (`test_store_interface.py`, `test_misc.py`, `test_replay_unit.py`, `test_v101_hardening.py`) | U-1/U-2/U-5/U-6 | Correctness on small fixtures | — |

**Conclusion:** no production caller needs the full history *as a single in-memory list*. Every production read caller needs either a bounded tail or an existence check. Complete traversal is needed only by the replay API, and it can be served in bounded pages.

---

## 11. Recovery / Replay Impact

| Component | Reads pulse history? | Evidence |
|:---|:---|:---|
| `StartupRecoveryEngine` | **No** | Reads `attempt_store.get_interrupted_attempts()`; only **publishes** `recovery.*` pulses (`startup_recovery.py:107-112, 169`) |
| Plan reconstruction | **No** | `PostgresPlanStore` / `plans` table (ADR-0045) |
| Checkpoint restore | **No** | `SpaceKernel.restore_checkpoint` → `plan_store.restore_graph()` |
| Execution recovery | **No** | `execution_attempts` table (ADR-0042) |
| Convergence recovery | **No** | In-memory (F-06, out of scope) |
| `Monitor` | **No** (live subscription) | `monitor.py:46 handle_pulse` |
| Redis reconciliation | Bounded | `get_unpublished(limit)` |
| `PulseReplayer` | **Yes** | U-5, U-6; causal chain is bounded |

**Exact requirement:** current recovery needs **no** pulse history. Replay needs (a) causal-chain reconstruction (already bounded), (b) correlation replay (PULSE-005/009), and (c) ordered Space traversal from a known position. Requirement (c) must stay *complete*, so it must be paginated, never truncated. If a future phase rebuilds state from pulses (for example, F-06 convergence reconstruction), it must use the complete-traversal iterator defined in §21, starting from a checkpointed `position`.

## 12. Memory / OOM Risk

- **Materialization:** the full Space result set is held in memory up to three times at once: the psycopg2 client buffer, the row tuples from `fetchall()`, and the `list[Pulse]` (plus a parsed `dict` per payload). `get_audit` then iterates `reversed(pulses)` over that list.
- **Driver:** client-side cursor, so there is no streaming (§7.4).
- **Payload:** `JSONB`, with no store-level size bound found.
- **Growth:** the `pulses` table has no retention (§8). The Phase 15 audit (§13, Chain 5) estimates about 5 pulses per task. That figure is the Phase 15 audit's estimate and was not re-measured here.
- **Multiplication:** each daemon audit or history request triggers a full read. Concurrent requests across Spaces (or for the same Space) each materialize their own full copy. Nothing limits concurrency.
- **Hot path:** U-3 runs during `publish()`, so a long correlation chain under taint adds O(N_correlation) memory and I/O to every tainted publish.
- **Complexity:** current per-request memory is **O(N_space × avg_payload)**, or **O(N_total)** for CLI `read(0)`. The target is **O(page_size × avg_payload)** for every ordinary API.

No numerical benchmark claims are made. See §19 for what must be measured.

## 13. Space Isolation Analysis

| Path | Space-filtered? | Risk |
|:---|:---|:---|
| U-1 `read_by_space` | `WHERE space_id = %s` | Isolated |
| U-2 `read` | **No** | CLI `audit stream` without `--space-id` reads every Space. This is an operator surface. Who may run global audit is an authority question outside F-03 (see §27). Any bounded replacement must be explicitly labeled global/operator. |
| U-3 `read_by_correlation` | No Space predicate | Correlation IDs are assumed unique per execution. A collision across Spaces would let a clearance pulse in Space B clear taint in Space A. This is a pre-existing, latent Law 1 concern. The bounded replacement must add a `space_id` predicate. |
| U-4 `read_by_parent` | No Space predicate | Children of a parent pulse ID; a cross-Space parent link is unusual but not prevented. |

**Thought experiment (Space A and Space B):** identical pulse types, task IDs, and timestamps; concurrent writers; concurrent readers.
- Positions are global and unique (BIGSERIAL), so no two pulses share a position.
- A keyset predicate `space_id = $1 AND position > $2` cannot return Space B rows to a Space A reader, **whatever cursor value is supplied**. A cursor taken from Space B can only make a Space A reader *skip* Space A rows below that position. It can never leak data.
- **Rule for the design:** isolation is enforced by the SQL predicate, not by secrecy of the cursor. Cursors are plain integers, are not authority tokens, and need no signing. Every bounded query **must** include `space_id = %s` as a mandatory bound parameter, and validation must reject an empty `space_id`.
- `timestamp` must never be used as a cursor, because identical timestamps across Spaces and producer-clock skew make it non-deterministic.

## 14. Ordering / Determinism Analysis

- **Authoritative order:** `position ASC`. `timestamp` and `created_at` are not ordering authorities.
- **Within a quiescent Space:** keyset pages over `position` are deterministic, with no duplicates and no gaps.
- **Late-commit hazard (real):** `BIGSERIAL` values are allocated at INSERT time but become visible at COMMIT. With concurrent appenders (each `append` uses its own connection and transaction), position 11 can commit before position 10. A paginating reader that has already returned up to 11 resumes with `position > 11` and **permanently skips 10**.
  - **Complete traversal:** the target design runs the whole iteration inside **one `REPEATABLE READ` read-only transaction using a server-side named cursor**. Every page then comes from the same MVCC snapshot, which removes skips and duplicates *within* one traversal.
  - **Stateless cross-call pages (tail / page APIs):** they are *not* snapshot-consistent across calls. This is acceptable because every current caller of these APIs is display or audit. No correctness-critical incremental consumer of stored pulses exists (§11). The contract must **state this explicitly**. Any future incremental consumer must either use the snapshot iterator or re-read with an overlap window.
- **InMemory parity:** a single-process append is serialized, so InMemory never shows the hazard. Parity tests must cover result semantics; the late-commit case is a PostgreSQL-only integration test.
- **Duplicates:** `ON CONFLICT (id) DO NOTHING` ensures one row per pulse ID, so pages never contain the same pulse twice.

## 15. Contract Impact

| Item | Current state | Phase 15.3 impact |
|:---|:---|:---|
| Pulse retrieval contract | **None.** PULSE-008 (durable persistence) and PULSE-009 (replay) say nothing about bounds. SPACE-005 covers scoped retrieval only. | **New contracts required** (§22) |
| `PulseStore` protocol | No bounded methods | Additive extension, plus a behavioral change to legacy methods (fail-closed above the cap). **Requires ADR.** |
| Pulse registry / payload schemas | 50 types | **No change.** Bound violations are raised to the caller as typed exceptions; no new pulse type is needed. |
| DB schema | No composite index | **Migration `008`** (index only; no column changes) |
| `Pulse` dataclass | Frozen Phase 0 | **Unchanged.** Positions travel in a new page type. |
| `CONTRACT_MATRIX.md`, `spec_map.yaml` | — | New entries in the implementation phase |
| ADR | — | **ADR-0047** required: it changes persistence/retrieval semantics and refines ADR-0002 replay semantics |
| Project Memory | — | **0029** at completion (material milestone) |
| Backward compatibility | — | `read_by_space(space_id)` keeps its signature and its full-result behavior up to the cap. Above the cap it **raises** instead of materializing. This is an intentional, explicit (non-silent) semantic change, recorded in the ADR. |

## 16. Test Coverage Analysis

Baseline: `core/pulse_bus/tests` — **39 passed** (executed during this audit). Integration harness `harness/cases/pulse_bus_integration/` requires `RYU_INTEGRATION_TESTS=1` and was not run in this audit.

| Scenario | Covered? | Where |
|:---|:---|:---|
| Empty Space | ✗ | — |
| One pulse | Partial | `test_append_and_retrieve` |
| Normal Space / ordering | ✓ (InMemory) | `test_ordering`, `test_read_by_space` |
| Long-lived Space (many pulses) | ✗ | — |
| Limit behavior on Space reads | ✗ | — (only `get_unpublished` is exercised indirectly) |
| Deterministic ordering (PostgreSQL) | ✗ | — |
| Duplicate pulses | ✓ | `test_duplicate_append_idempotent`, `test_duplicate_pulse_id` |
| Multiple Spaces | ✓ (PostgreSQL, 2 Spaces) | `test_space_scoped_retrieval` |
| Concurrent writers / readers | ✗ | — |
| Replay | ✓ (InMemory) | `test_replay_*`, `test_durable_replay.py` |
| Recovery | n/a (does not read pulses) | — |
| InMemory vs PostgreSQL parity | ✗ | — |
| Redis interaction during reads | ✗ (not applicable to reads) | — |
| Malformed / negative / zero / oversized limit | ✗ | — |
| Invalid / cross-Space cursor | ✗ | — |
| Large payload | ✗ | — |

**The current suite does not prove bounded retrieval in any way.**

## 17. Adversarial Threat Model

| ID | Vector | Current outcome | Required outcome |
|:---|:---|:---|:---|
| ADV-PULSE-01 | Unbounded read request (`read_by_space` on a huge Space) | Full materialization | Raise `PulseRetrievalBoundExceeded`; never materialize more than cap + 1 rows |
| ADV-PULSE-02 | Oversized limit (`limit=10**9`) | n/a | `ValueError`, no query issued |
| ADV-PULSE-03 | Negative limit | PostgreSQL error; InMemory silently slices | `ValueError` in both stores |
| ADV-PULSE-04 | Invalid cursor (negative, str, float, `bool`) | n/a | `ValueError` / `TypeError`; `bool` explicitly rejected |
| ADV-PULSE-05 | Cross-Space cursor (Space B's position used in Space A) | n/a | Returns only Space A rows with position > cursor; zero Space B rows |
| ADV-PULSE-06 | Cross-Space retrieval (empty / wildcard `space_id`, `%`, `'`) | Parameterized, safe | Empty `space_id` rejected; `%` treated literally |
| ADV-PULSE-07 | Duplicate / rewound cursor | n/a | Same query returns the same page (idempotent); no state change |
| ADV-PULSE-08 | Extremely large payloads | Unbounded | Page memory is bounded by row count. Payload size bounding is **deferred** (no evidence-based limit exists). The residual risk is documented. |
| ADV-PULSE-09 | Concurrent readers | Each reader gets a full copy | Each reader is bounded to O(page) |
| ADV-PULSE-10 | Long-lived Space with huge history | OOM risk | Tail and page APIs keep latency and memory O(page) with the composite index |
| ADV-PULSE-11 | Late-commit gap (concurrent appenders) | n/a | Snapshot iterator yields no gaps or duplicates within one traversal (PostgreSQL integration test) |
| ADV-PULSE-12 | Type confusion in limit (`True`, `"100"`, `1.5`) | n/a | Rejected (`bool` is a subclass of `int` in Python and must be excluded explicitly) |
| ADV-PULSE-13 | `LIKE` wildcard injection in type-prefix filter (`%`, `_`) | n/a | Prefix escaped; `LIKE … ESCAPE '\'` |
| ADV-PULSE-14 | Cross-Space taint clearance via colliding `correlation_id` | Possible (no Space predicate on U-3) | Clearance lookup scoped by `space_id` |

## 18. Retention / Archival Boundary

- **Retrieval boundedness** (F-03) concerns how many rows one call can load into memory. **Storage growth** concerns how many rows the table holds. The two are related but separate.
- Bounded retrieval alone fully removes the OOM risk named in F-03. With keyset reads on a composite index, query cost depends on page size, not Space size.
- Retention, archival, deletion, compaction, and partitioning are **not required** for F-03's correctness. Deleting history would also interact with PULSE-009 (replay) and the audit-trail requirement, so it needs its own ADR and governance policy.
- **Decision:** retention, archival, partitioning, and Redis stream trimming (`XADD` without `MAXLEN`, `transport.py:75`) are **deferred**. They are recorded in §27.

## 19. Performance Analysis

| Dimension | Current | Target |
|:---|:---|:---|
| DB work per Space read | O(N_space) rows read and sorted (or a PK scan with filter) | O(log N + k) index range scan with `(space_id, position)` |
| Tail (latest N) | O(N_space) transfer, then reverse in Python | Backward index scan, `ORDER BY position DESC LIMIT k` |
| Network transfer | All columns × N_space | Explicit columns × k |
| Python memory | O(N_space) × 3 copies | O(k) |
| Taint check | O(N_correlation) rows per tainted publish | `SELECT 1 … LIMIT 1` on an index |
| Pagination overhead | n/a | One connection per stateless page call (no pool). The snapshot iterator uses one connection for the whole traversal. Pooling is deferred. |
| Cursor stability | n/a | Stable (immutable positions); see the late-commit caveat in §14 |

**Benchmarks to run in Phase 15.3 implementation** (report measured numbers only):
1. `EXPLAIN (ANALYZE, BUFFERS)` for the page, tail, and typed-tail queries, before and after migration 008, on a seeded Space (for example, 100k pulses in the target Space plus 100k in another Space).
2. Peak RSS of `read_space_tail(limit=50)` compared with the legacy `read_by_space` on the same seed (`tracemalloc`).
3. Full traversal of the seeded Space via `iter_space`: peak memory must stay roughly constant as Space size grows.

## 20. Failure / Recovery Analysis

| Failure | Proposed behavior | Deterministic / recoverable? |
|:---|:---|:---|
| DB connection fails on page 1 | psycopg2 `OperationalError` propagates; nothing is returned (Law 6, never silent) | ✓; caller retries |
| DB fails on page N (stateless pages) | Error propagates; the caller still holds `next_after_position` from page N−1 and can resume | ✓ |
| DB fails mid-iteration (snapshot iterator) | Transaction aborts; error propagates; the iterator must not yield a partial page as complete | ✓. Restart from the last yielded position (gives up snapshot consistency across the restart; documented). |
| Stale cursor (positions older than every row) | Returns the next rows normally | ✓ |
| Pulse inserted between stateless pages | Appears in a later page if its position > cursor; skipped if it committed late with a lower position (§14) | Documented. The snapshot iterator avoids this. |
| Pulse ordering changes | Impossible: `position` is immutable and the store has no UPDATE of position | ✓ |
| Space deleted / restored | Pulses are never deleted (no retention). A restored Space keeps its rows and positions. | ✓ |
| Redis unavailable | Reads never touch Redis | ✓ unaffected |
| PostgreSQL temporarily unavailable | Errors propagate. **No silent fallback to InMemory on the read path.** (CLI context falls back to InMemory only at *construction* time. That behavior is pre-existing and noted as an observation.) | ✓ |

---

## 21. Proposed Target Architecture

### 21.1 Constants and Configuration (owner: `ryu.pulse_bus.config`, core layer)

| Constant | Value | Justification |
|:---|:---|:---|
| `DEFAULT_PULSE_PAGE_SIZE` | **100** | Matches existing repository bounds: `reconcile_unpublished(limit=100)`, `get_history(limit=100)`. Existing audit callers use 50, which falls within range. |
| `MAX_PULSE_PAGE_SIZE` | **1000** | Hard ceiling on a single materialization. No repository evidence supports a larger value. 10× the default leaves room for operators without unbounded growth. Overridable via env `RYU_PULSE_PAGE_MAX`, which is clamped to `[1, 10_000]`; invalid values fail at config load. |
| `MIN_PULSE_PAGE_SIZE` | 1 | |
| Legacy unpaged cap | = `MAX_PULSE_PAGE_SIZE` | Legacy methods fetch `cap + 1` rows. If more exist, they raise. |

**Over-limit requests are rejected (`ValueError`), not clamped.** Clamping would silently change the caller's semantics.

### 21.2 New Types (in `store.py`; `Pulse` unchanged)

```python
@dataclass(frozen=True)
class StoredPulse:
    position: int
    pulse: Pulse

@dataclass(frozen=True)
class PulsePage:
    space_id: str
    entries: tuple[StoredPulse, ...]
    next_after_position: int | None   # None when exhausted (ascending)
    next_before_position: int | None  # None when exhausted (descending tail)
    has_more: bool

class PulseRetrievalBoundExceeded(RuntimeError): ...
```

### 21.3 PulseStore Protocol — Additive Methods

```python
def read_space_page(self, space_id: str, *, after_position: int = 0,
                    limit: int = DEFAULT_PULSE_PAGE_SIZE,
                    pulse_types: frozenset[str] | None = None) -> PulsePage
    # WHERE space_id=%s AND position > %s [AND type = ANY(%s)] ORDER BY position ASC LIMIT limit+1

def read_space_tail(self, space_id: str, *, limit: int = DEFAULT_PULSE_PAGE_SIZE,
                    before_position: int | None = None,
                    pulse_types: frozenset[str] | None = None,
                    type_prefix: str | None = None) -> PulsePage
    # WHERE space_id=%s [AND position < %s] [AND type filter] ORDER BY position DESC LIMIT limit+1
    # entries returned in ASCENDING order for display consistency

def iter_space(self, space_id: str, *, after_position: int = 0,
               page_size: int = DEFAULT_PULSE_PAGE_SIZE) -> Iterator[StoredPulse]
    # PostgreSQL: one REPEATABLE READ READ ONLY transaction, server-side named cursor,
    # itersize = page_size; complete ordered traversal, O(page_size) memory.

def has_pulse_of_type(self, space_id: str, correlation_id: str, pulse_type: str) -> bool
    # SELECT 1 ... WHERE space_id=%s AND correlation_id=%s AND type=%s LIMIT 1

def read_page(self, *, after_position: int = 0, limit: int = DEFAULT_PULSE_PAGE_SIZE) -> PulsePage
    # Explicitly GLOBAL / operator-scope bounded read (replaces CLI read(0))
```

`has_more` is computed by fetching `limit + 1` rows, which avoids a `COUNT(*)`.

### 21.4 Legacy Methods — Fail-Closed Envelope (no silent truncation)

`read_by_space`, `read`, `read_by_correlation`, and `read_by_parent` keep their signatures. Each fetches at most `cap + 1` rows. If the result exceeds `cap`, it **raises `PulseRetrievalBoundExceeded`** with the Space/correlation ID and the cap. It never returns a truncated list.

### 21.5 Caller Migration (production)

| Caller | New API |
|:---|:---|
| `server.get_audit` | `read_space_tail(space_id, limit, pulse_types={pulse_type} if pulse_type else None)` |
| `history.get_history` | `read_space_tail(space_id, limit, pulse_types={"goal.defined"})` |
| CLI `audit stream --space-id` | `read_space_tail(space_id, limit, type_prefix=args.type)` |
| CLI `audit stream` (global) | `read_page`-based global tail, labeled operator-scope |
| `TaintResolver.is_taint_cleared` | `has_pulse_of_type(pulse.space_id, correlation_id, "security.taint.cleared")`. Semantically equivalent to the current any-clearance-exists check, plus a Space predicate. |
| `PulseReplayer.replay_by_space` / `replay_from` | Backed by `iter_space` / paged `read_page`. `replay_from` becomes a true generator. |

Channels use `hasattr`-based duck typing; the migration must keep the InMemory fallback working.

### 21.6 InMemoryPulseStore Mirror

Same validation, ordering, `limit + 1` / `has_more` semantics, cursor semantics, filter semantics, and exceptions. Positions stay `index + 1`. `iter_space` yields in pages from a snapshot copy of the matching index range.

### 21.7 Redis

**No change.** Redis is not a history source.

### 21.8 Validation (both stores, before any I/O)

- `space_id`: non-empty `str`.
- `limit` / `page_size`: `type(x) is int` (rejects `bool`), `1 ≤ x ≤ MAX`.
- `after_position` / `before_position`: `type(x) is int`, `≥ 0`.
- `pulse_types`: set of non-empty `str`.
- `type_prefix`: non-empty `str`, `LIKE`-escaped.

## 22. Proposed API / Contract Changes

New Pulse Bus contracts (continuing the existing PULSE series, after PULSE-012; no conflicts found):

| ID | Contract | Invariant |
|:---|:---|:---|
| PULSE-013 | Bounded Space retrieval | No ordinary PulseStore Space read materializes more than `MAX_PULSE_PAGE_SIZE` pulses |
| PULSE-014 | Deterministic keyset order | Space pages are ordered by `position ASC`. The cursor is `position`, the predicate always includes `space_id`, and a cross-Space cursor yields zero foreign rows. |
| PULSE-015 | Complete bounded traversal | `iter_space` yields every Space pulse exactly once, in order, from one snapshot, with O(page) memory |
| PULSE-016 | Fail-closed bounds validation | Invalid, zero, negative, oversized, or type-confused limits and cursors are rejected before I/O in both stores |
| PULSE-017 | No silent truncation | Legacy unpaged reads raise `PulseRetrievalBoundExceeded` above the cap; they never return partial history |

PULSE-009 (replay) remains satisfied through `iter_space`. The ADR must record the snapshot/late-commit semantics (§14) as a refinement of ADR-0002.

## 23. Proposed Database / Index Changes

`deploy/migrations/008_pulses_space_position_index.sql` (implementation phase):

```sql
CREATE INDEX IF NOT EXISTS idx_pulses_space_position ON pulses (space_id, position);          -- required
CREATE INDEX IF NOT EXISTS idx_pulses_space_type_position ON pulses (space_id, type, position); -- recommended (typed tails)
CREATE INDEX IF NOT EXISTS idx_pulses_space_corr_type ON pulses (space_id, correlation_id, type); -- recommended (taint EXISTS)
```

- No column changes. No data migration.
- `idx_pulses_space_id` becomes redundant once the composite index exists. **Keep it** in Phase 15.3; dropping it is a separate decision.
- Production note: use `CREATE INDEX CONCURRENTLY` outside a transaction block for large live tables. The dev `docker-entrypoint-initdb.d` path can use plain `CREATE INDEX`.

## 24. Proposed Tests (`core/pulse_bus/tests/test_phase15_3_bounded_pulse_retrieval.py` plus PostgreSQL integration)

Test IDs use the `PBR-` prefix because `PULSE-001…012` are existing contract IDs.

| Test ID | Store | Assertion |
|:---|:---|:---|
| PBR-001 | both | Empty Space → empty page, `has_more=False`, cursors `None` |
| PBR-002 | both | Single pulse page |
| PBR-003 | both | `len(entries) ≤ limit` for limit ∈ {1, 50, 100, 1000} |
| PBR-004 | both | Ascending order by position; positions strictly increasing |
| PBR-005 | both | Paging from cursor 0 to exhaustion yields every pulse exactly once |
| PBR-006 | both | Tail returns the latest N, presented in ascending order |
| PBR-007 | both | Tail `before_position` pagination reaches the oldest pulse |
| PBR-008 | both | Space isolation: A and B interleaved with identical types and task IDs; no foreign rows in any page or tail |
| PBR-009 | both | Exact-type filter returns `limit` matches even when non-matching pulses are interleaved |
| PBR-010 | both | `iter_space` traversal is complete and ordered |
| PBR-011 | both | Legacy `read_by_space` returns the full list when ≤ cap |
| PBR-012 | both | Legacy `read_by_space` raises `PulseRetrievalBoundExceeded` when > cap (cap lowered via config for the test) |
| PBR-013 | both | Legacy `read`, `read_by_correlation`, `read_by_parent` use the same envelope |
| PBR-014 | both | `has_pulse_of_type` gives the same truth table as the current `is_taint_cleared` (equivalence test); taint preservation tests still pass |
| PBR-015 | parity | InMemory and PostgreSQL produce identical `(position order, ids, has_more)` for the same seeded sequence |
| PBR-016 | PG | Duplicate append does not create duplicate page entries |
| PBR-017 | PG | `EXPLAIN` of the page query uses `idx_pulses_space_position` (no Seq Scan / Sort) on the seeded table |
| PBR-018 | PG | Deterministic: repeated identical page requests return identical results |
| PBR-019 | both | `replay_from` is a true generator (memory bounded; first item yielded before the full scan) |
| PBR-020 | regression | Existing `test_store_interface`, `test_replay_unit`, `test_taint_module`, `test_durable_bus_unit`, daemon/CLI audit and history tests all pass unchanged in assertion strength |

## 25. Proposed Adversarial Tests

ADV-PULSE-01 through ADV-PULSE-14 (§17), each a dedicated test. ADV-PULSE-11 (late commit) is a PostgreSQL integration test: two connections, where the lower position commits after the higher one, and the snapshot iterator is checked for no gaps.

## 26. Phase 15.3 Implementation Sequence

1. **ADR-0047** (Bounded Pulse Retrieval & Keyset Pagination), written before code. It refines ADR-0002 replay semantics and records the legacy fail-closed change and the snapshot semantics.
2. `CONTRACT_MATRIX.md` PULSE-013…017; `spec_map.yaml` entries.
3. `config.py` constants and env override with validation.
4. `store.py`: types, validation helper, InMemory implementation, PostgreSQL implementation (explicit column list, named cursor for `iter_space`), legacy envelope.
5. Migration `008`; apply to dev PostgreSQL.
6. `taint.py` → `has_pulse_of_type` (equivalence-tested first).
7. `replay.py` → `iter_space` / generator `replay_from`.
8. Channel caller migration (`server.py`, `history.py`, `cli/commands/audit.py`), keeping the `hasattr` fallback.
9. Tests PBR-001…020 and ADV-PULSE-01…14; run integration with `RYU_INTEGRATION_TESTS=1`.
10. Regression: pulse bus, harness pulse cases, channels, Phase 12/14/15.x suites.
11. Gates: `dep_guard`, `ruff`, targeted `mypy`, `contract_sync`, spec coverage, governance.
12. Verification report, Project Memory 0029, commit.

## 27. Explicit Non-Goals

Not part of Phase 15.3:
- F-04 concurrent DAG scheduler; F-05 semantic memory; F-06 durable convergence state; F-07 agent hierarchy.
- Repository locking; MCP redesign; Neo4j/Qdrant; voice; multimodal; new worker types.
- Retention, archival, deletion, compaction, partitioning; Redis `MAXLEN` trimming.
- Connection pooling; payload size limits; general DB optimization.
- Changes to `Pulse`, `bus.py` publish semantics, or the pulse registry.

**Newly identified concerns. These are not Phase 15 findings and have no assigned ID or severity. They are recorded for future triage only.**
- **NC-A:** `PulseBus._log` (in-memory bus, used by default by `SoftwareEngineeringWorkflow`, `workflows/software_engineering.py:134`) grows without bound, and `_resolve_taint` rebuilds a full `{id: pulse}` index on every publish (`bus.py:244`). This is in-process retention, not store retrieval.
- **NC-B:** `TaintResolver.is_taint_cleared` ignores clearance *position* (a documented Phase 1 simplification). The Phase 15.3 `EXISTS` replacement must preserve current semantics exactly and must not quietly "fix" this.
- **NC-C:** CLI `audit stream` without `--space-id` exposes a global, cross-Space view. Who may run global audit is an authority question.
- **NC-D:** Redis streams have no `MAXLEN`, so storage grows. This belongs to retention.

## 28. Risks

| Risk | Impact | Mitigation |
|:---|:---|:---|
| Legacy `read_by_space` raising breaks an unmigrated caller | Visible error instead of OOM | Migrate every production caller in the same phase. The raise is intentional (Law 6), and PBR-012 proves it. |
| Taint semantic drift from the query change | Security regression (PULSE-006/007) | Equivalence test PBR-014 written *before* the swap; existing taint tests stay green |
| Adding a `space_id` predicate to the taint lookup changes behavior for cross-Space correlation collisions | Previously cross-Space clearances applied; now they do not | Intended Law 1 tightening, recorded in the ADR. Existing tests use a single Space. |
| Named-cursor transaction held open during a slow consumer | Long-running snapshot, vacuum delay | Document; `iter_space` callers are replay tools. Consider a statement timeout. |
| Index build on large live tables | Locking | `CONCURRENTLY` in production |
| Late-commit skips in stateless paging | Display may miss a pulse | Documented (§14); snapshot iterator for completeness |
| Env override misconfiguration | Unbounded reads re-enabled | Clamp to `[1, 10_000]`; fail at load |

## 29. Acceptance Criteria (proposed — not yet satisfied)

1. No production `PulseStore` history query can materialize more than `MAX_PULSE_PAGE_SIZE` pulses through an ordinary API.
2. Every PostgreSQL pulse history retrieval is bounded (`LIMIT`), explicitly paginated, or streamed through a snapshot cursor.
3. Space pages are ordered deterministically by `position`, and every Space query includes `space_id`.
4. Cross-Space cursors and filters return zero foreign rows (PBR-008, ADV-PULSE-05/06).
5. Complete traversal (`iter_space`) yields every pulse exactly once, in order, from one snapshot (PBR-010, ADV-PULSE-11).
6. Legacy unpaged reads never silently truncate (PBR-012/013).
7. InMemory and PostgreSQL semantics agree (PBR-015).
8. Invalid, oversized, negative, zero, or type-confused bounds fail closed before I/O (ADV-PULSE-02/03/04/12).
9. Taint semantics are unchanged apart from the documented Space predicate (PBR-014; existing taint suites pass).
10. Recovery and replay semantics stay correct: all existing replay, recovery, and Phase 12/14/15.1/15.2 suites pass.
11. The page query uses the composite index (PBR-017 `EXPLAIN` evidence).
12. Governance gates pass: `dep_guard`, `ruff`, targeted `mypy`, `contract_sync`, spec coverage, governance audit.

## 30. Baseline Verification (executed during this audit)

| Command | Result |
|:---|:---|
| `python scripts/dep_guard.py` | PASS — no forbidden imports in `core/` |
| `python scripts/contract_sync.py` | PASS — 38/38 architecture types in registry (12 registry-only types warned, pre-existing F-08) |
| `python scripts/v1_audit_spec_coverage.py` | V1-001 PASS — 235 contract IDs, 193 spec-map entries, 0 orphaned, 0 duplicates |
| `python scripts/v1_audit_governance.py` | V1-005 PASS — ADR inventory 0001..0046 |
| `pytest core/pulse_bus/tests` | 39 passed |

No code was modified to obtain these results.

## 31. Final Gate Recommendation

The target architecture is defined: keyset pagination on `position`, a descending tail, a single-snapshot streaming traversal, an `EXISTS` taint query, and a fail-closed legacy envelope. Also defined are the evidence-based defaults (100 / 1000, reject-over-limit), the persistence semantics (no schema change; one required composite index), the recovery semantics (recovery reads no pulse history; replay stays complete), the late-commit consistency semantics, the contracts (PULSE-013…017; ADR-0047), the test and adversarial strategy, and the acceptance criteria. Implementation does not need to make any open architectural decisions.

**PHASE 15.3 AUDIT — READY FOR IMPLEMENTATION**
