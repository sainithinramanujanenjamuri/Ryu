"""Unit tests for BaseAgent state machine and ResearcherRole.

spec §7 (Cognitive Layer), CONTRACT_MATRIX AGENT-001 — Phase 5
"""

from __future__ import annotations

from agents.base import AgentState, BaseAgent
from agents.roles.researcher import ResearcherRole
from core.orchestrator.goal_analyzer import GoalSpec
from llm.provider import LLMResponse, MockLLMProvider
from llm.recorder import InMemoryLLMRecorder


def test_agent_deterministic_lifecycle_state_transitions() -> None:
    provider = MockLLMProvider()
    recorder = InMemoryLLMRecorder()
    agent = BaseAgent(
        agent_id="agent-unit-1",
        space_id="space-unit-1",
        provider=provider,
        recorder=recorder,
    )

    assert agent.state == AgentState.IDLE

    # Invariant (Correction 4): BaseAgent has no SecretResolver
    assert not hasattr(agent, "secret_resolver")
    assert not hasattr(agent, "resolve_secret")

    # Step: executes cognitive transition loop
    proposal = agent.step(
        task_id="task-101",
        instruction="Analyze dataset for outliers",
        correlation_id="corr-unit-1",
    )

    assert proposal.is_valid is True
    assert agent.state == AgentState.COMPLETED

    # Verify deterministic state sequence
    states = [s[0] for s in agent.state_history]
    expected_states = [
        AgentState.IDLE,
        AgentState.THINKING,
        AgentState.PROPOSING,
        AgentState.WAITING,
        AgentState.EXECUTING,
        AgentState.OBSERVING,
        AgentState.REFLECTING,
        AgentState.COMPLETED,
    ]
    assert states == expected_states

    # Verify call was recorded
    records = recorder.get_by_correlation("space-unit-1", "corr-unit-1")
    assert len(records) == 1
    assert records[0].call_id.startswith("req-agent-unit-1")


def test_agent_transitions_to_failed_on_invalid_proposal() -> None:
    provider = MockLLMProvider()
    # Enqueue a malicious response attempting authority bypass
    malicious = LLMResponse(
        request_id="req-bad",
        content="Hacking",
        structured_output={
            "intent": "hack",
            "requested_action": "modify_authoritative_plan",  # FORBIDDEN!
            "parameters": {},
        },
    )
    provider.enqueue_response(malicious)

    agent = BaseAgent(
        agent_id="agent-bad",
        space_id="space-unit-1",
        provider=provider,
    )

    proposal = agent.step("task-bad", "Attempt hack")

    assert proposal.is_valid is False
    assert "terminal.permission_denied" in str(proposal.rejection_reason)
    assert agent.state == AgentState.FAILED


def test_researcher_role_goal_fragment_decomposition() -> None:
    provider = MockLLMProvider()
    canned = LLMResponse(
        request_id="req-res-1",
        content="Research plan",
        structured_output={
            "intent": "gather_evidence",
            "reasoning": "Analyze system logs for memory leak",
            "requested_action": "query_logs",
            "parameters": {"time_window": "1h"},
            "required_capabilities": ["fs.read"],
        },
    )
    provider.enqueue_response(canned)

    role = ResearcherRole(agent_id="researcher-1", space_id="space-unit-1", provider=provider)
    spec = GoalSpec(
        goal_id="goal-r1",
        space_id="space-unit-1",
        objective="Investigate leak",
        constraints=[],
        required_capabilities=["fs.read"],
        single_agent_eligible=True,
        command_id="cmd-r1",
    )

    proposal = role.research_goal_fragment(spec, task_id="task-fragment-1")
    assert proposal.is_valid is True
    assert proposal.intent == "gather_evidence"
    assert proposal.requested_action == "query_logs"
    assert proposal.required_capabilities == ["fs.read"]
    assert role.state == AgentState.COMPLETED

