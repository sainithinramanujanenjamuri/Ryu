"""Harness case: Schema fuzzing — PULSE-010.

Tests contract validation with generated Pulse payloads.
- CI smoke mode: 1,000 cases (default)
- Full mode: 100,000 cases (set RYU_FUZZ_FULL=1)

Architecture acceptance criterion:
  0 invalid Pulses admitted, 0 valid Pulses rejected.

spec §6 (Pulse Bus Nervous System), PULSE-010 — Phase 1
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

import pytest
from hypothesis import HealthCheck, given, seed, settings
from hypothesis import strategies as st
from ryu.pulse_bus.durable_bus import DurablePulseBus
from ryu.pulse_bus.pulse import Pulse, Severity
from ryu.pulse_bus.reject import PulseRejectedError
from ryu.pulse_bus.store import InMemoryPulseStore
from ryu.pulse_bus.transport import NoopTransport
from ryu.pulse_bus.validator import PulseValidator

is_full = os.environ.get("RYU_FUZZ_FULL") == "1"
max_examples = 100000 if is_full else 1000

validator = PulseValidator()
valid_types = list(validator.known_types())


@pytest.fixture
def bus() -> DurablePulseBus:
    return DurablePulseBus(InMemoryPulseStore(), NoopTransport(), validator)


# Test: valid Pulse type + random payloads (most should fail schema validation)
@given(
    ptype=st.sampled_from(valid_types),
    payload=st.dictionaries(st.text(), st.text()),  # most will fail
)
@settings(
    max_examples=max_examples,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
    deadline=None,
)
@seed(42)
def test_fuzz_payload_rejection(bus: DurablePulseBus, ptype: str, payload: dict[str, Any]) -> None:
    """Random payloads against valid types: must either pass or raise PulseRejectedError."""
    p = Pulse(
        id="fuzz",
        space_id="s1",
        type=ptype,
        severity=Severity.INFO,
        source="test",
        timestamp=datetime.now(timezone.utc),
        payload=payload,
        taint=False,
        correlation_id="c1",
        parent_pulse_id=None,
    )
    # These are expected to fail most of the time due to missing required fields.
    # The invariant: no exception other than PulseRejectedError is allowed.
    try:
        bus.publish(p)
    except PulseRejectedError:
        pass  # expected


@given(
    ptype=st.text().filter(lambda t: t not in valid_types),
)
@settings(
    max_examples=max_examples,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
    deadline=None,
)
@seed(42)
def test_fuzz_unknown_types(bus: DurablePulseBus, ptype: str) -> None:
    """Unknown Pulse types must always raise PulseRejectedError — 0 admitted."""
    p = Pulse(
        id="fuzz",
        space_id="s1",
        type=ptype,
        severity=Severity.INFO,
        source="test",
        timestamp=datetime.now(timezone.utc),
        payload={},
        taint=False,
        correlation_id="c1",
        parent_pulse_id=None,
    )
    with pytest.raises(PulseRejectedError):
        bus.publish(p)
