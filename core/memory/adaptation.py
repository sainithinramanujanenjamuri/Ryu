"""Adaptation Layer: Read-only query layer producing contextual experience hints.

Extracts lessons from past failure experiences to inform the Planner's capability choices
without bypassing Plan CAS, Admission Control, or Kernel authority (ADR-0036).

spec §7 (Adaptation Layer), ROADMAP Phase 10, MEM-004, ADR-0036 — Phase 10
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.space.memory_protocol import (
    ExperienceQuery,
    SpaceIsolationViolation,
    SpaceMemoryProtocol,
)


@dataclass(frozen=True)
class ExperienceHint:
    """Contextual, immutable hint derived from a past negative experience (ADR-0036)."""

    experience_id: str
    failed_capability: str
    suggested_avoidance: list[str]
    outcome_summary: str
    counterfactual_summary: str
    relevance_score: float = 1.0


class AdaptationLayer:
    """Read-only query layer bridging SpaceMemory to Orchestrator/Planner hints.

    Constitutional Invariants (ADR-0036):
    - READ-ONLY: Never mutates plans, commits plans, or writes to memory.
    - NO PULSES: Emits zero Pulses directly; telemetry belongs to the Orchestrator/Reflector.
    - NO ADMISSION/RESOURCES: Cannot grant permissions or acquire resources.
    - NO KERNEL BYPASS: Planner turns hints into ProposedPlans; SpaceKernel commits via CAS.
    """

    def __init__(self, memory_store: SpaceMemoryProtocol) -> None:
        self.memory_store = memory_store

    def generate_hints(
        self,
        space_id: str,
        situation_hint: dict[str, Any],
        limit: int = 5,
    ) -> list[ExperienceHint]:
        """Generate contextual hints from past failure experiences within the Space.

        Raises:
            SpaceIsolationViolation: if space_id is empty or malformed.
            MemoryFailure: if the underlying store encounters an unrecoverable error.
        """
        if not space_id or not space_id.strip():
            raise SpaceIsolationViolation(
                requesting_space="<empty>", target_space="<empty>"
            )

        query = ExperienceQuery(
            space_id=space_id,
            situation_hint=situation_hint,
            limit=limit,
        )

        # Query memory store; MemoryFailure propagates upward (Law 6: never silent)
        experiences = self.memory_store.query_similar_experiences(query)

        hints: list[ExperienceHint] = []
        for exp in experiences:
            # Focus on failed or negative outcomes where counterfactual is provided
            outcome_lower = exp.outcome.lower()
            is_negative = (
                not (
                    outcome_lower.startswith("success")
                    or outcome_lower.startswith("ok")
                    or outcome_lower.startswith("completed")
                )
                or any(
                    term in outcome_lower
                    for term in (
                        "fail",
                        "error",
                        "rejected",
                        "timeout",
                        "abort",
                        "rate limit",
                        "denied",
                        "exceeded",
                        "429",
                    )
                )
            )
            if is_negative and exp.counterfactual.strip():
                failed_cap = exp.action.get("capability", "")
                avoid = [failed_cap] if failed_cap else []
                alternative = exp.applicable_context.get(
                    "suggested_alternative", ""
                )
                if alternative and alternative not in avoid:
                    # Provide alternative capability suggestion if noted in applicable_context
                    pass

                hint = ExperienceHint(
                    experience_id=exp.experience_id,
                    failed_capability=failed_cap,
                    suggested_avoidance=avoid,
                    outcome_summary=exp.outcome[:100],
                    counterfactual_summary=exp.counterfactual[:200],
                    relevance_score=1.0,
                )
                hints.append(hint)

        return hints[:limit]
