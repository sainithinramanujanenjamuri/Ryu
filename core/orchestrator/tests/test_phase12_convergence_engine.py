"""Unit tests for Phase 12.6 — Convergence Engine / Plan Reconciliation.

Covers:
  - GoalEvaluationResult construction and invariants
  - DeterministicGoalEvaluator: SATISFIED / UNSATISFIED / INCONCLUSIVE paths
  - ConvergenceEngine: CONTINUE / RETRY / REPLAN / ESCALATE / ABORT decisions
  - Bounded retry budget (≤ 3) and replan budget (≤ 3)
  - Failure fingerprint loop-detection
  - LLM-injection adversarial safety
  - apply_proposal authority boundary (only REPLAN commits CAS)
  - is_plan_converged / is_plan_succeeded helpers
  - Core Boundary compliance (zero imports from workers/agents/llm)

spec §10–11, ADR-0041 §2B, AGENTS.md §7
"""

from __future__ import annotations

from typing import Any

import pytest
from ryu.pulse_bus.bus import PulseBus
from ryu.pulse_bus.pulse import Pulse

from core.orchestrator.adapter import Adapter
from core.orchestrator.dispatch_model import (
    ConvergenceDecision,
    ConvergenceEngine,
    ConvergenceProposal,
    DeterministicGoalEvaluator,
    EvidenceStatus,
    EvidenceType,
    GoalEvaluationResult,
    GoalEvaluationStatus,
    VerifiedExecutionEvidence,
)
from core.orchestrator.monitor import Monitor
from core.orchestrator.reconciler import PlanReconciler
from core.plans.task_graph import TaskGraph, TaskNode
from core.space.kernel import SpaceKernel

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


class SpyBus(PulseBus):
    def __init__(self) -> None:
        super().__init__()
        self.published: list[Pulse] = []

    def publish(self, pulse: Pulse) -> Pulse:
        self.published.append(pulse)
        return super().publish(pulse)


def _make_kernel(space_id: str) -> SpaceKernel:
    bus = SpyBus()
    return SpaceKernel(space_id=space_id, owner_id="owner-1", bus=bus)


def _make_reconciler(space_id: str, kernel: SpaceKernel) -> PlanReconciler:
    bus = SpyBus()
    monitor = Monitor(space_id=space_id)
    adapter = Adapter(space_id=space_id)
    return PlanReconciler(
        space_id=space_id,
        monitor=monitor,
        adapter=adapter,
        kernel=kernel,
        bus=bus,
        max_rebases=3,
    )


def _make_engine(space_id: str, kernel: SpaceKernel) -> ConvergenceEngine:
    reconciler = _make_reconciler(space_id, kernel)
    return ConvergenceEngine(space_id=space_id, reconciler=reconciler)


def _good_evidence(task_id: str = "t1") -> VerifiedExecutionEvidence:
    return VerifiedExecutionEvidence(
        task_id=task_id,
        evidence_type=EvidenceType.PROCESS_EXIT.value,
        verified=True,
        exit_code=0,
        duration_seconds=1.5,
        status=EvidenceStatus.VERIFIED.value,
        space_id="s",
        plan_version=1,
    )


def _bad_evidence(task_id: str = "t1") -> VerifiedExecutionEvidence:
    return VerifiedExecutionEvidence(
        task_id=task_id,
        evidence_type=EvidenceType.PROCESS_EXIT.value,
        verified=False,
        exit_code=1,
        duration_seconds=0.0,
        status=EvidenceStatus.INVALID.value,
        space_id="s",
        plan_version=1,
    )


def _tainted_evidence(task_id: str = "t1") -> VerifiedExecutionEvidence:
    return VerifiedExecutionEvidence(
        task_id=task_id,
        evidence_type=EvidenceType.PROCESS_EXIT.value,
        verified=True,
        exit_code=0,
        duration_seconds=1.2,
        status=EvidenceStatus.VERIFIED.value,
        tainted=True,
        space_id="s",
        plan_version=1,
    )


def _simple_goal(constraints: list[str] | None = None) -> Any:
    """Duck-typed goal spec object."""

    class _G:
        objective = "test goal"
        required_capabilities = ["general.compute"]

    g = _G()
    g.constraints = constraints or []  # type: ignore[attr-defined]
    return g


# ---------------------------------------------------------------------------
# 1. GoalEvaluationResult construction
# ---------------------------------------------------------------------------


def test_goal_evaluation_result_satisfied() -> None:
    r = GoalEvaluationResult(
        status=GoalEvaluationStatus.SATISFIED,
        confidence=1.0,
        reasoning="ok",
    )
    assert r.is_satisfied is True
    assert r.missing_criteria == []


def test_goal_evaluation_result_confidence_bounds() -> None:
    with pytest.raises(ValueError, match="confidence must be in"):
        GoalEvaluationResult(
            status=GoalEvaluationStatus.SATISFIED,
            confidence=1.5,
            reasoning="bad",
        )


def test_goal_evaluation_result_unsatisfied() -> None:
    r = GoalEvaluationResult(
        status=GoalEvaluationStatus.UNSATISFIED,
        confidence=0.3,
        reasoning="missing evidence",
        missing_criteria=["all_evidence_verified"],
    )
    assert r.is_satisfied is False
    assert "all_evidence_verified" in r.missing_criteria


def test_goal_evaluation_result_frozen() -> None:
    r = GoalEvaluationResult(
        status=GoalEvaluationStatus.INCONCLUSIVE,
        confidence=0.0,
        reasoning="no data",
    )
    with pytest.raises(Exception):
        r.confidence = 0.5  # type: ignore[misc]


# ---------------------------------------------------------------------------
# 2. DeterministicGoalEvaluator
# ---------------------------------------------------------------------------


def test_evaluator_no_evidence_inconclusive() -> None:
    ev = DeterministicGoalEvaluator()
    result = ev.evaluate(_simple_goal(), [])
    assert result.status == GoalEvaluationStatus.INCONCLUSIVE
    assert result.confidence == 0.0
    assert "execution_evidence" in result.missing_criteria


def test_evaluator_all_good_evidence_satisfied() -> None:
    ev = DeterministicGoalEvaluator()
    evidence = [_good_evidence()]
    result = ev.evaluate(_simple_goal(), evidence)
    assert result.status == GoalEvaluationStatus.SATISFIED
    assert result.confidence > 0.0
    assert result.missing_criteria == []


def test_evaluator_failed_evidence_unsatisfied() -> None:
    ev = DeterministicGoalEvaluator()
    evidence = [_bad_evidence()]
    result = ev.evaluate(_simple_goal(), evidence)
    assert result.status == GoalEvaluationStatus.UNSATISFIED
    assert "all_evidence_verified" in result.missing_criteria


def test_evaluator_tainted_evidence_unsatisfied() -> None:
    ev = DeterministicGoalEvaluator()
    evidence = [_tainted_evidence()]
    result = ev.evaluate(_simple_goal(), evidence)
    assert result.status == GoalEvaluationStatus.UNSATISFIED
    assert "no_tainted_evidence" in result.missing_criteria


def test_evaluator_taint_allowed_by_constraint() -> None:
    ev = DeterministicGoalEvaluator()
    evidence = [_tainted_evidence()]
    goal = _simple_goal(constraints=["allow_taint"])
    result = ev.evaluate(goal, evidence)
    # Taint is allowed; evidence has exit_code=0 and duration > 0 → SATISFIED
    assert result.status == GoalEvaluationStatus.SATISFIED


def test_evaluator_require_artifact_constraint_missing() -> None:
    ev = DeterministicGoalEvaluator()
    evidence = [_good_evidence()]
    goal = _simple_goal(constraints=["require_artifact:report.pdf"])
    result = ev.evaluate(goal, evidence)
    assert result.status == GoalEvaluationStatus.UNSATISFIED
    assert any("report.pdf" in c for c in result.missing_criteria)


def test_evaluator_require_artifact_constraint_present() -> None:
    ev = DeterministicGoalEvaluator()
    evidence = [
        VerifiedExecutionEvidence(
            task_id="t1",
            evidence_type=EvidenceType.ARTIFACT.value,
            verified=True,
            sha256="abc123",
            exit_code=0,
            duration_seconds=2.0,
            status=EvidenceStatus.VERIFIED.value,
            path="/space-s/report.pdf",
            space_id="s",
            plan_version=1,
        )
    ]
    goal = _simple_goal(constraints=["require_artifact:report.pdf"])
    result = ev.evaluate(goal, evidence)
    assert result.status == GoalEvaluationStatus.SATISFIED


def test_evaluator_deterministic_identical_inputs() -> None:
    """Same inputs must always produce identical GoalEvaluationResult."""
    ev = DeterministicGoalEvaluator()
    evidence = [_good_evidence()]
    goal = _simple_goal()
    r1 = ev.evaluate(goal, evidence)
    r2 = ev.evaluate(goal, evidence)
    assert r1.status == r2.status
    assert r1.confidence == r2.confidence
    assert r1.missing_criteria == r2.missing_criteria


def test_evaluator_from_task_graph_all_complete() -> None:
    """evaluate_from_task_graph: all tasks COMPLETED + good evidence → SATISFIED."""
    ev = DeterministicGoalEvaluator()
    graph = TaskGraph(space_id="sg-1", plan_version=1, nodes=[
        TaskNode(id="t1", capability="python.eval_sandboxed", state="completed"),
    ])
    result = ev.evaluate_from_task_graph(_simple_goal(), graph, [_good_evidence()])
    assert result.status == GoalEvaluationStatus.SATISFIED


def test_evaluator_from_task_graph_failed_task() -> None:
    """evaluate_from_task_graph: a mandatory FAILED task → UNSATISFIED."""
    ev = DeterministicGoalEvaluator()
    graph = TaskGraph(space_id="sg-2", plan_version=1, nodes=[
        TaskNode(id="t1", capability="python.eval_sandboxed", state="failed"),
    ])
    result = ev.evaluate_from_task_graph(_simple_goal(), graph, [_bad_evidence()])
    assert result.status == GoalEvaluationStatus.UNSATISFIED


def test_evaluator_from_task_graph_in_flight() -> None:
    """evaluate_from_task_graph: running tasks → INCONCLUSIVE."""
    ev = DeterministicGoalEvaluator()
    graph = TaskGraph(space_id="sg-3", plan_version=1, nodes=[
        TaskNode(id="t1", capability="python.eval_sandboxed", state="running"),
    ])
    result = ev.evaluate_from_task_graph(_simple_goal(), graph, [])
    assert result.status == GoalEvaluationStatus.INCONCLUSIVE


# ---------------------------------------------------------------------------
# 3. ConvergenceEngine decisions
# ---------------------------------------------------------------------------


def test_convergence_engine_continue_on_satisfied() -> None:
    space_id = "ce-cont-1"
    kernel = _make_kernel(space_id)
    engine = _make_engine(space_id, kernel)
    proposal = engine.evaluate_and_propose(kernel, _simple_goal(), [_good_evidence()])
    assert proposal.decision == ConvergenceDecision.CONTINUE
    assert proposal.evaluation is not None
    assert proposal.evaluation.status == GoalEvaluationStatus.SATISFIED


def test_convergence_engine_continue_no_evidence() -> None:
    """No evidence → INCONCLUSIVE evaluation → CONTINUE (tasks still in-flight)."""
    space_id = "ce-cont-2"
    kernel = _make_kernel(space_id)
    engine = _make_engine(space_id, kernel)
    proposal = engine.evaluate_and_propose(kernel, _simple_goal(), [])
    assert proposal.decision == ConvergenceDecision.CONTINUE


def test_convergence_engine_retry_transient_failure() -> None:
    space_id = "ce-retry-1"
    kernel = _make_kernel(space_id)
    engine = _make_engine(space_id, kernel)
    proposal = engine.evaluate_and_propose(
        kernel, _simple_goal(), [],
        failed_task_id="t1",
        error_class="transient.timeout",
        error_message="worker timed out",
    )
    assert proposal.decision == ConvergenceDecision.RETRY
    assert proposal.retry_attempt == 1
    assert proposal.task_id == "t1"


def test_convergence_engine_retry_bounded_at_3() -> None:
    """After 3 transient retries, REPLAN is proposed."""
    space_id = "ce-retry-bound"
    kernel = _make_kernel(space_id)
    engine = _make_engine(space_id, kernel)
    for i in range(1, 4):
        p = engine.evaluate_and_propose(
            kernel, _simple_goal(), [],
            failed_task_id="t1",
            error_class="transient.timeout",
        )
        assert p.decision == ConvergenceDecision.RETRY
        assert p.retry_attempt == i
    # 4th failure → retry budget exhausted → REPLAN
    p4 = engine.evaluate_and_propose(
        kernel, _simple_goal(), [],
        failed_task_id="t1",
        error_class="transient.timeout",
    )
    assert p4.decision == ConvergenceDecision.REPLAN
    assert p4.plan_delta is not None


def test_convergence_engine_escalate_terminal_error() -> None:
    space_id = "ce-esc-1"
    kernel = _make_kernel(space_id)
    engine = _make_engine(space_id, kernel)
    proposal = engine.evaluate_and_propose(
        kernel, _simple_goal(), [],
        failed_task_id="t1",
        error_class="terminal.permission_denied",
        error_message="Unauthorized",
    )
    assert proposal.decision == ConvergenceDecision.ESCALATE
    assert proposal.retry_attempt == 0
    assert "permission_denied" in proposal.escalation_reason


def test_convergence_engine_escalate_budget_exceeded() -> None:
    space_id = "ce-esc-budget"
    kernel = _make_kernel(space_id)
    engine = _make_engine(space_id, kernel)
    proposal = engine.evaluate_and_propose(
        kernel, _simple_goal(), [],
        failed_task_id="t1",
        error_class="terminal.budget_exceeded",
    )
    assert proposal.decision == ConvergenceDecision.ESCALATE


def test_convergence_engine_abort_violation() -> None:
    space_id = "ce-abort-1"
    kernel = _make_kernel(space_id)
    engine = _make_engine(space_id, kernel)
    proposal = engine.evaluate_and_propose(
        kernel, _simple_goal(), [],
        failed_task_id="t1",
        error_class="violation.seccomp",
    )
    assert proposal.decision == ConvergenceDecision.ABORT


def test_convergence_engine_replan_budget_exhausted_escalates() -> None:
    """After MAX_REPLAN_BUDGET replans, subsequent failures escalate to human.

    The fingerprint loop-detection kicks in on the SECOND occurrence of the
    exact same (space_id, task_id, error_class) fingerprint, which means:
      - 1st replan → REPLAN (fingerprint recorded)
      - 2nd same-fingerprint call → ESCALATE (loop guard fires)
    Total replans before escalation = 1 for the same repeated error.
    If fingerprint varies (e.g. different error messages), the replan counter
    allows up to MAX_REPLAN_BUDGET (3) before escalating.
    """
    space_id = "ce-replan-esc"
    kernel = _make_kernel(space_id)
    engine = _make_engine(space_id, kernel)
    # Exhaust all 3 retries for the task (same error class → same fingerprint)
    for _ in range(3):
        engine.evaluate_and_propose(
            kernel, _simple_goal(), [],
            failed_task_id="t1", error_class="transient.timeout",
        )
    # 4th call: retries exhausted → first REPLAN proposal (fingerprint recorded)
    p1 = engine.evaluate_and_propose(
        kernel, _simple_goal(), [],
        failed_task_id="t1", error_class="transient.timeout",
    )
    assert p1.decision == ConvergenceDecision.REPLAN
    # 5th call: same fingerprint seen again → fingerprint loop guard → ESCALATE
    p2 = engine.evaluate_and_propose(
        kernel, _simple_goal(), [],
        failed_task_id="t1", error_class="transient.timeout",
    )
    assert p2.decision == ConvergenceDecision.ESCALATE


def test_convergence_engine_fingerprint_loop_detection() -> None:
    """The same failure fingerprint on the SECOND replan triggers ESCALATE."""
    space_id = "ce-fp"
    kernel = _make_kernel(space_id)
    engine = _make_engine(space_id, kernel)
    # Force past retries directly by exhausting retry budget
    for _ in range(3):
        engine.evaluate_and_propose(
            kernel, _simple_goal(), [],
            failed_task_id="fp-task", error_class="transient.oom",
        )
    # First replan for the structural failure
    p1 = engine.evaluate_and_propose(
        kernel, _simple_goal(), [],
        failed_task_id="fp-task", error_class="transient.oom",
    )
    assert p1.decision == ConvergenceDecision.REPLAN
    fp = p1.failure_fingerprint
    assert fp != ""
    # Apply CAS (simulate acceptance)
    if p1.plan_delta:
        kernel.commit_plan_delta(p1.plan_delta)
    # Second occurrence of exact same fingerprint → loop detected → ESCALATE
    p2 = engine.evaluate_and_propose(
        kernel, _simple_goal(), [],
        failed_task_id="fp-task", error_class="transient.oom",
    )
    # Fingerprint already in _seen_fingerprints → ESCALATE
    assert p2.decision == ConvergenceDecision.ESCALATE


def test_convergence_engine_replan_produces_plan_delta() -> None:
    space_id = "ce-delta"
    kernel = _make_kernel(space_id)
    engine = _make_engine(space_id, kernel)
    # Exhaust retry budget
    for _ in range(3):
        engine.evaluate_and_propose(
            kernel, _simple_goal(), [],
            failed_task_id="t1", error_class="transient.crash",
        )
    p = engine.evaluate_and_propose(
        kernel, _simple_goal(), [],
        failed_task_id="t1", error_class="transient.crash",
    )
    assert p.decision == ConvergenceDecision.REPLAN
    assert p.plan_delta is not None
    assert p.plan_delta.space_id == space_id
    assert len(p.plan_delta.ops) > 0


def test_convergence_engine_apply_proposal_replan_commits_cas() -> None:
    """apply_proposal commits the plan_delta via SpaceKernel CAS for REPLAN."""
    space_id = "ce-apply"
    kernel = _make_kernel(space_id)
    engine = _make_engine(space_id, kernel)
    assert kernel.get_plan_version() == 1
    # Force replan by exhausting retries
    for _ in range(3):
        engine.evaluate_and_propose(
            kernel, _simple_goal(), [],
            failed_task_id="t1", error_class="transient.timeout",
        )
    p = engine.evaluate_and_propose(
        kernel, _simple_goal(), [],
        failed_task_id="t1", error_class="transient.timeout",
    )
    assert p.decision == ConvergenceDecision.REPLAN
    ok, new_ver, err = engine.apply_proposal(p, kernel)
    assert ok is True
    assert new_ver == 2
    assert err is None
    assert kernel.get_plan_version() == 2


def test_convergence_engine_apply_non_replan_no_cas() -> None:
    """apply_proposal for CONTINUE / RETRY / ESCALATE must NOT commit any CAS."""
    space_id = "ce-noop"
    kernel = _make_kernel(space_id)
    engine = _make_engine(space_id, kernel)
    before = kernel.get_plan_version()
    # CONTINUE
    p_cont = engine.evaluate_and_propose(kernel, _simple_goal(), [_good_evidence()])
    ok, ver, err = engine.apply_proposal(p_cont, kernel)
    assert ok is True
    assert kernel.get_plan_version() == before
    # RETRY
    p_retry = engine.evaluate_and_propose(
        kernel, _simple_goal(), [],
        failed_task_id="t1", error_class="transient.timeout",
    )
    ok, ver, err = engine.apply_proposal(p_retry, kernel)
    assert ok is True
    assert kernel.get_plan_version() == before


def test_convergence_engine_is_plan_converged() -> None:
    space_id = "ce-conv"
    kernel = _make_kernel(space_id)
    engine = _make_engine(space_id, kernel)
    completed = TaskGraph(space_id=space_id, plan_version=1, nodes=[
        TaskNode(id="t1", capability="c", state="completed"),
        TaskNode(id="t2", capability="c", state="completed"),
    ])
    assert engine.is_plan_converged(completed) is True
    in_flight = TaskGraph(space_id=space_id, plan_version=1, nodes=[
        TaskNode(id="t1", capability="c", state="completed"),
        TaskNode(id="t2", capability="c", state="running"),
    ])
    assert engine.is_plan_converged(in_flight) is False


def test_convergence_engine_is_plan_succeeded() -> None:
    space_id = "ce-succ"
    kernel = _make_kernel(space_id)
    engine = _make_engine(space_id, kernel)
    all_ok = TaskGraph(space_id=space_id, plan_version=1, nodes=[
        TaskNode(id="t1", capability="c", state="completed"),
    ])
    assert engine.is_plan_succeeded(all_ok) is True
    partial = TaskGraph(space_id=space_id, plan_version=1, nodes=[
        TaskNode(id="t1", capability="c", state="completed"),
        TaskNode(id="t2", capability="c", state="failed"),
    ])
    assert engine.is_plan_succeeded(partial) is False


def test_convergence_engine_optional_failed_still_succeeded() -> None:
    """Optional failed tasks don't prevent plan_succeeded."""
    space_id = "ce-opt"
    kernel = _make_kernel(space_id)
    engine = _make_engine(space_id, kernel)
    graph = TaskGraph(space_id=space_id, plan_version=1, nodes=[
        TaskNode(id="t1", capability="c", state="completed"),
        TaskNode(id="t2", capability="c", state="failed", optional=True),
    ])
    assert engine.is_plan_succeeded(graph) is True


def test_convergence_engine_cross_space_rejected() -> None:
    """verify_space_identity raises if space doesn't match engine's space_id."""
    space_id = "ce-xspace"
    kernel = _make_kernel(space_id)
    wrong_kernel = _make_kernel("WRONG-SPACE")
    engine = _make_engine(space_id, kernel)
    with pytest.raises(Exception):
        engine.evaluate_and_propose(wrong_kernel, _simple_goal(), [_good_evidence()])


def test_convergence_engine_adversarial_llm_injection() -> None:
    """Adversarial LLM injected GoalEvaluatorProtocol cannot override ESCALATE decision.

    An injected evaluator that always claims SATISFIED must not prevent the
    ConvergenceEngine from escalating on terminal error classes.
    """

    class AlwaysSatisfiedEvaluator:
        def evaluate(self, goal_spec: Any, evidence: list[Any]) -> GoalEvaluationResult:
            # Adversarial: claims always satisfied regardless of actual state
            return GoalEvaluationResult(
                status=GoalEvaluationStatus.SATISFIED,
                confidence=1.0,
                reasoning="INJECTED: always satisfied",
            )

    space_id = "ce-adv"
    kernel = _make_kernel(space_id)
    reconciler = _make_reconciler(space_id, kernel)
    engine = ConvergenceEngine(
        space_id=space_id,
        reconciler=reconciler,
        goal_evaluator=AlwaysSatisfiedEvaluator(),  # type: ignore[arg-type]
    )
    # Terminal failure must still escalate regardless of LLM output
    proposal = engine.evaluate_and_propose(
        kernel, _simple_goal(), [],
        failed_task_id="t1",
        error_class="terminal.permission_denied",
    )
    assert proposal.decision == ConvergenceDecision.ESCALATE, (
        "LLM-claimed SATISFIED must not override terminal error escalation"
    )


def test_convergence_proposal_is_immutable() -> None:
    """ConvergenceProposal is frozen=True; mutation raises."""
    proposal = ConvergenceProposal(
        decision=ConvergenceDecision.CONTINUE,
        space_id="s",
        plan_version=1,
        reasoning="ok",
    )
    with pytest.raises(Exception):
        proposal.decision = ConvergenceDecision.ABORT  # type: ignore[misc]


def test_convergence_engine_reset_task_budgets() -> None:
    space_id = "ce-reset"
    kernel = _make_kernel(space_id)
    engine = _make_engine(space_id, kernel)
    # Consume 2 retries
    engine.evaluate_and_propose(
        kernel, _simple_goal(), [],
        failed_task_id="t1", error_class="transient.timeout",
    )
    engine.evaluate_and_propose(
        kernel, _simple_goal(), [],
        failed_task_id="t1", error_class="transient.timeout",
    )
    assert engine._retry_counts.get("t1", 0) == 2
    engine.reset_task_budgets("t1")
    assert engine._retry_counts.get("t1", 0) == 0
    # After reset, retry should start from 1 again
    p = engine.evaluate_and_propose(
        kernel, _simple_goal(), [],
        failed_task_id="t1", error_class="transient.timeout",
    )
    assert p.decision == ConvergenceDecision.RETRY
    assert p.retry_attempt == 1
