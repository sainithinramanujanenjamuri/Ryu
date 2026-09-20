"""CLI space command: list and inspect spaces.

spec §2, §4, ROADMAP Phase 8, CLI-006 — Phase 8
"""

from __future__ import annotations

import argparse
from typing import Any

from channels.cli.context import CLIContext
from channels.cli.formatters import BOLD, RESET, format_table


def register_space_parser(subparsers: argparse._SubParsersAction[Any]) -> None:
    parser = subparsers.add_parser("space", help="Manage and inspect Spaces")
    space_subs = parser.add_subparsers(dest="space_subcommand", required=True)

    # space list
    list_p = space_subs.add_parser("list", help="List registered Spaces")
    list_p.set_defaults(handler=execute_space_list)

    # space inspect <space_id>
    inspect_p = space_subs.add_parser("inspect", help="Inspect details of a Space")
    inspect_p.add_argument("space_id", help="Space identifier")
    inspect_p.set_defaults(handler=execute_space_inspect)


def execute_space_list(args: argparse.Namespace, ctx: CLIContext) -> int:
    """Execute 'space list'."""
    spaces: list[dict[str, Any]] = []
    if ctx.spaces_registry:
        for s_id, data in ctx.spaces_registry.items():
            spaces.append({
                "space_id": s_id,
                "status": data.get("status", "active"),
                "approver": data.get("approver", "human_operator"),
            })
    elif ctx.kernel is not None:
        approver = "human_operator"
        if hasattr(ctx.kernel, "approver_id"):
            approver = ctx.kernel.approver_id
        spaces.append({
            "space_id": ctx.kernel.space_id,
            "status": "active",
            "approver": approver,
        })
    else:
        spaces.append({
            "space_id": "default",
            "status": "active",
            "approver": "human_operator",
        })

    if ctx.json_output:
        ctx.write_json(spaces)
        return 0

    headers = ["SPACE ID", "STATUS", "DESIGNATED APPROVER"]
    rows = [[s["space_id"], s["status"], s["approver"]] for s in spaces]
    ctx.write_out(format_table(headers, rows))
    return 0


def execute_space_inspect(args: argparse.Namespace, ctx: CLIContext) -> int:
    """Execute 'space inspect <space_id>'."""
    s_id = args.space_id
    info: dict[str, Any] = {"space_id": s_id, "status": "active"}

    if ctx.kernel is not None and ctx.kernel.space_id == s_id:
        info["plan_version"] = ctx.kernel.get_plan_version() if hasattr(ctx.kernel, "get_plan_version") else 1
        info["approver_id"] = getattr(ctx.kernel, "approver_id", "human_operator")
        if hasattr(ctx.kernel, "attention"):
            info["attention_saturated"] = ctx.kernel.attention.is_saturated(s_id)
            info["attention_report"] = ctx.kernel.attention.get_state_report(s_id)
    elif ctx.spaces_registry and s_id in ctx.spaces_registry:
        info.update(ctx.spaces_registry[s_id])
    else:
        info["approver_id"] = "human_operator"

    if ctx.approval_client is not None:
        pending = ctx.approval_client.list_pending(s_id)
        info["pending_approvals_count"] = len(pending)

    if ctx.json_output:
        ctx.write_json(info)
        return 0

    ctx.write_out(f"{BOLD}Space Details: {s_id}{RESET}")
    for k, v in info.items():
        ctx.write_out(f"  {k}: {v}")
    return 0

