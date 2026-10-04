"""Phase 15.3 Test Suite: Bounded Pulse Retrieval (Finding F-03 — P1).

Verifies PULSE-013 through PULSE-017, PBR-001 through PBR-020, and ADV-PULSE-01 through ADV-PULSE-14.
Governed by: docs/PHASE_15_3_ARCHITECTURE_AUDIT.md, ADR-0047, CONTRACT PULSE-013..017.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from ryu.pulse_bus.config import (
    MAX_PULSE_PAGE_SIZE,
    PostgresConfig,
)
from ryu.pulse_bus.pulse import Pulse, Severity
from ryu.pulse_bus.replay import PulseReplayer
from ryu.pulse_bus.store import (
    InMemoryPulseStore,
    PostgresPulseStore,
    PulsePage,
    PulseRetrievalBoundExceeded,
    StoredPulse,
)
from ryu.pulse_bus.taint import TaintResolver


def _create_pulse(
    pulse_id: str,
    space_id: str = "space-1",
    pulse_type: str = "task.created",
    correlation_id: str = "corr-1",
    parent_pulse_id: str | None = None,
    taint: bool = False,
    payload: dict | None = None,
) -> Pulse:
    return Pulse(
        id=pulse_id,
        space_id=space_id,
        type=pulse_type,
        severity=Severity.INFO,
        source="test",
        timestamp=datetime.now(timezone.utc),
        payload=payload or {"msg": f"pulse_{pulse_id}"},
        taint=taint,
        correlation_id=correlation_id,
        parent_pulse_id=parent_pulse_id,
    )


# ============================================================================
# PBR-001..010: Core Bounded Retrieval Semantics (PULSE-013, PULSE-014, PULSE-015)
# ============================================================================


def test_pbr_001_empty_space() -> None:
    """PBR-001: Empty Space returns empty page, has_more=False, next cursors None."""
    store = InMemoryPulseStore()
    page = store.read_space_page("empty-space")
    assert isinstance(page, PulsePage)
    assert page.space_id == "empty-space"
    assert len(page.entries) == 0
    assert page.has_more is False
    assert page.next_after_position is None
    assert page.next_before_position is None

    tail = store.read_space_tail("empty-space")
    assert len(tail.entries) == 0
    assert tail.has_more is False
    assert tail.next_after_position is None
    assert tail.next_before_position is None


def test_pbr_002_single_pulse() -> None:
    """PBR-002: Single pulse in Space returns 1 entry with exact position."""
    store = InMemoryPulseStore()
    p = _create_pulse("p-1", space_id="s1")
    pos = store.append(p)
    assert pos == 1

    page = store.read_space_page("s1", limit=10)
    assert len(page.entries) == 1
    assert page.has_more is False
    assert page.next_after_position is None
    assert page.entries[0].position == 1
    assert page.entries[0].pulse.id == "p-1"


def test_pbr_003_limit_enforcement() -> None:
    """PBR-003: len(entries) <= limit for all valid limits."""
    store = InMemoryPulseStore()
    for i in range(25):
        store.append(_create_pulse(f"p-{i}", space_id="s1"))

    for limit in [1, 5, 10, 20]:
        page = store.read_space_page("s1", limit=limit)
        assert len(page.entries) == limit
        assert page.has_more is True
        assert page.next_after_position == limit


def test_pbr_004_ascending_keyset_order() -> None:
    """PBR-004: Positions strictly increasing in ascending keyset order."""
    store = InMemoryPulseStore()
    for i in range(10):
        store.append(_create_pulse(f"p-{i}", space_id="s1"))

    page = store.read_space_page("s1", limit=10)
    positions = [entry.position for entry in page.entries]
    assert positions == sorted(positions)
    assert len(positions) == len(set(positions))


def test_pbr_005_cursor_exhaustion_completeness() -> None:
    """PBR-005: Paging from cursor 0 to exhaustion yields every pulse exactly once."""
    store = InMemoryPulseStore()
    total = 35
    for i in range(total):
        store.append(_create_pulse(f"p-{i}", space_id="s1"))

    collected: list[StoredPulse] = []
    cursor = 0
    while True:
        page = store.read_space_page("s1", after_position=cursor, limit=10)
        collected.extend(page.entries)
        if not page.has_more:
            break
        assert page.next_after_position is not None
        cursor = page.next_after_position

    assert len(collected) == total
    assert [entry.pulse.id for entry in collected] == [f"p-{i}" for i in range(total)]


def test_pbr_006_tail_latest_n_in_ascending_presentation() -> None:
    """PBR-006: Tail returns latest N pulses presented in ascending chronological order."""
    store = InMemoryPulseStore()
    for i in range(20):
        store.append(_create_pulse(f"p-{i}", space_id="s1"))

    tail = store.read_space_tail("s1", limit=5)
    assert len(tail.entries) == 5
    assert tail.has_more is True
    # Should be the last 5 pulses: p-15..p-19 in ascending order
    assert [e.pulse.id for e in tail.entries] == [f"p-{i}" for i in range(15, 20)]


def test_pbr_007_tail_pagination_before_position() -> None:
    """PBR-007: Tail before_position pagination walks backward to oldest pulse."""
    store = InMemoryPulseStore()
    for i in range(25):
        store.append(_create_pulse(f"p-{i}", space_id="s1"))

    collected: list[StoredPulse] = []
    before: int | None = None
    while True:
        page = store.read_space_tail("s1", limit=10, before_position=before)
        # Prepend to build full chronological order
        collected = list(page.entries) + collected
        if not page.has_more:
            break
        assert page.next_before_position is not None
        before = page.next_before_position

    assert len(collected) == 25
    assert [e.pulse.id for e in collected] == [f"p-{i}" for i in range(25)]


def test_pbr_008_strict_space_isolation() -> None:
    """PBR-008: Interleaved pulses in Spaces A and B never leak foreign rows."""
    store = InMemoryPulseStore()
    for i in range(20):
        store.append(_create_pulse(f"pa-{i}", space_id="space-A", pulse_type="task.tick"))
        store.append(_create_pulse(f"pb-{i}", space_id="space-B", pulse_type="task.tick"))

    page_a = store.read_space_page("space-A", limit=50)
    assert all(e.pulse.space_id == "space-A" for e in page_a.entries)
    assert len(page_a.entries) == 20

    page_b = store.read_space_page("space-B", limit=50)
    assert all(e.pulse.space_id == "space-B" for e in page_b.entries)
    assert len(page_b.entries) == 20


def test_pbr_009_typed_filtering() -> None:
    """PBR-009: Filtering by pulse_types returns requested types without skipping matches."""
    store = InMemoryPulseStore()
    for i in range(10):
        store.append(_create_pulse(f"p-task-{i}", space_id="s1", pulse_type="task.created"))
        store.append(_create_pulse(f"p-log-{i}", space_id="s1", pulse_type="log.emitted"))

    page = store.read_space_page("s1", limit=10, pulse_types=frozenset(["task.created"]))
    assert len(page.entries) == 10
    assert all(e.pulse.type == "task.created" for e in page.entries)

    tail = store.read_space_tail("s1", limit=5, type_prefix="log")
    assert len(tail.entries) == 5
    assert all(e.pulse.type == "log.emitted" for e in tail.entries)


def test_pbr_010_iter_space_streaming() -> None:
    """PBR-010: iter_space yields all pulses in space in monotonic order."""
    store = InMemoryPulseStore()
    total = 30
    for i in range(total):
        store.append(_create_pulse(f"p-{i}", space_id="s1"))

    streamed = list(store.iter_space("s1"))
    assert len(streamed) == total
    assert [e.pulse.id for e in streamed] == [f"p-{i}" for i in range(total)]
    positions = [e.position for e in streamed]
    assert positions == sorted(positions)


# ============================================================================
# PBR-011..013: Fail-Closed Legacy Envelope (PULSE-017)
# ============================================================================


def test_pbr_011_legacy_read_by_space_within_cap() -> None:
    """PBR-011: Legacy read_by_space returns full list when count <= cap."""
    store = InMemoryPulseStore()
    for i in range(5):
        store.append(_create_pulse(f"p-{i}", space_id="s1"))

    res = store.read_by_space("s1")
    assert len(res) == 5
    assert [p.id for p in res] == [f"p-{i}" for i in range(5)]


def test_pbr_012_legacy_read_by_space_raises_above_cap() -> None:
    """PBR-012: Legacy read_by_space raises PulseRetrievalBoundExceeded when > cap."""
    store = InMemoryPulseStore()
    for i in range(15):
        store.append(_create_pulse(f"p-{i}", space_id="s1"))

    # Lower cap to 10 via environment override
    with patch.dict(os.environ, {"RYU_PULSE_PAGE_MAX": "10"}):
        with pytest.raises(PulseRetrievalBoundExceeded, match="exceeding safe cap"):
            store.read_by_space("s1")


def test_pbr_013_legacy_read_correlation_and_parent_raise_above_cap() -> None:
    """PBR-013: Legacy read_by_correlation and read_by_parent raise when > cap."""
    store = InMemoryPulseStore()
    for i in range(12):
        store.append(_create_pulse(f"p-{i}", space_id="s1", correlation_id="c-shared", parent_pulse_id="parent-1"))

    with patch.dict(os.environ, {"RYU_PULSE_PAGE_MAX": "10"}):
        with pytest.raises(PulseRetrievalBoundExceeded, match="read_by_correlation"):
            store.read_by_correlation("c-shared")
        with pytest.raises(PulseRetrievalBoundExceeded, match="read_by_parent"):
            store.read_by_parent("parent-1")
        with pytest.raises(PulseRetrievalBoundExceeded, match="Global read"):
            store.read(0)


# ============================================================================
# PBR-014..016: Taint Integration & Parity
# ============================================================================


def test_pbr_014_has_pulse_of_type_and_taint_resolver() -> None:
    """PBR-014: has_pulse_of_type efficiently resolves taint clearance."""
    store = InMemoryPulseStore()
    store.append(_create_pulse("p-root", space_id="s1", correlation_id="c1", taint=True))
    store.append(_create_pulse("p-clear", space_id="s1", pulse_type="security.taint.cleared", correlation_id="c1"))

    resolver = TaintResolver(store)
    # Child with tainted parent should be cleared because security.taint.cleared exists
    child = _create_pulse("p-child", space_id="s1", correlation_id="c1", parent_pulse_id="p-root", taint=False)

    resolved_taint = resolver.resolve_taint(child)
    assert resolved_taint is False


def test_pbr_015_duplicate_append_idempotence() -> None:
    """PBR-015: Duplicate append does not duplicate page entries."""
    store = InMemoryPulseStore()
    p1 = _create_pulse("p-dup", space_id="s1")
    pos1 = store.append(p1)
    pos2 = store.append(p1)
    assert pos1 == pos2

    page = store.read_space_page("s1")
    assert len(page.entries) == 1


def test_pbr_016_replay_uses_bounded_paths() -> None:
    """PBR-016: PulseReplayer uses iter_space and read_page without unbounded reads."""
    store = InMemoryPulseStore()
    for i in range(15):
        store.append(_create_pulse(f"p-{i}", space_id="s1"))

    replayer = PulseReplayer(store)
    replayed = list(replayer.replay_from(0))
    assert len(replayed) == 15
    assert [p.id for p in replayed] == [f"p-{i}" for i in range(15)]

    space_replayed = replayer.replay_by_space("s1")
    assert len(space_replayed) == 15


# ============================================================================
# ADV-PULSE-01..14: Adversarial Security & Boundary Matrix (PULSE-016)
# ============================================================================


def test_adv_pulse_01_unbounded_read_prevented() -> None:
    """ADV-PULSE-01: Massive pulse history triggers fail-closed exception on unpaged reads."""
    store = InMemoryPulseStore()
    for i in range(25):
        store.append(_create_pulse(f"p-{i}", space_id="s1"))

    with patch.dict(os.environ, {"RYU_PULSE_PAGE_MAX": "20"}):
        with pytest.raises(PulseRetrievalBoundExceeded):
            store.read_by_space("s1")


def test_adv_pulse_02_oversized_limit_rejected() -> None:
    """ADV-PULSE-02: limit exceeding MAX_PULSE_PAGE_SIZE raises ValueError."""
    store = InMemoryPulseStore()
    with pytest.raises(ValueError, match="out of bounds"):
        store.read_space_page("s1", limit=MAX_PULSE_PAGE_SIZE + 1)
    with pytest.raises(ValueError, match="out of bounds"):
        store.read_space_tail("s1", limit=10_000_000)


def test_adv_pulse_03_negative_limit_rejected() -> None:
    """ADV-PULSE-03: Negative limit raises ValueError."""
    store = InMemoryPulseStore()
    with pytest.raises(ValueError, match="out of bounds"):
        store.read_space_page("s1", limit=-1)
    with pytest.raises(ValueError, match="out of bounds"):
        store.read_space_tail("s1", limit=-100)


def test_adv_pulse_04_negative_cursor_rejected() -> None:
    """ADV-PULSE-04: Negative after_position or before_position raises ValueError."""
    store = InMemoryPulseStore()
    with pytest.raises(ValueError, match="must be non-negative"):
        store.read_space_page("s1", after_position=-1)
    with pytest.raises(ValueError, match="must be non-negative"):
        store.read_space_tail("s1", before_position=-5)


def test_adv_pulse_05_cross_space_cursor_safety() -> None:
    """ADV-PULSE-05: Using Space B's cursor in Space A returns zero Space B rows."""
    store = InMemoryPulseStore()
    # Fill Space B with 10 pulses (positions 1..10)
    for i in range(10):
        store.append(_create_pulse(f"pb-{i}", space_id="space-B"))
    # Space A has 2 pulses (positions 11, 12)
    store.append(_create_pulse("pa-0", space_id="space-A"))
    store.append(_create_pulse("pa-1", space_id="space-A"))

    # Reading Space A after position 10 returns only pa-0 and pa-1
    page = store.read_space_page("space-A", after_position=10)
    assert [e.pulse.id for e in page.entries] == ["pa-0", "pa-1"]
    assert all(e.pulse.space_id == "space-A" for e in page.entries)

    # Reading Space A after position 12 returns empty
    page_empty = store.read_space_page("space-A", after_position=12)
    assert len(page_empty.entries) == 0


def test_adv_pulse_06_invalid_space_id_rejected() -> None:
    """ADV-PULSE-06: Empty, whitespace, or non-string space_id raises ValueError."""
    store = InMemoryPulseStore()
    with pytest.raises(ValueError, match="Invalid space_id"):
        store.read_space_page("")
    with pytest.raises(ValueError, match="Invalid space_id"):
        store.read_space_page("   ")
    with pytest.raises(ValueError, match="Invalid space_id"):
        store.read_space_tail("")


def test_adv_pulse_07_cursor_repeatability_idempotent() -> None:
    """ADV-PULSE-07: Querying with the same cursor produces identical results."""
    store = InMemoryPulseStore()
    for i in range(10):
        store.append(_create_pulse(f"p-{i}", space_id="s1"))

    page1 = store.read_space_page("s1", after_position=3, limit=4)
    page2 = store.read_space_page("s1", after_position=3, limit=4)
    assert [e.pulse.id for e in page1.entries] == [e.pulse.id for e in page2.entries]
    assert page1.next_after_position == page2.next_after_position


def test_adv_pulse_08_type_confusion_in_limit_rejected() -> None:
    """ADV-PULSE-08: Passing bool, str, or float as limit raises TypeError."""
    store = InMemoryPulseStore()
    with pytest.raises(TypeError, match="must be an integer"):
        store.read_space_page("s1", limit=True)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="must be an integer"):
        store.read_space_page("s1", limit=False)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="must be an integer"):
        store.read_space_page("s1", limit="10")  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="must be an integer"):
        store.read_space_page("s1", limit=5.5)  # type: ignore[arg-type]


def test_adv_pulse_09_type_confusion_in_cursor_rejected() -> None:
    """ADV-PULSE-09: Passing bool, str, or float as cursor raises TypeError."""
    store = InMemoryPulseStore()
    with pytest.raises(TypeError, match="must be an integer"):
        store.read_space_page("s1", after_position=True)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="must be an integer"):
        store.read_space_tail("s1", before_position="5")  # type: ignore[arg-type]


def test_adv_pulse_10_wildcard_escaping_in_type_prefix() -> None:
    """ADV-PULSE-10: SQL wildcards (%, _) in type_prefix are escaped safely."""
    store = InMemoryPulseStore()
    store.append(_create_pulse("p-exact", space_id="s1", pulse_type="100%_complete"))
    store.append(_create_pulse("p-other", space_id="s1", pulse_type="1000_other"))

    tail = store.read_space_tail("s1", type_prefix="100%_")
    assert len(tail.entries) == 1
    assert tail.entries[0].pulse.id == "p-exact"


def test_adv_pulse_11_cross_space_taint_clearance_isolation() -> None:
    """ADV-PULSE-11: Clearance in Space B does NOT clear taint in Space A."""
    store = InMemoryPulseStore()
    # Space A has tainted root
    store.append(_create_pulse("pa-root", space_id="space-A", correlation_id="c-shared", taint=True))
    # Space B emits clearance with same correlation_id
    store.append(_create_pulse("pb-clear", space_id="space-B", pulse_type="security.taint.cleared", correlation_id="c-shared"))

    resolver = TaintResolver(store)
    # Child in Space A must NOT be cleared by Space B's clearance
    child_a = _create_pulse("pa-child", space_id="space-A", correlation_id="c-shared", parent_pulse_id="pa-root", taint=False)
    resolved_taint = resolver.resolve_taint(child_a)
    assert resolved_taint is True


def test_adv_pulse_12_zero_limit_rejected() -> None:
    """ADV-PULSE-12: limit=0 is rejected with ValueError."""
    store = InMemoryPulseStore()
    with pytest.raises(ValueError, match="out of bounds"):
        store.read_space_page("s1", limit=0)


def test_adv_pulse_13_empty_type_prefix_rejected() -> None:
    """ADV-PULSE-13: Empty type_prefix raises ValueError."""
    store = InMemoryPulseStore()
    with pytest.raises(ValueError, match="type_prefix must be a non-empty string"):
        store.read_space_tail("s1", type_prefix="")


def test_adv_pulse_14_invalid_pulse_types_rejected() -> None:
    """ADV-PULSE-14: pulse_types not a set/frozenset raises TypeError."""
    store = InMemoryPulseStore()
    with pytest.raises(TypeError, match="must be a set or frozenset"):
        store.read_space_page("s1", pulse_types=["task.created"])  # type: ignore[arg-type]


# ============================================================================
# PostgreSQL Store Query & Mock Unit Tests (Integration Guarded)
# ============================================================================


def test_postgres_store_query_generation_mock() -> None:
    """Verify PostgresPulseStore query construction and limit handling."""
    config = PostgresConfig(host="localhost", port=5432, db="test", user="u", password="p")
    store = PostgresPulseStore(config)

    mock_conn = MagicMock()
    mock_conn.__enter__.return_value = mock_conn
    mock_cur = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cur
    store._get_conn = MagicMock(return_value=mock_conn)

    # Mock return 2 rows for limit=2 (has_more=False)
    now = datetime.now(timezone.utc)
    mock_cur.fetchall.return_value = [
        (1, "p-1", "s1", "task.tick", "info", "src", now, "{}", False, "c1", None),
        (2, "p-2", "s1", "task.tick", "info", "src", now, "{}", False, "c1", None),
    ]

    page = store.read_space_page("s1", limit=2)
    assert len(page.entries) == 2
    assert page.has_more is False
    assert page.entries[0].position == 1

    # Verify query had LIMIT 3 (limit + 1)
    called_sql, called_params = mock_cur.execute.call_args[0]
    assert "LIMIT %s" in called_sql
    assert called_params[-1] == 3  # limit + 1


def test_postgres_store_read_space_tail_mock() -> None:
    """Verify PostgresPulseStore read_space_tail descending query and reversal."""
    config = PostgresConfig(host="localhost", port=5432, db="test", user="u", password="p")
    store = PostgresPulseStore(config)

    mock_conn = MagicMock()
    mock_conn.__enter__.return_value = mock_conn
    mock_cur = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cur
    store._get_conn = MagicMock(return_value=mock_conn)

    now = datetime.now(timezone.utc)
    # Database returns in DESC order: position 20 then position 19
    mock_cur.fetchall.return_value = [
        (20, "p-20", "s1", "task.tick", "info", "src", now, "{}", False, "c1", None),
        (19, "p-19", "s1", "task.tick", "info", "src", now, "{}", False, "c1", None),
    ]

    tail = store.read_space_tail("s1", limit=2)
    assert len(tail.entries) == 2
    # Output should be reversed to ASC order
    assert tail.entries[0].position == 19
    assert tail.entries[1].position == 20


def test_postgres_store_legacy_raises_above_cap_mock() -> None:
    """Verify PostgresPulseStore raises PulseRetrievalBoundExceeded when count > cap."""
    config = PostgresConfig(host="localhost", port=5432, db="test", user="u", password="p")
    store = PostgresPulseStore(config)

    mock_conn = MagicMock()
    mock_conn.__enter__.return_value = mock_conn
    mock_cur = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cur
    store._get_conn = MagicMock(return_value=mock_conn)

    now = datetime.now(timezone.utc)
    # Mock returning 11 rows when cap is 10
    mock_cur.fetchall.return_value = [
        (i, f"p-{i}", "s1", "tick", "info", "src", now, "{}", False, "c1", None)
        for i in range(1, 12)
    ]

    with patch.dict(os.environ, {"RYU_PULSE_PAGE_MAX": "10"}):
        with pytest.raises(PulseRetrievalBoundExceeded):
            store.read_by_space("s1")
