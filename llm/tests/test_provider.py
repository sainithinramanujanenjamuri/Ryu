"""Unit tests for LLM provider abstraction and contracts.

spec §12 (LLM call recording), CONTRACT_MATRIX AGENT-001/002 — Phase 5
"""

from __future__ import annotations

from llm.provider import (
    LLMError,
    LLMMetadata,
    LLMRequest,
    LLMResponse,
    LLMUsage,
    MockLLMProvider,
)


def test_llm_request_and_response_dataclasses() -> None:
    req = LLMRequest(
        request_id="req-1",
        correlation_id="corr-1",
        space_id="space-1",
        agent_id="agent-1",
        model="gpt-test",
        provider="mock",
        messages=[{"role": "user", "content": "Analyze task"}],
        parameters={"temp": 0.2},
    )
    assert req.request_id == "req-1"
    assert req.correlation_id == "corr-1"
    assert req.model == "gpt-test"

    usage = LLMUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15)
    meta = LLMMetadata(model="gpt-test", provider="mock", temperature=0.2)
    err = LLMError(error_class="transient.timeout", message="Timeout after 10s", retryable=True)

    resp = LLMResponse(
        request_id="req-1",
        content="Task analysis result",
        structured_output={"intent": "research"},
        usage=usage,
        status="ok",
        error=None,
    )
    assert resp.status == "ok"
    assert resp.usage.total_tokens == 15
    assert meta.temperature == 0.2
    assert err.retryable is True


def test_mock_llm_provider_default_and_canned_queue() -> None:
    provider = MockLLMProvider()
    req1 = LLMRequest(
        request_id="r1",
        correlation_id="c1",
        space_id="s1",
        agent_id="a1",
        model="mock-gpt",
        provider="mock",
        messages=[],
    )

    # 1. Default completion
    resp1 = provider.complete(req1)
    assert resp1.status == "ok"
    assert resp1.structured_output is not None
    assert resp1.structured_output["intent"] == "analyze_goal"
    assert provider.call_count == 1

    # 2. Enqueued canned completion
    canned = LLMResponse(
        request_id="r2",
        content="Custom response",
        structured_output={"intent": "custom_intent", "requested_action": "custom_act"},
        status="ok",
    )
    provider.enqueue_response(canned)

    req2 = LLMRequest(
        request_id="r2",
        correlation_id="c1",
        space_id="s1",
        agent_id="a1",
        model="mock-gpt",
        provider="mock",
        messages=[],
    )
    resp2 = provider.complete(req2)
    assert resp2.content == "Custom response"
    assert resp2.structured_output is not None
    assert resp2.structured_output["intent"] == "custom_intent"
    assert provider.call_count == 2
