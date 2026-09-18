"""Future harness cases: Worker sandbox, tool isolation, injection canary.

All cases skip — Phase 6.

spec §7 (Execution Layer), CONTRACT_MATRIX WORKER-001 through WORKER-005 — Phase 6
"""

import pytest


def test_worker_tool_isolation() -> None:
    pytest.skip("spec §7, WORKER-001 — Phase 6: Worker execution not implemented.")


def test_worker_sandbox_escape_denied() -> None:
    pytest.skip("spec §7, WORKER-003 — Phase 6: Sandbox not implemented.")


def test_worker_injection_canary() -> None:
    pytest.skip("spec §10, WORKER-002 — Phase 6: Data/instruction separation not implemented.")

