"""Unit tests for Reflector: experience persistence, event emissions, and failure containment.

spec §4 (Space Memory), MEM-002, MEM-003, ADR-0034 — Phase 10
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from core.orchestrator.adapter import Adapter
from core.space.memory_protocol import MemoryFailure
from memory.adapters.in_memory import InMemoryMemoryAdapter
from memory.reflector import Reflector


def test_reflector_reflect_success() -> None:
    bus = MagicMock()
    adapter = Adapter(space_id="space-ref-1", bus=bus)
    store = InMemoryMemoryAdapter()
    reflector = Reflector(adapter=adapter, memory_store=store, bus=bus)

    rec = reflector.reflect(
        situation={"task": "query external api"},
        action={"capability": "net.api_call"},
        outcome="Failed with 503 Service Unavailable",
        counterfactual="Implement exponential backoff or use cached response",
        applicable_context={"service": "weather"},
        space_id="space-ref-1",
    )

    assert rec.experience_id.startswith("exp-")
    assert rec.space_id == "space-ref-1"

    # Verify persisted in store
    fetched = store.get_experience("space-ref-1", rec.experience_id)
    assert fetched is not None
    assert fetched.counterfactual == rec.counterfactual

    # Verify pulses published: experience.stored and memory.updated
    published_types = [call.args[0].type for call in bus.publish.call_args_list]
    assert "experience.stored" in published_types
    assert "memory.updated" in published_types


def test_reflector_counterfactual_validation_failure() -> None:
    bus = MagicMock()
    adapter = Adapter(space_id="space-ref-2", bus=bus)
    store = InMemoryMemoryAdapter()
    reflector = Reflector(adapter=adapter, memory_store=store, bus=bus)

    # Empty counterfactual raises ValueError before persistence or pulse emission
    with pytest.raises(ValueError, match="counterfactual must not be empty"):
        reflector.reflect(
            situation={},
            action={},
            outcome="Failed",
            counterfactual="",
            space_id="space-ref-2",
        )

    # Zero pulses emitted on validation failure
    assert bus.publish.call_count == 0


def test_reflector_storage_failure_propagates_without_pulse() -> None:
    bus = MagicMock()
    adapter = Adapter(space_id="space-ref-3", bus=bus)

    # Failing memory store
    failing_store = MagicMock()
    failing_store.store_experience.side_effect = MemoryFailure(
        operation="store_experience", reason="disk full"
    )

    reflector = Reflector(adapter=adapter, memory_store=failing_store, bus=bus)

    # MemoryFailure must propagate (never swallowed)
    with pytest.raises(MemoryFailure, match="disk full"):
        reflector.reflect(
            situation={},
            action={},
            outcome="Failed",
            counterfactual="Valid counterfactual",
            space_id="space-ref-3",
        )

    # Zero pulses emitted when store fails (Law 6)
    assert bus.publish.call_count == 0

