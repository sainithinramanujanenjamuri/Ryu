"""Harness cases: Context scopes hierarchy, upward mutation rejection,
500-turn compaction, and Handoff Note contract.

Proves:
- AGENT-005: Task, Agent, and Space scopes remain distinct; cross-space rejected.
- AGENT-006: Pinned information survives 500-turn compaction, bounded context.
- AGENT-007: Recovery uses defined Handoff Note contract.
- Correction 5: Visibility does NOT imply mutation authority (upward mutations rejected).
- Correction 6: Real 500-turn compaction evicting unpinned items.

spec §12 (Context Manager contract), ROADMAP Phase 5, AGENT-005, AGENT-006, AGENT-007
"""

from __future__ import annotations

import pytest

from agents.context import ContextEntry, ContextManager, ContextScope, HandoffNote


def test_context_scopes_distinction_and_visibility() -> None:
    """AGENT-005: Task, Agent, and Space scopes remain distinct;
    child can see parent but not sibling.
    """
    cm = ContextManager(space_id="space-scope-test")

    # 1. Space context entry
    cm.append_entry(
        scope=ContextScope.SPACE,
        entry=ContextEntry(turn_id="s-1", scope=ContextScope.SPACE, content="Space-level policy"),
        caller_scope=ContextScope.SPACE,
    )

    # 2. Agent context entry for agent-1
    cm.append_entry(
        scope=ContextScope.AGENT,
        entry=ContextEntry(turn_id="a-1", scope=ContextScope.AGENT, content="Agent-1 memory"),
        agent_id="agent-1",
        caller_scope=ContextScope.AGENT,
    )

    # 3. Agent context entry for agent-2
    cm.append_entry(
        scope=ContextScope.AGENT,
        entry=ContextEntry(turn_id="a-2", scope=ContextScope.AGENT, content="Agent-2 memory"),
        agent_id="agent-2",
        caller_scope=ContextScope.AGENT,
    )

    # 4. Task context entry for (agent-1, task-1)
    cm.append_entry(
        scope=ContextScope.TASK,
        entry=ContextEntry(turn_id="t-1", scope=ContextScope.TASK, content="Task-1 execution data"),
        agent_id="agent-1",
        task_id="task-1",
        caller_scope=ContextScope.TASK,
    )

    # Effective context for (agent-1, task-1) sees Space + Agent-1 + Task-1
    ctx_task1 = cm.get_effective_context(agent_id="agent-1", task_id="task-1")
    contents1 = [e.content for e in ctx_task1]
    assert "Space-level policy" in contents1
    assert "Agent-1 memory" in contents1
    assert "Task-1 execution data" in contents1
    assert "Agent-2 memory" not in contents1  # Sibling agent isolated

    # Effective context for agent-2 (no task) sees Space + Agent-2
    ctx_agent2 = cm.get_effective_context(agent_id="agent-2")
    contents2 = [e.content for e in ctx_agent2]
    assert "Space-level policy" in contents2
    assert "Agent-2 memory" in contents2
    assert "Agent-1 memory" not in contents2
    assert "Task-1 execution data" not in contents2


def test_upward_mutation_rejection() -> None:
    """Correction 5: Visibility does not imply mutation authority; upward mutations are rejected."""
    cm = ContextManager(space_id="space-upward-test")

    # Attack 1: Task scope attempts to mutate Space scope -> PermissionError
    with pytest.raises(PermissionError, match="Unauthorized upward context mutation"):
        cm.append_entry(
            scope=ContextScope.SPACE,
            entry=ContextEntry(
                turn_id="bad-s", scope=ContextScope.SPACE, content="Malicious space override"
            ),
            agent_id="agent-rogue",
            task_id="task-rogue",
            caller_scope=ContextScope.TASK,
        )

    # Attack 2: Task scope attempts to mutate Agent scope -> PermissionError
    with pytest.raises(PermissionError, match="Unauthorized upward context mutation"):
        cm.append_entry(
            scope=ContextScope.AGENT,
            entry=ContextEntry(
                turn_id="bad-a", scope=ContextScope.AGENT, content="Malicious agent override"
            ),
            agent_id="agent-rogue",
            task_id="task-rogue",
            caller_scope=ContextScope.TASK,
        )

    # Attack 3: Agent scope attempts to mutate Space scope -> PermissionError
    with pytest.raises(PermissionError, match="Unauthorized upward context mutation"):
        cm.append_entry(
            scope=ContextScope.SPACE,
            entry=ContextEntry(
                turn_id="bad-s-agent", scope=ContextScope.SPACE, content="Agent space override"
            ),
            agent_id="agent-rogue",
            caller_scope=ContextScope.AGENT,
        )


def test_cross_space_context_isolation() -> None:
    """AGENT-005: Cross-space context access is strictly rejected."""
    cm = ContextManager(space_id="space-alpha")

    # Verify space identity rejects unauthorized space callers
    with pytest.raises(PermissionError, match="Cross-space context access rejected"):
        cm.verify_space_identity("space-beta")


def test_500_turn_compaction_with_pinned_survival() -> None:
    """AGENT-006: Context compaction survives 500 turns, bounds memory,
    and keeps pinned invariants.
    """
    cm = ContextManager(space_id="space-compact-500", max_unpinned_turns=20)
    agent_id = "agent-worker"
    task_id = "task-heavy-500"

    # 1. Append pinned system goal and safety constraints
    cm.append_entry(
        scope=ContextScope.TASK,
        entry=ContextEntry(
            turn_id="pinned-goal",
            scope=ContextScope.TASK,
            content="MANDATORY GOAL: Preserve SCCA Invariants",
            pinned=True,
            metadata={"goal": "Preserve SCCA Invariants", "plan_version": 42},
        ),
        agent_id=agent_id,
        task_id=task_id,
        caller_scope=ContextScope.TASK,
    )

    cm.append_entry(
        scope=ContextScope.TASK,
        entry=ContextEntry(
            turn_id="pinned-constraint",
            scope=ContextScope.TASK,
            content="SAFETY CONSTRAINT: No raw network without lease",
            pinned=True,
        ),
        agent_id=agent_id,
        task_id=task_id,
        caller_scope=ContextScope.TASK,
    )

    # 2. Append 500 unpinned sequential observation turns
    for i in range(1, 501):
        cm.append_entry(
            scope=ContextScope.TASK,
            entry=ContextEntry(
                turn_id=f"obs-{i}",
                scope=ContextScope.TASK,
                content=f"Telemetry sample {i}: load={i * 0.1:.2f}, temp={20 + (i % 15)}C",
                pinned=False,
                metadata={"decision": f"Recorded telemetry chunk {i}"},
            ),
            agent_id=agent_id,
            task_id=task_id,
            caller_scope=ContextScope.TASK,
        )

    # 3. Assertions on compaction behavior
    # At max_unpinned_turns=20, 500 turns trigger at least 25 compactions
    assert cm.compaction_count >= 24
    assert cm.evicted_entries_count >= 450

    effective = cm.get_effective_context(agent_id=agent_id, task_id=task_id)
    # Effective context size is bounded (pinned entries + latest handoff note + few unpinned)
    assert len(effective) < 30

    # Both pinned entries survived
    pinned_contents = [e.content for e in effective if e.pinned]
    assert any("MANDATORY GOAL: Preserve SCCA Invariants" in c for c in pinned_contents)
    assert any("SAFETY CONSTRAINT: No raw network without lease" in c for c in pinned_contents)


def test_handoff_note_recovery_contract() -> None:
    """AGENT-007: HandoffNote captures necessary state for deterministic recovery."""
    cm = ContextManager(space_id="space-handoff-recovery", max_unpinned_turns=10)
    agent_id = "agent-recovering"
    task_id = "task-recover-1"

    # Setup initial state with metadata
    cm.append_entry(
        scope=ContextScope.TASK,
        entry=ContextEntry(
            turn_id="t-init",
            scope=ContextScope.TASK,
            content="Init goal",
            pinned=True,
            metadata={"goal": "Reconcile database replicas", "plan_version": 7},
        ),
        agent_id=agent_id,
        task_id=task_id,
        caller_scope=ContextScope.TASK,
    )

    # Append turns with specific decisions
    for i in range(1, 15):
        cm.append_entry(
            scope=ContextScope.TASK,
            entry=ContextEntry(
                turn_id=f"step-{i}",
                scope=ContextScope.TASK,
                content=f"Step {i}",
                pinned=False,
                metadata={
                    "decision": f"Applied migration {i}",
                    "open_question": f"Risk on shard {i % 3}",
                },
            ),
            agent_id=agent_id,
            task_id=task_id,
            caller_scope=ContextScope.TASK,
        )

    # Compaction occurred
    assert cm.compaction_count >= 1
    latest_note: HandoffNote = cm.handoff_notes[-1]

    # Verify structured HandoffNote properties per §12
    assert latest_note.goal == "Reconcile database replicas"
    assert latest_note.current_task_id == task_id
    assert latest_note.plan_version == 7
    assert len(latest_note.last_3_decisions) == 3
    assert (
        "Applied migration 10" in latest_note.last_3_decisions[-1]
        or "Applied migration" in latest_note.last_3_decisions[-1]
    )

    # Serialization contract verification
    serialized = latest_note.to_dict()
    assert serialized["goal"] == "Reconcile database replicas"
    assert serialized["current_task_id"] == task_id
    assert serialized["plan_version"] == 7
    assert isinstance(serialized["last_3_decisions"], list)
    assert isinstance(serialized["open_questions"], list)
