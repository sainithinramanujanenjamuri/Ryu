"""Unit and Integration Tests for Phase 15.6.3 — Experience Memory Compaction & Retention Policy.

Covers:
  - RetentionPolicy initialization and invariant validation (MEM-RETAIN-001)
  - count_experiences Space-scoped counting and isolation
  - Compaction no-op when within bounds
  - TTL-based expiration and evidence-preserving protections
  - Redundant duplicate experience pruning (keeps newest)
  - Capacity ceiling enforcement with deterministic victim priority tiers:
      Tier 0: Unembedded failures
      Tier 1: Non-unique failures
      Tier 2: Other records
      Tier 3: Unique failure fingerprints (protected until earlier tiers exhausted)
      Tier 4: Successful adaptation strategies (protected last)
  - Secondary index synchronization after compaction
  - Auto-prune on store integration
  - Cross-space isolation (pruning in Space A never affects Space B)
  - Deterministic victim selection repeatability

Contracts:
  - MEM-RETAIN-001 (Bounded Space Experience Retention & Pruning)
  - ADR-0050 §6

AGENTS.md §5, §7
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from core.space.memory_protocol import (
    ExperienceRecord,
    RetentionPolicy,
    SpaceIsolationViolation,
    select_compaction_victims,
)
from memory.adapters.in_memory import InMemoryMemoryAdapter
from memory.embeddings.deterministic_mock import DeterministicMockEmbeddingProvider

_MOCK_PROV = DeterministicMockEmbeddingProvider()
_MOCK_VEC = _MOCK_PROV.embed("retention test record").vector


def _make_record(
    exp_id: str,
    space_id: str = "space-retention-test",
    capability: str = "python.exec",
    failure_fingerprint: str | None = None,
    outcome: str = "failure",
    counterfactual: str = "Retry with fallback parameters",
    stored_at: datetime | None = None,
    embedding: tuple[float, ...] | None = _MOCK_VEC,
    error_class: str = "transient.timeout",
) -> ExperienceRecord:
    now = stored_at or datetime.now(timezone.utc)
    return ExperienceRecord(
        experience_id=exp_id,
        space_id=space_id,
        situation={"task_id": "task-test", "capability": capability},
        action={"capability": capability},
        outcome=outcome,
        counterfactual=counterfactual,
        applicable_context={
            "error_class": error_class,
            "failure_fingerprint": failure_fingerprint,
        },
        stored_at=now,
        embedding=embedding,
        embedding_model=_MOCK_PROV.model_name if embedding is not None else None,
        embedding_dimension=_MOCK_PROV.dimension if embedding is not None else None,
        embedding_version=_MOCK_PROV.version if embedding is not None else None,
        failure_fingerprint=failure_fingerprint,
    )


def test_retention_policy_validation() -> None:
    """MEM-RETAIN-001: RetentionPolicy validates bounds and enforces immutability."""
    pol = RetentionPolicy(max_experiences=500, ttl_seconds=3600.0)
    assert pol.max_experiences == 500
    assert pol.ttl_seconds == 3600.0
    assert pol.preserve_fingerprints is True
    assert pol.preserve_successful is True

    # Invalid max_experiences
    with pytest.raises(ValueError, match="max_experiences must be >= 1"):
        RetentionPolicy(max_experiences=0)

    # Invalid ttl_seconds
    with pytest.raises(ValueError, match="ttl_seconds must be > 0.0"):
        RetentionPolicy(ttl_seconds=0.0)
    with pytest.raises(ValueError, match="ttl_seconds must be > 0.0"):
        RetentionPolicy(ttl_seconds=-10.0)
    with pytest.raises(TypeError, match="ttl_seconds must be a float or int"):
        RetentionPolicy(ttl_seconds="invalid")  # type: ignore[arg-type]


def test_count_experiences_basic_and_space_isolation() -> None:
    """MEM-RETAIN-001: count_experiences returns exact space counts and isolates spaces."""
    adapter = InMemoryMemoryAdapter()
    assert adapter.count_experiences("space-1") == 0

    adapter.store_experience(_make_record("e1", space_id="space-1"))
    adapter.store_experience(_make_record("e2", space_id="space-1"))
    adapter.store_experience(_make_record("e3", space_id="space-2"))

    assert adapter.count_experiences("space-1") == 2
    assert adapter.count_experiences("space-2") == 1
    assert adapter.count_experiences("space-3") == 0

    with pytest.raises(SpaceIsolationViolation):
        adapter.count_experiences("")


def test_compaction_no_op_when_under_capacity_and_no_ttl() -> None:
    """MEM-RETAIN-001: Pruning is a no-op when within capacity and no TTL is configured."""
    adapter = InMemoryMemoryAdapter()
    for i in range(5):
        adapter.store_experience(_make_record(f"e{i}", space_id="space-noop", capability=f"cap-{i}"))

    res = adapter.prune_experiences("space-noop", RetentionPolicy(max_experiences=10))
    assert res.initial_count == 5
    assert res.final_count == 5
    assert res.pruned_count == 0
    assert res.pruned_experience_ids == ()
    assert adapter.count_experiences("space-noop") == 5


def test_compaction_ttl_expiration_basic() -> None:
    """MEM-RETAIN-001: Non-protected records exceeding ttl_seconds are pruned."""
    now = datetime.now(timezone.utc)
    old_time = now - timedelta(seconds=7200)  # 2 hours old

    adapter = InMemoryMemoryAdapter()
    # 2 old non-protected records (duplicate fingerprint so neither is unique, outcome=failure)
    adapter.store_experience(
        _make_record("old-1", failure_fingerprint="fp-shared", outcome="failure", stored_at=old_time)
    )
    adapter.store_experience(
        _make_record("old-2", failure_fingerprint="fp-shared", outcome="failure", stored_at=old_time)
    )
    # 1 fresh record
    adapter.store_experience(_make_record("fresh-1", stored_at=now))

    # TTL = 1 hour (3600s), preserve_fingerprints=True (but fp-shared is not unique)
    pol = RetentionPolicy(ttl_seconds=3600.0, max_experiences=100)
    res = adapter.prune_experiences("space-retention-test", pol)

    assert res.initial_count == 3
    assert res.pruned_count == 2
    assert "old-1" in res.pruned_experience_ids
    assert "old-2" in res.pruned_experience_ids
    assert res.reasons.get("ttl_expired") == 2
    assert adapter.count_experiences("space-retention-test") == 1
    assert adapter.get_experience("space-retention-test", "fresh-1") is not None


def test_compaction_preserves_unique_fingerprints_under_ttl() -> None:
    """ADR-0050 §6: Unique failure fingerprints are protected from TTL pruning."""
    now = datetime.now(timezone.utc)
    old_time = now - timedelta(seconds=7200)

    adapter = InMemoryMemoryAdapter()
    # Unique failure fingerprint
    adapter.store_experience(
        _make_record("unique-fp", failure_fingerprint="fp-unique-crit", outcome="failure", stored_at=old_time)
    )
    # Non-unique failure fingerprint (2 records share it)
    adapter.store_experience(
        _make_record("dup-fp-1", failure_fingerprint="fp-common", outcome="failure", stored_at=old_time)
    )
    adapter.store_experience(
        _make_record("dup-fp-2", failure_fingerprint="fp-common", outcome="failure", stored_at=old_time)
    )

    pol = RetentionPolicy(ttl_seconds=3600.0, preserve_fingerprints=True)
    res = adapter.prune_experiences("space-retention-test", pol)

    # unique-fp must NOT be pruned by TTL
    assert "unique-fp" not in res.pruned_experience_ids
    assert adapter.get_experience("space-retention-test", "unique-fp") is not None


def test_compaction_preserves_successful_strategies_under_ttl() -> None:
    """ADR-0050 §6: Validated successful strategies are protected from TTL pruning."""
    now = datetime.now(timezone.utc)
    old_time = now - timedelta(seconds=7200)

    adapter = InMemoryMemoryAdapter()
    adapter.store_experience(
        _make_record("succ-1", outcome="success", stored_at=old_time)
    )
    adapter.store_experience(
        _make_record("fail-1", outcome="failure", stored_at=old_time)
    )

    pol = RetentionPolicy(ttl_seconds=3600.0, preserve_successful=True)
    res = adapter.prune_experiences("space-retention-test", pol)

    # succ-1 must be preserved; fail-1 pruned
    assert "succ-1" not in res.pruned_experience_ids
    assert "fail-1" in res.pruned_experience_ids
    assert adapter.get_experience("space-retention-test", "succ-1") is not None


def test_compaction_prunes_redundant_duplicates() -> None:
    """ADR-0050 §6: Redundant duplicates are pruned, keeping only the newest record."""
    t0 = datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 1, 1, 11, 0, 0, tzinfo=timezone.utc)
    t2 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

    adapter = InMemoryMemoryAdapter()
    # 3 identical experiences differing only by stored_at
    adapter.store_experience(
        _make_record("dup-old", capability="tool.exec", failure_fingerprint="fp-dup", stored_at=t0)
    )
    adapter.store_experience(
        _make_record("dup-mid", capability="tool.exec", failure_fingerprint="fp-dup", stored_at=t1)
    )
    adapter.store_experience(
        _make_record("dup-newest", capability="tool.exec", failure_fingerprint="fp-dup", stored_at=t2)
    )

    res = adapter.prune_experiences("space-retention-test", RetentionPolicy(max_experiences=100))
    assert res.pruned_count == 2
    assert "dup-old" in res.pruned_experience_ids
    assert "dup-mid" in res.pruned_experience_ids
    assert "dup-newest" not in res.pruned_experience_ids
    assert res.reasons.get("redundant_duplicate") == 2
    assert adapter.count_experiences("space-retention-test") == 1
    assert adapter.get_experience("space-retention-test", "dup-newest") is not None


def test_compaction_capacity_limit_prioritizes_unembedded_failures() -> None:
    """ADR-0050 §6: Under capacity pressure, unembedded failures are evicted before embedded records."""
    t0 = datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 1, 1, 11, 0, 0, tzinfo=timezone.utc)

    adapter = InMemoryMemoryAdapter()
    # e1: failure without embedding (Tier 0)
    adapter.store_experience(
        _make_record("unemb-fail", outcome="failure", embedding=None, stored_at=t0)
    )
    # e2: failure with embedding (Tier 1/3)
    adapter.store_experience(
        _make_record("emb-fail", outcome="failure", embedding=_MOCK_VEC, stored_at=t1)
    )

    # Max capacity = 1 -> must evict 1
    res = adapter.prune_experiences("space-retention-test", RetentionPolicy(max_experiences=1))
    assert res.pruned_count == 1
    assert res.pruned_experience_ids[0] == "unemb-fail"
    assert adapter.get_experience("space-retention-test", "emb-fail") is not None


def test_compaction_capacity_limit_preserves_successful_last() -> None:
    """ADR-0050 §6: Under capacity pressure, successful strategies are preserved last."""
    t0 = datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 1, 1, 11, 0, 0, tzinfo=timezone.utc)

    adapter = InMemoryMemoryAdapter()
    adapter.store_experience(_make_record("fail-rec", outcome="failure", stored_at=t0))
    adapter.store_experience(_make_record("succ-rec", outcome="success", stored_at=t1))

    # Max capacity = 1
    res = adapter.prune_experiences("space-retention-test", RetentionPolicy(max_experiences=1))
    assert res.pruned_count == 1
    assert res.pruned_experience_ids[0] == "fail-rec"
    assert adapter.get_experience("space-retention-test", "succ-rec") is not None


def test_compaction_secondary_indexes_cleaned() -> None:
    """MEM-RETAIN-001: Secondary lookup indexes are synchronized when records are pruned."""
    adapter = InMemoryMemoryAdapter()
    adapter.store_experience(
        _make_record("exp-indexed", capability="tool.clean", failure_fingerprint="fp-clean", error_class="clean.err")
    )
    assert "exp-indexed" in adapter._fingerprint_idx["space-retention-test"]["fp-clean"]
    assert "exp-indexed" in adapter._capability_idx["space-retention-test"]["tool.clean"]
    assert "exp-indexed" in adapter._error_class_idx["space-retention-test"]["clean.err"]
    assert "exp-indexed" in adapter._recency_idx["space-retention-test"]

    # Store second record as success, then prune to max_experiences=1 (failure exp-indexed evicted first)
    adapter.store_experience(_make_record("exp-survivor", capability="tool.survive", outcome="success"))
    res = adapter.prune_experiences("space-retention-test", RetentionPolicy(max_experiences=1))
    assert "exp-indexed" in res.pruned_experience_ids

    # exp-indexed must be removed from secondary indexes
    assert "exp-indexed" not in adapter._fingerprint_idx["space-retention-test"].get("fp-clean", [])
    assert "exp-indexed" not in adapter._capability_idx["space-retention-test"].get("tool.clean", [])
    assert "exp-indexed" not in adapter._error_class_idx["space-retention-test"].get("clean.err", [])
    assert "exp-indexed" not in adapter._recency_idx["space-retention-test"]


def test_auto_prune_on_store() -> None:
    """MEM-RETAIN-001: auto_prune=True automatically bounds space capacity during store_experience."""
    pol = RetentionPolicy(max_experiences=3)
    adapter = InMemoryMemoryAdapter(default_retention_policy=pol, auto_prune=True)

    for i in range(10):
        adapter.store_experience(_make_record(f"auto-{i}", space_id="space-auto", capability=f"cap-{i}"))

    # Must be bounded to max_experiences = 3
    assert adapter.count_experiences("space-auto") == 3


def test_compaction_cross_space_isolation() -> None:
    """SCCA Law 1 & MEM-RETAIN-001: Pruning space A never modifies space B."""
    adapter = InMemoryMemoryAdapter()
    adapter.store_experience(_make_record("sa-1", space_id="space-A"))
    adapter.store_experience(_make_record("sa-2", space_id="space-A"))
    adapter.store_experience(_make_record("sb-1", space_id="space-B"))
    adapter.store_experience(_make_record("sb-2", space_id="space-B"))

    res = adapter.prune_experiences("space-A", RetentionPolicy(max_experiences=1))
    assert res.pruned_count == 1
    assert adapter.count_experiences("space-A") == 1
    # Space B is completely untouched
    assert adapter.count_experiences("space-B") == 2
    assert adapter.get_experience("space-B", "sb-1") is not None
    assert adapter.get_experience("space-B", "sb-2") is not None


def test_deterministic_victim_selection_repeatability() -> None:
    """MEM-SEM-002 & MEM-RETAIN-001: Victim selection is 100% deterministic over identical inputs."""
    records = [
        _make_record(f"det-{i}", stored_at=datetime(2026, 1, 1, 10, i, 0, tzinfo=timezone.utc))
        for i in range(10)
    ]
    pol = RetentionPolicy(max_experiences=5)

    base_victims, base_reasons = select_compaction_victims(records, pol)
    for _ in range(10):
        v, r = select_compaction_victims(records, pol)
        assert v == base_victims
        assert r == base_reasons
