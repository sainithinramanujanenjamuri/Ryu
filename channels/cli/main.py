"""RYU AI CLI Channel: entrypoint, router, and argument parsing.

spec §2, §4, ROADMAP Phase 8, CLI-001 through CLI-008 — Phase 8
"""

from __future__ import annotations

import argparse
import sys
from typing import NoReturn

from channels.cli.commands.approval import register_approval_parser
from channels.cli.commands.audit import register_audit_parser
from channels.cli.commands.prompt import register_prompt_parser
from channels.cli.commands.space import register_space_parser
from channels.cli.commands.status import register_status_parser
from channels.cli.commands.task import register_task_parser
from channels.cli.context import CLIContext, create_default_context

EXIT_SUCCESS = 0
EXIT_GENERAL_ERROR = 1
EXIT_SYNTAX_ERROR = 2
EXIT_AUTH_FAILURE = 3
EXIT_TIMEOUT_OR_HELD = 4
EXIT_SECURITY_OR_REPLAY = 5


class CLIArgumentParser(argparse.ArgumentParser):
    """Custom parser that raises an exception on syntax errors instead of sys.exit."""

    def error(self, message: str) -> NoReturn:
        raise SyntaxError(message)


def create_parser() -> argparse.ArgumentParser:
    """Construct the top-level CLI argument parser."""
    parser = CLIArgumentParser(
        prog="ryu",
        description="RYU AI Cognitive Architecture Command Line Interface",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Format output as JSON",
    )

    subparsers = parser.add_subparsers(dest="subcommand", required=True)
    register_status_parser(subparsers)
    register_space_parser(subparsers)
    register_approval_parser(subparsers)
    register_task_parser(subparsers)
    register_audit_parser(subparsers)
    register_prompt_parser(subparsers)

    shell_p = subparsers.add_parser("shell", help="Launch interactive developer shell")
    shell_p.set_defaults(handler=lambda args, ctx: _launch_shell(ctx))

    return parser


def _launch_shell(ctx: CLIContext) -> int:
    from channels.cli.shell import RyuInteractiveShell
    shell = RyuInteractiveShell(ctx=ctx)
    return shell.run()


def main(argv: list[str] | None = None, ctx: CLIContext | None = None) -> int:
    """Execute the CLI router and dispatch commands."""
    args_list = list(argv) if argv is not None else sys.argv[1:]
    context = ctx if ctx is not None else create_default_context()

    if "--json" in args_list:
        context.json_output = True
        args_list = [a for a in args_list if a != "--json"]

    # If invoked with no arguments and stdin is a TTY, launch interactive shell
    if len(args_list) == 0 and hasattr(context.in_stream, "isatty") and context.in_stream.isatty():
        return _launch_shell(context)

    # Normalize leading slash commands if passed directly via CLI
    if len(args_list) > 0 and args_list[0].startswith("/"):
        raw_slash = args_list[0].lower()
        if raw_slash == "/status":
            args_list = ["status"] + args_list[1:]
        elif raw_slash in ("/space", "/spaces"):
            args_list = ["space"] + (args_list[1:] if args_list[1:] else ["list"])
        elif raw_slash in ("/approval", "/approvals"):
            args_list = ["approval"] + (args_list[1:] if args_list[1:] else ["list"])
        elif raw_slash in ("/task", "/tasks"):
            args_list = ["task"] + (args_list[1:] if args_list[1:] else ["list"])
        elif raw_slash in ("/stream", "/audit"):
            args_list = ["audit", "stream"] + args_list[1:]
        elif raw_slash == "/prompt":
            args_list = ["prompt"] + args_list[1:]
        elif raw_slash == "/help":
            args_list = ["--help"]

    parser = create_parser()
    try:
        args = parser.parse_args(args_list)
    except SyntaxError as e:
        context.write_err(f"Usage error: {e}")
        return EXIT_SYNTAX_ERROR
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else EXIT_SYNTAX_ERROR

    if hasattr(args, "handler"):
        res = args.handler(args, context)
        return int(res) if isinstance(res, int) else EXIT_SUCCESS

    context.write_err("Error: No handler registered for subcommand.")
    return EXIT_SYNTAX_ERROR


if __name__ == "__main__":
    sys.exit(main())
