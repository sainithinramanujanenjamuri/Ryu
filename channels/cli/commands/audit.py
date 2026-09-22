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
    stream_p.add_argument("--follow", "-f", action="store_true", help="Continuously tail live pulse events")
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

    if ctx.json_output and not args.follow:
        ctx.write_json(pulses)
        return 0

    headers = ["PULSE ID", "SPACE ID", "TYPE", "SEVERITY", "SOURCE", "TAINT"]
    if pulses:
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
    elif not args.follow:
        ctx.write_out("No audit pulses found matching criteria.")
        return 0

    if args.follow:
        ctx.write_out("\n--- Streaming live pulses (Ctrl+C to stop) ---")
        import queue
        import time

        event_q: queue.Queue[Any] = queue.Queue()

        def on_pulse(p: Any) -> None:
            if args.space_id and getattr(p, "space_id", None) != args.space_id:
                return
            if args.type and not getattr(p, "type", "").startswith(args.type):
                return
            event_q.put(p)

        sub = None
        if ctx.bus is not None and hasattr(ctx.bus, "subscribe"):
            sub = ctx.bus.subscribe(on_pulse)

        try:
            while True:
                try:
                    p = event_q.get(timeout=0.5)
                    p_id = p.id[:12] if len(p.id) > 12 else p.id
                    p_type = p.type[:28] if len(p.type) > 28 else p.type
                    sev_str = p.severity.value if hasattr(p.severity, "value") else str(p.severity)
                    row = [p_id, p.space_id, p_type, sev_str.upper(), p.source, color_taint(p.taint)]
                    ctx.write_out(f"{row[0]:<14} {row[1]:<12} {row[2]:<30} {row[3]:<10} {row[4]:<15} {row[5]}")
                except queue.Empty:
                    continue
        except (KeyboardInterrupt, SystemExit):
            ctx.write_out("\nStream stopped.")
        finally:
            if sub is not None and ctx.bus is not None and hasattr(ctx.bus, "unsubscribe"):
                try:
                    ctx.bus.unsubscribe(sub)
                except Exception:
                    pass

    return 0

