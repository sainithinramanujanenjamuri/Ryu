"""Future harness cases: Orchestrator deterministic loop and plan cycle.

All cases skip — Phase 4.

spec §4 (Space Orchestrator), CONTRACT_MATRIX ORCH-001 through ORCH-007 — Phase 4
"""

import pytest


def test_orchestrator_full_deterministic_loop() -> None:
    pytest.skip("spec §4, ORCH-001/006 — Phase 4: Orchestrator not implemented.")


def test_orchestrator_goal_analyzer() -> None:
    pytest.skip("spec §4, ORCH-002 — Phase 4: Goal Analyzer not implemented.")


def test_orchestrator_planner() -> None:
    pytest.skip("spec §4, ORCH-003 — Phase 4: Planner not implemented.")


def test_orchestrator_core_independence() -> None:
    pytest.skip("spec §4, ORCH-007 — Phase 4: core-independence job not yet wired.")

