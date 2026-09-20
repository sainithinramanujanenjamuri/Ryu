"""CLI approval command: list, inspect, approve, and reject human approval gates.

spec §2, §4, §16, ROADMAP Phase 8, CLI-002, CLI-003, CLI-004, CLI-005, HUMAN-001 — Phase 8
"""

from __future__ import annotations

import argparse
from typing import Any

from channels.approval.auth import (
    AuthenticationError,
    ClockSkewError,
    MalformedAuthenticationPayloadError,
    ReplayDetectedError,
)
from channels.cli.context import CLIContext
from channels.cli.formatters import (
    format_approvals_table,
)
from channels.cli.terminal import render_approval_card


def register_approval_parser(subparsers: argparse._SubParsersAction[Any]) -> None:
    parser = subparsers.add_parser("approval", help="Inspect and resolve human approval gates")
    app_subs = parser.add_subparsers(dest="approval_subcommand", required=True)

    # approval list
    list_p = app_subs.add_parser("list", help="List approval requests")
    list_p.add_argument("--space-id", "-s", default="default", help="Filter by space ID")
    list_p.add_argument("--status", choices=["pending", "approved", "denied", "expired", "held", "consumed"], help="Filter by status")
    list_p.add_argument("--queue-state", choices=["active", "queued", "resolved"], help="Filter by queue state")
    list_p.set_defaults(handler=execute_approval_list)

    # approval inspect <approval_id>
    inspect_p = app_subs.add_parser("inspect", help="Inspect an approval request in detail")
    inspect_p.add_argument("approval_id", help="Approval identifier")
    inspect_p.set_defaults(handler=execute_approval_inspect)

    # approval approve <approval_id>
    approve_p = app_subs.add_parser("approve", help="Approve a human gate request")
    approve_p.add_argument("approval_id", help="Approval identifier")
    approve_p.add_argument("--approver", "-a", required=True, help="Authenticated approver ID")
    approve_p.add_argument("--token", "-t", required=True, help="Approver secret token key material")
    approve_p.add_argument("--space-id", "-s", help="Space identifier (defaults to request's space)")
    approve_p.add_argument("--plan-version", "-v", type=int, help="Expected plan version")
    approve_p.add_argument("--hash", help="Expected capability request SHA256 hash")
    approve_p.add_argument("--nonce", help="Explicit nonce for wire protocol")
    approve_p.add_argument("--timestamp", type=int, help="Explicit unix timestamp")
    approve_p.set_defaults(handler=execute_approval_approve)

    # approval reject <approval_id>
    reject_p = app_subs.add_parser("reject", help="Reject/deny a human gate request")
    reject_p.add_argument("approval_id", help="Approval identifier")
    reject_p.add_argument("--approver", "-a", required=True, help="Authenticated approver ID")
    reject_p.add_argument("--token", "-t", required=True, help="Approver secret token key material")
    reject_p.add_argument("--reason", "-r", default="Rejected by human operator via CLI", help="Rejection reason")
    reject_p.add_argument("--space-id", "-s", help="Space identifier (defaults to request's space)")
    reject_p.add_argument("--plan-version", "-v", type=int, help="Expected plan version")
    reject_p.add_argument("--hash", help="Expected capability request SHA256 hash")
    reject_p.add_argument("--nonce", help="Explicit nonce for wire protocol")
    reject_p.add_argument("--timestamp", type=int, help="Explicit unix timestamp")
    reject_p.set_defaults(handler=execute_approval_reject)


def execute_approval_list(args: argparse.Namespace, ctx: CLIContext) -> int:
    """Execute 'approval list'."""
    if ctx.approval_client is None:
        ctx.write_err("Error: Approval client is unavailable.")
        return 1

    approvals = ctx.approval_client.list_approvals(
        space_id=args.space_id,
        status=args.status,
        queue_state=args.queue_state,
    )

    if ctx.json_output:
        ctx.write_json(approvals)
        return 0

    ctx.write_out(format_approvals_table(approvals))
    return 0


def execute_approval_inspect(args: argparse.Namespace, ctx: CLIContext) -> int:
    """Execute 'approval inspect <approval_id>'."""
    if ctx.approval_client is None:
        ctx.write_err("Error: Approval client is unavailable.")
        return 1

    req = ctx.approval_client.get_request(args.approval_id)
    if req is None:
        ctx.write_err(f"Error: Approval request '{args.approval_id}' not found.")
        return 1

    if ctx.json_output:
        ctx.write_json(req)
        return 0

    render_approval_card(req, out_stream=ctx.out_stream)
    return 0


def execute_approval_approve(args: argparse.Namespace, ctx: CLIContext) -> int:
    """Execute 'approval approve <approval_id>'."""
    return _resolve_decision(args, ctx, decision="APPROVE")


def execute_approval_reject(args: argparse.Namespace, ctx: CLIContext) -> int:
    """Execute 'approval reject <approval_id>'."""
    return _resolve_decision(args, ctx, decision="REJECT")


def _resolve_decision(
    args: argparse.Namespace,
    ctx: CLIContext,
    decision: str,
) -> int:
    if ctx.approval_client is None:
        ctx.write_err("Error: Approval client is unavailable.")
        return 1

    req = ctx.approval_client.get_request(args.approval_id)
    if req is None:
        ctx.write_err(f"Error: Approval request '{args.approval_id}' not found.")
        return 1

    space_id = args.space_id or req.space_id
    plan_version = args.plan_version if args.plan_version is not None else req.plan_version
    req_hash = args.hash or req.capability_request_hash

    try:
        success = ctx.approval_client.sign_and_submit_decision(
            approver_id=args.approver,
            token_secret=args.token,
            space_id=space_id,
            approval_id=args.approval_id,
            decision=decision,
            plan_version=plan_version,
            capability_request_hash=req_hash,
            nonce=getattr(args, "nonce", None),
            timestamp=getattr(args, "timestamp", None),
        )
        if not success:
            ctx.write_err(
                f"Resolution failed: request status '{req.status}' cannot transition to '{decision.lower()}'"
            )
            return 1

        action_word = "approved" if decision == "APPROVE" else "rejected"
        if ctx.json_output:
            ctx.write_json({"approval_id": args.approval_id, "status": action_word, "success": True})
        else:
            ctx.write_out(f"Successfully {action_word} request '{args.approval_id}'.")
        return 0

    except ClockSkewError as e:
        ctx.write_err(f"Clock skew error: {e}")
        return 4
    except ReplayDetectedError as e:
        ctx.write_err(f"Replay rejected: {e}")
        return 5
    except MalformedAuthenticationPayloadError as e:
        ctx.write_err(f"Malformed payload: {e}")
        return 5
    except PermissionError as e:
        ctx.write_err(f"Authorization error: {e}")
        return 3
    except AuthenticationError as e:
        ctx.write_err(f"Authentication error: {e}")
        return 3
    except Exception as e:
        ctx.write_err(f"Unexpected error: {e}")
        return 1

