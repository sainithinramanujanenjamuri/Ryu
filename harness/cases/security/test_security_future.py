"""Future harness cases: Taint clearance audit, injection defense, grant forgery.

All cases skip — Phase 1/2/6.

spec §10 (Prompt-Injection & Taint Model), CONTRACT_MATRIX TAINT-001 through TAINT-006 — Phase 1/2/6
"""

import pytest


def test_taint_clearance_forward_only_audit() -> None:
    pytest.skip("spec §10, TAINT-003/004 — Phase 1/2: Durable taint-clear audit not implemented.")


def test_taint_grant_protection() -> None:
    pytest.skip("spec §10, TAINT-005 — Phase 2/6: Admission Control not implemented.")


def test_injection_canary_tainted_grant_denied() -> None:
    pytest.skip("spec §10, TAINT-005 — Phase 2/6: Injection canary requires Admission Control.")


def test_secret_not_in_pulse_payload() -> None:
    pytest.skip("spec §10, SECRET-003 — Phase 2: Secret containment validation not implemented.")

