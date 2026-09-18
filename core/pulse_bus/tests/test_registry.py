"""Unit tests: PulseValidator registry type checking.

Tests that known types are accepted and unknown types are rejected
with PulseRejectedError BEFORE any append.

spec §16 (Pulse Type Registry), PULSE-001, PULSE-012 — Phase 0
"""

import pytest
from ryu.pulse_bus.reject import PulseRejectedError
from ryu.pulse_bus.validator import PulseValidator


@pytest.fixture()
def validator() -> PulseValidator:
    return PulseValidator()


def test_known_types_loaded(validator: PulseValidator) -> None:
    """Registry must load at least 38 known types."""
    known = validator.known_types()
    assert len(known) == 38, f"Expected 38 types, got {len(known)}: {sorted(known)}"


def test_valid_type_accepted(validator: PulseValidator) -> None:
    """A registered type does not raise."""
    validator.validate_type("space.created")  # must not raise


def test_all_registered_types_accepted(validator: PulseValidator) -> None:
    """Every type in the registry must pass validate_type."""
    for ptype in validator.known_types():
        validator.validate_type(ptype)  # must not raise


def test_unknown_type_raises(validator: PulseValidator) -> None:
    """An unregistered type must raise PulseRejectedError."""
    with pytest.raises(PulseRejectedError) as exc_info:
        validator.validate_type("tool.faild")
    err = exc_info.value
    assert err.reason == "unknown_type"
    assert err.offending_type == "tool.faild"


def test_rejection_error_exposes_required_fields(validator: PulseValidator) -> None:
    """PulseRejectedError must expose reason, offending_type, and details."""
    with pytest.raises(PulseRejectedError) as exc_info:
        validator.validate_type("nonexistent.fake.type")
    err = exc_info.value
    assert hasattr(err, "reason")
    assert hasattr(err, "offending_type")
    assert hasattr(err, "details")
    assert err.offending_type == "nonexistent.fake.type"


def test_rate_limited_type_registered(validator: PulseValidator) -> None:
    """rate.limited must be registered (severity info per ROADMAP Phase 0 §98)."""
    validator.validate_type("rate.limited")  # must not raise
    assert "rate.limited" in validator.known_types()

