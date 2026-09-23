"""Harness Case: MEM-004 Actionable learning (Phase 10 Exit Gate 1).

Acceptance Criterion (ROADMAP Phase 10 Exit Gate):
Experience round-trip: a recorded failure causes a measurable, eval-backed plan change
on a repeated similar task — evaluated against frozen LLM traces, not vibes.

spec §4 (Space Memory), §7 (Adaptation Layer), CONTRACT_MATRIX MEM-004 — Phase 10
"""

from __future__ import annotations

from datetime import datetime, timezone

from core.memory.adaptation import AdaptationLayer
from core.orchestrator.goal_analyzer import GoalSpec
from core.orchestrator.planner import Planner
from core.space.memory_protocol import ExperienceRecord
from memory.adapters.in_memory import InMemoryMemoryAdapter
from memory.evaluation import (
    EvaluationModule,
    FrozenTrace,
    FrozenTraceCorpus,
)


def test_experience_changes_plan_on_repeated_task() -> None:
    """MEM-004: Recorded failure causes measurable, eval-backed plan change on repeated task."""
    space_id = "space-adapt-gate"
    memory_store = InMemoryMemoryAdapter()
    planner = Planner()

    # Step 1: Initial task runs and fails with capability 'tool.web_search'
    failure_record = ExperienceRecord(
        experience_id="exp-web-fail-001",
        space_id=space_id,
        situation={"task": "lookup live currency rates", "domain": "finance"},
        action={"capability": "tool.web_search", "query": "USD to EUR"},
        outcome="HTTP 429 Rate Limited from external search API",
        counterfactual="Use local cached exchange rates or secondary rate provider",
        applicable_context={"alternative_capability": "tool.local_cache"},
        stored_at=datetime.now(timezone.utc),
    )
    memory_store.store_experience(failure_record)

    # Step 2: Evaluate against frozen reference corpus (benchmark deltas, not vibes)
    corpus = FrozenTraceCorpus(
        corpus_id="corpus-web-reliability",
        version="1.0.0",
        traces=(
            FrozenTrace(
                trace_id="trace-web-01",
                capability="tool.web_search",
                situation_description="Search query",
                expected_outcome="200 OK",
                actual_outcome="HTTP 429",
                succeeded=False,
            ),
        ),
    )
    eval_result = EvaluationModule.evaluate(failure_record, corpus)
    assert eval_result.score > 0.0, "Evaluation against frozen corpus must produce positive evidence score"
    assert "tool.web_search" in eval_result.delta_description

    # Step 3: Repeated similar task submitted to Space
    adaptation_layer = AdaptationLayer(memory_store=memory_store)
    hints = adaptation_layer.generate_hints(
        space_id=space_id,
        situation_hint={"capability": "tool.web_search"},
    )
    assert len(hints) >= 1
    assert hints[0].failed_capability == "tool.web_search"

    # Step 4: Plan before adaptation
    unadapted_spec = GoalSpec(
        goal_id="goal-initial",
        space_id=space_id,
        objective="lookup live currency rates",
        constraints=[],
        required_capabilities=["tool.web_search"],
        single_agent_eligible=True,
        command_id="cmd-initial",
        metadata={"alternative_capability": "tool.local_cache"},
    )
    initial_plan = planner.plan_goal(unadapted_spec)
    assert initial_plan.task_graph.nodes[0].capability == "tool.web_search"

    # Step 5: Plan after adaptation (injecting experience hints)
    adapted_spec = GoalSpec(
        goal_id="goal-repeated",
        space_id=space_id,
        objective="lookup live currency rates",
        constraints=[],
        required_capabilities=["tool.web_search"],
        single_agent_eligible=True,
        command_id="cmd-repeated",
        metadata={
            "experience_hints": hints,
            "alternative_capability": "tool.local_cache",
        },
    )
    adapted_plan = planner.plan_goal(adapted_spec)

    # Measurable plan change: Planned capability changed from failed tool to alternative
    assert adapted_plan.task_graph.nodes[0].capability == "tool.local_cache"
    assert adapted_plan.task_graph.nodes[0].capability != initial_plan.task_graph.nodes[0].capability
    assert adapted_plan.metadata["applied_hints_count"] >= 1

