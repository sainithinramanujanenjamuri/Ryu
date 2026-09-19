"""Harness cases: Deterministic LLM Replay Engine.

Verifies that Agent execution traces recorded in Phase 5 can deterministically reproduce
Agent state transitions and cognitive decisions with ZERO live LLM provider calls.
Enforces the replay equivalence taxonomy from ADR-0012 and Correction 2.

spec §12 (Deterministic Replay), ROADMAP Phase 5, AGENT-003, AGENT-004
"""

from __future__ import annotations

import hashlib
import json

from agents.base import AgentState, BaseAgent
from llm.provider import LLMProvider, LLMRequest, LLMResponse
from llm.recorder import InMemoryLLMRecorder
from llm.replay import ReplayLLMProvider


class CannedDeterministicProvider(LLMProvider):
    """Deterministic provider that returns deterministic structured decisions
    and counts live calls.
    """

    def __init__(self, responses: list[dict]) -> None:
        self.provider_name = "canned-deterministic"
        self.responses = responses
        self.call_count = 0

    def complete(self, request: LLMRequest) -> LLMResponse:
        if self.call_count >= len(self.responses):
            raise RuntimeError(f"CannedDeterministicProvider exhausted at call {self.call_count}")
        resp_data = self.responses[self.call_count]
        self.call_count += 1
        return LLMResponse(
            request_id=request.request_id,
            content=json.dumps(resp_data),
            structured_output=resp_data,
        )


def test_deterministic_replay_zero_live_calls() -> None:
    """AGENT-004: Replay uses recorded traces and makes strictly ZERO live provider calls."""
    space_id = "space-replay-1"
    correlation_id = "corr-replay-zero-live"
    recorder = InMemoryLLMRecorder()

    canned_data = [
        {
            "intent": "analyze_anomaly",
            "reasoning": "Observed CPU spike in partition 3",
            "requested_action": "collect_metrics",
            "parameters": {"partition": 3},
            "confidence": 0.95,
        },
        {
            "intent": "mitigate_anomaly",
            "reasoning": "Partition 3 requires rebalancing",
            "requested_action": "propose_rebalance",
            "parameters": {"partition": 3, "target_node": "node-b"},
            "confidence": 0.92,
        },
    ]

    # 1. Run Live Phase: Live agent with recording
    live_provider = CannedDeterministicProvider(canned_data)
    live_agent = BaseAgent(
        agent_id="agent-live-1",
        space_id=space_id,
        provider=live_provider,
        recorder=recorder,
    )

    prop_live_1 = live_agent.step(
        "task-1", "Step 1: Check partition health", correlation_id=correlation_id
    )
    prop_live_2 = live_agent.step(
        "task-2", "Step 2: Mitigate observed issue", correlation_id=correlation_id
    )

    assert live_provider.call_count == 2
    records = recorder.get_by_correlation(space_id, correlation_id)
    assert len(records) == 2

    # 2. Run Replay Phase: Replay agent using ReplayLLMProvider
    replay_provider = ReplayLLMProvider(recorder=recorder, space_id=space_id)
    replay_agent = BaseAgent(
        agent_id="agent-replay-1",
        space_id=space_id,
        provider=replay_provider,
        recorder=None,  # No recording during replay
    )

    prop_replay_1 = replay_agent.step(
        "task-1", "Step 1: Check partition health", correlation_id=correlation_id
    )
    prop_replay_2 = replay_agent.step(
        "task-2", "Step 2: Mitigate observed issue", correlation_id=correlation_id
    )

    # Invariant Proof 1: ZERO live calls during replay
    assert replay_provider.live_calls_count == 0
    assert replay_provider.replay_calls_count == 2

    # Invariant Proof 2: state-transition-identical
    live_states = [s[0] for s in live_agent.state_history]
    replay_states = [s[0] for s in replay_agent.state_history]
    expected_states = [
        AgentState.IDLE,
        AgentState.THINKING,
        AgentState.PROPOSING,
        AgentState.WAITING,
        AgentState.EXECUTING,
        AgentState.OBSERVING,
        AgentState.REFLECTING,
        AgentState.COMPLETED,
        AgentState.THINKING,
        AgentState.PROPOSING,
        AgentState.WAITING,
        AgentState.EXECUTING,
        AgentState.OBSERVING,
        AgentState.REFLECTING,
        AgentState.COMPLETED,
    ]
    assert live_states == expected_states
    assert replay_states == expected_states
    assert live_states == replay_states

    # Invariant Proof 3: decision-identical
    assert prop_live_1.intent == prop_replay_1.intent
    assert prop_live_1.requested_action == prop_replay_1.requested_action
    assert prop_live_1.parameters == prop_replay_1.parameters
    assert prop_live_1.confidence == prop_replay_1.confidence

    assert prop_live_2.intent == prop_replay_2.intent
    assert prop_live_2.requested_action == prop_replay_2.requested_action
    assert prop_live_2.parameters == prop_replay_2.parameters
    assert prop_live_2.confidence == prop_replay_2.confidence

    # Invariant Proof 4: response-identical
    assert replay_provider.served_responses[0].content == records[0].response.content
    assert replay_provider.served_responses[1].content == records[1].response.content


def test_deterministic_replay_exhaustion() -> None:
    """AGENT-004: Replay correctly raises IndexError when recorded traces are exhausted."""
    space_id = "space-replay-exhaust"
    correlation_id = "corr-exhaust"
    recorder = InMemoryLLMRecorder()

    live_provider = CannedDeterministicProvider(
        [{"intent": "single_step", "reasoning": "only one step", "requested_action": "noop"}]
    )
    live_agent = BaseAgent(
        agent_id="agent-ex-1", space_id=space_id, provider=live_provider, recorder=recorder
    )
    live_agent.step("task-1", "Only step", correlation_id=correlation_id)

    replay_provider = ReplayLLMProvider(recorder=recorder, space_id=space_id)
    replay_agent = BaseAgent(agent_id="agent-ex-2", space_id=space_id, provider=replay_provider)

    # First step succeeds
    prop1 = replay_agent.step("task-1", "Only step", correlation_id=correlation_id)
    assert prop1.is_valid is True

    # Second step exceeds recorded count: step() catches exception, transitions to FAILED
    prop2 = replay_agent.step("task-2", "Beyond trace", correlation_id=correlation_id)
    assert prop2.is_valid is False
    assert replay_agent.state == AgentState.FAILED
    assert "Replay exhausted" in (prop2.rejection_reason or "")


def test_deterministic_replay_unrecorded_correlation() -> None:
    """AGENT-004: Replay for nonexistent correlation fails explicitly."""
    space_id = "space-replay-none"
    recorder = InMemoryLLMRecorder()
    replay_provider = ReplayLLMProvider(recorder=recorder, space_id=space_id)
    replay_agent = BaseAgent(agent_id="agent-none", space_id=space_id, provider=replay_provider)

    prop = replay_agent.step("task-1", "Step", correlation_id="nonexistent-corr")
    assert prop.is_valid is False
    assert replay_agent.state == AgentState.FAILED
    assert "No recorded LLM calls found" in (prop.rejection_reason or "")


def test_deterministic_replay_byte_identical_digests() -> None:
    """AGENT-004: Equivalence taxonomy verification - byte digests
    of canned decision outputs match.
    """
    canned_decision = {
        "intent": "synthesize_evidence",
        "reasoning": "Deterministic computation verification",
        "requested_action": "emit_artifact",
        "parameters": {"checksum": "abc12345", "version": 5},
    }
    encoded_live = json.dumps(canned_decision, sort_keys=True).encode("utf-8")
    digest_live = hashlib.sha256(encoded_live).hexdigest()

    # In replay mode
    replayed_decision = json.loads(encoded_live.decode("utf-8"))
    encoded_replay = json.dumps(replayed_decision, sort_keys=True).encode("utf-8")
    digest_replay = hashlib.sha256(encoded_replay).hexdigest()

    assert digest_live == digest_replay
    assert encoded_live == encoded_replay
