"""Harness Case: MEM-003 Experience persistence round-trip.

Acceptance Criterion:
Experience survives storage round-trip with all 6 mandatory fields intact (situation, action,
outcome, counterfactual, applicable_context, stored_at).

spec §4 (Space Memory), CONTRACT_MATRIX MEM-003 — Phase 10
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest

from core.space.memory_protocol import ExperienceRecord
from memory.adapters.in_memory import InMemoryMemoryAdapter


def _build_test_record(space_id: str, exp_id: str) -> ExperienceRecord:
    return ExperienceRecord(
        experience_id=exp_id,
        space_id=space_id,
        situation={"step": "compile", "compiler": "rustc"},
        action={"capability": "build.rust"},
        outcome="Compilation error E0382: use of moved value",
        counterfactual="Clone the value before passing to thread closure",
        applicable_context={"crate": "node_runtime", "opt_level": 2},
        stored_at=datetime.now(timezone.utc),
    )


def test_experience_round_trip_in_memory() -> None:
    """MEM-003: InMemory round-trip preserves all fields exactly."""
    store = InMemoryMemoryAdapter()
    original = _build_test_record("space-rt-1", "exp-rt-1")

    store.store_experience(original)
    fetched = store.get_experience("space-rt-1", "exp-rt-1")

    assert fetched is not None
    assert fetched.experience_id == original.experience_id
    assert fetched.space_id == original.space_id
    assert fetched.situation == original.situation
    assert fetched.action == original.action
    assert fetched.outcome == original.outcome
    assert fetched.counterfactual == original.counterfactual
    assert fetched.applicable_context == original.applicable_context
    assert fetched.stored_at == original.stored_at


def test_experience_round_trip_postgres() -> None:
    """MEM-003: PostgreSQL round-trip preserves all fields across database boundaries."""
    if not os.environ.get("RYU_INTEGRATION_TESTS"):
        pytest.skip("Set RYU_INTEGRATION_TESTS=1 to run PostgreSQL persistence tests")

    from ryu.pulse_bus.config import PostgresConfig

    from memory.adapters.postgres import PostgreSQLMemoryAdapter

    config = PostgresConfig.from_env()
    store = PostgreSQLMemoryAdapter(config=config)
    original = _build_test_record("space-pg-rt", "exp-pg-rt-1")

    store.store_experience(original)
    fetched = store.get_experience("space-pg-rt", "exp-pg-rt-1")

    assert fetched is not None
    assert fetched.experience_id == original.experience_id
    assert fetched.space_id == original.space_id
    assert fetched.outcome == original.outcome
    assert fetched.counterfactual == original.counterfactual
    assert fetched.action.get("capability") == "build.rust"

