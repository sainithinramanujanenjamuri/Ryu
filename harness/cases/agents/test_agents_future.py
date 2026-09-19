"""Harness cases: Agent state machine, LLM boundary, and context management.

Activated for Phase 5 verification.

spec §7 (Cognitive Layer), §12 (Context Manager contract),
CONTRACT_MATRIX AGENT-001, AGENT-002, AGENT-006 — Phase 5
"""

from __future__ import annotations

import ast
from pathlib import Path

from agents.base import AgentState, BaseAgent
from agents.context import ContextEntry, ContextManager, ContextScope
from llm.provider import MockLLMProvider


def test_agent_state_machine() -> None:
    """AGENT-001: Agent behavior is represented as deterministic states
    with LLM transition function.
    """
    provider = MockLLMProvider()
    agent = BaseAgent(
        agent_id="agent-harness-1",
        space_id="space-harness-1",
        provider=provider,
    )

    assert agent.state == AgentState.IDLE

    proposal = agent.step(
        task_id="task-sm-1",
        instruction="Formulate hypothesis on dataset",
    )

    assert proposal.is_valid is True
    assert agent.state == AgentState.COMPLETED
    states = [s[0] for s in agent.state_history]
    assert states == [
        AgentState.IDLE,
        AgentState.THINKING,
        AgentState.PROPOSING,
        AgentState.WAITING,
        AgentState.EXECUTING,
        AgentState.OBSERVING,
        AgentState.REFLECTING,
        AgentState.COMPLETED,
    ]


def test_llm_boundary_not_in_core() -> None:
    """AGENT-002: Deterministic core has zero imports of agents, llm, or provider SDKs."""
    repo_root = Path(__file__).resolve().parents[3]
    core_dir = repo_root / "core"
    forbidden = (
        "agents",
        "workers",
        "skills",
        "workflows",
        "llm",
        "openai",
        "anthropic",
        "ollama",
        "transformers",
    )

    violations: list[str] = []
    for py_file in core_dir.rglob("*.py"):
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root_pkg = alias.name.split(".")[0]
                    if root_pkg in forbidden:
                        violations.append(f"{py_file.name} imports {root_pkg}")
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    root_pkg = node.module.split(".")[0]
                    if root_pkg in forbidden:
                        violations.append(f"{py_file.name} from-imports {root_pkg}")

    assert not violations, f"Core dependency boundary violated: {violations}"


def test_context_compaction_500_turns() -> None:
    """AGENT-006: Pinned information survives 500-turn compaction."""
    cm = ContextManager(space_id="space-harness-ctx", max_unpinned_turns=20)
    agent_id = "agent-harness-ctx"
    task_id = "task-harness-500"

    # Pinned goal
    cm.append_entry(
        scope=ContextScope.TASK,
        entry=ContextEntry(
            turn_id="turn-pinned-goal",
            scope=ContextScope.TASK,
            content="Critical Pinned Goal: Verify Ryu Phase 5 Invariants",
            pinned=True,
            metadata={"goal": "Verify Ryu Phase 5 Invariants", "plan_version": 5},
        ),
        agent_id=agent_id,
        task_id=task_id,
        caller_scope=ContextScope.TASK,
    )

    # 500 sequential turns
    for i in range(1, 501):
        cm.append_entry(
            scope=ContextScope.TASK,
            entry=ContextEntry(
                turn_id=f"turn-{i}",
                scope=ContextScope.TASK,
                content=f"Observation turn {i}: intermediate data {i * 5}",
                pinned=False,
                metadata={"decision": f"Decision {i}"},
            ),
            agent_id=agent_id,
            task_id=task_id,
            caller_scope=ContextScope.TASK,
        )

    # Context bounded and pinned goal survives
    assert cm.compaction_count >= 20
    assert cm.evicted_entries_count >= 400

    effective = cm.get_effective_context(agent_id=agent_id, task_id=task_id)
    assert len(effective) < 30
    pinned_texts = [e.content for e in effective if e.pinned]
    assert any("Verify Ryu Phase 5 Invariants" in t for t in pinned_texts)

    # Latest HandoffNote verified
    note = cm.handoff_notes[-1]
    assert note.goal == "Verify Ryu Phase 5 Invariants"
    assert note.current_task_id == task_id
    assert note.plan_version == 5
    assert len(note.last_3_decisions) == 3
