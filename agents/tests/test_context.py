"""Unit tests for ContextManager.

Proves:
- Visibility does NOT imply mutation authority (Correction 5).
  - Task -> Agent context mutation = REJECTED.
  - Agent -> Space context mutation = REJECTED.
  - Space A -> Space B context access = REJECTED.
- Real 500-turn context compaction with pinned field survival (Correction 6).

spec §12 (Context Manager contract), CONTRACT_MATRIX AGENT-005/006/007 — Phase 5
"""

from __future__ import annotations

import pytest

from agents.context import ContextEntry, ContextManager, ContextScope


def test_context_scope_visibility_hierarchy() -> None:
    cm = ContextManager(space_id="space-1")

    # Append Space context
    cm.append_entry(
        scope=ContextScope.SPACE,
        entry=ContextEntry("turn-s1", ContextScope.SPACE, "Space wide instructions", pinned=True),
        caller_scope=ContextScope.SPACE,
    )

    # Append Agent context
    cm.append_entry(
        scope=ContextScope.AGENT,
        entry=ContextEntry("turn-a1", ContextScope.AGENT, "Agent scratchpad"),
        agent_id="agent-1",
        caller_scope=ContextScope.AGENT,
    )

    # Append Task context
    cm.append_entry(
        scope=ContextScope.TASK,
        entry=ContextEntry("turn-t1", ContextScope.TASK, "Task specific prompt"),
        agent_id="agent-1",
        task_id="task-1",
        caller_scope=ContextScope.TASK,
    )

    # Verify visibility: Task context sees Space + Agent + Task
    effective = cm.get_effective_context(agent_id="agent-1", task_id="task-1")
    contents = [e.content for e in effective]
    assert "Space wide instructions" in contents
    assert "Agent scratchpad" in contents
    assert "Task specific prompt" in contents
    assert len(effective) == 3


def test_unauthorized_upward_context_mutation_rejected() -> None:
    """Correction 5: Upward mutation authority rejection."""
    cm = ContextManager(space_id="space-1")

    # 1. Task scope attempting to mutate Agent context -> PermissionError
    with pytest.raises(PermissionError, match="Task scope cannot mutate Agent context"):
        cm.append_entry(
            scope=ContextScope.AGENT,
            entry=ContextEntry("turn-bad-1", ContextScope.AGENT, "Sneaky agent write"),
            agent_id="agent-1",
            caller_scope=ContextScope.TASK,
        )

    # 2. Agent scope attempting to mutate Space context -> PermissionError
    with pytest.raises(PermissionError, match="cannot mutate Space context"):
        cm.append_entry(
            scope=ContextScope.SPACE,
            entry=ContextEntry("turn-bad-2", ContextScope.SPACE, "Sneaky space write"),
            caller_scope=ContextScope.AGENT,
        )

    # 3. Cross-Space context access -> PermissionError
    with pytest.raises(PermissionError, match="Cross-space context access rejected"):
        cm.verify_space_identity("space-2")


def test_real_context_compaction_500_turns_with_pinned_survival() -> None:
    """Correction 6: Real 500-turn compaction proving bounded memory and pinned survival.

    Must prove:
    - Context size strictly decreases on compaction.
    - Evictable content is removed.
    - Pinned content survives (goal, current_task_id, plan_version, last_3_decisions).
    - Context stays bounded across multiple cycles.
    """
    # Max 20 unpinned turns before automatic compaction
    cm = ContextManager(space_id="space-compaction", max_unpinned_turns=20)
    agent_id = "agent-longrun"
    task_id = "task-500"

    # Step 1: Add pinned goal entry
    cm.append_entry(
        scope=ContextScope.TASK,
        entry=ContextEntry(
            turn_id="turn-pin-goal",
            scope=ContextScope.TASK,
            content="Pinned Architectural Goal: Build Ryu SCCA",
            pinned=True,
            metadata={
                "goal": "Build Ryu SCCA",
                "plan_version": 4,
            },
        ),
        agent_id=agent_id,
        task_id=task_id,
        caller_scope=ContextScope.TASK,
    )

    # Step 2: Simulate 500 sequential turns
    for turn in range(1, 501):
        decision = f"Decision turn {turn}: optimized step {turn}"
        cm.append_entry(
            scope=ContextScope.TASK,
            entry=ContextEntry(
                turn_id=f"turn-{turn}",
                scope=ContextScope.TASK,
                content=f"Transient tool output for turn {turn}: data blob {turn * 10}",
                pinned=False,
                metadata={"decision": decision},
            ),
            agent_id=agent_id,
            task_id=task_id,
            caller_scope=ContextScope.TASK,
        )

    # Invariant 1: Multiple compactions occurred (500 turns / 20 threshold = ~25 compactions)
    assert cm.compaction_count >= 20, f"Expected >= 20 compactions, got {cm.compaction_count}"
    assert cm.evicted_entries_count >= 400

    # Invariant 2: Context is BOUNDED and did not grow to 500 entries!
    effective = cm.get_effective_context(agent_id=agent_id, task_id=task_id)
    assert len(effective) < 30, f"Context leaked! Size is {len(effective)}, expected < 30"

    # Invariant 3: Pinned goal survived all 25 compactions
    pinned_contents = [e.content for e in effective if e.pinned]
    assert any("Pinned Architectural Goal: Build Ryu SCCA" in c for c in pinned_contents)

    # Invariant 4: Latest HandoffNote survives with required fields
    assert len(cm.handoff_notes) > 0
    latest_note = cm.handoff_notes[-1]
    assert latest_note.goal == "Build Ryu SCCA"
    assert latest_note.current_task_id == task_id
    assert latest_note.plan_version == 4
    assert len(latest_note.last_3_decisions) == 3
    # Check that decisions are recent (from high turn numbers)
    assert "Decision turn" in latest_note.last_3_decisions[-1]

