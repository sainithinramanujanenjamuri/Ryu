"""Unit tests for terminal taint boundary and relay input tagging.

spec §2, §4, ROADMAP Phase 8, ADR-0021 — Phase 8
"""

import io
from unittest.mock import MagicMock

from channels.cli.terminal import capture_input, is_stdin_interactive, render_approval_card
from core.space.approver import ApprovalRequest


def test_is_stdin_interactive_non_tty():
    stream = io.StringIO("hello\n")
    assert is_stdin_interactive(stream) is False


def test_capture_input_piped_is_tagged_tainted():
    stream = io.StringIO("y\n")
    out = io.StringIO()

    choice, is_tainted = capture_input(prompt="Approve? ", in_stream=stream, out_stream=out)
    assert choice == "y"
    # Piped input MUST be tagged as tainted (ADR-0021)
    assert is_tainted is True


def test_capture_input_interactive_is_clean():
    mock_stream = MagicMock()
    mock_stream.isatty.return_value = True
    mock_stream.readline.return_value = "yes\n"
    out = io.StringIO()

    choice, is_tainted = capture_input(prompt="Approve? ", in_stream=mock_stream, out_stream=out)
    assert choice == "yes"
    # Real interactive tty input is clean
    assert is_tainted is False


def test_render_approval_card():
    req = ApprovalRequest(
        request_id="app-card-1",
        space_id="space-card",
        capability="fs.delete",
        approver_id="alice",
        taint=True,
        summary="Delete critical file",
    )
    out = io.StringIO()
    render_approval_card(req, out_stream=out)
    val = out.getvalue()
    assert "HUMAN APPROVAL GATE INSPECTION" in val
    assert "fs.delete" in val
    assert "[TAINTED]" in val
    assert "WARNING" in val

