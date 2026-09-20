"""CLI audit command: inspect immutable pulse audit trail.

spec §2, §4, §16, ROADMAP Phase 8, CLI-008 — Phase 8
"""

from __future__ import annotations

import argparse
from typing import Any

from channels.cli.context import CLIContext
from channels.cli.formatters import color_taint, format_table


def register_audit_parser(subparsers: argparse._SubParsersAction[Any]) -> None:
    parser = subparsers.add_parser("audit", help="Inspect pulse audit trail and event stream")
    audit_subs = parser.add_subparsers(dest="audit_subcommand", required=True)

    # audit stream
    stream_p = audit_subs.add_parser("stream", help="Stream or list audit pulses")
    stream_p.add_argument("--space-id", "-s", help="Filter by space ID")
    stream_p.add_argument("--type", "-t", help="Filter by pulse type prefix")
    stream_p.add_argument("--limit", "-l", type=int, default=50, help="Maximum number of pulses")
    stream_p.set_defaults(handler=execute_audit_stream)


def execute_audit_stream(args: argparse.Namespace, ctx: CLIContext) -> int:
    """Execute 'audit stream'."""
    pulses = []

    # Read from pulse store if available
    store = ctx.pulse_store
    if store is None and ctx.bus is not None and hasattr(ctx.bus, "store"):
        store = ctx.bus.store

    if store is not None:
        if args.space_id and hasattr(store, "read_by_space"):
            pulses = store.read_by_space(args.space_id)
        elif hasattr(store, "read"):
            pulses = store.read(0)

    if args.type:
        pulses = [p for p in pulses if p.type.startswith(args.type)]

    if args.limit and len(pulses) > args.limit:
        pulses = pulses[-args.limit:]

    if ctx.json_output:
        ctx.write_json(pulses)
        return 0

    if not pulses:
        ctx.write_out("No audit pulses found matching criteria.")
        return 0

    headers = ["PULSE ID", "SPACE ID", "TYPE", "SEVERITY", "SOURCE", "TAINT"]
    rows = []
    for p in pulses:
        p_id = p.id[:12] if len(p.id) > 12 else p.id
        p_type = p.type[:28] if len(p.type) > 28 else p.type
        sev_str = p.severity.value if hasattr(p.severity, "value") else str(p.severity)
        rows.append([
            p_id,
            p.space_id,
            p_type,
            sev_str.upper(),
            p.source,
            color_taint(p.taint),
        ])

    ctx.write_out(format_table(headers, rows))
    return 0

