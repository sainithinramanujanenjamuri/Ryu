"""Network sandbox isolation and egress policy enforcement.

spec §7 (Execution Layer), §10 (Prompt-Injection & Taint Model),
CONTRACT_MATRIX WORKER-003, ADR-0014
"""

from __future__ import annotations

import contextlib
import socket
from typing import Any, Iterator

from workers.contract import NetworkPolicy


class NetworkSandbox:
    """Enforces network egress policies and intercepts socket creation."""

    def __init__(self, policy: NetworkPolicy | None = None) -> None:
        self.policy = policy or NetworkPolicy()

    def validate_connection(self, host: str, port: int) -> None:
        """Validate destination host and port against policy.

        Raises PermissionError if egress is not permitted.
        """
        if not self.policy.is_allowed(host, port):
            raise PermissionError(
                f"Network egress denied by policy: mode={self.policy.mode.value}, "
                f"destination={host}:{port}"
            )

    @contextlib.contextmanager
    def intercept_sockets(self) -> Iterator[None]:
        """Context manager intercepting low-level socket connections in Python runtime."""
        orig_connect = socket.socket.connect
        sandbox = self

        def _guarded_connect(sock: socket.socket, address: Any) -> None:
            if isinstance(address, tuple) and len(address) >= 2:
                host, port = address[0], address[1]
                sandbox.validate_connection(str(host), int(port))
            elif isinstance(address, str):
                # Unix domain socket or named pipe
                sandbox.validate_connection(address, 0)
            orig_connect(sock, address)

        try:
            socket.socket.connect = _guarded_connect  # type: ignore[assignment]
            yield
        finally:
            socket.socket.connect = orig_connect  # type: ignore[assignment]
