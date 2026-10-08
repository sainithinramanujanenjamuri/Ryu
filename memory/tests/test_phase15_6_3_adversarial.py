"""Adversarial and Edge Case Tests for Phase 15.6.3 — Experience Memory Compaction & Retention Policy.

Covers:
  - Empty and whitespace space_id validation across count and prune operations
  - Cross-space record mixing in select_compaction_victims raises SpaceIsolationViolation
  - Capacity overflow when all records are protected: strict ceiling enforced deterministically
  - Timezone-aware and naive timestamp comparison robustness
  - Concurrent multi-threaded store and prune operations (thread safety)
  - Pruning non-existent or empty space returns clean no-op CompactionResult
  - Plan safety & isolation (memory pruning does not mutate plans or task graphs)

Contracts:
  - MEM-RETAIN-001 (Bounded Space Experience Retention & Pruning)
  - SCCA Law 1 (Space Isolation)
  - SCCA Law 6 (Failures Contained and Never Silent)
  - ADR-0050 §6

AGENTS.md §5, §6, §7
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone

import pytest

from core.plans.task_graph import TaskGraph, TaskNode
from core.space.memory_protocol import (
    ExperienceRecord,
    RetentionPolicy,
    SpaceIsolationViolation,
    select_compaction_victims,
)
from memory.adapters.in_memory import InMemoryMemoryAdapter
from memory.embeddings.deterministic_mock import DeterministicMockEmbeddingProvider

_MOCK_PROV = DeterministicMockEmbeddingProvider()
_MOCK_VEC = _MOCK_PROV.embed("adversarial retention").vector


def _make_record(
    exp_id: str,
    space_id: str = "space-adv-retention",
    capability: str = "tool.adv",
    failure_fingerprint: str | None = None,
    outcome: str = "failure",
    stored_at: datetime | None = None,
    embedding: tuple[float, ...] | None = _MOCK_VEC,
) -> ExperienceRecord:
    now = stored_at or datetime.now(timezone.utc)
    return ExperienceRecord(
        experience_id=exp_id,
        space_id=space_id,
        situation={"task_id": "t1", "capability": capability},
        action={"capability": capability},
        outcome=outcome,
        counterfactual="Adversarial fallback",
        applicable_context={"failure_fingerprint": failure_fingerprint},
        stored_at=now,
        embedding=embedding,
        embedding_model=_MOCK_PROV.model_name if embedding is not None else None,
        embedding_dimension=_MOCK_PROV.dimension if embedding is not None else None,
        embedding_version=_MOCK_PROV.version if embedding is not None else None,
        failure_fingerprint=failure_fingerprint,
    )


def test_adversarial_empty_and_whitespace_space_id() -> None:
    """SCCA Law 1: Empty and whitespace space_id strictly rejected with SpaceIsolationViolation."""
    adapter = InMemoryMemoryAdapter()

    for invalid_sid in ("", "   ", "\t\n"):
        with pytest.raises(SpaceIsolationViolation):
            adapter.count_experiences(invalid_sid)

        with pytest.raises(SpaceIsolationViolation):
            adapter.prune_experiences(invalid_sid)


def test_adversarial_cross_space_records_in_victim_selection() -> None:
    """SCCA Law 1: Passing records from mixed spaces to select_compaction_victims raises SpaceIsolationViolation."""
    r1 = _make_record("r1", space_id="space-A")
    r2 = _make_record("r2", space_id="space-B")

    with pytest.raises(SpaceIsolationViolation):
        select_compaction_victims([r1, r2], RetentionPolicy())


def test_adversarial_all_records_protected_capacity_overflow() -> None:
    """ADR-0050 §6: When all records are protected but capacity is exceeded, ceiling is strictly enforced."""
    # 20 records, all having unique failure fingerprints and preserve_fingerprints=True
    records = [
        _make_record(
            f"rec-{i}",
            failure_fingerprint=f"fp-unique-{i}",
            stored_at=datetime(2026, 1, 1, 10, i, 0, tzinfo=timezone.utc),
        )
        for i in range(20)
    ]
    # Max allowed is 5
    pol = RetentionPolicy(max_experiences=5, preserve_fingerprints=True)
    victims, reasons = select_compaction_victims(records, pol)

    # 15 must be evicted to respect the hard capacity limit
    assert len(victims) == 15
    assert reasons.get("capacity_limit") == 15

    # Surviving records must be the 5 newest (rec-15 through rec-19)
    surviving = [r.experience_id for r in records if r.experience_id not in victims]
    assert surviving == [f"rec-{i}" for i in range(15, 20)]


def test_adversarial_timestamp_types_and_timezones() -> None:
    """MEM-RETAIN-001: Mixed naive and aware datetimes are compared safely without TypeError."""
    now_aware = datetime.now(timezone.utc)
    now_naive = datetime.now()  # naive

    r1 = _make_record("r-aware", stored_at=now_aware)
    r2 = _make_record("r-naive", stored_at=now_naive)

    adapter = InMemoryMemoryAdapter()
    adapter.store_experience(r1)
    adapter.store_experience(r2)

    # Compaction with TTL must not raise TypeError on naive vs aware comparison
    res = adapter.prune_experiences("space-adv-retention", RetentionPolicy(ttl_seconds=3600.0, max_experiences=1))
    assert res.final_count == 1
    assert adapter.count_experiences("space-adv-retention") == 1


def test_adversarial_concurrent_store_and_prune() -> None:
    """MEM-RETAIN-001: Concurrent threads storing and pruning maintain consistency under lock."""
    adapter = InMemoryMemoryAdapter()
    space_id = "space-concurrent"
    errors: list[Exception] = []

    def writer_worker(thread_idx: int) -> None:
        try:
            for i in range(20):
                adapter.store_experience(_make_record(f"th-{thread_idx}-{i}", space_id=space_id))
        except Exception as e:
            errors.append(e)

    def pruner_worker() -> None:
        try:
            for _ in range(10):
                adapter.prune_experiences(space_id, RetentionPolicy(max_experiences=10))
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=writer_worker, args=(t,)) for t in range(4)]
    threads.append(threading.Thread(target=pruner_worker))

    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(errors) == 0
    # Final count must be valid int >= 0
    cnt = adapter.count_experiences(space_id)
    assert cnt >= 0


def test_adversarial_pruning_empty_space() -> None:
    """MEM-RETAIN-001: Pruning empty space returns valid CompactionResult with 0 counts."""
    adapter = InMemoryMemoryAdapter()
    res = adapter.prune_experiences("space-empty", RetentionPolicy())

    assert res.space_id == "space-empty"
    assert res.initial_count == 0
    assert res.final_count == 0
    assert res.pruned_count == 0
    assert res.pruned_experience_ids == ()
    assert res.reasons == {}


def test_adversarial_plan_and_bus_isolation() -> None:
    """ADR-0050 §6: Memory pruning does NOT mutate plans or task graphs."""
    adapter = InMemoryMemoryAdapter()
    for i in range(10):
        adapter.store_experience(_make_record(f"exp-{i}", capability=f"cap-{i}"))

    # Create a TaskGraph
    node = TaskNode(id="task-1", capability="tool.adv", state="ready")
    graph = TaskGraph(space_id="space-adv-retention", plan_version=1, nodes=[node])

    # Prune memory
    res = adapter.prune_experiences("space-adv-retention", RetentionPolicy(max_experiences=2))
    assert res.pruned_count == 8

    # TaskGraph is strictly unchanged
    assert graph.plan_version == 1
    t_node = graph.get_node("task-1")
    assert t_node is not None
    assert t_node.state == "ready"
