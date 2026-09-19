"""Unit tests for ReplayLLMProvider.

Proves: 'live LLM provider calls during replay = 0' (ADR-0012, Correction 2).

spec §12 (LLM call recording), CONTRACT_MATRIX AGENT-004 — Phase 5
"""

from __future__ import annotations

import pytest

from llm.provider import LLMRequest, LLMResponse
from llm.recorder import InMemoryLLMRecorder, LLMRecord
from llm.replay import ReplayLLMProvider


def test_replay_provider_serves_recorded_responses_with_zero_live_calls() -> None:
    recorder = InMemoryLLMRecorder()
    space_id = "space-replay-1"
    correlation_id = "corr-replay-1"

    # Step 1: Record 2 calls from live interaction
    req1 = LLMRequest("call-1", correlation_id, space_id, "agent-1", "m", "p", [])
    resp1 = LLMResponse("call-1", "First answer", structured_output={"step": 1})
    recorder.record(LLMRecord("call-1", correlation_id, space_id, "agent-1", "p", "m", req1, resp1))

    req2 = LLMRequest("call-2", correlation_id, space_id, "agent-1", "m", "p", [])
    resp2 = LLMResponse("call-2", "Second answer", structured_output={"step": 2})
    recorder.record(LLMRecord("call-2", correlation_id, space_id, "agent-1", "p", "m", req2, resp2))

    # Step 2: Initialize ReplayLLMProvider
    replay_provider = ReplayLLMProvider(recorder=recorder, space_id=space_id)

    # Invariant: live calls start at 0
    assert replay_provider.live_calls_count == 0
    assert replay_provider.replay_calls_count == 0

    # Step 3: Replay first call
    rep_resp1 = replay_provider.complete(req1)
    assert rep_resp1.content == "First answer"
    assert rep_resp1.structured_output is not None
    assert rep_resp1.structured_output["step"] == 1
    assert replay_provider.live_calls_count == 0
    assert replay_provider.replay_calls_count == 1

    # Step 4: Replay second call
    rep_resp2 = replay_provider.complete(req2)
    assert rep_resp2.content == "Second answer"
    assert rep_resp2.structured_output is not None
    assert rep_resp2.structured_output["step"] == 2
    assert replay_provider.live_calls_count == 0
    assert replay_provider.replay_calls_count == 2

    # Step 5: Exhaustion raises IndexError
    with pytest.raises(IndexError, match="Replay exhausted"):
        replay_provider.complete(req1)

    # Invariant: live calls remained strictly 0 throughout
    assert replay_provider.live_calls_count == 0
