"""Future harness cases: Security contracts scheduled for Phase 6 (Sandbox execution).

spec §10 (Prompt-Injection & Taint Model), CONTRACT_MATRIX TAINT-006, SECRET-004 — Phase 6
"""

from __future__ import annotations

import pytest


def test_sandbox_syscall_filter() -> None:
    pytest.skip("spec §10, SECRET-004 — Phase 6: Sandbox seccomp filter not implemented.")


def test_taint_data_instruction_separation_at_worker() -> None:
    pytest.skip("spec §10, TAINT-006 — Phase 6: Worker boundary not implemented.")
