"""Adversarial unit tests for SecretSanitizer.

Proves: resolved_secret ∉ persisted_record across all 10 adversarial vectors (Correction 3).
Proves: secret://provider/name remains an opaque reference and is NOT redacted (ADR-0011).

spec §10 (Secret containment), ROADMAP Phase 5, OPEN-007 — Phase 5
"""

from __future__ import annotations

from core.security.secrets import SecretStore
from llm.sanitizer import SecretSanitizer


def _make_store_with_secrets() -> tuple[SecretStore, dict[str, str]]:
    store = SecretStore()
    secrets = {
        "db_password": "super_secret_db_password_xyz123",
        "api_key": "sk-live-998877665544332211",
        "private_token": "ghp_alpha_numeric_secret_token_99",
    }
    for k, v in secrets.items():
        store.register(f"secret://test/{k}", v)
    return store, secrets


def test_opaque_reference_is_not_redacted() -> None:
    """Invariant: secret:// references must remain untouched opaque references."""
    store, _ = _make_store_with_secrets()
    sanitizer = SecretSanitizer(store)

    text = "Connecting to database using SecretRef: secret://test/db_password in config."
    result = sanitizer.sanitize(text)
    assert "secret://test/db_password" in result
    assert sanitizer.REDACTION_MARKER not in result


def test_adversarial_vector_01_secret_only_in_prompt() -> None:
    """Vector 1: Plain prompt text containing resolved secret."""
    store, secrets = _make_store_with_secrets()
    sanitizer = SecretSanitizer(store)

    prompt = f"Please connect to the database with password {secrets['db_password']} and run query."
    result = sanitizer.sanitize(prompt)

    assert secrets["db_password"] not in result
    assert sanitizer.REDACTION_MARKER in result


def test_adversarial_vector_02_secret_only_in_response() -> None:
    """Vector 2: Model response containing resolved secret."""
    store, secrets = _make_store_with_secrets()
    sanitizer = SecretSanitizer(store)

    response = f"I found the key: {secrets['api_key']}. Use it for authentication."
    result = sanitizer.sanitize(response)

    assert secrets["api_key"] not in result
    assert sanitizer.REDACTION_MARKER in result


def test_adversarial_vector_03_secret_in_structured_json() -> None:
    """Vector 3: Secret inside structured dictionary output."""
    store, secrets = _make_store_with_secrets()
    sanitizer = SecretSanitizer(store)

    data = {
        "intent": "execute",
        "parameters": {"auth_token": secrets["private_token"]},
    }
    result = sanitizer.sanitize(data)

    assert secrets["private_token"] not in str(result)
    assert result["parameters"]["auth_token"] == sanitizer.REDACTION_MARKER


def test_adversarial_vector_04_secret_inside_nested_json() -> None:
    """Vector 4: Secret inside deeply nested JSON list and dicts."""
    store, secrets = _make_store_with_secrets()
    sanitizer = SecretSanitizer(store)

    data = {
        "root": {
            "sub_array": [
                1,
                {"nested_key": f"Bearer {secrets['api_key']}"},
            ]
        }
    }
    result = sanitizer.sanitize(data)

    assert secrets["api_key"] not in str(result)
    assert sanitizer.REDACTION_MARKER in result["root"]["sub_array"][1]["nested_key"]


def test_adversarial_vector_05_secret_inside_exception() -> None:
    """Vector 5: Secret inside an Exception string representation."""
    store, secrets = _make_store_with_secrets()
    sanitizer = SecretSanitizer(store)

    exc = RuntimeError(f"Connection failed with credentials: {secrets['db_password']}")
    result = sanitizer.sanitize(exc)

    assert secrets["db_password"] not in result
    assert sanitizer.REDACTION_MARKER in result


def test_adversarial_vector_06_secret_inside_metadata() -> None:
    """Vector 6: Secret inside invocation metadata dictionary."""
    store, secrets = _make_store_with_secrets()
    sanitizer = SecretSanitizer(store)

    metadata = {"headers": {"X-Api-Key": secrets["api_key"]}}
    result = sanitizer.sanitize(metadata)

    assert secrets["api_key"] not in str(result)
    assert result["headers"]["X-Api-Key"] == sanitizer.REDACTION_MARKER


def test_adversarial_vector_07_secret_inside_tool_output() -> None:
    """Vector 7: Secret returned inside a simulated tool execution output."""
    store, secrets = _make_store_with_secrets()
    sanitizer = SecretSanitizer(store)

    tool_output = f"stdout: user logged in with session={secrets['private_token']}\nstderr: None"
    result = sanitizer.sanitize(tool_output)

    assert secrets["private_token"] not in result
    assert sanitizer.REDACTION_MARKER in result


def test_adversarial_vector_08_multiple_distinct_secrets() -> None:
    """Vector 8: Multiple distinct secrets in the same payload."""
    store, secrets = _make_store_with_secrets()
    sanitizer = SecretSanitizer(store)

    combined = (
        f"db: {secrets['db_password']}, api: {secrets['api_key']}, "
        f"token: {secrets['private_token']}"
    )
    result = sanitizer.sanitize(combined)

    for val in secrets.values():
        assert val not in result
    assert result.count(sanitizer.REDACTION_MARKER) == 3


def test_adversarial_vector_09_secret_appearing_multiple_times() -> None:
    """Vector 9: Same secret appearing repeatedly in the string."""
    store, secrets = _make_store_with_secrets()
    sanitizer = SecretSanitizer(store)

    repeated = f"Key: {secrets['api_key']} (repeated: {secrets['api_key']})"
    result = sanitizer.sanitize(repeated)

    assert secrets["api_key"] not in result
    assert result.count(sanitizer.REDACTION_MARKER) == 2


def test_adversarial_vector_10_secret_embedded_inside_longer_string() -> None:
    """Vector 10: Secret embedded inside a longer URL/path without delimiter boundaries."""
    store, secrets = _make_store_with_secrets()
    sanitizer = SecretSanitizer(store)

    url = f"https://api.internal/auth/{secrets['api_key']}/v1/resource?cache=true"
    result = sanitizer.sanitize(url)

    expected_url = f"https://api.internal/auth/{sanitizer.REDACTION_MARKER}/v1/resource?cache=true"
    assert result == expected_url
