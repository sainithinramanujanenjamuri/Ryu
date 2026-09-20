"""CLI Execution Context: encapsulates dependencies, I/O streams, and output formatting.

spec §2, §4, ROADMAP Phase 8 — Phase 8
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Any, TextIO

from channels.approval.client import ApprovalClient
from channels.cli.formatters import format_json


@dataclass
class CLIContext:
    """Carries runtime dependencies and I/O streams for CLI commands."""

    approval_client: ApprovalClient | None = None
    bus: Any | None = None
    pulse_store: Any | None = None
    kernel: Any | None = None
    spaces_registry: dict[str, Any] | None = None
    json_output: bool = False
    out_stream: TextIO = sys.stdout
    err_stream: TextIO = sys.stderr
    in_stream: TextIO = sys.stdin

    def write_out(self, text: str) -> None:
        """Write text to configured stdout stream."""
        self.out_stream.write(text + "\n")
        self.out_stream.flush()

    def write_err(self, text: str) -> None:
        """Write text to configured stderr stream."""
        self.err_stream.write(text + "\n")
        self.err_stream.flush()

    def write_json(self, data: Any) -> None:
        """Write JSON serialized data to stdout stream."""
        self.out_stream.write(format_json(data) + "\n")
        self.out_stream.flush()

