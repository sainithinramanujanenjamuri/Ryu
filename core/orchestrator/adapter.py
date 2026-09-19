"""Adapter / Reflector: Proposes Plan Deltas and stores Experience records.

spec §4 (Adapter / Reflector), §16 (PlanDelta & experience.stored), ORCH-005 — Phase 4
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Protocol

from ryu.pulse_bus.pulse import Pulse, Severity

from core.plans.delta import PlanDelta


class PulsePublisher(Protocol):
    def publish(self, pulse: Pulse) -> Pulse: ...


class Adapter:
    """Computes repair proposals (PlanDeltas) and formats experience records.

    Invariants:
    - Never mutates authoritative plans directly; only outputs PlanDelta proposals.
    - Resulting PlanDelta must be committed via Space Kernel CAS.
    - Formats experience records with required counterfactual fields (§16).
    """

    def __init__(self, space_id: str, bus: PulsePublisher | None = None) -> None:
        self.space_id = space_id
        self.bus = bus

    def propose_reassignment(
        self,
        base_version: int,
        target_node_id: str,
        new_assignee: str,
    ) -> PlanDelta:
        """Propose a PlanDelta reassigning a failed or stalled node."""
        ops = [
            {
                "op": "reassign",
                "target_node_id": target_node_id,
                "payload": {"assignee_id": new_assignee},
            }
        ]
        return PlanDelta(
            space_id=self.space_id,
            base_version=base_version,
            resulting_version=base_version + 1,
            ops=ops,
        )

    def propose_node_removal(
        self,
        base_version: int,
        target_node_id: str,
        reason: str = "degraded_mode_prune",
    ) -> PlanDelta:
        """Propose a PlanDelta removing an optional or obsolete node."""
        ops = [
            {
                "op": "remove",
                "target_node_id": target_node_id,
                "payload": {"reason": reason},
            }
        ]
        return PlanDelta(
            space_id=self.space_id,
            base_version=base_version,
            resulting_version=base_version + 1,
            ops=ops,
        )

    def propose_node_addition(
        self,
        base_version: int,
        new_node_id: str,
        capability: str,
        params: dict[str, Any] | None = None,
        optional: bool = False,
    ) -> PlanDelta:
        """Propose a PlanDelta adding a recovery/compensation node."""
        ops = [
            {
                "op": "add",
                "target_node_id": new_node_id,
                "capability": capability,
                "params": params or {},
                "optional": optional,
            }
        ]
        return PlanDelta(
            space_id=self.space_id,
            base_version=base_version,
            resulting_version=base_version + 1,
            ops=ops,
        )

    def record_experience(
        self,
        situation: dict[str, Any],
        action: dict[str, Any],
        outcome: str,
        counterfactual: str,
        applicable_context: dict[str, Any] | None = None,
        correlation_id: str | None = None,
    ) -> Pulse | None:
        """Emit an experience.stored Pulse per §16 Component Contracts."""
        exp_id = f"exp-{uuid.uuid4().hex[:8]}"
        now = datetime.now(timezone.utc)
        payload = {
            "experience_id": exp_id,
            "situation": situation,
            "action": action,
            "outcome": outcome,
            "counterfactual": counterfactual,
            "applicable_context": applicable_context or {},
            "stored_at": now.isoformat(),
        }

        if self.bus is not None:
            pulse = Pulse(
                id=f"pulse-exp-{exp_id}",
                space_id=self.space_id,
                type="experience.stored",
                severity=Severity.INFO,
                source="adapter_reflector",
                correlation_id=correlation_id or f"corr-{self.space_id}",
                payload=payload,
                timestamp=now,
            )
            return self.bus.publish(pulse)
        return None
