"""Entrypoint for running the Local Channel Daemon from CLI (python -m channels.daemon).

spec §2, §4, ADR-0026, CONTRACT APP-002 — Phase 8.5
"""

from __future__ import annotations

import sys

from channels.cli.context import create_default_context
from channels.daemon.config import DaemonConfig
from channels.daemon.server import LocalDaemon


def main() -> None:
    ctx = create_default_context()
    config = DaemonConfig.from_env()
    daemon = LocalDaemon(
        config=config,
        approval_client=ctx.approval_client,
        bus=ctx.bus,
        pulse_store=ctx.pulse_store,
        space_registry=ctx.spaces_registry,
    )

    print("=" * 64)
    print("  RYU AI — Local Channel Daemon (SCCA Phase 8.5)")
    print("=" * 64)
    print(f"  Bound URL    : {daemon.url}")
    print(f"  Bearer Token : {config.auth_token}")
    print(f"  Token File   : {config.token_file_path}")
    print("=" * 64)
    print("  Listening for loopback requests. Press Ctrl+C to stop.\n")

    try:
        daemon.start(block=True)
    except KeyboardInterrupt:
        print("\nStopping daemon...")
        daemon.stop()
        print("Daemon stopped cleanly.")
        sys.exit(0)


if __name__ == "__main__":
    main()

