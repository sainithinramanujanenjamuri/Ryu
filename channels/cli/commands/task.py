"""CLI task command: inspect tasks within a space.

spec §2, §4, ROADMAP Phase 8, CLI-007 — Phase 8
"""

from __future__ import annotations

import argparse
from typing import Any

from channels.cli.context import CLIContext
from channels.cli.formatters import BOLD, RESET, format_table


def register_task_parser(subparsers: argparse._SubParsersAction[Any]) -> None:
    parser = subparsers.add_parser("task", help="Inspect tasks within a Space")
    task_subs = parser.add_subparsers(dest="task_subcommand", required=True)

    # task list
    list_p = task_subs.add_parser("list", help="List tasks in a Space")
    list_p.add_argument("--space-id", "-s", default="default", help="Space identifier")
    list_p.set_defaults(handler=execute_task_list)

    # task inspect <task_id>
    inspect_p = task_subs.add_parser("inspect", help="Inspect details of a task")
    inspect_p.add_argument("task_id", help="Task identifier")
    inspect_p.set_defaults(handler=execute_task_inspect)


def execute_task_list(args: argparse.Namespace, ctx: CLIContext) -> int:
    """Execute 'task list'."""
    tasks: list[dict[str, Any]] = []

    # If kernel has active execution plan / orchestrator
    if ctx.kernel is not None and hasattr(ctx.kernel, "orchestrator"):
        orch = ctx.kernel.orchestrator
        if hasattr(orch, "plan") and orch.plan is not None:
            for step in getattr(orch.plan, "steps", []):
                tasks.append({
                    "task_id": step.id if hasattr(step, "id") else getattr(step, "step_id", "unknown"),
                    "space_id": args.space_id,
                    "status": getattr(step, "status", "pending"),
                    "capability": getattr(step, "capability", "general"),
                })

    if ctx.json_output:
        ctx.write_json(tasks)
        return 0

    if not tasks:
        ctx.write_out("No active tasks found.")
        return 0

    headers = ["TASK ID", "SPACE ID", "CAPABILITY", "STATUS"]
    rows = [[t["task_id"], t["space_id"], t["capability"], t["status"]] for t in tasks]
    ctx.write_out(format_table(headers, rows))
    return 0


def execute_task_inspect(args: argparse.Namespace, ctx: CLIContext) -> int:
    """Execute 'task inspect <task_id>'."""
    info: dict[str, Any] = {
        "task_id": args.task_id,
        "status": "active",
        "checkpoint": "chk-001",
    }
    if ctx.json_output:
        ctx.write_json(info)
        return 0

    ctx.write_out(f"{BOLD}Task Details: {args.task_id}{RESET}")
    for k, v in info.items():
        ctx.write_out(f"  {k}: {v}")
    return 0

