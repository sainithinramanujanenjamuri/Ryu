"""Unit and Integration Tests for Phase 15.6.1 — Convergence Correctness & Strategy Oscillation.

Covers:
  - PlanDelta rollback parameter reconciliation (params vs payload vs top-level) (PLAN-ROLLBACK-001)
  - Task state machine reset on rollback (FAILED/TIMED_OUT/BLOCKED/ESCALATED -> READY/PENDING)
  - SpaceKernel CAS authority preservation during rollback
  - Deterministic strategy sequence tracking and bounded history (CONV-OSC-001)
  - Cycle detection:
      * A -> B -> A (direct reversal)
      * A -> B -> A -> B (alternating 2-cycle)
      * A -> A -> A (stagnant repeating loop)
      * A -> B -> C -> A -> B -> C (period-3 cycle)
      * Non-oscillating progressive paths (A -> B -> C -> D)
  - Space isolation of strategy histories
  - Bounded history invariant (N <= 10)
  - Budget reset clearing strategy history
  - Replay determinism and non-mutation

Contracts:
  - PLAN-ROLLBACK-001
  - CONV-OSC-001
  - ADR-0050

AGENTS.md §5, §7
"""

from __future__ import annotations

from typing import Any

from ryu.pulse_bus.bus import PulseBus
from ryu.pulse_bus.pulse import Pulse

from core.orchestrator.dispatch_model import (
    ConvergenceDecision,
    ConvergenceEngine,
)
from core.plans.delta import PlanDelta
from core.space.kernel import SpaceKernel


class SpyBus(PulseBus):
    """Spy pulse bus capturing all published pulses for verification."""

    def __init__(self) -> None:
        super().__init__()
        self.published: list[Pulse] = []

    def publish(self, pulse: Pulse) -> Pulse:
        self.published.append(pulse)
        return super().publish(pulse)


def _make_kernel(space_id: str = "space-test") -> SpaceKernel:
    bus = SpyBus()
    return SpaceKernel(space_id=space_id, owner_id="owner-test", bus=bus)


def _add_task_to_kernel(
    kernel: SpaceKernel,
    task_id: str,
    capability: str = "python.eval",
    state: str = "ready",
    dependencies: list[str] | None = None,
    optional: bool = False,
    params: dict[str, Any] | None = None,
) -> int:
    base_ver = kernel.get_plan_version()
    delta = PlanDelta(
        space_id=kernel.space_id,
        base_version=base_ver,
        resulting_version=base_ver + 1,
        ops=[
            {
                "op": "add",
                "target_node_id": task_id,
                "capability": capability,
                "state": state,
                "dependencies": dependencies or [],
                "optional": optional,
                "params": params or {},
            }
        ],
    )
    ok, new_ver, err = kernel.commit_plan_delta(delta)
    assert ok, f"Failed to add task: {err}"
    return new_ver


# ── Test Suite: PlanDelta Rollback Parameter Reconciliation ─────────────────


def test_rollback_param_reconciliation_with_params_dict() -> None:
    """PLAN-ROLLBACK-001: Rollback op with nested 'params' dict updates target node params."""
    kernel = _make_kernel("space-rollback-1")
    _add_task_to_kernel(kernel, "task-1", capability="tool.a", state="failed")

    graph_before = kernel.get_task_graph()
    node_before = graph_before.get_node("task-1")
    assert node_before is not None
    assert node_before.state == "failed"

    # Commit rollback with "params"
    cur_ver = kernel.get_plan_version()
    rollback_delta = PlanDelta(
        space_id=kernel.space_id,
        base_version=cur_ver,
        resulting_version=cur_ver + 1,
        ops=[
            {
                "op": "rollback",
                "target_node_id": "task-1",
                "params": {
                    "suggested_alternative": "tool.b",
                    "counterfactual_recommendation": "Use tool.b for robustness",
                    "replan_attempt": 1,
                },
            }
        ],
    )
    ok, new_ver, err = kernel.commit_plan_delta(rollback_delta)
    assert ok, f"Rollback failed: {err}"

    graph_after = kernel.get_task_graph()
    node = graph_after.get_node("task-1")
    assert node is not None
    assert node.state == "ready"
    assert node.params.get("suggested_alternative") == "tool.b"
    assert node.params.get("counterfactual_recommendation") == "Use tool.b for robustness"
    assert node.params.get("replan_attempt") == 1
    assert node.error is None


def test_rollback_param_reconciliation_with_payload_dict() -> None:
    """PLAN-ROLLBACK-001: Rollback op with nested 'payload' dict updates target node params."""
    kernel = _make_kernel("space-rollback-2")
    _add_task_to_kernel(kernel, "task-1", capability="tool.a", state="failed")

    cur_ver = kernel.get_plan_version()
    rollback_delta = PlanDelta(
        space_id=kernel.space_id,
        base_version=cur_ver,
        resulting_version=cur_ver + 1,
        ops=[
            {
                "op": "rollback",
                "target_node_id": "task-1",
                "payload": {
                    "reason": "Process timed out",
                    "suggested_alternative": "tool.c",
                    "replan_attempt": 2,
                },
            }
        ],
    )
    ok, new_ver, err = kernel.commit_plan_delta(rollback_delta)
    assert ok, f"Rollback failed: {err}"

    graph_after = kernel.get_task_graph()
    node = graph_after.get_node("task-1")
    assert node is not None
    assert node.state == "ready"
    assert node.params.get("suggested_alternative") == "tool.c"
    assert node.params.get("reason") == "Process timed out"
    assert node.params.get("replan_attempt") == 2
    assert node.error is None


def test_rollback_param_reconciliation_with_top_level_keys() -> None:
    """PLAN-ROLLBACK-001: Rollback op with flattened top-level metadata updates target node params."""
    kernel = _make_kernel("space-rollback-3")
    _add_task_to_kernel(kernel, "task-1", capability="tool.a", state="failed")

    cur_ver = kernel.get_plan_version()
    rollback_delta = PlanDelta(
        space_id=kernel.space_id,
        base_version=cur_ver,
        resulting_version=cur_ver + 1,
        ops=[
            {
                "op": "rollback",
                "target_node_id": "task-1",
                "reason": "Top level reason",
                "suggested_alternative": "tool.d",
                "failure_fingerprint": "fp-12345",
            }
        ],
    )
    ok, new_ver, err = kernel.commit_plan_delta(rollback_delta)
    assert ok, f"Rollback failed: {err}"

    graph_after = kernel.get_task_graph()
    node = graph_after.get_node("task-1")
    assert node is not None
    assert node.state == "ready"
    assert node.params.get("suggested_alternative") == "tool.d"
    assert node.params.get("reason") == "Top level reason"
    assert node.params.get("failure_fingerprint") == "fp-12345"


# ── Test Suite: Failed Task Rollback State Reset ────────────────────────────


def test_rollback_resets_failed_task_to_ready_when_deps_satisfied() -> None:
    """Rollback resets FAILED task with satisfied upstream dependencies to READY."""
    kernel = _make_kernel("space-state-1")
    _add_task_to_kernel(kernel, "dep-1", state="completed")
    _add_task_to_kernel(kernel, "task-2", dependencies=["dep-1"], state="failed")

    # Rollback task-2
    cur_ver = kernel.get_plan_version()
    delta = PlanDelta(
        space_id=kernel.space_id,
        base_version=cur_ver,
        resulting_version=cur_ver + 1,
        ops=[{"op": "rollback", "target_node_id": "task-2", "params": {"reason": "replan"}}],
    )
    ok, _, _ = kernel.commit_plan_delta(delta)
    assert ok

    node = kernel.get_task_graph().get_node("task-2")
    assert node is not None
    assert node.state == "ready"
    assert node.error is None


def test_rollback_resets_failed_task_to_pending_when_deps_unsatisfied() -> None:
    """Rollback resets FAILED task with unfulfilled dependencies to PENDING, avoiding premature dispatch."""
    kernel = _make_kernel("space-state-2")
    _add_task_to_kernel(kernel, "dep-1", state="ready")
    _add_task_to_kernel(kernel, "task-2", dependencies=["dep-1"], state="failed")

    # Now rollback task-2 while dep-1 is still in "ready" (not completed)
    cur_ver = kernel.get_plan_version()
    rollback_delta = PlanDelta(
        space_id=kernel.space_id,
        base_version=cur_ver,
        resulting_version=cur_ver + 1,
        ops=[{"op": "rollback", "target_node_id": "task-2", "params": {"reason": "reset"}}],
    )
    ok, _, _ = kernel.commit_plan_delta(rollback_delta)
    assert ok

    node = kernel.get_task_graph().get_node("task-2")
    assert node is not None
    assert node.state == "pending"  # Must remain pending because dep-1 is not completed
    assert node.error is None


def test_rollback_resets_timed_out_blocked_escalated_states() -> None:
    """Rollback legally resets tasks in TIMED_OUT, BLOCKED, and ESCALATED states to READY."""
    kernel = _make_kernel("space-state-3")
    _add_task_to_kernel(kernel, "t-timeout", state="timed_out")
    _add_task_to_kernel(kernel, "t-blocked", state="blocked")
    _add_task_to_kernel(kernel, "t-escalated", state="escalated")

    cur_ver = kernel.get_plan_version()
    rollback_delta = PlanDelta(
        space_id=kernel.space_id,
        base_version=cur_ver,
        resulting_version=cur_ver + 1,
        ops=[
            {"op": "rollback", "target_node_id": "t-timeout", "params": {"reason": "timeout reset"}},
            {"op": "rollback", "target_node_id": "t-blocked", "params": {"reason": "unblock"}},
            {"op": "rollback", "target_node_id": "t-escalated", "params": {"reason": "de-escalate"}},
        ],
    )
    ok, _, _ = kernel.commit_plan_delta(rollback_delta)
    assert ok

    graph = kernel.get_task_graph()
    n_timeout = graph.get_node("t-timeout")
    n_blocked = graph.get_node("t-blocked")
    n_escalated = graph.get_node("t-escalated")
    assert n_timeout is not None and n_timeout.state == "ready"
    assert n_blocked is not None and n_blocked.state == "ready"
    assert n_escalated is not None and n_escalated.state == "ready"


def test_kernel_cas_authority_preserved_on_rollback() -> None:
    """SpaceKernel CAS rejects stale base_version rollbacks and increments version monotonically."""
    kernel = _make_kernel("space-cas-1")
    _add_task_to_kernel(kernel, "task-1")

    # Attempt rollback with wrong base version
    stale_delta = PlanDelta(
        space_id=kernel.space_id,
        base_version=999,  # Stale
        resulting_version=1000,
        ops=[{"op": "rollback", "target_node_id": "task-1", "params": {}}],
    )
    ok, ver, err = kernel.commit_plan_delta(stale_delta)
    assert not ok
    assert "Plan version conflict" in str(err) or "stale" in str(err).lower() or ver != 1000


# ── Test Suite: Deterministic Strategy Oscillation Detection ─────────────────


def test_oscillation_detection_direct_reversal_aba() -> None:
    """CONV-OSC-001: Detects direct capability reversal A -> B -> A."""
    engine = ConvergenceEngine(space_id="space-osc-1")

    engine.record_strategy("task-1", "capability.python")
    engine.record_strategy("task-1", "capability.shell")
    assert not engine.detect_strategy_oscillation("task-1")[0]

    engine.record_strategy("task-1", "capability.python")
    is_osc, reason = engine.detect_strategy_oscillation("task-1")
    assert is_osc is True
    assert "Direct strategy reversal detected" in reason
    assert "capability.python -> capability.shell -> capability.python" in reason


def test_oscillation_detection_alternating_2cycle_abab() -> None:
    """CONV-OSC-001: Detects alternating 2-cycle A -> B -> A -> B."""
    engine = ConvergenceEngine(space_id="space-osc-2")

    engine.record_strategy("task-1", "strategy.A")
    engine.record_strategy("task-1", "strategy.B")
    engine.record_strategy("task-1", "strategy.A")
    # At step 3, direct reversal is detected
    assert engine.detect_strategy_oscillation("task-1")[0] is True

    # At step 4, alternating 2-cycle is also confirmed
    engine.record_strategy("task-1", "strategy.B")
    is_osc, reason = engine.detect_strategy_oscillation("task-1")
    assert is_osc is True
    assert "2-cycle" in reason or "reversal" in reason


def test_oscillation_detection_stagnant_repeating_loop_aaa() -> None:
    """CONV-OSC-001: Detects stagnant repeated strategy loop A -> A -> A."""
    engine = ConvergenceEngine(space_id="space-osc-3")

    engine.record_strategy("task-1", "strategy.same")
    engine.record_strategy("task-1", "strategy.same")
    assert not engine.detect_strategy_oscillation("task-1")[0]

    engine.record_strategy("task-1", "strategy.same")
    is_osc, reason = engine.detect_strategy_oscillation("task-1")
    assert is_osc is True
    assert "Stagnant strategy loop detected" in reason
    assert "strategy.same" in reason


def test_oscillation_detection_period3_cycle() -> None:
    """CONV-OSC-001: Detects period-3 cycle A -> B -> C -> A -> B -> C."""
    engine = ConvergenceEngine(space_id="space-osc-4")

    sequence = ["cap.1", "cap.2", "cap.3", "cap.1", "cap.2", "cap.3"]
    for cap in sequence:
        engine.record_strategy("task-1", cap)

    is_osc, reason = engine.detect_strategy_oscillation("task-1")
    assert is_osc is True
    assert "Period-3 cycle" in reason or "oscillation" in reason


def test_oscillation_detection_non_oscillating_progressions() -> None:
    """CONV-OSC-001: Progressive non-repeating strategies do not trigger oscillation."""
    engine = ConvergenceEngine(space_id="space-osc-5")

    engine.record_strategy("task-1", "strategy.A")
    assert not engine.detect_strategy_oscillation("task-1")[0]

    engine.record_strategy("task-1", "strategy.B")
    assert not engine.detect_strategy_oscillation("task-1")[0]

    engine.record_strategy("task-1", "strategy.C")
    assert not engine.detect_strategy_oscillation("task-1")[0]

    engine.record_strategy("task-1", "strategy.D")
    assert not engine.detect_strategy_oscillation("task-1")[0]


def test_oscillation_space_isolation() -> None:
    """SCCA Law 1: Strategy history is strictly Space-scoped and never bleeds across Spaces."""
    engine_a = ConvergenceEngine(space_id="space-alpha")
    engine_b = ConvergenceEngine(space_id="space-beta")

    # Space Alpha has oscillating sequence
    engine_a.record_strategy("shared-task-id", "cap.A")
    engine_a.record_strategy("shared-task-id", "cap.B")
    engine_a.record_strategy("shared-task-id", "cap.A")

    # Space Beta has single entry
    engine_b.record_strategy("shared-task-id", "cap.A")

    assert engine_a.detect_strategy_oscillation("shared-task-id")[0] is True
    assert engine_b.detect_strategy_oscillation("shared-task-id")[0] is False
    assert len(engine_b.get_strategy_history("shared-task-id")) == 1


def test_oscillation_history_bounded_to_max_10() -> None:
    """CONV-OSC-001: Strategy history is bounded to MAX_STRATEGY_HISTORY (10 entries)."""
    engine = ConvergenceEngine(space_id="space-bounded")

    for i in range(25):
        engine.record_strategy("task-bound", f"strategy-{i}")

    history = engine.get_strategy_history("task-bound")
    assert len(history) == 10
    # Must contain the latest 10 items (15 through 24)
    expected = [f"strategy-{i}" for i in range(15, 25)]
    assert history == expected


def test_reset_task_budgets_clears_strategy_history() -> None:
    """Calling reset_task_budgets clears strategy history for the task."""
    engine = ConvergenceEngine(space_id="space-reset")
    engine.record_strategy("task-1", "strategy.A")
    engine.record_strategy("task-1", "strategy.B")
    assert len(engine.get_strategy_history("task-1")) == 2

    engine.reset_task_budgets("task-1")
    assert engine.get_strategy_history("task-1") == []


def test_propose_replan_escalates_on_strategy_oscillation() -> None:
    """ConvergenceEngine.evaluate_and_propose returns ESCALATE when oscillation occurs during replan."""
    kernel = _make_kernel("space-e2e-osc")
    _add_task_to_kernel(kernel, "task-1", capability="python.eval")

    engine = ConvergenceEngine(space_id=kernel.space_id)

    class DummyGoal:
        objective = "Compute sum"
        constraints: list[str] = []
        required_capabilities = ["python.eval"]

    goal = DummyGoal()

    # Pre-record history: python.eval -> shell.exec
    engine.record_strategy("task-1", "python.eval")
    engine.record_strategy("task-1", "shell.exec")

    # Now task fails, and replan suggests python.eval again!
    # Mock adaptation hint suggesting alternative "python.eval"
    class MockAdaptationLayer:
        def generate_hints(self, space_id: str, situation_hint: dict, limit: int = 5):
            class Hint:
                suggested_alternative_capability = "python.eval"
                counterfactual_summary = "Switch back to python"
                experience_id = "exp-1"
            return [Hint()]

    engine.adaptation_layer = MockAdaptationLayer()

    proposal = engine.evaluate_and_propose(
        kernel=kernel,
        goal_spec=goal,
        evidence=[],
        failed_task_id="task-1",
        error_class="transient.timeout",
        error_message="Execution timed out",
    )

    # First attempt at transient error is retry
    assert proposal.decision == ConvergenceDecision.RETRY

    # Exhaust retries so it falls through to replan
    for _ in range(2):
        engine.evaluate_and_propose(
            kernel=kernel,
            goal_spec=goal,
            evidence=[],
            failed_task_id="task-1",
            error_class="transient.timeout",
            error_message="Execution timed out",
        )

    # 4th failure: triggers replan, which evaluates strategy sequence:
    # History had [python.eval, shell.exec], now proposing python.eval -> A -> B -> A detected!
    proposal_replan = engine.evaluate_and_propose(
        kernel=kernel,
        goal_spec=goal,
        evidence=[],
        failed_task_id="task-1",
        error_class="transient.timeout",
        error_message="Execution timed out",
    )

    assert proposal_replan.decision == ConvergenceDecision.ESCALATE
    assert "Strategy oscillation" in proposal_replan.escalation_reason


def test_convergence_engine_replay_mode_determinism() -> None:
    """ConvergenceEngine in replay_mode produces identical deterministic output without side-effects."""
    kernel = _make_kernel("space-replay")
    _add_task_to_kernel(kernel, "task-1", capability="python.eval")

    class DummyGoal:
        objective = "Test determinism"
        constraints: list[str] = []
        required_capabilities = ["python.eval"]

    goal = DummyGoal()

    engine1 = ConvergenceEngine(space_id=kernel.space_id, replay_mode=True)
    engine2 = ConvergenceEngine(space_id=kernel.space_id, replay_mode=True)

    prop1 = engine1.evaluate_and_propose(
        kernel=kernel,
        goal_spec=goal,
        evidence=[],
        failed_task_id="task-1",
        error_class="structural.bad_input",
        error_message="Invalid data format",
    )

    prop2 = engine2.evaluate_and_propose(
        kernel=kernel,
        goal_spec=goal,
        evidence=[],
        failed_task_id="task-1",
        error_class="structural.bad_input",
        error_message="Invalid data format",
    )

    assert prop1.decision == prop2.decision == ConvergenceDecision.REPLAN
    assert prop1.reasoning == prop2.reasoning
    assert prop1.replan_attempt == prop2.replan_attempt
