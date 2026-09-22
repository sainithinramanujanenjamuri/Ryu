"""CLI prompt command: submit natural language instructions and goals to a space.

spec §2, §4, §18, ROADMAP Phase 8.5
"""

from __future__ import annotations

import argparse
from typing import Any

from channels.cli.context import CLIContext


def register_prompt_parser(subparsers: argparse._SubParsersAction[Any]) -> None:
    parser = subparsers.add_parser("prompt", help="Submit natural language goal to a space")
    parser.add_argument("objective", nargs="+", help="Natural language prompt or goal objective")
    parser.add_argument("--space-id", "-s", default="default", help="Target Space ID")
    parser.set_defaults(handler=execute_prompt)


def execute_prompt(args: argparse.Namespace, ctx: CLIContext) -> int:
    """Execute prompt command."""
    prompt_text = " ".join(args.objective)
    from channels.cli.shell import RyuInteractiveShell

    shell = RyuInteractiveShell(ctx=ctx, space_id=args.space_id)
    shell.handle_prompt(prompt_text)
    return 0

