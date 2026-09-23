"""Harness Case: MEM-002 Experience schema enforcement.

Acceptance Criterion:
The Reflector cannot store an Experience record without situation, action, outcome,
counterfactual, applicable_context, and stored_at. Records lacking counterfactual are rejected.

spec §4 (Space Memory), §16 (experience.stored), CONTRACT_MATRIX MEM-002 — Phase 10
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from ryu.pulse_bus.bus import PulseBus
from ryu.pulse_bus.pulse import Pulse, Severity
from ryu.pulse_bus.reject import PulseRejectedError

from core.space.memory_protocol import ExperienceRecord


def test_experience_requires_counterfactual() -> None:
    """MEM-002 Layer 1: Dataclass rejects Experience without counterfactual."""
    now = datetime.now(timezone.utc)
    with pytest.raises(ValueError, match="counterfactual must not be empty"):
        ExperienceRecord(
            experience_id="exp-no-cf",
            space_id="space-schema",
            situation={"task": "index data"},
            action={"capability": "fs.read"},
            outcome="Failed",
            counterfactual="",
            applicable_context={},
            stored_at=now,
        )


def test_experience_whitespace_counterfactual_rejected() -> None:
    """MEM-002 Layer 1: Dataclass rejects whitespace-only counterfactual."""
    now = datetime.now(timezone.utc)
    with pytest.raises(ValueError, match="counterfactual must not be empty"):
        ExperienceRecord(
            experience_id="exp-ws-cf",
            space_id="space-schema",
            situation={},
            action={},
            outcome="Failed",
            counterfactual="   \t\n  ",
            applicable_context={},
            stored_at=now,
        )


def test_experience_pulse_bus_validation() -> None:
    """MEM-002 Layer 2: Pulse Bus schema validation rejects experience.stored without counterfactual."""
    bus = PulseBus()
    now = datetime.now(timezone.utc)

    # Malformed payload: missing 'counterfactual'
    bad_payload = {
        "experience_id": "exp-bus-reject",
        "situation": {"state": "error"},
        "action": {"capability": "net.http"},
        "outcome": "Connection refused",
        # "counterfactual" omitted
        "applicable_context": {},
        "stored_at": now.isoformat(),
    }

    bad_pulse = Pulse(
        id="pulse-bad-exp-1",
        space_id="space-schema",
        type="experience.stored",
        severity=Severity.INFO,
        source="test_harness",
        payload=bad_payload,
        timestamp=now,
        correlation_id="corr-bad-exp-1",
    )

    with pytest.raises(PulseRejectedError):
        bus.publish(bad_pulse)


def test_experience_all_fields_accepted() -> None:
    """MEM-002 Layer 2: Valid 6-field experience.stored Pulse is accepted and published."""
    bus = PulseBus()
    now = datetime.now(timezone.utc)

    valid_payload = {
        "experience_id": "exp-bus-valid",
        "situation": {"target": "gateway"},
        "action": {"capability": "net.http"},
        "outcome": "Success",
        "counterfactual": "Continue monitoring heartbeat",
        "applicable_context": {"latency_ms": 12},
        "stored_at": now.isoformat(),
    }

    valid_pulse = Pulse(
        id="pulse-valid-exp-1",
        space_id="space-schema",
        type="experience.stored",
        severity=Severity.INFO,
        source="test_harness",
        payload=valid_payload,
        timestamp=now,
        correlation_id="corr-valid-exp-1",
    )

    published = bus.publish(valid_pulse)
    assert published.id == "pulse-valid-exp-1"
