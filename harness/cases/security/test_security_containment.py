"""Harness cases: Secret containment and Taint injection defense (SECRET-003, TAINT-005).

spec §10 (Prompt-Injection & Taint Model), CONTRACT_MATRIX SECRET-003, TAINT-005 — Phase 2
"""

from __future__ import annotations

import pytest
from ryu.pulse_bus.pulse import Pulse
from ryu.pulse_bus.reject import PulseRejectedError
from ryu.pulse_bus.validator import PulseValidator

from core.capabilities.admission import CapabilityRequest
from core.security.secrets import SecretStore
from core.space.kernel import SpaceKernel


class SpyPulseBus:
    def __init__(self) -> None:
        self.published: list[Pulse] = []

    def publish(self, pulse: Pulse) -> Pulse:
        self.published.append(pulse)
        return pulse


def test_secret_not_in_pulse_payload() -> None:
    """SECRET-003: Validator rejects any Pulse payload containing a resolved secret."""
    store = SecretStore()
    store.register("secret://vault/api_token", "SuperSecretTokenXYZ987")

    validator = PulseValidator(secret_store=store)

    # Payload with resolved secret in nested field
    leaking_payload = {
        "space_id": "space-1",
        "owner_id": "user-1",
        "metadata": {
            "auth": "Bearer SuperSecretTokenXYZ987"
        },
    }

    with pytest.raises(PulseRejectedError) as exc:
        validator.validate_secrets("space.created", leaking_payload)

    assert exc.value.reason == "secret_leak_detected"
    assert "SuperSecretTokenXYZ987" not in str(exc.value.details)  # Do not echo secret in error


def test_injection_canary_tainted_grant_denied() -> None:
    """TAINT-005: Tainted payload with embedded instructions cannot forge a security grant."""
    bus = SpyPulseBus()
    kernel = SpaceKernel(space_id="space-sec", owner_id="user-1", bus=bus, budget=100.0)

    # Capability request attempting a security grant under tainted context
    req = CapabilityRequest(
        requester_id="adversary-worker",
        space_id="space-sec",
        capability="security.grant.privilege_escalation",
        params={"role": "admin"},
    )

    # Dispatched with is_tainted=True
    resp = kernel.request_capability(req, is_tainted=True)
    assert resp.status == "denied"
    assert resp.error == "tainted_security_grant_blocked"

    # Verifies security.grant.denied emitted with critical severity and taint=True
    grants_denied = [p for p in bus.published if p.type == "security.grant.denied"]
    assert len(grants_denied) == 1
    assert grants_denied[0].taint is True
    assert grants_denied[0].severity == "critical"
    assert grants_denied[0].payload["capability"] == "security.grant.privilege_escalation"

