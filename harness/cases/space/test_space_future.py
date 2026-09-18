"""Future harness cases: Space boundary contracts scheduled for Phase 3+.

spec §4 (Space Kernel), CONTRACT_MATRIX SPACE-002, SPACE-003, SPACE-004 — Phase 3+
"""

from __future__ import annotations

import pytest


def test_space_resource_isolation() -> None:
    pytest.skip("spec §4, SPACE-002 — Phase 3: Resource Manager not implemented.")


def test_space_agent_isolation() -> None:
    pytest.skip("spec §4, SPACE-003 — Phase 4: Space Kernel and Agent runtime not implemented.")


def test_space_artifact_isolation() -> None:
    pytest.skip("spec §4, SPACE-004 — Phase 10: Space Memory not implemented.")
