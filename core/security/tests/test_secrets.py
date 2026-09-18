"""Unit tests for SecretStore, SecretResolver, and Secret Containment validator.

spec §10 (Secret Containment), §16 (SecretRef), SECRET-003, ADR-0004 — Phase 2
"""

from __future__ import annotations

import pytest
from ryu.pulse_bus.reject import PulseRejectedError
from ryu.pulse_bus.validator import PulseValidator

from core.security.secrets import SecretRef, SecretResolver, SecretStore


def test_secret_ref_uri_validation() -> None:
    # Valid URIs
    ref1 = SecretRef("secret://aws/prod_db_pass")
    assert ref1.uri == "secret://aws/prod_db_pass"

    ref2 = SecretRef("secret://vault/api-key-1")
    assert ref2.uri == "secret://vault/api-key-1"

    # Invalid URIs
    with pytest.raises(ValueError):
        SecretRef("http://vault/secret")

    with pytest.raises(ValueError):
        SecretRef("secret://invalid")

    with pytest.raises(ValueError):
        SecretRef("secret://provider/name/extra")


def test_secret_store_registration_and_resolution() -> None:
    store = SecretStore()
    store.register("secret://db/password", "SuperSecretPass123!")

    # Minimum length validation
    with pytest.raises(ValueError):
        store.register("secret://db/short", "12345")  # < 6 chars

    resolver = SecretResolver(store)
    val = resolver.resolve("secret://db/password")
    assert val == "SuperSecretPass123!"

    with pytest.raises(KeyError):
        resolver.resolve("secret://db/nonexistent")


def test_validator_exact_match_rejection() -> None:
    """ADR-0004: PulseValidator rejects payloads containing exact resolved secrets."""
    store = SecretStore()
    store.register("secret://db/password", "SuperSecretPass123!")

    validator = PulseValidator(secret_store=store)

    # 1. Plain top-level leak
    leak_payload = {"space_id": "1", "owner_id": "user-SuperSecretPass123!"}
    with pytest.raises(PulseRejectedError) as exc:
        validator.validate("space.created", leak_payload)
    assert exc.value.reason == "secret_leak_detected"

    # 2. Nested dictionary/list leak
    nested_leak = {
        "space_id": "1",
        "owner_id": "u1",
        "nested": {
            "items": ["safe_string", "Bearer SuperSecretPass123!"]
        },
    }
    with pytest.raises(PulseRejectedError) as exc:
        validator.validate_secrets("space.created", nested_leak)
    assert exc.value.reason == "secret_leak_detected"

    # 3. Clean payload without secrets is admitted
    clean_payload = {"space_id": "1", "owner_id": "legitimate_user"}
    # Must not raise
    validator.validate("space.created", clean_payload)

    # 4. SecretRef URI itself is permitted and not rejected
    uri_payload = {
        "space_id": "1",
        "owner_id": "u1",
        "ref": "secret://db/password",
    }
    validator.validate_secrets("space.created", uri_payload)

