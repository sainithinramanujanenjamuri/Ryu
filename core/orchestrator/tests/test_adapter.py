"""Unit tests for Adapter / Reflector.

spec §4 (Adapter / Reflector), §16 (PlanDelta & experience.stored), ORCH-005 — Phase 4
"""

from __future__ import annotations

from ryu.pulse_bus.bus import PulseBus
from ryu.pulse_bus.pulse import Pulse

from core.orchestrator.adapter import Adapter


class SpyPulseBus(PulseBus):
    def __init__(self) -> None:
        super().__init__()
        self.published: list[Pulse] = []

    def publish(self, pulse: Pulse) -> Pulse:
        self.published.append(pulse)
        return super().publish(pulse)


def test_adapter_propose_reassignment() -> None:
    adapter = Adapter(space_id="space-adapt")
    delta = adapter.propose_reassignment(
        base_version=1,
        target_node_id="task-stalled",
        new_assignee="agent-fallback",
    )
    assert delta.space_id == "space-adapt"
    assert delta.base_version == 1
    assert delta.resulting_version == 2
    assert len(delta.ops) == 1
    assert delta.ops[0]["op"] == "reassign"
    assert delta.ops[0]["target_node_id"] == "task-stalled"
    assert delta.ops[0]["payload"]["assignee_id"] == "agent-fallback"


def test_adapter_propose_node_removal() -> None:
    adapter = Adapter(space_id="space-adapt")
    delta = adapter.propose_node_removal(
        base_version=3,
        target_node_id="task-optional",
        reason="budget_degraded",
    )
    assert delta.base_version == 3
    assert delta.resulting_version == 4
    assert len(delta.ops) == 1
    assert delta.ops[0]["op"] == "remove"
    assert delta.ops[0]["target_node_id"] == "task-optional"


def test_adapter_record_experience() -> None:
    bus = SpyPulseBus()
    adapter = Adapter(space_id="space-adapt", bus=bus)

    pulse = adapter.record_experience(
        situation={"task": "gpu_training", "error": "rate_limit"},
        action={"reconcile": "retried_with_backoff"},
        outcome="success",
        counterfactual="could_have_requested_larger_token_bucket",
        applicable_context={"resource": "gpu"},
    )
    assert pulse is not None
    assert pulse.type == "experience.stored"
    assert pulse.payload["outcome"] == "success"
    assert pulse.payload["counterfactual"] == "could_have_requested_larger_token_bucket"
    assert "experience_id" in pulse.payload
    assert "stored_at" in pulse.payload
