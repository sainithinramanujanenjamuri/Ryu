"""Conversation History Rehydration & Space Dialogue Store.

Stores and rehydrates dialogue turns per Space from durable persistence
and the authoritative Pulse store, preserving causal ordering.
spec §2, §4, ADR-0040, CONTRACT DESKTOP-002 — Phase 8.5+
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
import threading
import time
from typing import Any

logger = logging.getLogger("ryu.channels.daemon.history")


@dataclass
class DialogueTurn:
    """Represents a single conversational turn within a Space."""

    turn_id: str
    space_id: str
    user_prompt: str
    assistant_response: str
    timestamp: float
    goal_id: str | None = None
    status: str = "completed"  # "completed" | "error"
    single_agent_eligible: bool = True
    required_capabilities: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DialogueTurn:
        return cls(
            turn_id=str(data.get("turn_id", f"turn-{int(time.time() * 1000)}")),
            space_id=str(data.get("space_id", "default")),
            user_prompt=str(data.get("user_prompt", "")),
            assistant_response=str(data.get("assistant_response", "")),
            timestamp=float(data.get("timestamp", time.time())),
            goal_id=data.get("goal_id"),
            status=str(data.get("status", "completed")),
            single_agent_eligible=bool(data.get("single_agent_eligible", True)),
            required_capabilities=list(data.get("required_capabilities", [])),
        )


class SpaceHistoryStore:
    """Authoritative storage and rehydration engine for Space dialogue history.

    Invariants:
    - Space isolation: History for space A is stored strictly in spaces/space_A/history.jsonl
    - Causal ordering: Appends are strictly chronological
    - Pulse store integration: When history.jsonl is absent or partial, rehydrates
      from authoritative `goal.defined` pulses
    - Resilience: Malformed or unparseable lines are skipped gracefully
    """

    def __init__(self, base_dir: Path | None = None, pulse_store: Any | None = None) -> None:
        self.base_dir = base_dir if base_dir is not None else Path.home() / ".ryu" / "spaces"
        self.pulse_store = pulse_store
        self._lock = threading.RLock()

    def _get_history_file(self, space_id: str) -> Path:
        space_dir = self.base_dir / space_id
        space_dir.mkdir(parents=True, exist_ok=True)
        return space_dir / "history.jsonl"

    def append_turn(self, turn: DialogueTurn) -> None:
        """Durably append a dialogue turn for a space."""
        with self._lock:
            history_file = self._get_history_file(turn.space_id)
            line = json.dumps(turn.to_dict(), ensure_ascii=False)
            with open(history_file, "a", encoding="utf-8") as f:
                f.write(line + "\n")

    def get_history(self, space_id: str, limit: int = 100) -> list[dict[str, Any]]:
        """Retrieve dialogue history for a space in chronological order."""
        with self._lock:
            turns: list[dict[str, Any]] = []
            history_file = self._get_history_file(space_id)

            if history_file.is_file():
                try:
                    with open(history_file, "r", encoding="utf-8") as f:
                        for line in f:
                            line = line.strip()
                            if not line:
                                continue
                            try:
                                record = json.loads(line)
                                if isinstance(record, dict) and record.get("space_id") == space_id:
                                    turns.append(record)
                            except Exception as e:
                                logger.warning(f"Skipping malformed history record in {history_file}: {e}")
                except Exception as e:
                    logger.error(f"Error reading history file for space '{space_id}': {e}")

            # Fallback or supplemental rehydration from pulse_store if available
            if not turns and self.pulse_store is not None:
                try:
                    if hasattr(self.pulse_store, "read_space_tail"):
                        page = self.pulse_store.read_space_tail(
                            space_id,
                            limit=limit or 100,
                            pulse_types=frozenset(["goal.defined"]),
                        )
                        pulses = [sp.pulse for sp in page.entries]
                    elif hasattr(self.pulse_store, "read_by_space"):
                        pulses = self.pulse_store.read_by_space(space_id)
                    else:
                        pulses = []
                    for p in pulses:
                        p_type = getattr(p, "type", "")
                        if p_type == "goal.defined":
                            payload = getattr(p, "payload", {})
                            goal_spec = payload.get("goal_spec", {})
                            objective = goal_spec.get("objective", "")
                            if objective:
                                turn_record = {
                                    "turn_id": f"pulse-{getattr(p, 'id', '')}",
                                    "space_id": space_id,
                                    "user_prompt": objective,
                                    "assistant_response": "Goal analyzed and registered in SCCA plan.",
                                    "timestamp": (
                                        p.timestamp.timestamp()
                                        if hasattr(p.timestamp, "timestamp")
                                        else time.time()
                                    ),
                                    "goal_id": payload.get("goal_id"),
                                    "status": "completed",
                                    "single_agent_eligible": payload.get("single_agent_eligible", True),
                                    "required_capabilities": goal_spec.get("required_capabilities", []),
                                }
                                turns.append(turn_record)
                except Exception as e:
                    logger.error(f"Pulse store history rehydration failed for space '{space_id}': {e}")

            # Sort chronologically by timestamp
            turns.sort(key=lambda x: x.get("timestamp", 0.0))
            if limit and len(turns) > limit:
                turns = turns[-limit:]
            return turns
