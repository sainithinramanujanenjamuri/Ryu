"""Unit tests for AdaptationLayer and Planner integration.

spec §7 (Adaptation Layer), MEM-004, ADR-0036 — Phase 10
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from core.memory.adaptation import AdaptationLayer, ExperienceHint
from core.orchestrator.goal_analyzer import Command, GoalAnalyzer, GoalSpec
from core.orchestrator.orchestrator import SpaceOrchestrator
from core.orchestrator.planner import Planner
from core.resources.manager import ResourceManager
from core.space.kernel import SpaceKernel
from core.space.memory_protocol import (
    ExperienceRecord,
    MemoryFailure,
    SpaceIsolationViolation,
)
from memory.adapters.in_memory import InMemoryMemoryAdapter


def _failed_record(space_id: str, cap: str = "net.http") -> ExperienceRecord:
    return ExperienceRecord(
        experience_id="exp-fail-1",
        space_id=space_id,
        situation={"task": "download file"},
        action={"capability": cap},
        outcome="Failed with connection timeout",
        counterfactual=f"Use fallback capability '{cap}.cached' instead",
        applicable_context={"suggested_alternative": f"{cap}.cached"},
        stored_at=datetime.now(timezone.utc),
    )


def test_adaptation_layer_generates_hints_from_failed_experiences() -> None:
    store = InMemoryMemoryAdapter()
    store.store_experience(_failed_record("space-ad-1", "net.download"))

    layer = AdaptationLayer(memory_store=store)
    hints = layer.generate_hints(
        space_id="space-ad-1",
        situation_hint={"capability": "net.download"},
    )

    assert len(hints) == 1
    assert hints[0].failed_capability == "net.download"
    assert "net.download" in hints[0].suggested_avoidance
    assert "fallback" in hints[0].counterfactual_summary


def test_adaptation_layer_empty_space_raises_isolation_violation() -> None:
    store = InMemoryMemoryAdapter()
    layer = AdaptationLayer(memory_store=store)

    with pytest.raises(SpaceIsolationViolation):
        layer.generate_hints(space_id="", situation_hint={})


def test_adaptation_layer_memory_failure_propagates() -> None:
    failing_store = MagicMock()
    failing_store.query_similar_experiences.side_effect = MemoryFailure(
        operation="query", reason="timeout"
    )

    layer = AdaptationLayer(memory_store=failing_store)
    with pytest.raises(MemoryFailure, match="timeout"):
        layer.generate_hints("space-ad-2", situation_hint={})


def test_planner_adapts_from_hints() -> None:
    planner = Planner()

    # Case 1: Un-adapted goal spec
    spec_clean = GoalSpec(
        goal_id="goal-clean",
        space_id="space-p",
        objective="Fetch data",
        constraints=[],
        required_capabilities=["net.download"],
        single_agent_eligible=True,
        command_id="cmd-clean",
    )
    plan_clean = planner.plan_goal(spec_clean)
    assert plan_clean.task_graph.nodes[0].capability == "net.download"

    # Case 2: Adapted goal spec carrying ExperienceHint
    hint = ExperienceHint(
        experience_id="exp-1",
        failed_capability="net.download",
        suggested_avoidance=["net.download"],
        outcome_summary="Timeout",
        counterfactual_summary="Use mirror",
    )
    spec_adapted = GoalSpec(
        goal_id="goal-adapted",
        space_id="space-p",
        objective="Fetch data",
        constraints=[],
        required_capabilities=["net.download"],
        single_agent_eligible=True,
        command_id="cmd-adapted",
        metadata={"experience_hints": [hint], "alternative_capability": "net.mirror"},
    )
    plan_adapted = planner.plan_goal(spec_adapted)

    # Capability adapted from net.download to net.mirror
    assert plan_adapted.task_graph.nodes[0].capability == "net.mirror"
    assert plan_adapted.metadata["applied_hints_count"] == 1


def test_orchestrator_injects_adaptation_hints() -> None:
    bus = MagicMock()
    kernel = SpaceKernel(space_id="space-orch-1", owner_id="user-1", bus=bus)
    rm = ResourceManager(bus=bus)
    store = InMemoryMemoryAdapter()
    store.store_experience(_failed_record("space-orch-1", "general.compute"))

    layer = AdaptationLayer(memory_store=store)
    orch = SpaceOrchestrator(
        space_id="space-orch-1",
        kernel=kernel,
        resource_mgr=rm,
        bus=bus,
        memory_store=store,
        adaptation_layer=layer,
    )

    cmd = Command(
        command_id="cmd-adapt-1",
        space_id="space-orch-1",
        objective="Compute factorial",
        params={
            "required_capabilities": ["general.compute"],
            "metadata": {"alternative_capability": "general.compute.safe"},
        },
    )

    session = orch.submit_goal(cmd)

    # Verify session plan was adapted
    node = session.task_graph.nodes[0]
    assert node.capability == "general.compute.safe"
    assert node.capability == "general.compute.safe"
