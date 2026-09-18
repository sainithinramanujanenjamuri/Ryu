"""Future harness cases: Space Kernel admission control, budget enforcement, plan versioning.

All cases skip — Phase 2.

spec §4 (Admission Control), CONTRACT_MATRIX KERNEL-001 through KERNEL-007,
PLAN-001 through PLAN-006 — Phase 2
"""

import pytest


def test_kernel_pre_dispatch_admission() -> None:
    pytest.skip("spec §4, KERNEL-001 — Phase 2: Admission Control not implemented.")


def test_kernel_hard_stop_at_zero_budget() -> None:
    pytest.skip("spec §4, KERNEL-002 — Phase 2: Budget enforcement not implemented.")


def test_kernel_single_escalation_per_window() -> None:
    pytest.skip("spec §4, KERNEL-003 — Phase 2: window_id enforcement not implemented.")


def test_plan_cas_versioning() -> None:
    pytest.skip("spec §16, PLAN-001 — Phase 2: Plan CAS not implemented.")


def test_plan_superseded_notification() -> None:
    pytest.skip("spec §16, PLAN-003 — Phase 2: plan.version.superseded not implemented.")

