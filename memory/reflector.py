"""Reflector: Space-level reflection orchestrating experience persistence and event publication.

Captures task outcomes as structured ExperienceRecords (including mandatory counterfactual),
persists them to SpaceMemoryProtocol, and publishes experience.stored and memory.updated Pulses.

Invariants (ADR-0034, ADR-0036):
- PERSISTENCE FIRST: Experience is persisted to SpaceMemoryProtocol before Pulse emission.
- FAILURE CONTAINMENT: Storage failures raise MemoryFailure; no Pulse is emitted on failure.
- NO PLAN MUTATION: The Reflector cannot mutate plans or create PlanDeltas.
- NO GLOBAL WRITES: Writes are strictly Space-local; global knowledge requires PromotionPipeline.

spec §4 (Space Memory, Adapter/Reflector), §16 (experience.stored), MEM-002, MEM-003 — Phase 10
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Protocol

from ryu.pulse_bus.pulse import Pulse, Severity

from core.orchestrator.adapter import Adapter
from core.space.memory_protocol import ExperienceRecord, SpaceMemoryProtocol


class PulsePublisher(Protocol):
    def publish(self, pulse: Pulse) -> Pulse: ...


class Reflector:
    """Orchestrates structured experience storage and memory update events for a Space."""

    def __init__(
        self,
        adapter: Adapter,
        memory_store: SpaceMemoryProtocol,
        bus: PulsePublisher | None = None,
    ) -> None:
        self.adapter = adapter
        self.memory_store = memory_store
        self.bus = bus

    def reflect(
        self,
        situation: dict[str, Any],
        action: dict[str, Any],
        outcome: str,
        counterfactual: str,
        applicable_context: dict[str, Any] | None = None,
        space_id: str | None = None,
        correlation_id: str | None = None,
    ) -> ExperienceRecord:
        """Capture and persist structured experience upon task completion.

        Raises:
            ValueError: if counterfactual, space_id, or required fields are missing.
            MemoryFailure: if persistence to the memory store fails (never silent).
        """
        eff_space_id = space_id or self.adapter.space_id
        exp_id = f"exp-{uuid.uuid4().hex[:8]}"
        now = datetime.now(timezone.utc)

        # Step 1: Construct validated ExperienceRecord (enforces counterfactual at dataclass level)
        record = ExperienceRecord(
            experience_id=exp_id,
            space_id=eff_space_id,
            situation=situation,
            action=action,
            outcome=outcome,
            counterfactual=counterfactual,
            applicable_context=applicable_context or {},
            stored_at=now,
        )

        # Step 2: Persist to memory store BEFORE pulse emission (MemoryFailure propagates if store fails)
        self.memory_store.store_experience(record)

        # Step 3: Emit experience.stored Pulse via Adapter
        corr_id = correlation_id or f"corr-{eff_space_id}"
        self.adapter.record_experience(
            situation=situation,
            action=action,
            outcome=outcome,
            counterfactual=counterfactual,
            applicable_context=applicable_context,
            correlation_id=corr_id,
        )

        # Step 4: Emit memory.updated Pulse
        if self.bus is not None:
            self.bus.publish(
                Pulse(
                    id=f"pulse-mem-upd-{exp_id}",
                    space_id=eff_space_id,
                    type="memory.updated",
                    severity=Severity.INFO,
                    source="reflector",
                    payload={
                        "memory_id": exp_id,
                        "scope": "space",
                        "version": 1,
                    },
                    taint=False,
                    correlation_id=corr_id,
                    parent_pulse_id=None,
                    timestamp=now,
                )
            )

        return record

