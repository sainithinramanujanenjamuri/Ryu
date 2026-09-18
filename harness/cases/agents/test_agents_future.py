"""Future harness cases: Agent state machine, LLM boundary, context management.

All cases skip — Phase 5.

spec §7 (Cognitive Layer), CONTRACT_MATRIX AGENT-001 through AGENT-007 — Phase 5
"""

import pytest


def test_agent_state_machine() -> None:
    pytest.skip("spec §7, AGENT-001 — Phase 5: Agent state machine not implemented.")


def test_llm_boundary_not_in_core() -> None:
    pytest.skip(
        "spec (LLM Boundary), AGENT-002 — Phase 0/4: "
        "Enforced by dep-guard CI; runtime check deferred."
    )


def test_context_compaction_500_turns() -> None:
    pytest.skip("spec §12, AGENT-006 — Phase 5: Context Manager not implemented.")

