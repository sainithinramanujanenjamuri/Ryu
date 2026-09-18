"""Future harness cases: Space Kernel capabilities scheduled for Phase 3+.

spec §4 (Space Kernel), CONTRACT_MATRIX KERNEL-004 through KERNEL-007,
PLAN-005 through PLAN-006 — Phase 3+
"""

from __future__ import annotations

import pytest


def test_kernel_concurrency_lease_enforcement() -> None:
    pytest.skip("spec §4, KERNEL-004 — Phase 3: Lease enforcement not implemented.")


def test_kernel_agent_execution_supervision() -> None:
    pytest.skip("spec §4, KERNEL-005 — Phase 5: Agent execution supervision not implemented.")


def test_kernel_checkpoint_restore() -> None:
    pytest.skip("spec §4, KERNEL-006 — Phase 4: Checkpoint restoration not implemented.")
