"""Future harness cases: Space isolation and boundary contracts.

All cases in this file skip — they are not implemented in Phase 0.
They will be activated in Phase 2 when the Space Kernel is built.

spec §4 (Space Kernel), CONTRACT_MATRIX SPACE-001 through SPACE-006 — Phase 2
"""

import pytest


def test_space_isolation_cross_space_access_denied() -> None:
    pytest.skip("spec §4, SPACE-001 — Phase 2: Space Kernel not implemented.")


def test_space_resource_isolation() -> None:
    pytest.skip("spec §4, SPACE-002 — Phase 2/3: Resource Manager not implemented.")


def test_space_agent_isolation() -> None:
    pytest.skip("spec §4, SPACE-003 — Phase 2/4: Space Kernel and Agent runtime not implemented.")


def test_space_artifact_isolation() -> None:
    pytest.skip("spec §4, SPACE-004 — Phase 2+: Space Memory not implemented.")


def test_space_subscription_isolation() -> None:
    pytest.skip("spec §4, SPACE-005 — Phase 1/2: Durable Pulse Bus not implemented.")

