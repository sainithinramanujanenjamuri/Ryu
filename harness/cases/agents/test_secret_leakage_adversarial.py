"""Harness cases: Adversarial Secret Leakage and Containment.

Proves:
- End-to-end secret containment across SecretStore -> LLM -> Recorder -> Replay.
- All 10 adversarial vectors are masked with [REDACTED_SECRET].
- Opaque secret references (secret://...) are preserved.
- Zero secret authority: LLM/Agent has no SecretResolver instance and cannot read secrets.
- Resolves OPEN-007 per ADR-0011.

spec §12 (LLM call recording), §16 (Secret Containment), CONTRACT_MATRIX OPEN-007, Corrections 3 & 4
"""

from __future__ import annotations

import json

from agents.base import AgentProposal, BaseAgent
from core.security.secrets import SecretStore
from llm.provider import LLMError, LLMRequest, LLMResponse, MockLLMProvider
from llm.recorder import InMemoryLLMRecorder, LLMRecord
from llm.replay import ReplayLLMProvider
from llm.sanitizer import SecretSanitizer


def test_adversarial_all_10_vectors_masked_in_recorder() -> None:
    """Correction 3: Proves masking of all 10 adversarial vectors before persistence."""
    store = SecretStore()
    secret_val1 = "super_secret_auth_token_999"
    secret_val2 = "production_db_password_xyz"
    store.register("secret://auth/token", secret_val1)
    store.register("secret://db/password", secret_val2)

    sanitizer = SecretSanitizer(secret_store=store)
    recorder = InMemoryLLMRecorder(sanitizer=sanitizer)
    space_id = "space-sec-adversarial"

    # Vectors:
    # 1. Direct literal
    # 2. JSON string encoding
    # 3. URL query parameter
    # 4. Authorization header
    # 5. Multi-secret payload
    # 6. Nested dict/list payload
    # 7. Prompt text
    # 8. Assistant response
    # 9. Error message
    # 10. System prompt

    vectors = [
        ("v1-literal", f"Secret value is {secret_val1}"),
        ("v2-json", json.dumps({"token": secret_val1, "safe": "ok"})),
        ("v3-url", f"https://api.internal/v1/auth?token={secret_val1}&id=1"),
        ("v4-auth-header", f"Authorization: Bearer {secret_val1}"),
        ("v5-multi-secret", f"User token: {secret_val1}, DB password: {secret_val2}"),
        ("v6-nested", {"outer": [{"inner_token": secret_val1}], "other": [secret_val2]}),
        ("v7-prompt", f"Please process this prompt with secret={secret_val1}"),
        ("v8-response", f"Assistant extracted key: {secret_val1}"),
        ("v9-error", f"Database connection failed with credentials {secret_val2}"),
        ("v10-system-prompt", f"System directive: Authorized with master key {secret_val1}"),
    ]

    for idx, (vec_name, payload) in enumerate(vectors):
        content_str = json.dumps(payload) if not isinstance(payload, str) else payload
        nested_dict = payload if isinstance(payload, dict) else {"payload": payload}

        req = LLMRequest(
            request_id=f"req-{vec_name}",
            correlation_id=f"corr-{vec_name}",
            space_id=space_id,
            agent_id="agent-sec",
            model="sec-model",
            provider="mock",
            messages=[
                {"role": "system", "content": f"System prompt with {secret_val1}"},
                {"role": "user", "content": content_str},
            ],
            parameters={"nested": nested_dict},
        )

        resp = LLMResponse(
            request_id=f"req-{vec_name}",
            content=f"Response output: {content_str}",
            structured_output={"result": nested_dict},
            status="ok",
        )

        record = LLMRecord(
            call_id=f"call-{idx}",
            correlation_id=f"corr-{vec_name}",
            space_id=space_id,
            agent_id="agent-sec",
            provider="mock",
            model="sec-model",
            request=req,
            response=resp,
            error=LLMError(error_class="transient.error", message=f"Failed with {secret_val2}"),
        )

        # Persist through recorder
        recorder.record(record)

        # Retrieve and verify: raw secret MUST NOT appear anywhere in the persisted record
        persisted = recorder.get_by_call_id(space_id, f"call-{idx}")
        assert persisted is not None

        # Verify request messages
        for msg in persisted.request.messages:
            assert secret_val1 not in msg["content"]
            assert secret_val2 not in msg["content"]
            assert "[REDACTED_SECRET]" in msg["content"]

        # Verify request parameters
        serialized_params = json.dumps(persisted.request.parameters)
        assert secret_val1 not in serialized_params
        assert secret_val2 not in serialized_params
        assert "[REDACTED_SECRET]" in serialized_params

        # Verify response content
        assert secret_val1 not in persisted.response.content
        assert secret_val2 not in persisted.response.content
        assert "[REDACTED_SECRET]" in persisted.response.content

        # Verify response structured output
        serialized_so = json.dumps(persisted.response.structured_output)
        assert secret_val1 not in serialized_so
        assert secret_val2 not in serialized_so
        assert "[REDACTED_SECRET]" in serialized_so

        # Verify error message
        assert persisted.error is not None
        assert secret_val2 not in persisted.error.message
        assert "[REDACTED_SECRET]" in persisted.error.message


def test_opaque_secret_uri_preservation() -> None:
    """Correction 3 & ADR-0011: Opaque secret references (secret://...)
    are PRESERVED and not redacted.
    """
    store = SecretStore()
    secret_val = "very_secret_passphrase_123"
    store.register("secret://vault/api_key", secret_val)

    sanitizer = SecretSanitizer(secret_store=store)

    text_with_uri = (
        "Acquired capability with credential handle secret://vault/api_key for operation."
    )
    sanitized = sanitizer.sanitize(text_with_uri)

    # The URI itself must be preserved intact
    assert "secret://vault/api_key" in sanitized
    assert "[REDACTED_SECRET]" not in sanitized

    # But if the resolved raw value is present, it must be masked
    text_with_leak = f"Found handle secret://vault/api_key with resolved value {secret_val}"
    sanitized_leak = sanitizer.sanitize(text_with_leak)
    assert "secret://vault/api_key" in sanitized_leak
    assert secret_val not in sanitized_leak
    assert "[REDACTED_SECRET]" in sanitized_leak


def test_agent_zero_secret_authority() -> None:
    """Correction 4: LLM and Agent have ZERO SecretResolver authority."""
    agent = BaseAgent(
        agent_id="agent-no-secret-auth",
        space_id="space-sec-zero",
    )

    # Invariant 1: Agent does not possess a secret resolver attribute
    assert not hasattr(agent, "secret_resolver")
    assert not hasattr(agent, "resolver")

    # Invariant 2: Direct read_secret proposal is rejected deterministically
    bad_proposal = AgentProposal(
        intent="steal_secret",
        reasoning="Need secret for external call",
        requested_action="read_secret",
        parameters={"uri": "secret://vault/key"},
    )
    is_valid, reason = agent.validator.validate(bad_proposal)
    assert is_valid is False
    assert "terminal.permission_denied" in (reason or "")

    # Invariant 3: resolve_secret proposal is rejected
    bad_proposal_resolve = AgentProposal(
        intent="resolve_secret",
        reasoning="Attempting bypass",
        requested_action="resolve_secret",
        parameters={"secret_id": "key"},
    )
    is_valid2, reason2 = agent.validator.validate(bad_proposal_resolve)
    assert is_valid2 is False
    assert "terminal.permission_denied" in (reason2 or "")


def test_end_to_end_secret_containment_in_replay() -> None:
    """Proves end-to-end: SecretStore -> LLM -> Recorder -> Replay does not leak secrets."""
    store = SecretStore()
    secret_val = "highly_sensitive_credentials_456"
    store.register("secret://corp/prod_token", secret_val)

    sanitizer = SecretSanitizer(secret_store=store)
    recorder = InMemoryLLMRecorder(sanitizer=sanitizer)
    space_id = "space-e2e-sec"
    correlation_id = "corr-e2e-sec"

    # Mock provider simulates LLM that inadvertently reflects the secret
    mock_provider = MockLLMProvider()
    mock_provider.enqueue_response(
        LLMResponse(
            request_id="req-leak",
            content=f"Observation with leaked token: {secret_val}",
            structured_output={
                "intent": "summarize",
                "reasoning": f"Leaked token {secret_val}",
                "requested_action": "report_status",
                "parameters": {"token_sample": secret_val},
                "confidence": 1.0,
            },
            status="ok",
        )
    )

    agent = BaseAgent(
        agent_id="agent-leak-test",
        space_id=space_id,
        provider=mock_provider,
        recorder=recorder,
    )

    # Agent executes step
    proposal = agent.step("task-sec-1", "Inspect logs", correlation_id=correlation_id)
    assert proposal.is_valid is True

    # Check recorder persistence: raw secret NEVER saved
    records = recorder.get_by_correlation(space_id, correlation_id)
    assert len(records) == 1
    persisted_text = json.dumps(records[0].response.structured_output)
    assert secret_val not in persisted_text
    assert "[REDACTED_SECRET]" in persisted_text

    # Check replay: replay serves the sanitized response
    replay_provider = ReplayLLMProvider(recorder=recorder, space_id=space_id)
    replayed_resp = replay_provider.complete(records[0].request)
    assert secret_val not in replayed_resp.content
    assert secret_val not in json.dumps(replayed_resp.structured_output)
    assert "[REDACTED_SECRET]" in replayed_resp.content
