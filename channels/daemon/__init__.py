"""RYU AI Local Channel Daemon.

Provides a loopback adapter for desktop applications and interactive CLI
without conferring independent authority.

spec §2, §4, ADR-0026, CONTRACT APP-002, APP-006 — Phase 8.5
"""

from __future__ import annotations

from channels.daemon.auth import DaemonAuthenticator, DaemonAuthError
from channels.daemon.client import DaemonClient, DaemonClientError
from channels.daemon.config import DaemonConfig
from channels.daemon.server import DaemonHTTPServer, LocalDaemon

__all__ = [
    "DaemonAuthenticator",
    "DaemonAuthError",
    "DaemonClient",
    "DaemonClientError",
    "DaemonConfig",
    "DaemonHTTPServer",
    "LocalDaemon",
]

