"""Context Manager subsystem: three nested scopes, LRU eviction, and compaction.

Implements ADR-0010, formalizing:
- Three nested scopes: SpaceContext -> AgentContext -> TaskContext (docs/Architecture §12).
- Visibility does NOT imply mutation authority (Correction 5):
  - Task cannot mutate Agent context.
  - Agent cannot mutate Space context.
  - Cross-Space context access is strictly rejected.
- Real context compaction preserving pinned information across 500+ turns (Correction 6).

spec §12 (Context Manager contract), ROADMAP Phase 5, AGENT-005/006/007 — Phase 5
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class ContextScope(str, Enum):
    """Context scope hierarchy."""

    TASK = "task"
    AGENT = "agent"
    SPACE = "space"


@dataclass
class ContextEntry:
    """Individual context entry with explicit scope and pinning."""

    turn_id: str
    scope: ContextScope
    content: str
    pinned: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class HandoffNote:
    """Structured compaction artifact surviving context eviction (Architecture §12, AGENT-007)."""

    goal: str
    current_task_id: str
    plan_version: int
    last_3_decisions: list[str] = field(default_factory=list)
    open_questions: list[str] = field(default_factory=list)
    compaction_turn: int = 0
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal": self.goal,
            "current_task_id": self.current_task_id,
            "plan_version": self.plan_version,
            "last_3_decisions": list(self.last_3_decisions),
            "open_questions": list(self.open_questions),
            "compaction_turn": self.compaction_turn,
            "timestamp": self.timestamp.isoformat(),
        }


class ContextManager:
    """Manages scoped context windows, enforces mutation authority, and compacts history.

    Invariants (ADR-0010, Corrections 5 & 6):
    - Child context can read parent context (visibility).
    - Child context CANNOT mutate parent context without authorization (PermissionError).
    - Space A caller CANNOT access Space B context (PermissionError).
    - Compaction actually evicts unpinned history while preserving pinned entries and HandoffNotes.
    """

    def __init__(
        self,
        space_id: str,
        max_unpinned_turns: int = 20,
        compaction_threshold_ratio: float = 0.80,
    ) -> None:
        self.space_id = space_id
        self.max_unpinned_turns = max_unpinned_turns
        self.compaction_threshold_ratio = compaction_threshold_ratio
        self._lock = threading.Lock()

        # Scoped stores
        self._space_context: list[ContextEntry] = []
        self._agent_contexts: dict[str, list[ContextEntry]] = {}  # agent_id -> entries
        self._task_contexts: dict[
            tuple[str, str], list[ContextEntry]
        ] = {}  # (agent_id, task_id) -> entries

        # Compaction state
        self.handoff_notes: list[HandoffNote] = []
        self.compaction_count: int = 0
        self.evicted_entries_count: int = 0

    def verify_space_identity(self, incoming_space_id: str) -> None:
        """Enforce Space boundary on all context interactions."""
        if incoming_space_id != self.space_id:
            raise PermissionError(
                f"Cross-space context access rejected: manager is for '{self.space_id}', "
                f"caller is in '{incoming_space_id}'"
            )

    def append_entry(
        self,
        scope: ContextScope,
        entry: ContextEntry,
        agent_id: str | None = None,
        task_id: str | None = None,
        caller_scope: ContextScope = ContextScope.TASK,
    ) -> None:
        """Append an entry with strict upward mutation authority enforcement (Correction 5)."""
        with self._lock:
            # Correction 5: Check upward mutation authority
            if scope == ContextScope.SPACE and caller_scope != ContextScope.SPACE:
                raise PermissionError(
                    f"Unauthorized upward context mutation: caller scope '{caller_scope}' "
                    f"cannot mutate Space context"
                )
            if scope == ContextScope.AGENT and caller_scope == ContextScope.TASK:
                raise PermissionError(
                    "Unauthorized upward context mutation: Task scope cannot mutate Agent context"
                )

            if scope == ContextScope.SPACE:
                self._space_context.append(entry)
            elif scope == ContextScope.AGENT:
                if not agent_id:
                    raise ValueError("agent_id required for Agent scope context")
                if agent_id not in self._agent_contexts:
                    self._agent_contexts[agent_id] = []
                self._agent_contexts[agent_id].append(entry)
            elif scope == ContextScope.TASK:
                if not agent_id or not task_id:
                    raise ValueError("agent_id and task_id required for Task scope context")
                key = (agent_id, task_id)
                if key not in self._task_contexts:
                    self._task_contexts[key] = []
                self._task_contexts[key].append(entry)

                # Check if task context requires compaction
                unpinned = [e for e in self._task_contexts[key] if not e.pinned]
                if len(unpinned) >= self.max_unpinned_turns:
                    self._compact_task_context_locked(agent_id, task_id)

    def get_effective_context(
        self,
        agent_id: str,
        task_id: str | None = None,
    ) -> list[ContextEntry]:
        """Assemble visible context (Space pinned + Agent context + Task context)."""
        with self._lock:
            visible: list[ContextEntry] = []
            # Space context (pinned or general)
            visible.extend(self._space_context)

            # Agent context
            if agent_id in self._agent_contexts:
                visible.extend(self._agent_contexts[agent_id])

            # Task context
            if task_id:
                key = (agent_id, task_id)
                if key in self._task_contexts:
                    visible.extend(self._task_contexts[key])

            return visible

    def get_context_size(self, agent_id: str, task_id: str | None = None) -> int:
        """Count total context entries visible to this task/agent."""
        return len(self.get_effective_context(agent_id, task_id))

    def _compact_task_context_locked(self, agent_id: str, task_id: str) -> HandoffNote:
        """Perform real compaction: evicts unpinned history, preserves pinned info.
        (Correction 6)
        """
        key = (agent_id, task_id)
        entries = self._task_contexts.get(key, [])

        pinned_entries = [e for e in entries if e.pinned]
        unpinned_entries = [e for e in entries if not e.pinned]

        # Extract structured handoff data from pinned entries and recent decisions
        goal = "Unknown goal"
        plan_ver = 1
        decisions: list[str] = []
        open_q: list[str] = []

        for e in entries:
            if "goal" in e.metadata:
                goal = str(e.metadata["goal"])
            if "plan_version" in e.metadata:
                plan_ver = int(e.metadata["plan_version"])
            if "decision" in e.metadata:
                decisions.append(str(e.metadata["decision"]))
            if "open_question" in e.metadata:
                open_q.append(str(e.metadata["open_question"]))

        # Synthesize HandoffNote per spec §12
        note = HandoffNote(
            goal=goal,
            current_task_id=task_id,
            plan_version=plan_ver,
            last_3_decisions=decisions[-3:] if decisions else ["step_completed"],
            open_questions=open_q[-3:],
            compaction_turn=len(entries),
        )
        self.handoff_notes.append(note)
        self.compaction_count += 1
        self.evicted_entries_count += len(unpinned_entries)

        # Replace task context with pinned entries + note entry
        note_entry = ContextEntry(
            turn_id=f"compaction-note-{self.compaction_count}",
            scope=ContextScope.TASK,
            content=f"HandoffNote(goal='{note.goal}', task='{note.current_task_id}')",
            pinned=True,
            metadata={"handoff_note": note.to_dict()},
        )
        self._task_contexts[key] = pinned_entries + [note_entry]
        return note

    def force_compaction(self, agent_id: str, task_id: str) -> HandoffNote:
        """Explicitly trigger compaction for a task context."""
        with self._lock:
            return self._compact_task_context_locked(agent_id, task_id)
