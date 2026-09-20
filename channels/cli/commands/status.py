"""CLI status command: display runtime health, spaces, saturation, and pending gates.

spec §2, §4, ROADMAP Phase 8, CLI-001 — Phase 8
"""

from __future__ import annotations

import argparse
from typing import Any

from channels.cli.context import CLIContext
from channels.cli.formatters import BOLD, GREEN, RED, RESET, format_table


def register_status_parser(subparsers: argparse._SubParsersAction[Any]) -> None:
    parser = subparsers.add_parser("status", help="Show RYU AI runtime status and attention budgets")
    parser.set_defaults(handler=execute_status)


def execute_status(args: argparse.Namespace, ctx: CLIContext) -> int:
    """Execute status command."""
    spaces_info: list[dict[str, Any]] = []

    # If kernel is present, inspect active spaces
    if ctx.kernel is not None:
        spaces = [ctx.kernel.space_id] if hasattr(ctx.kernel, "space_id") else []
        for s_id in spaces:
            saturated = False
            if hasattr(ctx.kernel, "attention") and hasattr(ctx.kernel.attention, "is_saturated"):
                saturated = ctx.kernel.attention.is_saturated(s_id)
            pending_count = 0
            if ctx.approval_client is not None:
                pending = ctx.approval_client.list_pending(s_id)
                pending_count = len(pending)
            spaces_info.append({
                "space_id": s_id,
                "attention_saturated": saturated,
                "pending_approvals": pending_count,
            })
    elif ctx.spaces_registry:
        for s_id, s_data in ctx.spaces_registry.items():
            spaces_info.append({
                "space_id": s_id,
                "status": s_data.get("status", "active"),
                "pending_approvals": s_data.get("pending_approvals", 0),
            })
    else:
        # Generic status
        spaces_info.append({
            "space_id": "default",
            "attention_saturated": False,
            "pending_approvals": 0,
        })

    payload = {
        "status": "online",
        "spaces_count": len(spaces_info),
        "spaces": spaces_info,
    }

    if ctx.json_output:
        ctx.write_json(payload)
        return 0

    ctx.write_out(f"{BOLD}RYU AI Runtime Status:{RESET} {GREEN}ONLINE{RESET}")
    ctx.write_out(f"Active Spaces: {len(spaces_info)}\n")

    headers = ["SPACE ID", "ATTENTION BUDGET", "PENDING GATES"]
    rows = []
    for s in spaces_info:
        sat = s.get("attention_saturated", False)
        sat_text = f"{RED}SATURATED{RESET}" if sat else f"{GREEN}NORMAL{RESET}"
        rows.append([
            s["space_id"],
            sat_text,
            str(s.get("pending_approvals", 0)),
        ])

    ctx.write_out(format_table(headers, rows))
    return 0

