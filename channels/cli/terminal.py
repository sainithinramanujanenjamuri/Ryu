"""CLI Terminal interactions and untrusted relay boundary taint tagging.

Enforces ADR-0021: Interactive CLI input from a real tty is clean, but any input
sourced from non-interactive stdin (pipes, redirects, automated relays) is strictly
tagged as tainted (taint=True).

spec §2, §4, ROADMAP Phase 8, ADR-0021 — Phase 8
"""

from __future__ import annotations

import sys
from typing import TextIO

from channels.cli.formatters import BOLD, RED, RESET, color_status, color_taint
from core.space.approver import ApprovalRequest


def is_stdin_interactive(stream: TextIO | None = None) -> bool:
    """Check if standard input is an interactive terminal."""
    target = stream if stream is not None else sys.stdin
    try:
        return target.isatty()
    except Exception:
        return False


def render_approval_card(
    approval: ApprovalRequest, out_stream: TextIO | None = None
) -> None:
    """Render a formatted card showing capability details for human inspection."""
    out = out_stream if out_stream is not None else sys.stdout
    lines = [
        f"{BOLD}================================================================{RESET}",
        f"{BOLD}HUMAN APPROVAL GATE INSPECTION{RESET}",
        f"{BOLD}================================================================{RESET}",
        f"  Approval ID:     {approval.approval_id}",
        f"  Space ID:        {approval.space_id}",
        f"  Capability:      {BOLD}{approval.capability}{RESET}",
        f"  Risk Tier:       {approval.risk_tier.upper()}",
        f"  Taint Status:    {color_taint(approval.taint)}",
        f"  Lifecycle State: {color_status(approval.status)}",
        f"  Attention Queue: {approval.queue_state.upper()}",
        f"  Plan Version:    {approval.plan_version}",
        f"  Requester ID:    {approval.requester_id}",
        f"  Request Hash:    {approval.capability_request_hash}",
        f"  Timeout Policy:  {approval.timeout_class} ({approval.timeout_seconds:.1f}s)",
        f"  Summary:         {approval.summary}",
    ]
    if approval.taint:
        lines.append(
            f"  {RED}{BOLD}WARNING: This operation originates from an untrusted / tainted context!{RESET}"
        )
    lines.append(f"{BOLD}================================================================{RESET}")
    out.write("\n".join(lines) + "\n")
    out.flush()


def capture_input(
    prompt: str = "Decision [y/N]: ",
    in_stream: TextIO | None = None,
    out_stream: TextIO | None = None,
) -> tuple[str, bool]:
    """
    Capture user response and report whether the input stream is tainted.

    Returns:
        tuple[str, bool]: (cleaned_input, is_tainted)
        If in_stream is not a tty (e.g. piped or redirected), is_tainted=True (ADR-0021).
    """
    inp = in_stream if in_stream is not None else sys.stdin
    out = out_stream if out_stream is not None else sys.stdout

    is_interactive = is_stdin_interactive(inp)
    is_tainted = not is_interactive

    if is_interactive:
        out.write(prompt)
        out.flush()

    line = inp.readline()
    if not line:
        return "", is_tainted
    return line.strip(), is_tainted

