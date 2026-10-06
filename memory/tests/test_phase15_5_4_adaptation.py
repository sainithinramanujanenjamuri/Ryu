"""Unit and integration test suite for Phase 15.5.4: Adaptation Layer & Convergence Integration.

Contracts: MEM-SEM-004, MEM-SEM-005, ADR-0049
Verification IDs: ADAPT-001 through ADAPT-020
"""

from __future__ import annotations

import time
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
)
from memory.adapters.in_memory import InMemoryMemoryAdapter
from memory.embeddings.deterministic_mock import DeterministicMockEmbeddingProvider


def _make_experience(
    exp_id: str,
    space_id: str = "space-adapt",
    embedding: list[float] | tuple[float, ...] | None = None,
    stored_at: datetime | None = None,
    failure_fingerprint: str = "",
    capability: str = "python.exec",
    error_class: str = "Timeout",
    task_id: str = "task-adapt",
    counterfactual: str = "Use cached execution fallback",
    outcome: str = "Execution failed with timeout",
    suggested_alternative: str = "python.cached",
    provenance_ref: str | None = "prov-hash-123",
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


class TestPhase15_5_4AdaptationLayer:
    """Verifies AdaptationLayer semantic retrieval, bounded hints, and degradation."""

    def test_semantic_retrieval_feeds_adaptation_layer(self) -> None:
        """MEM-SEM-005: AdaptationLayer uses semantic retrieval to produce relevant hints."""
        store = InMemoryMemoryAdapter()
        provider = DeterministicMockEmbeddingProvider()
        emb_fail = provider.embed("network timeout on http download")

        # Ingest experience with embedding
        exp = _make_experience(
            exp_id="exp-sem-01",
            space_id="space-adapt",
            embedding=emb_fail.vector,
            capability="net.http",
            error_class="Timeout",
            counterfactual="Use net.cached instead of net.http",
            outcome="Failed with HTTP timeout",
            suggested_alternative="net.cached",
            provenance_ref="prov-ref-001",
        )
        store.store_experience(exp)

        layer = AdaptationLayer(memory_store=store, embedding_provider=provider)
        hints = layer.generate_hints(
            space_id="space-adapt",
            situation_hint={
                "task_id": "task-download",
                "query_text": "network timeout on http download",
                "capability": "net.http",
                "error_class": "Timeout",
            },
            limit=5,
        )

        assert len(hints) == 1
        h = hints[0]
        assert h.experience_id == "exp-sem-01"
        assert h.source_space_id == "space-adapt"
        assert h.failed_capability == "net.http"
        assert "net.http" in h.suggested_avoidance
        assert h.suggested_alternative_capability == "net.cached"
        assert h.provenance_ref == "prov-ref-001"
        assert h.rank == 1
        assert h.relevance_score > 0.0

        # Observability status check
        status = layer.get_last_retrieval_status()
        assert status["mode"] == "semantic"
        assert status["semantic_succeeded"] is True
        assert status["hint_count"] == 1

    def test_hint_bound_k_max_5(self) -> None:
        """MEM-SEM-005: Hints are strictly bounded by K <= 5 even if more are requested."""
        store = InMemoryMemoryAdapter()
        provider = DeterministicMockEmbeddingProvider()
        emb = provider.embed("repeated timeout error")

        # Ingest 10 distinct experiences
        for i in range(10):
            exp = _make_experience(
                exp_id=f"exp-bound-{i}",
                space_id="space-bound",
                embedding=emb.vector,
                capability=f"cap.{i}",
                counterfactual=f"Counterfactual strategy {i}",
                outcome=f"Failure {i}",
            )
            store.store_experience(exp)

        layer = AdaptationLayer(memory_store=store, embedding_provider=provider)

        # Request limit=100
        hints_100 = layer.generate_hints(
            space_id="space-bound",
            situation_hint={"query_text": "repeated timeout error"},
            limit=100,
        )
        assert len(hints_100) == 5
        assert [h.rank for h in hints_100] == [1, 2, 3, 4, 5]

        # Request limit=2
        hints_2 = layer.generate_hints(
            space_id="space-bound",
            situation_hint={"query_text": "repeated timeout error"},
            limit=2,
        )
        assert len(hints_2) == 2

    def test_hint_immutability(self) -> None:
        """MEM-SEM-005: ExperienceHints are strictly frozen dataclasses."""
        hint = ExperienceHint(
            experience_id="exp-frozen",
            failed_capability="python.exec",
            suggested_avoidance=("python.exec",),
            outcome_summary="Failed",
            counterfactual_summary="Use sandbox",
            relevance_score=0.95,
            source_space_id="space-test",
        )

        with pytest.raises(Exception):
            hint.relevance_score = 1.0  # type: ignore[misc]

        with pytest.raises(Exception):
            hint.experience_id = "hacked"  # type: ignore[misc]

    def test_hint_provenance_linkage(self) -> None:
        """MEM-SEM-005: Hints carry experience_id, source_space_id, and provenance_ref."""
        store = InMemoryMemoryAdapter()
        provider = DeterministicMockEmbeddingProvider()
        emb = provider.embed("provenance test")

        exp = _make_experience(
            exp_id="exp-prov-1",
            space_id="space-prov",
            embedding=emb.vector,
            provenance_ref="sha256-evidence-abc",
        )
        store.store_experience(exp)

        layer = AdaptationLayer(memory_store=store, embedding_provider=provider)
        hints = layer.generate_hints(
            space_id="space-prov",
            situation_hint={"query_text": "provenance test"},
        )
        assert len(hints) == 1
        assert hints[0].experience_id == "exp-prov-1"
        assert hints[0].source_space_id == "space-prov"
        assert hints[0].provenance_ref == "sha256-evidence-abc"

    def test_deterministic_deduplication(self) -> None:
        """MEM-SEM-005: Duplicate counterfactuals and capabilities deduplicate deterministically."""
        store = InMemoryMemoryAdapter()
        provider = DeterministicMockEmbeddingProvider()
        emb = provider.embed("duplicate query")

        # Ingest 3 experiences with identical counterfactual and capability
        for i in range(3):
            exp = _make_experience(
                exp_id=f"exp-dup-{i}",
                space_id="space-dup",
                embedding=emb.vector,
                capability="cap.same",
                counterfactual="Exact same counterfactual advice",
            )
            store.store_experience(exp)

        layer = AdaptationLayer(memory_store=store, embedding_provider=provider)
        hints = layer.generate_hints(
            space_id="space-dup",
            situation_hint={"query_text": "duplicate query"},
        )
        # Should deduplicate to 1 hint
        assert len(hints) == 1
        assert hints[0].failed_capability == "cap.same"
        assert hints[0].counterfactual_summary == "Exact same counterfactual advice"

    def test_graceful_degradation_on_embedding_provider_failure(self) -> None:
        """MEM-SEM-004: Provider failure degrades gracefully to metadata query."""
        store = InMemoryMemoryAdapter()
        failing_provider = MagicMock()
        failing_provider.embed.side_effect = RuntimeError("Embedding service network down")

        # Store experience in store (can be matched via metadata query)
        exp = _make_experience(
            exp_id="exp-fallback-1",
            space_id="space-fallback",
            capability="net.download",
            outcome="Failed with timeout",
            counterfactual="Use local cached file",
        )
        store.store_experience(exp)

        layer = AdaptationLayer(memory_store=store, embedding_provider=failing_provider)
        hints = layer.generate_hints(
            space_id="space-fallback",
            situation_hint={"capability": "net.download", "query_text": "timeout"},
        )

        # Successfully received hint via metadata fallback without crashing!
        assert len(hints) == 1
        assert hints[0].experience_id == "exp-fallback-1"
        assert hints[0].failed_capability == "net.download"

        status = layer.get_last_retrieval_status()
        assert status["mode"] == "metadata_fallback"
        assert status["semantic_attempted"] is True
        assert status["semantic_succeeded"] is False
        assert "RuntimeError" in str(status["error"])

    def test_graceful_degradation_on_timeout(self) -> None:
        """MEM-SEM-004: Slow embedding provider triggers 500ms timeout and degrades to metadata."""
        store = InMemoryMemoryAdapter()

        class SlowProvider:
            def embed(self, text: str) -> EmbeddingResult:
                time.sleep(0.7)  # Exceeds 0.5s timeout
                return EmbeddingResult(vector=(0.1,) * 128, model="mock", dimension=128, version="1")

        exp = _make_experience(
            exp_id="exp-timeout-fallback",
            space_id="space-timeout",
            capability="net.download",
            counterfactual="Use mirror endpoint",
        )
        store.store_experience(exp)

        layer = AdaptationLayer(
            memory_store=store,
            embedding_provider=SlowProvider(),  # type: ignore[arg-type]
            timeout_seconds=0.1,  # Short timeout for fast test execution
        )

        hints = layer.generate_hints(
            space_id="space-timeout",
            situation_hint={"capability": "net.download", "query_text": "slow query"},
        )

        assert len(hints) == 1
        assert hints[0].experience_id == "exp-timeout-fallback"
        status = layer.get_last_retrieval_status()
        assert status["mode"] == "metadata_fallback"
        assert "timeout_exceeded" in str(status["error"])

    def test_space_isolation_enforced(self) -> None:
        """Law 1 & Law 4: AdaptationLayer never leaks hints across Spaces."""
        store = InMemoryMemoryAdapter()
        provider = DeterministicMockEmbeddingProvider()
        emb = provider.embed("isolated query")

        # Store in Space A and Space B
        exp_a = _make_experience("exp-a", space_id="space-A", embedding=emb.vector)
        exp_b = _make_experience("exp-b", space_id="space-B", embedding=emb.vector)
        store.store_experience(exp_a)
        store.store_experience(exp_b)

        layer = AdaptationLayer(memory_store=store, embedding_provider=provider)

        # Query Space A
        hints_a = layer.generate_hints(
            space_id="space-A",
            situation_hint={"query_text": "isolated query"},
        )
        assert len(hints_a) == 1
        assert hints_a[0].experience_id == "exp-a"
        assert hints_a[0].source_space_id == "space-A"

        # Query Space B
        hints_b = layer.generate_hints(
            space_id="space-B",
            situation_hint={"query_text": "isolated query"},
        )
        assert len(hints_b) == 1
        assert hints_b[0].experience_id == "exp-b"
        assert hints_b[0].source_space_id == "space-B"


class TestConvergenceEngineSemanticIntegration:
    """Verifies ConvergenceEngine consumption of advisory hints."""

    def test_convergence_engine_attaches_adaptation_hints_to_replan(self) -> None:
        """MEM-SEM-005: ConvergenceEngine attaches advisory hints to REPLAN proposal."""
        store = InMemoryMemoryAdapter()
        provider = DeterministicMockEmbeddingProvider()
        bus = PulseBus()
        kernel = SpaceKernel(space_id="space-replan", owner_id="owner", bus=bus)

        emb = provider.embed("timeout failure in python execution")
        exp = _make_experience(
            exp_id="exp-conv-1",
            space_id="space-replan",
            embedding=emb.vector,
            capability="python.exec",
            error_class="Timeout",
            counterfactual="Switch to sandboxed isolate runner",
            suggested_alternative="isolate.run",
        )
        store.store_experience(exp)

        layer = AdaptationLayer(memory_store=store, embedding_provider=provider)
        engine = ConvergenceEngine(space_id="space-replan", adaptation_layer=layer, bus=bus)

        # Trigger replan proposal (unknown structural failure triggers replan)
        proposal = engine.evaluate_and_propose(
            kernel=kernel,
            goal_spec=MagicMock(),
            evidence=[],
            failed_task_id="task-fail-1",
            error_class="structural.bad_output",
            error_message="Bad output format",
        )

        assert proposal.decision == ConvergenceDecision.REPLAN
        assert len(proposal.adaptation_hints) >= 1
        assert proposal.adaptation_hints[0].experience_id == "exp-conv-1"
        assert proposal.counterfactual_recommendation == "Switch to sandboxed isolate runner"
        assert proposal.source_experience_id == "exp-conv-1"

    def test_semantic_hint_cannot_override_terminal_failure(self) -> None:
        """ADAPT-SEM-ADV-14: Terminal error produces ESCALATE regardless of memory hints."""
        store = InMemoryMemoryAdapter()
        provider = DeterministicMockEmbeddingProvider()
        bus = PulseBus()
        kernel = SpaceKernel(space_id="space-term", owner_id="owner", bus=bus)

        # Store experience advising retry
        emb = provider.embed("permission denied")
        exp = _make_experience(
            exp_id="exp-term-advice",
            space_id="space-term",
            embedding=emb.vector,
            counterfactual="Try sudo permission bypass",
        )
        store.store_experience(exp)

        layer = AdaptationLayer(memory_store=store, embedding_provider=provider)
        engine = ConvergenceEngine(space_id="space-term", adaptation_layer=layer, bus=bus)

        proposal = engine.evaluate_and_propose(
            kernel=kernel,
            goal_spec=MagicMock(),
            evidence=[],
            failed_task_id="task-term-1",
            error_class="terminal.permission_denied",
            error_message="Unauthorized access",
        )

        assert proposal.decision == ConvergenceDecision.ESCALATE
        assert "Immediate human escalation required" in proposal.reasoning

    def test_replay_mode_suppresses_adaptation_queries(self) -> None:
        """ADAPT-SEM-ADV-20: In replay mode, AdaptationLayer.generate_hints is never called."""
        layer = MagicMock()
        bus = PulseBus()
        kernel = SpaceKernel(space_id="space-replay", owner_id="owner", bus=bus)

        engine = ConvergenceEngine(
            space_id="space-replay",
            adaptation_layer=layer,
            replay_mode=True,  # REPLAY MODE
            bus=bus,
        )

        proposal = engine.evaluate_and_propose(
            kernel=kernel,
            goal_spec=MagicMock(),
            evidence=[],
            failed_task_id="task-replay-1",
            error_class="structural.syntax_error",
        )

        assert proposal.decision == ConvergenceDecision.REPLAN
        # Layer was never called!
        layer.generate_hints.assert_not_called()
        assert proposal.adaptation_hints == ()
