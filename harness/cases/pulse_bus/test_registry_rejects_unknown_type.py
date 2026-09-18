"""Harness case: Registry rejects unknown Pulse types.

Architecture acceptance criterion (docs/Architecture §6):
  "an unknown `type` is rejected synchronously by the Bus with a protocol-level
   error to the publisher, not admitted as a Pulse."

Verifies PULSE-001 and PULSE-012:
  - Publishing a Pulse with type "tool.faild" (intentional typo — unregistered)
    must raise PulseRejectedError.
  - The bus log must remain empty (nothing appended).

spec §6, §16 (Pulse Type Registry), CONTRACT_MATRIX PULSE-001, PULSE-012
ROADMAP Phase 0 §86 — harness/cases/pulse_bus/test_registry_rejects_unknown_type.py
"""

import pytest
from ryu.pulse_bus.bus import PulseBus
from ryu.pulse_bus.pulse import Pulse
from ryu.pulse_bus.reject import PulseRejectedError


@pytest.fixture()
def bus() -> PulseBus:
    return PulseBus()


def test_unknown_type_raises_pulse_rejected_error(bus: PulseBus) -> None:
    """
    Publishing an unregistered Pulse type must raise PulseRejectedError
    synchronously and not append anything to the log.

    This is the primary Phase 0 harness acceptance test.
    'tool.faild' is the exact typo from ROADMAP Phase 0 §86.
    """
    pulse = Pulse(
        type="tool.faild",
        payload={"error": "this type does not exist in the registry"},
        space_id="space-001",
        source="harness",
        correlation_id="corr-harness-001",
    )

    with pytest.raises(PulseRejectedError) as exc_info:
        bus.publish(pulse)

    err = exc_info.value
    assert err.reason == "unknown_type", f"Expected 'unknown_type', got {err.reason!r}"
    assert err.offending_type == "tool.faild", f"Expected 'tool.faild', got {err.offending_type!r}"
    assert bus.log_size() == 0, (
        "Bus log must be empty: the rejected pulse must not have been appended. "
        f"Got {bus.log_size()} entries."
    )


def test_rejection_error_exposes_all_required_fields(bus: PulseBus) -> None:
    """PulseRejectedError must expose reason, offending_type, and details."""
    pulse = Pulse(
        type="not.registered",
        payload={},
        space_id="space-001",
        source="harness",
        correlation_id="corr-harness-002",
    )
    with pytest.raises(PulseRejectedError) as exc_info:
        bus.publish(pulse)
    err = exc_info.value
    assert hasattr(err, "reason")
    assert hasattr(err, "offending_type")
    assert hasattr(err, "details")


def test_known_types_accepted_after_rejection(bus: PulseBus) -> None:
    """Bus must remain functional after a rejection — valid pulses must still succeed."""
    # Attempt invalid
    invalid = Pulse(
        type="tool.faild",
        payload={},
        space_id="space-001",
        source="harness",
        correlation_id="corr-harness-003",
    )
    with pytest.raises(PulseRejectedError):
        bus.publish(invalid)
    assert bus.log_size() == 0

    # Subsequent valid pulse must be accepted
    valid = Pulse(
        type="space.created",
        payload={"space_id": "space-001", "owner_id": "user-001"},
        space_id="space-001",
        source="harness",
        correlation_id="corr-harness-003",
    )
    bus.publish(valid)
    assert bus.log_size() == 1

