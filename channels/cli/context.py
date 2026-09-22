"""CLI Execution Context: encapsulates dependencies, I/O streams, and output formatting.

spec §2, §4, ROADMAP Phase 8 — Phase 8
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Any, TextIO

from channels.approval.client import ApprovalClient
from channels.cli.formatters import format_json


@dataclass
class CLIContext:
    """Carries runtime dependencies and I/O streams for CLI commands."""

    approval_client: ApprovalClient | None = None
    bus: Any | None = None
    pulse_store: Any | None = None
    kernel: Any | None = None
    spaces_registry: dict[str, Any] | None = None
    json_output: bool = False
    out_stream: TextIO = sys.stdout
    err_stream: TextIO = sys.stderr
    in_stream: TextIO = sys.stdin

    def write_out(self, text: str) -> None:
        """Write text to configured stdout stream with safe encoding fallback."""
        try:
            self.out_stream.write(text + "\n")
        except UnicodeEncodeError:
            enc = getattr(self.out_stream, "encoding", None) or "ascii"
            safe_text = text.encode(enc, errors="replace").decode(enc)
            self.out_stream.write(safe_text + "\n")
        self.out_stream.flush()

    def write_err(self, text: str) -> None:
        """Write text to configured stderr stream with safe encoding fallback."""
        try:
            self.err_stream.write(text + "\n")
        except UnicodeEncodeError:
            enc = getattr(self.err_stream, "encoding", None) or "ascii"
            safe_text = text.encode(enc, errors="replace").decode(enc)
            self.err_stream.write(safe_text + "\n")
        self.err_stream.flush()

    def write_json(self, data: Any) -> None:
        """Write JSON serialized data to stdout stream."""
        self.out_stream.write(format_json(data) + "\n")
        self.out_stream.flush()


def create_default_context() -> CLIContext:
    """Create a CLIContext with default fallback stores and approval client."""
    from ryu.pulse_bus.config import DurableBusConfig

    from channels.approval.auth import ApproverAuthenticator, InMemoryCredentialStore
    from channels.approval.client import ApprovalClient
    from channels.approval.store import PostgresApprovalStore
    from core.space.approver import ApprovalManager, InMemoryApprovalStore

    store: Any
    try:
        pg_cfg = DurableBusConfig.from_env().pg
        pg_store = PostgresApprovalStore(pg_cfg)
        conn = pg_store._get_conn()
        conn.close()
        store = pg_store
    except Exception:
        store = InMemoryApprovalStore()

    manager = ApprovalManager(store=store)
    cred_store = InMemoryCredentialStore()
    auth = ApproverAuthenticator(
        cred_store=cred_store,
        nonce_store=cred_store,
        secret_store={},
    )
    client = ApprovalClient(manager=manager, authenticator=auth)
    return CLIContext(approval_client=client)


