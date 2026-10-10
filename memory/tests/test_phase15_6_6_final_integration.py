"""Phase 15.6.6 — Final Integration Verification Suite.

Validates the complete closed-loop production semantic loop (ADR-0050):
TaskExecutionOutcome → ExperienceObserver → Reflector → Durable Memory →
Embedding Outbox → Embedding Provider → Semantic Retrieval → Adaptation →
ExperienceHints → ConvergenceEngine → PlanDelta → SpaceKernel CAS → Task Graph Unblocking

Tests:
1. End-to-end closed loop reflection-embedding-retrieval-convergence-CAS unblocking
2. Migration 010 and 011 schema syntax & compatibility
3. Concurrent multi-Space execution & strict space isolation (Law 1, Law 4)
4. Memory compaction interaction with pending and completed outbox records
5. Replay trace isolation (replay mode strictly bypasses live mutable memory)
6. Convergence replan budget ceiling (replan attempts capped at MAX_REPLAN_BUDGET)
7. Deterministic strategy oscillation escalation (A -> B -> A -> B triggers human escalation)
"""

from __future__ import annotations

import concurrent.futures
from datetime import datetime, timezone
from pathlib import Path

from ryu.pulse_bus.bus import PulseBus

from core.memory.adaptation import AdaptationLayer
from core.orchestrator.adapter import Adapter
from core.orchestrator.dispatch_model import (
    ConvergenceDecision,
    ConvergenceEngine,
    VerifiedExecutionEvidence,
    _compute_failure_fingerprint,
)
from core.plans.delta import PlanDelta
from core.plans.task_graph import TaskState
from core.space.kernel import SpaceKernel
from core.space.memory_protocol import (
    ExperienceRecord,
    RetentionPolicy,
    TaskExecutionOutcome,
)
from memory.adapters.in_memory import InMemoryMemoryAdapter
from memory.embeddings.deterministic_mock import DeterministicMockEmbeddingProvider
from memory.experience_observer import ExecutionExperienceObserver
from memory.ingestion.pipeline import EmbeddingIngestionPipeline
from memory.reflector import Reflector

_MOCK_PROV = DeterministicMockEmbeddingProvider()


def test_end_to_end_closed_loop_reflection_embedding_convergence() -> None:
    """F05-AUDIT-01..06: Full closed-loop verification of production semantic learning cycle."""
    space_id = "space-closed-loop-final"
    bus = PulseBus()
    kernel = SpaceKernel(space_id=space_id, owner_id="owner-e2e", bus=bus)

    # Step 1: Initialize Task Graph with a ready task via Plan CAS
    init_delta = PlanDelta(
        space_id=space_id,
        base_version=1,
        resulting_version=2,
        ops=[
            {
                "op": "add",
                "target_node_id": "task-download",
                "capability": "tool.net_download",
                "params": {"url": "https://example.com/data.bin", "timeout": 10},
            }
        ],
    )
    ok, ver, err = kernel.commit_plan_delta(init_delta)
    assert ok is True
    assert ver == 2

    # Step 2: Set up memory subsystem with ingestion pipeline and auto_embed
    mem_store = InMemoryMemoryAdapter()
    pipeline = EmbeddingIngestionPipeline(
        memory_store=mem_store,
        embedding_provider=_MOCK_PROV,
    )
    adapter = Adapter(space_id=space_id, bus=bus)
    reflector = Reflector(
        adapter=adapter,
        memory_store=mem_store,
        bus=bus,
        ingestion_pipeline=pipeline,
        auto_embed=True,
    )
    observer = ExecutionExperienceObserver(
        reflector=reflector,
        sanitize_secrets=True,
    )

    # Step 3: Simulate task failure reported by Dispatcher (ADAPT-001)
    fp = _compute_failure_fingerprint(space_id, "task-download", "network.socket_timeout")
    outcome = TaskExecutionOutcome(
        task_id="task-download",
        space_id=space_id,
        plan_version=2,
        capability="tool.net_download",
        params={"url": "https://example.com/data.bin", "api_key": "secret-token-xyz"},
        status="failed",
        exit_code=1,
        duration_seconds=1.25,
        error_class="network.socket_timeout",
        error_message="Socket timed out waiting for connection",
        failure_fingerprint=fp,
    )
    exp_id = observer.observe_task_outcome(outcome)
    assert exp_id is not None, "Experience must be stored"

    # Step 4: Verify record was durably stored and embedded automatically (F05-AUDIT-01 closure)
    stored_rec = mem_store.get_experience(space_id, exp_id)
    assert stored_rec is not None
    assert stored_rec.embedding_status == "completed"
    assert stored_rec.embedding is not None
    assert stored_rec.embedding_dimension == _MOCK_PROV.dimension
    # Verify secret was sanitized
    assert "secret-token-xyz" not in str(stored_rec.situation)

    # Step 5: Mark task failed in kernel task graph (simulating dispatcher completion)
    dl_node = kernel.get_task_graph().get_node("task-download")
    assert dl_node is not None
    dl_node.state = "failed"

    # Step 6: Set up AdaptationLayer and ConvergenceEngine
    adaptation_layer = AdaptationLayer(
        memory_store=mem_store,
        embedding_provider=_MOCK_PROV,
        timeout_seconds=0.5,
    )
    engine = ConvergenceEngine(
        space_id=space_id,
        adaptation_layer=adaptation_layer,
    )

    evidence: list[VerifiedExecutionEvidence] = []

    proposal = engine.evaluate_and_propose(
        kernel=kernel,
        goal_spec=None,
        evidence=evidence,
        failed_task_id="task-download",
        error_class="network.socket_timeout",
        error_message="Socket timed out waiting for connection",
    )

    # Step 8: Verify proposal contains advisory hints and PlanDelta rollback
    assert proposal.decision == ConvergenceDecision.REPLAN
    assert len(proposal.adaptation_hints) > 0, "Semantic retrieval must return advisory hints"
    top_hint = proposal.adaptation_hints[0]
    assert top_hint.experience_id == exp_id
    assert proposal.plan_delta is not None

    rollback_ops = [op for op in proposal.plan_delta.ops if op.get("op") == "rollback"]
    assert len(rollback_ops) == 1
    assert rollback_ops[0]["target_node_id"] == "task-download"
    assert "counterfactual_recommendation" in rollback_ops[0]["params"]

    # Step 9: Apply proposal via SpaceKernel CAS (F05-AUDIT-05 closure)
    success, new_version, err = engine.apply_proposal(proposal, kernel)
    assert success is True
    assert new_version == 3
    assert err is None

    # Step 10: Verify node state reset to READY and params reconciled
    updated_node = kernel.get_task_graph().get_node("task-download")
    assert updated_node is not None
    assert updated_node.state in ("ready", TaskState.READY.value), "Failed node must transition to READY upon rollback"
    assert "counterfactual_recommendation" in updated_node.params
    assert "replan_attempt" in updated_node.params

    pipeline.close()
    adaptation_layer.close()


def test_migration_010_and_011_schema_coherence() -> None:
    """MEM-PG-001 & MEM-INGEST-001: Migration 010 and 011 SQL scripts are compatible and complete."""
    mig_010_path = Path("deploy/migrations/010_add_space_experience_candidate_indexes.sql")
    mig_011_path = Path("deploy/migrations/011_add_experience_embedding_outbox.sql")

    assert mig_010_path.exists()
    assert mig_011_path.exists()

    sql_010 = mig_010_path.read_text(encoding="utf-8")
    sql_011 = mig_011_path.read_text(encoding="utf-8")

    # Migration 010 defines expression indexes for multi-prong candidate selection
    assert "idx_space_exp_cap" in sql_010
    assert "idx_space_exp_error_class" in sql_010
    assert "idx_space_exp_cap_stored" in sql_010

    # Migration 011 defines outbox status tracking and partial index
    assert "embedding_status" in sql_011
    assert "embedding_attempts" in sql_011
    assert "embedding_error" in sql_011
    assert "idx_space_exp_embedding_outbox" in sql_011


def test_concurrent_multi_space_isolation() -> None:
    """SCCA Law 1 & Law 4: Concurrent multi-space operations strictly maintain isolation."""
    spaces = [f"space-concurrent-{i}" for i in range(3)]
    mem_store = InMemoryMemoryAdapter()
    bus = PulseBus()
    pipeline = EmbeddingIngestionPipeline(
        memory_store=mem_store,
        embedding_provider=_MOCK_PROV,
    )

    def _run_space_workflow(sid: str) -> None:
        adapter = Adapter(space_id=sid, bus=bus)
        reflector = Reflector(
            adapter=adapter,
            memory_store=mem_store,
            bus=bus,
            ingestion_pipeline=pipeline,
            auto_embed=True,
        )
        observer = ExecutionExperienceObserver(reflector=reflector)

        for j in range(3):
            outcome = TaskExecutionOutcome(
                task_id=f"t-{sid}-{j}",
                space_id=sid,
                plan_version=1,
                capability=f"cap.{sid}",
                params={},
                status="failed",
                error_class="err.test",
                error_message=f"Error in {sid}",
            )
            observer.observe_task_outcome(outcome)

    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        futures = [executor.submit(_run_space_workflow, sid) for sid in spaces]
        for f in concurrent.futures.as_completed(futures):
            f.result()

    # Verify each space has exactly 3 records, all embedded, with no cross-space leakage
    for sid in spaces:
        records = mem_store.list_experiences(sid, limit=50)
        assert len(records) == 3
        for r in records:
            assert r.space_id == sid
            assert r.embedding_status == "completed"
            assert r.embedding is not None

    pipeline.close()


def test_memory_compaction_with_pending_and_completed_outbox() -> None:
    """MEM-RETAIN-001 & MEM-INGEST-001: Compaction safely manages pending and completed outbox records."""
    space_id = "space-compaction-outbox"
    adapter = InMemoryMemoryAdapter()
    now = datetime.now(timezone.utc)

    # Store 4 records: 2 with embeddings, 2 pending
    r1 = ExperienceRecord(
        experience_id="exp-c1",
        space_id=space_id,
        situation={"task": "t1"},
        action={"capability": "tool.run"},
        outcome="failure 1",
        counterfactual="fix 1",
        applicable_context={"failure_fingerprint": "fp-unique-1"},
        stored_at=now,
    ).with_embedding(_MOCK_PROV.embed("fix 1"))
    adapter.store_experience(r1)

    r2 = ExperienceRecord(
        experience_id="exp-c2",
        space_id=space_id,
        situation={"task": "t2"},
        action={"capability": "tool.run"},
        outcome="failure 2",
        counterfactual="fix 2",
        applicable_context={"failure_fingerprint": "fp-unique-2"},
        stored_at=now,
    ).with_embedding(_MOCK_PROV.embed("fix 2"))
    adapter.store_experience(r2)

    r3 = ExperienceRecord(
        experience_id="exp-p3",
        space_id=space_id,
        situation={"task": "t3"},
        action={"capability": "tool.run"},
        outcome="failure 3",
        counterfactual="fix 3",
        applicable_context={},
        stored_at=now,
        embedding_status="pending",
    )
    adapter.store_experience(r3)

    r4 = ExperienceRecord(
        experience_id="exp-p4",
        space_id=space_id,
        situation={"task": "t4"},
        action={"capability": "tool.run"},
        outcome="failure 4",
        counterfactual="fix 4",
        applicable_context={},
        stored_at=now,
        embedding_status="pending",
    )
    adapter.store_experience(r4)

    # Prune with max_experiences=2
    policy = RetentionPolicy(max_experiences=2, preserve_fingerprints=True)
    compaction_res = adapter.prune_experiences(space_id, policy)

    assert compaction_res.pruned_count == 2
    assert adapter.count_experiences(space_id) == 2

    # Tier 0 (un-embedded failures without fingerprints) should be evicted first!
    assert "exp-p3" in compaction_res.pruned_experience_ids
    assert "exp-p4" in compaction_res.pruned_experience_ids
    # Protected records with fingerprints and embeddings survive
    surviving = [r.experience_id for r in adapter.list_experiences(space_id)]
    assert "exp-c1" in surviving
    assert "exp-c2" in surviving


def test_replay_trace_isolation_from_live_memory() -> None:
    """ADR-0050 §8: Replay mode strictly bypasses live mutable memory queries."""
    space_id = "space-replay-iso"
    bus = PulseBus()
    kernel = SpaceKernel(space_id=space_id, owner_id="owner-replay", bus=bus)

    mem_store = InMemoryMemoryAdapter()
    # Populate live memory with relevant records
    rec = ExperienceRecord(
        experience_id="exp-live",
        space_id=space_id,
        situation={"task": "t"},
        action={"capability": "c"},
        outcome="failure",
        counterfactual="advice",
        applicable_context={"error_class": "test.err"},
        stored_at=datetime.now(timezone.utc),
    ).with_embedding(_MOCK_PROV.embed("advice"))
    mem_store.store_experience(rec)

    adaptation_layer = AdaptationLayer(memory_store=mem_store, embedding_provider=_MOCK_PROV)

    # ConvergenceEngine with replay_mode=True
    engine = ConvergenceEngine(
        space_id=space_id,
        adaptation_layer=adaptation_layer,
        replay_mode=True,
    )

    proposal = engine.evaluate_and_propose(
        kernel=kernel,
        goal_spec=None,
        evidence=[],
        failed_task_id="t",
        error_class="test.err",
    )

    # Replay trace guarantee: adaptation hints are bypassed
    assert len(proposal.adaptation_hints) == 0

    adaptation_layer.close()


def test_convergence_replan_budget_exhaustion() -> None:
    """ADR-0050 & Law 5: Replans are strictly capped at MAX_REPLAN_BUDGET=3 before human escalation."""
    space_id = "space-replan-budget"
    bus = PulseBus()
    kernel = SpaceKernel(space_id=space_id, owner_id="owner-budget", bus=bus)

    engine = ConvergenceEngine(space_id=space_id)

    # Replan attempt 1 -> REPLAN
    p1 = engine.evaluate_and_propose(kernel, None, [], failed_task_id="t1", error_class="err1")
    assert p1.decision == ConvergenceDecision.REPLAN
    assert p1.replan_attempt == 1

    # Replan attempt 2 -> REPLAN
    p2 = engine.evaluate_and_propose(kernel, None, [], failed_task_id="t1", error_class="err2")
    assert p2.decision == ConvergenceDecision.REPLAN
    assert p2.replan_attempt == 2

    # Replan attempt 3 -> REPLAN
    p3 = engine.evaluate_and_propose(kernel, None, [], failed_task_id="t1", error_class="err3")
    assert p3.decision == ConvergenceDecision.REPLAN
    assert p3.replan_attempt == 3

    # Replan attempt 4 -> ESCALATE (Budget exhausted)
    p4 = engine.evaluate_and_propose(kernel, None, [], failed_task_id="t1", error_class="err4")
    assert p4.decision == ConvergenceDecision.ESCALATE
    assert "budget" in (p4.escalation_reason or "").lower()


def test_strategy_oscillation_cycle_escalation() -> None:
    """CONV-OSC-001 & F05-AUDIT-06: Strategy oscillation (A -> B -> A -> B) escalates to human."""
    space_id = "space-osc-e2e"
    engine = ConvergenceEngine(space_id=space_id)

    replan_key = "task-network"

    # Strategy sequence: net.http -> net.socket -> net.http -> net.socket
    engine.record_strategy(replan_key, "net.http")
    engine.record_strategy(replan_key, "net.socket")
    assert engine.detect_strategy_oscillation(replan_key)[0] is False

    engine.record_strategy(replan_key, "net.http")
    is_osc, reason = engine.detect_strategy_oscillation(replan_key)
    assert is_osc is True
    assert "Direct strategy reversal" in reason

    engine.record_strategy(replan_key, "net.socket")
    is_osc2, reason2 = engine.detect_strategy_oscillation(replan_key)
    assert is_osc2 is True
    assert "strategy reversal" in reason2.lower() or "oscillation" in reason2.lower()
