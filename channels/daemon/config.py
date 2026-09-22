"""Local Channel Daemon Configuration.

spec §2, §4, ADR-0026, CONTRACT APP-002, APP-006 — Phase 8.5
"""

from __future__ import annotations

import os
from pathlib import Path
import secrets
from typing import Any


LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


class DaemonConfig:
    """Configuration for the local channel daemon adapter."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 8420,
        auth_token: str | None = None,
        token_file_path: Path | None = None,
        cors_allowed_origins: list[str] | None = None,
    ) -> None:
        if host not in LOOPBACK_HOSTS:
            raise ValueError(
                f"Security violation: Daemon must bind strictly to local loopback ({LOOPBACK_HOSTS}), "
                f"got '{host}'"
            )
        self.host = host
        self.port = int(port)
        self.token_file_path = (
            token_file_path
            if token_file_path is not None
            else Path.home() / ".ryu" / "daemon.token"
        )
        self.auth_token = auth_token or self._resolve_or_generate_token()
        self.cors_allowed_origins = cors_allowed_origins or [
            "tauri://localhost",
            "http://localhost",
            "http://127.0.0.1",
        ]

    def _resolve_or_generate_token(self) -> str:
        """Resolve bearer auth token from environment, file, or generate a fresh one."""
        env_token = os.environ.get("RYU_DAEMON_TOKEN", "").strip()
        if env_token:
            return env_token

        # Check existing token file
        try:
            if self.token_file_path.is_file():
                token = self.token_file_path.read_text(encoding="utf-8").strip()
                if token:
                    return token
        except Exception:
            pass

        # Generate a new cryptographic token and persist locally
        new_token = secrets.token_hex(32)
        try:
            self.token_file_path.parent.mkdir(parents=True, exist_ok=True)
            self.token_file_path.write_text(new_token, encoding="utf-8")
            # Restrict file permissions if on POSIX
            try:
                os.chmod(self.token_file_path, 0o600)
            except Exception:
                pass
        except Exception:
            # Fall back to in-memory ephemeral token if filesystem not writable
            pass
        return new_token

    @classmethod
    def from_env(cls) -> DaemonConfig:
        """Construct DaemonConfig from environment variables."""
        host = os.environ.get("RYU_DAEMON_HOST", "127.0.0.1").strip()
        port = int(os.environ.get("RYU_DAEMON_PORT", "8420"))
        token = os.environ.get("RYU_DAEMON_TOKEN", None)
        token_file = os.environ.get("RYU_DAEMON_TOKEN_FILE", None)
        path = Path(token_file) if token_file else None
        return cls(host=host, port=port, auth_token=token, token_file_path=path)

    def to_dict(self) -> dict[str, Any]:
        """Export config without exposing sensitive auth tokens in logs."""
        return {
            "host": self.host,
            "port": self.port,
            "token_file_path": str(self.token_file_path),
            "cors_allowed_origins": self.cors_allowed_origins,
            "auth_token_set": bool(self.auth_token),
        }

