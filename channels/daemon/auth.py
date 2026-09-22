"""Local Channel Daemon Bearer Authentication.

spec §2, §4, ADR-0026, CONTRACT APP-002 — Phase 8.5
"""

from __future__ import annotations

import hmac


class DaemonAuthError(Exception):
    """Raised when daemon bearer token authentication fails."""


class DaemonAuthenticator:
    """Authenticates loopback HTTP/WS transport requests using bearer tokens."""

    def __init__(self, auth_token: str) -> None:
        if not auth_token:
            raise ValueError("Daemon auth_token cannot be empty.")
        self._auth_token = auth_token

    def authenticate_header(self, authorization_header: str | None) -> bool:
        """Validate an HTTP Authorization header in constant time."""
        if not authorization_header:
            return False

        parts = authorization_header.strip().split(" ", 1)
        if len(parts) != 2 or parts[0].lower() != "bearer":
            return False

        presented_token = parts[1].strip()
        return hmac.compare_digest(
            presented_token.encode("utf-8"),
            self._auth_token.encode("utf-8"),
        )

    def verify_or_raise(self, authorization_header: str | None) -> None:
        """Verify bearer token or raise DaemonAuthError."""
        if not self.authenticate_header(authorization_header):
            raise DaemonAuthError("Unauthorized: Missing or invalid daemon bearer token.")

