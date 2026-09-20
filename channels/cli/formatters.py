"""CLI output formatters: ANSI tables and JSON serialization.

spec §2, §4, ROADMAP Phase 8, ADR-0021 — Phase 8
"""

from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from typing import Any

from core.space.approver import ApprovalRequest

# ANSI Color Codes
RESET = "\033[0m"
BOLD = "\033[1m"
GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
CYAN = "\033[36m"
GRAY = "\033[90m"
MAGENTA = "\033[35m"


def color_status(status: str) -> str:
    """Format status with appropriate ANSI color."""
    st = status.lower()
    if st == "approved":
        return f"{GREEN}{status.upper()}{RESET}"
    if st in ("denied", "rejected"):
        return f"{RED}{status.upper()}{RESET}"
    if st in ("pending", "held"):
        return f"{YELLOW}{status.upper()}{RESET}"
    if st in ("expired", "consumed"):
        return f"{GRAY}{status.upper()}{RESET}"
    return status


def color_taint(tainted: bool) -> str:
    """Format taint badge."""
    if tainted:
        return f"{RED}{BOLD}[TAINTED]{RESET}"
    return f"{GREEN}[CLEAN]{RESET}"


def format_table(headers: list[str], rows: list[list[str]]) -> str:
    """Format tabular data into an aligned ASCII table."""
    if not headers and not rows:
        return ""

    col_widths = [len(h) for h in headers]
    for row in rows:
        for i, val in enumerate(row):
            # Strip ANSI escape codes when calculating column width
            clean_val = val
            for code in (RESET, BOLD, GREEN, RED, YELLOW, CYAN, GRAY, MAGENTA):
                clean_val = clean_val.replace(code, "")
            if i < len(col_widths):
                col_widths[i] = max(col_widths[i], len(clean_val))
            else:
                col_widths.append(len(clean_val))

    header_line = "  ".join(h.ljust(col_widths[i]) for i, h in enumerate(headers))
    sep_line = "  ".join("-" * col_widths[i] for i in range(len(headers)))
    row_lines = []
    for row in rows:
        formatted_cols = []
        for i, val in enumerate(row):
            clean_val = val
            for code in (RESET, BOLD, GREEN, RED, YELLOW, CYAN, GRAY, MAGENTA):
                clean_val = clean_val.replace(code, "")
            pad = col_widths[i] - len(clean_val)
            formatted_cols.append(val + (" " * pad))
        row_lines.append("  ".join(formatted_cols))

    return "\n".join([header_line, sep_line] + row_lines)


def format_approvals_table(approvals: list[ApprovalRequest]) -> str:
    """Format a list of ApprovalRequests into a table."""
    if not approvals:
        return "No approval requests found."

    headers = ["APPROVAL ID", "SPACE", "CAPABILITY", "STATUS", "QUEUE", "APPROVER", "TAINT", "SUMMARY"]
    rows = []
    for a in approvals:
        rows.append([
            a.approval_id[:12] if len(a.approval_id) > 12 else a.approval_id,
            a.space_id,
            a.capability,
            color_status(a.status),
            a.queue_state.upper(),
            a.approver_id or "-",
            color_taint(a.taint),
            (a.summary[:30] + "...") if len(a.summary) > 30 else a.summary,
        ])
    return format_table(headers, rows)


def format_json(data: Any) -> str:
    """Serialize object to formatted JSON string."""
    def custom_default(o: Any) -> Any:
        if is_dataclass(o) and not isinstance(o, type):
            return asdict(o)
        to_dict = getattr(o, "to_dict", None)
        if to_dict is not None:
            return to_dict() if callable(to_dict) else to_dict
        isoformat = getattr(o, "isoformat", None)
        if callable(isoformat):
            return isoformat()
        return str(o)

    return json.dumps(data, default=custom_default, indent=2)

