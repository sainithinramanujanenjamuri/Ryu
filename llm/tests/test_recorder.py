"""Unit tests for InMemoryLLMRecorder with Space scoping and automatic sanitization.

spec §12 (LLM call recording), CONTRACT_MATRIX AGENT-003, OPEN-007 — Phase 5
"""

from __future__ import annotations

import pytest

from core.security.secrets import SecretStore
from llm.provider import LLMRequest, LLMResponse, LLMUsage
from llm.recorder import InMemoryLLMRecorder, LLMRecord
from llm.sanitizer import SecretSanitizer


def _setup_recorder_with_secret() -> tuple[InMemoryLLMRecorder, str]:
    store = SecretStore()
    secret_val = "secret_api_key_value_999"
    store.register("secret://provider/api_key", secret_val)
    sanitizer = SecretSanitizer(store)
    return InMemoryLLMRecorder(sanitizer=sanitizer), secret_val


def test_recorder_sanitizes_call_before_persistence() -> None:
    recorder, secret_val = _setup_recorder_with_secret()

    req = LLMRequest(
        request_id="call-1",
        correlation_id="corr-1",
        space_id="space-A",
        agent_id="agent-1",
        model="gpt-test",
        provider="mock",
        messages=[{"role": "user", "content": f"Authorization: {secret_val}"}],
    )
    resp = LLMResponse(
        request_id="call-1",
        content=f"Token received: {secret_val}",
        structured_output={"token": secret_val},
        usage=LLMUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
        status="ok",
    )
    record = LLMRecord(
        call_id="call-1",
        correlation_id="corr-1",
        space_id="space-A",
        agent_id="agent-1",
        provider="mock",
        model="gpt-test",
        request=req,
        response=resp,
    )

    recorder.record(record)

    # Retrieve and verify: resolved_secret ∉ persisted_record
    retrieved = recorder.get_by_call_id("space-A", "call-1")
    assert retrieved is not None
    assert secret_val not in retrieved.request.messages[0]["content"]
    assert secret_val not in retrieved.response.content
    assert retrieved.response.structured_output is not None
    assert retrieved.response.structured_output["token"] == "[REDACTED_SECRET]"


def test_recorder_space_isolation_enforcement() -> None:
    recorder = InMemoryLLMRecorder()

    req_a = LLMRequest("call-a", "corr-a", "space-A", "agent-a", "m", "p", [])
    resp_a = LLMResponse("call-a", "Response A")
    rec_a = LLMRecord("call-a", "corr-a", "space-A", "agent-a", "p", "m", req_a, resp_a)
    recorder.record(rec_a)

    # 1. Caller in space-A succeeds
    res_a = recorder.get_by_correlation("space-A", "corr-a")
    assert len(res_a) == 1
    assert res_a[0].call_id == "call-a"

    # 2. Caller in space-B attempting to access space-A correlation -> PermissionError
    with pytest.raises(PermissionError, match="Cross-space LLM record access rejected"):
        recorder.get_by_correlation("space-B", "corr-a")

    # 3. Caller in space-B attempting to access space-A call_id -> PermissionError
    with pytest.raises(PermissionError, match="Cross-space LLM record access rejected"):
        recorder.get_by_call_id("space-B", "call-a")

    # 4. list_records is strictly Space-scoped
    assert len(recorder.list_records("space-A")) == 1
    assert len(recorder.list_records("space-B")) == 0
