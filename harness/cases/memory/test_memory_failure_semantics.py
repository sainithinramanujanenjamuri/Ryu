"""Harness Case: Memory Failure Semantics and Law 6 Non-Silent Failure Containment.

Acceptance Criterion (SCCA Law 6, ADR-0034):
Failures are contained, escalated, and never silent. Memory failures raise typed MemoryFailure
exceptions and are never silently swallowed as empty lists or None.

spec §4 (Space Memory), SCCA Law 6, ADR-0034 — Phase 10
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from core.memory.adaptation import AdaptationLayer
from core.orchestrator.adapter import Adapter
from core.space.memory_protocol import MemoryFailure
from memory.reflector import Reflector


def test_reflector_persistence_failure_surfaces_without_pulse() -> None:
    """Law 6: Persistence failure mid-reflect raises MemoryFailure; no Pulse is emitted."""
    bus = MagicMock()
    adapter = Adapter(space_id="space-fail-1", bus=bus)
    failing_store = MagicMock()
    failing_store.store_experience.side_effect = MemoryFailure(
        operation="store_experience", reason="connection timeout to storage"
    )

    reflector = Reflector(adapter=adapter, memory_store=failing_store, bus=bus)

    with pytest.raises(MemoryFailure, match="connection timeout to storage"):
        reflector.reflect(
            situation={"step": "write"},
            action={"capability": "fs.write"},
            outcome="Failed",
            counterfactual="Retry write",
            space_id="space-fail-1",
        )

    # Law 6: Event does not exist if state mutation never succeeded
    assert bus.publish.call_count == 0


def test_adaptation_query_failure_surfaces() -> None:
    """Law 6: AdaptationLayer propagates store failure instead of returning fake empty hints."""
    failing_store = MagicMock()
    failing_store.query_similar_experiences.side_effect = MemoryFailure(
        operation="query_similar_experiences", reason="database read failure"
    )

    layer = AdaptationLayer(memory_store=failing_store)

    with pytest.raises(MemoryFailure, match="database read failure"):
        layer.generate_hints(space_id="space-fail-2", situation_hint={"cap": "fs.read"})

