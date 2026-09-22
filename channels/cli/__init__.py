"""CLI Channel: interactive command-line interface and human gate interactions.

spec §2, §4, ROADMAP Phase 8 — Phase 8
"""

from __future__ import annotations

from channels.cli.context import CLIContext
from channels.cli.main import (
    EXIT_AUTH_FAILURE,
    EXIT_GENERAL_ERROR,
    EXIT_SECURITY_OR_REPLAY,
    EXIT_SUCCESS,
    EXIT_SYNTAX_ERROR,
    EXIT_TIMEOUT_OR_HELD,
    main,
)

__all__ = [
    "CLIContext",
    "EXIT_AUTH_FAILURE",
    "EXIT_GENERAL_ERROR",
    "EXIT_SECURITY_OR_REPLAY",
    "EXIT_SUCCESS",
    "EXIT_SYNTAX_ERROR",
    "EXIT_TIMEOUT_OR_HELD",
    "main",
]
