"""Unit tests: PulseBus payload validation and pre-append rejection.

Verifies that invalid payloads are rejected BEFORE append (PULSE-002, PULSE-003).
Nothing must enter the bus log when validation fails.

spec §16 (Pulse Bus), PULSE-002, PULSE-003 — Phase 0
"""

import pytest
from ryu.pulse_bus.bus import PulseBus
from ryu.pulse_bus.pulse import Pulse
from ryu.pulse_bus.reject import PulseRejectedError


def _valid_space_created_pulse(**overrides: object) -> Pulse:
    """Construct a minimal valid space.created pulse."""
    return Pulse(
        type="space.created",
        payload={"space_id": "space-001", "owner_id": "user-001"},
        space_id="space-001",
        source="test",
        correlation_id="corr-001",
        **overrides,  # type: ignore[arg-type]
    )


@pytest.fixture()
def bus() -> PulseBus:
    return PulseBus()


def test_valid_pulse_is_accepted(bus: PulseBus) -> None:
    """A fully valid pulse must be accepted and appear in the log."""
    pulse = _valid_space_created_pulse()
    bus.publish(pulse)
    assert bus.log_size() == 1
    assert bus.log()[0].id == pulse.id


def test_unknown_type_not_appended(bus: PulseBus) -> None:
    """Publishing an unknown type must raise PulseRejectedError and append nothing."""
    pulse = Pulse(
        type="tool.faild",  # typo — not registered
        payload={"error": "oops"},
        space_id="space-001",
        source="test",
        correlation_id="corr-001",
    )
    with pytest.raises(PulseRejectedError) as exc_info:
        bus.publish(pulse)

    assert exc_info.value.reason == "unknown_type"
    assert exc_info.value.offending_type == "tool.faild"
    # PULSE-003: nothing must have been appended
    assert bus.log_size() == 0, "Bus log must remain empty after rejection"


def test_invalid_payload_not_appended(bus: PulseBus) -> None:
    """A valid type with a missing required field must be rejected before append."""
    pulse = Pulse(
        type="space.created",
        payload={"space_id": "space-001"},  # missing required 'owner_id'
        space_id="space-001",
        source="test",
        correlation_id="corr-001",
    )
    with pytest.raises(PulseRejectedError) as exc_info:
        bus.publish(pulse)

    assert exc_info.value.reason == "invalid_payload"
    # PULSE-003: nothing appended
    assert bus.log_size() == 0, "Bus log must remain empty after payload rejection"


def test_multiple_valid_types_accepted(bus: PulseBus) -> None:
    """Multiple distinct valid pulses must all be admitted."""
    bus.publish(Pulse(
        type="space.created",
        payload={"space_id": "space-001", "owner_id": "user-001"},
        space_id="space-001", source="test", correlation_id="corr-001",
    ))
    bus.publish(Pulse(
        type="goal.defined",
        payload={
            "goal_id": "goal-001",
            "goal_spec": {"objective": "test"},
            "single_agent_eligible": True,
        },
        space_id="space-001", source="test", correlation_id="corr-001",
    ))
    assert bus.log_size() == 2


def test_rejection_does_not_affect_subsequent_valid_pulses(bus: PulseBus) -> None:
    """A rejected pulse must not corrupt bus state; subsequent valid pulses must succeed."""
    invalid = Pulse(
        type="tool.faild",
        payload={},
        space_id="space-001", source="test", correlation_id="corr-001",
    )
    with pytest.raises(PulseRejectedError):
        bus.publish(invalid)

    # Bus must still be functional
    valid = _valid_space_created_pulse()
    bus.publish(valid)
    assert bus.log_size() == 1


def test_subscriber_notified_on_valid_pulse(bus: PulseBus) -> None:
    """Subscribers must be called with the admitted pulse."""
    received: list[Pulse] = []
    bus.subscribe(received.append, pulse_type="space.created")

    pulse = _valid_space_created_pulse()
    bus.publish(pulse)

    assert len(received) == 1
    assert received[0].id == pulse.id


def test_subscriber_not_notified_on_rejected_pulse(bus: PulseBus) -> None:
    """Subscribers must NOT be called when a pulse is rejected."""
    received: list[Pulse] = []
    bus.subscribe(received.append)

    invalid = Pulse(
        type="tool.faild",
        payload={},
        space_id="space-001", source="test", correlation_id="corr-001",
    )
    with pytest.raises(PulseRejectedError):
        bus.publish(invalid)

    assert len(received) == 0

