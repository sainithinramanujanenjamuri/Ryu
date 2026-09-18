"""Future harness cases: Resource Manager leases, contention, chaos.

All cases skip — Phase 3.

spec §9 (Resource Manager), CONTRACT_MATRIX RESOURCE-001 through RESOURCE-008 — Phase 3
"""

import pytest


def test_resource_lease_issued() -> None:
    pytest.skip("spec §9, RESOURCE-001/002 — Phase 3: Resource Manager not implemented.")


def test_resource_no_double_grant() -> None:
    pytest.skip("spec §9, RESOURCE-005 — Phase 3: Concurrency enforcement not implemented.")


def test_resource_conflict_pulse_emitted() -> None:
    pytest.skip("spec §9, RESOURCE-006 — Phase 3: resource.conflict pulse not implemented.")


def test_rate_limited_severity_info() -> None:
    pytest.skip("spec §12, RESOURCE-008 — Phase 3: Rate Limiter not implemented.")

