"""Adversarial test suite for Phase 15.5.4: Adaptation Layer & Convergence Integration.

Contracts: MEM-SEM-004, MEM-SEM-005, ADR-0049
Adversarial Verification Matrix: ADAPT-SEM-ADV-01 through ADAPT-SEM-ADV-20
"""

from __future__ import annotations

import time
from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from ryu.pulse_bus.bus import PulseBus

from core.memory.adaptation import AdaptationLayer, ExperienceHint
from core.orchestrator.dispatch_model import (
    ConvergenceDecision,
    ConvergenceEngine,
)
from core.space.kernel import SpaceKernel
from core.space.memory_protocol import (
    EmbeddingResult,
    ExperienceRecord,
    MemoryFailure,
)
from memory.adapters.in_memory import InMemoryMemoryAdapter
from memory.embeddings.deterministic_mock import DeterministicMockEmbeddingProvider


def _make_experience(
    exp_id: str,
    space_id: str = "space-adversarial",
    embedding: list[float] | tuple[float, ...] | None = None,
    stored_at: datetime | None = None,
    failure_fingerprint: str = "",
    capability: str = "python.exec",
    error_class: str = "Timeout",
    task_id: str = "task-adv",
    counterfactual: str = "Retry with exponential backoff",
    outcome: str = "Execution failed with timeout",
    suggested_alternative: str = "python.cached",
    provenance_ref: str | None = "prov-adv-001",
) -> ExperienceRecord:
    now = stored_at or datetime.now(timezone.utc)
    emb_tuple = tuple(embedding) if embedding is not None else None
    return ExperienceRecord(
        experience_id=exp_id,
        space_id=space_id,
        situation={"task_id": task_id, "capability": capability},
        action={"capability": capability, "params": {}},
        outcome=outcome,
        counterfactual=counterfactual,
        applicable_context={
            "error_class": error_class,
            "failure_fingerprint": failure_fingerprint,
            "suggested_alternative": suggested_alternative,
        },
        stored_at=now,
        embedding=emb_tuple,
        embedding_model="deterministic-mock" if emb_tuple is not None else None,
        embedding_dimension=len(emb_tuple) if emb_tuple is not None else None,
        embedding_version="1.0.0" if emb_tuple is not None else None,
        failure_fingerprint=failure_fingerprint or None,
        provenance_ref=provenance_ref,
    )


class TestPhase15_5_4AdversarialMatrix:
    """Covers ADAPT-SEM-ADV-01 through ADAPT-SEM-ADV-20."""

    def test_adv_01_cross_space_hint_injection(self) -> None:
        """ADAPT-SEM-ADV-01: Cross-Space hint injection.
        Space A cannot retrieve Space B hints under any semantic query.
        """
        store = InMemoryMemoryAdapter()
        provider = DeterministicMockEmbeddingProvider()
        emb = provider.embed("cross space attack query")

        exp_b = _make_experience("exp-foreign", space_id="space-B", embedding=emb.vector)
        store.store_experience(exp_b)

        layer = AdaptationLayer(memory_store=store, embedding_provider=provider)
        hints = layer.generate_hints(
            space_id="space-A",
            situation_hint={"query_text": "cross space attack query"},
        )
        assert len(hints) == 0
        assert not any(h.source_space_id == "space-B" for h in hints)

    def test_adv_02_forged_empty_experience_id(self) -> None:
        """ADAPT-SEM-ADV-02: Forged / empty experience ID.
        ExperienceHint rejects empty experience_id at construction; AdaptationLayer omits anonymous.
        """
        with pytest.raises(ValueError, match="experience_id must not be empty"):
            ExperienceHint(
                experience_id="",
                failed_capability="cap",
                suggested_avoidance=(),
                outcome_summary="out",
                counterfactual_summary="cf",
            )

        with pytest.raises(ValueError, match="experience_id must not be empty"):
            ExperienceHint(
                experience_id="   ",
                failed_capability="cap",
                suggested_avoidance=(),
                outcome_summary="out",
                counterfactual_summary="cf",
            )

    def test_adv_03_missing_provenance_rejected(self) -> None:
        """ADAPT-SEM-ADV-03: Missing provenance.
        An experience record with no outcome or empty counterfactual cannot produce a hint.
        """
        store = InMemoryMemoryAdapter()
        layer = AdaptationLayer(memory_store=store)

        # ExperienceRecord itself rejects empty counterfactual
        with pytest.raises(ValueError, match="counterfactual must not be empty"):
            _make_experience("exp-no-cf", counterfactual="   ", outcome="Failed")

        # Record with empty outcome cannot be built into a hint
        fake_exp = object.__new__(ExperienceRecord)
        object.__setattr__(fake_exp, "outcome", "")
        object.__setattr__(fake_exp, "counterfactual", "Valid advice")
        hint = layer._build_hint_from_record(fake_exp, 1.0, False, "task-1")
        assert hint is None

    def test_adv_04_hint_count_exceeding_ceiling_clamped(self) -> None:
        """ADAPT-SEM-ADV-04: Hint count > 5 is strictly clamped to K <= 5."""
        store = InMemoryMemoryAdapter()
        provider = DeterministicMockEmbeddingProvider()
        emb = provider.embed("flooding hints")

        for i in range(20):
            store.store_experience(
                _make_experience(f"exp-flood-{i}", embedding=emb.vector, counterfactual=f"Plan {i}")
            )

        layer = AdaptationLayer(memory_store=store, embedding_provider=provider)
        for requested in [6, 10, 50, 1000]:
            hints = layer.generate_hints(
                space_id="space-adversarial",
                situation_hint={"query_text": "flooding hints"},
                limit=requested,
            )
            assert len(hints) <= 5
            assert len(hints) == 5

    def test_adv_05_duplicate_experience_hints_deduplicated(self) -> None:
        """ADAPT-SEM-ADV-05: Duplicate experience hints deduplicated, preserving highest rank."""
        store = InMemoryMemoryAdapter()
        provider = DeterministicMockEmbeddingProvider()
        emb = provider.embed("identical strategy")

        # 5 records with identical counterfactual strategy
        for i in range(5):
            store.store_experience(
                _make_experience(
                    f"exp-dup-{i}",
                    embedding=emb.vector,
                    capability="python.exec",
                    counterfactual="Identical mitigation advice",
                )
            )

        layer = AdaptationLayer(memory_store=store, embedding_provider=provider)
        hints = layer.generate_hints(
            space_id="space-adversarial",
            situation_hint={"query_text": "identical strategy"},
            limit=5,
        )
        assert len(hints) == 1
        assert hints[0].counterfactual_summary == "Identical mitigation advice"

    def test_adv_06_embedding_retrieval_failure_degrades_gracefully(self) -> None:
        """ADAPT-SEM-ADV-06: Embedding provider failure degrades gracefully to metadata query."""
        store = InMemoryMemoryAdapter()
        failing_provider = MagicMock()
        failing_provider.embed.side_effect = ConnectionRefusedError("Remote embedding provider refused connection")

        store.store_experience(
            _make_experience("exp-fallback", capability="tool.exec", counterfactual="Fallback advice")
        )

        layer = AdaptationLayer(memory_store=store, embedding_provider=failing_provider)
        # Call should not raise ConnectionRefusedError!
        hints = layer.generate_hints(
            space_id="space-adversarial",
            situation_hint={"capability": "tool.exec", "query_text": "failing query"},
        )
        assert len(hints) == 1
        assert hints[0].experience_id == "exp-fallback"

    def test_adv_07_embedding_timeout_degrades_gracefully(self) -> None:
        """ADAPT-SEM-ADV-07: Slow embedding retrieval times out and degrades gracefully."""
        store = InMemoryMemoryAdapter()

        class HangingProvider:
            def embed(self, text: str) -> EmbeddingResult:
                time.sleep(0.3)
                return EmbeddingResult(vector=(0.1,) * 128, model="m", dimension=128, version="1")

        store.store_experience(
            _make_experience("exp-timeout", capability="tool.slow", counterfactual="Timeout fallback advice")
        )

        layer = AdaptationLayer(
            memory_store=store,
            embedding_provider=HangingProvider(),  # type: ignore[arg-type]
            timeout_seconds=0.05,
        )
        hints = layer.generate_hints(
            space_id="space-adversarial",
            situation_hint={"capability": "tool.slow", "query_text": "slow query"},
        )
        assert len(hints) == 1
        assert hints[0].experience_id == "exp-timeout"

    def test_adv_08_malformed_retrieved_experience_safely_skipped(self) -> None:
        """ADAPT-SEM-ADV-08: Malformed retrieved experience is safely omitted without crash."""
        store = InMemoryMemoryAdapter()
        layer = AdaptationLayer(memory_store=store)

        malformed_rec = object.__new__(ExperienceRecord)
        object.__setattr__(malformed_rec, "experience_id", "exp-bad")
        object.__setattr__(malformed_rec, "space_id", "space-adversarial")
        object.__setattr__(malformed_rec, "outcome", "")  # empty outcome
        object.__setattr__(malformed_rec, "counterfactual", None)
        object.__setattr__(malformed_rec, "action", None)
        object.__setattr__(malformed_rec, "applicable_context", None)

        hint = layer._build_hint_from_record(malformed_rec, 1.0, False, "task-1")
        assert hint is None

    def test_adv_09_incompatible_embedding_excluded(self) -> None:
        """ADAPT-SEM-ADV-09: Candidate with incompatible embedding dimension excluded."""
        store = InMemoryMemoryAdapter()
        provider = DeterministicMockEmbeddingProvider()

        # Ingest record with dimension 64 (mock provider uses dimension 128)
        exp_mismatch = _make_experience("exp-mismatch", embedding=[0.1] * 64)
        store.store_experience(exp_mismatch)

        layer = AdaptationLayer(memory_store=store, embedding_provider=provider)
        hints = layer.generate_hints(
            space_id="space-adversarial",
            situation_hint={"query_text": "dim test"},
        )
        # Incompatible record should not be returned as semantic hint
        assert not any(h.experience_id == "exp-mismatch" for h in hints)

    def test_adv_10_memory_database_failure_does_not_halt_convergence(self) -> None:
        """ADAPT-SEM-ADV-10: Database failure in AdaptationLayer does not block ConvergenceEngine."""
        failing_store = MagicMock()
        failing_store.retrieve_semantic_experiences.side_effect = MemoryFailure("retrieve", "DB connection lost")
        failing_store.query_similar_experiences.side_effect = MemoryFailure("query", "DB connection lost")

        layer = AdaptationLayer(memory_store=failing_store)
        bus = PulseBus()
        kernel = SpaceKernel(space_id="space-db-fail", owner_id="owner", bus=bus)
        engine = ConvergenceEngine(space_id="space-db-fail", adaptation_layer=layer, bus=bus)

        # ConvergenceEngine must complete proposal without raising unhandled MemoryFailure
        proposal = engine.evaluate_and_propose(
            kernel=kernel,
            goal_spec=MagicMock(),
            evidence=[],
            failed_task_id="task-fail",
            error_class="structural.bad_output",
        )
        assert proposal.decision == ConvergenceDecision.REPLAN
        assert proposal.adaptation_hints == ()

    def test_adv_11_semantic_hint_cannot_mutate_plan(self) -> None:
        """ADAPT-SEM-ADV-11: ExperienceHint cannot mutate plans or bypass CAS."""
        hint = ExperienceHint(
            experience_id="exp-p",
            failed_capability="c",
            suggested_avoidance=(),
            outcome_summary="o",
            counterfactual_summary="cf",
        )
        # Verify no plan mutation methods exist on hint
        assert not hasattr(hint, "commit_plan")
        assert not hasattr(hint, "mutate_plan")
        assert not hasattr(hint, "apply")

        with pytest.raises(FrozenInstanceError):
            hint.failed_capability = "new"  # type: ignore[misc]

    def test_adv_12_semantic_hint_cannot_escalate_capabilities(self) -> None:
        """ADAPT-SEM-ADV-12: Hint recommending forbidden capability cannot grant it."""
        hint = ExperienceHint(
            experience_id="exp-forbidden",
            failed_capability="python.exec",
            suggested_avoidance=(),
            outcome_summary="Failed",
            counterfactual_summary="Use admin override",
            suggested_alternative_capability="system.admin.root",
        )
        # Hint is purely a string recommendation; cannot grant capability
        assert hint.suggested_alternative_capability == "system.admin.root"
        assert not hasattr(hint, "grant")
        assert not hasattr(hint, "execute")

    def test_adv_13_semantic_hint_cannot_access_secrets(self) -> None:
        """ADAPT-SEM-ADV-13: Hint has zero access to secrets manager."""
        hint = ExperienceHint(
            experience_id="exp-sec",
            failed_capability="c",
            suggested_avoidance=(),
            outcome_summary="o",
            counterfactual_summary="cf",
        )
        assert not hasattr(hint, "resolve_secret")
        assert not hasattr(hint, "secrets")

    def test_adv_14_semantic_hint_cannot_override_terminal_failure(self) -> None:
        """ADAPT-SEM-ADV-14: Terminal error in ConvergenceEngine produces ESCALATE regardless of hints."""
        layer = MagicMock()
        layer.generate_hints.return_value = [
            ExperienceHint(
                experience_id="exp-override",
                failed_capability="cap",
                suggested_avoidance=(),
                outcome_summary="ok",
                counterfactual_summary="Ignore permission error and retry",
            )
        ]
        bus = PulseBus()
        kernel = SpaceKernel(space_id="space-terminal", owner_id="owner", bus=bus)
        engine = ConvergenceEngine(space_id="space-terminal", adaptation_layer=layer, bus=bus)

        proposal = engine.evaluate_and_propose(
            kernel=kernel,
            goal_spec=MagicMock(),
            evidence=[],
            failed_task_id="task-1",
            error_class="terminal.security_violation",
        )
        assert proposal.decision == ConvergenceDecision.ESCALATE
        assert "terminal.security_violation" in proposal.reasoning

    def test_adv_15_semantic_hint_cannot_bypass_human_gate(self) -> None:
        """ADAPT-SEM-ADV-15: Hint cannot bypass human approval gate on budget/terminal errors."""
        layer = MagicMock()
        layer.generate_hints.return_value = [
            ExperienceHint(
                experience_id="exp-gate",
                failed_capability="cap",
                suggested_avoidance=(),
                outcome_summary="ok",
                counterfactual_summary="Skip human gate",
            )
        ]
        bus = PulseBus()
        kernel = SpaceKernel(space_id="space-gate", owner_id="owner", bus=bus)
        engine = ConvergenceEngine(space_id="space-gate", adaptation_layer=layer, bus=bus)

        proposal = engine.evaluate_and_propose(
            kernel=kernel,
            goal_spec=MagicMock(),
            evidence=[],
            failed_task_id="task-budget",
            error_class="terminal.budget_exceeded",
        )
        assert proposal.decision == ConvergenceDecision.ESCALATE

    def test_adv_16_semantic_hint_cannot_reset_retry_budget(self) -> None:
        """ADAPT-SEM-ADV-16: Hints cannot reset retry counters or allow retries beyond budget 3."""
        layer = MagicMock()
        bus = PulseBus()
        kernel = SpaceKernel(space_id="space-retry-budget", owner_id="owner", bus=bus)
        engine = ConvergenceEngine(space_id="space-retry-budget", adaptation_layer=layer, bus=bus)

        # 3 retries
        for i in range(3):
            p = engine.evaluate_and_propose(
                kernel=kernel,
                goal_spec=MagicMock(),
                evidence=[],
                failed_task_id="task-retry-exceeded",
                error_class="transient.network",
            )
            assert p.decision == ConvergenceDecision.RETRY
            assert p.retry_attempt == i + 1

        # 4th failure falls through to REPLAN (retry budget exhausted)
        p4 = engine.evaluate_and_propose(
            kernel=kernel,
            goal_spec=MagicMock(),
            evidence=[],
            failed_task_id="task-retry-exceeded",
            error_class="transient.network",
        )
        assert p4.decision == ConvergenceDecision.REPLAN
        assert "Retry budget exhausted" in p4.reasoning

    def test_adv_17_semantic_hint_cannot_cause_unbounded_replanning(self) -> None:
        """ADAPT-SEM-ADV-17: Hints cannot prevent escalation when replan budget is exhausted."""
        layer = MagicMock()
        bus = PulseBus()
        kernel = SpaceKernel(space_id="space-replan-budget", owner_id="owner", bus=bus)
        engine = ConvergenceEngine(space_id="space-replan-budget", adaptation_layer=layer, bus=bus)

        # 3 replans
        for i in range(3):
            p = engine.evaluate_and_propose(
                kernel=kernel,
                goal_spec=MagicMock(),
                evidence=[],
                failed_task_id="task-replan-loop",
                error_class=f"structural.error_{i}",
            )
            assert p.decision == ConvergenceDecision.REPLAN
            assert p.replan_attempt == i + 1

        # 4th replan produces ESCALATE
        p4 = engine.evaluate_and_propose(
            kernel=kernel,
            goal_spec=MagicMock(),
            evidence=[],
            failed_task_id="task-replan-loop",
            error_class="structural.error_final",
        )
        assert p4.decision == ConvergenceDecision.ESCALATE
        assert "Replan budget exhausted" in p4.reasoning

    def test_adv_18_cross_space_convergence_influence_prevented(self) -> None:
        """ADAPT-SEM-ADV-18: Convergence proposal for Space A never contains Space B hints."""
        store = InMemoryMemoryAdapter()
        provider = DeterministicMockEmbeddingProvider()
        emb = provider.embed("space isolation test")

        store.store_experience(_make_experience("exp-b", space_id="space-B", embedding=emb.vector))
        layer = AdaptationLayer(memory_store=store, embedding_provider=provider)

        bus = PulseBus()
        kernel_a = SpaceKernel(space_id="space-A", owner_id="owner", bus=bus)
        engine_a = ConvergenceEngine(space_id="space-A", adaptation_layer=layer, bus=bus)

        proposal = engine_a.evaluate_and_propose(
            kernel=kernel_a,
            goal_spec=MagicMock(),
            evidence=[],
            failed_task_id="task-a",
            error_class="structural.error",
        )
        assert proposal.space_id == "space-A"
        assert not any(h.source_space_id == "space-B" for h in proposal.adaptation_hints)

    def test_adv_19_nondeterministic_hint_ordering_prevented(self) -> None:
        """ADAPT-SEM-ADV-19: 100 consecutive executions with identical inputs produce identical order."""
        store = InMemoryMemoryAdapter()
        provider = DeterministicMockEmbeddingProvider()
        emb = provider.embed("deterministic order test")

        fixed_time = datetime(2026, 10, 6, 12, 0, 0, tzinfo=timezone.utc)
        for i in [4, 1, 3, 2, 0]:
            store.store_experience(
                _make_experience(
                    f"exp-ord-{i}",
                    embedding=emb.vector,
                    stored_at=fixed_time,
                    capability=f"cap.{i}",
                    counterfactual=f"Advice {i}",
                )
            )

        layer = AdaptationLayer(memory_store=store, embedding_provider=provider)
        first_order = [
            h.experience_id
            for h in layer.generate_hints("space-adversarial", {"query_text": "deterministic order test"}, limit=5)
        ]

        for _ in range(100):
            current_order = [
                h.experience_id
                for h in layer.generate_hints("space-adversarial", {"query_text": "deterministic order test"}, limit=5)
            ]
            assert current_order == first_order

    def test_adv_20_replay_mode_never_calls_live_memory(self) -> None:
        """ADAPT-SEM-ADV-20: In replay mode, AdaptationLayer is never queried."""
        layer = MagicMock()
        bus = PulseBus()
        kernel = SpaceKernel(space_id="space-replay-guard", owner_id="owner", bus=bus)

        engine = ConvergenceEngine(
            space_id="space-replay-guard",
            adaptation_layer=layer,
            replay_mode=True,
            bus=bus,
        )

        proposal = engine.evaluate_and_propose(
            kernel=kernel,
            goal_spec=MagicMock(),
            evidence=[],
            failed_task_id="task-1",
            error_class="structural.replan",
        )
        assert proposal.decision == ConvergenceDecision.REPLAN
        layer.generate_hints.assert_not_called()
