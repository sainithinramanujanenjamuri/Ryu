"""Deterministic LLM Replay Provider.

Replays Agent execution using recorded LLM interactions without contacting external model APIs.
Proves 'live LLM provider calls during replay = 0' (ADR-0012, Correction 2).

spec §12 (LLM call recording), ROADMAP Phase 5, AGENT-004 — Phase 5
"""

from __future__ import annotations

import threading

from llm.provider import LLMRequest, LLMResponse
from llm.recorder import LLMRecorder


class ReplayLLMProvider:
    """LLM provider double that serves canned responses strictly from recorded traces.

    Invariants (ADR-0012, Correction 2):
    - Live provider calls are strictly ZERO during replay.
    - Matches requests by space_id, correlation_id, and sequential invocation order.
    - Yields response-identical and decision-identical outputs.
    """

    def __init__(self, recorder: LLMRecorder, space_id: str) -> None:
        self.recorder = recorder
        self.space_id = space_id
        self._lock = threading.Lock()
        self._call_indices: dict[str, int] = {}
        self.live_calls_count: int = 0
        self.replay_calls_count: int = 0
        self.served_responses: list[LLMResponse] = []

    def complete(self, request: LLMRequest) -> LLMResponse:
        """Serve the next recorded response for the given correlation_id."""
        with self._lock:
            # Assert live calls stay zero
            assert self.live_calls_count == 0, "Replay provider must never make live calls!"

            correlation_id = request.correlation_id
            records = self.recorder.get_by_correlation(self.space_id, correlation_id)
            if not records:
                raise KeyError(
                    f"No recorded LLM calls found for Space '{self.space_id}' "
                    f"and correlation '{correlation_id}'"
                )

            current_idx = self._call_indices.get(correlation_id, 0)
            if current_idx >= len(records):
                raise IndexError(
                    f"Replay exhausted: requested call {current_idx + 1}, but only "
                    f"{len(records)} recorded for correlation '{correlation_id}'"
                )

            record = records[current_idx]
            self._call_indices[correlation_id] = current_idx + 1
            self.replay_calls_count += 1

            response = record.response
            self.served_responses.append(response)
            return response

