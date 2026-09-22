"""MCP Server configuration and trust registry.

spec §5 (Extensibility Layer), §9 (Registry Service), ADR-0029 — Phase 9
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from skills.model import SEMVER_REGEX, RiskTier


class MCPTrustLevel(str, Enum):
    """Trust classifications for MCP servers."""

    UNTRUSTED = "UNTRUSTED"  # Local/custom scripts, no external network, isolated
    VERIFIED = "VERIFIED"    # Signed community plugins with proven provenance
    TRUSTED = "TRUSTED"      # Official built-in servers signed by platform release key


class MCPServerState(str, Enum):
    """Lifecycle states for registered MCP servers."""

    REGISTERED = "REGISTERED"
    RUNNING = "RUNNING"
    STOPPED = "STOPPED"
    FAILED = "FAILED"
    REVOKED = "REVOKED"


@dataclass(frozen=True)
class MCPServerRegistration:
    """Registration contract for an MCP server process."""

    server_id: str
    version: str
    command: str
    args: tuple[str, ...] = field(default_factory=tuple)
    env_allowlist: tuple[str, ...] = field(default_factory=tuple)
    trust_level: MCPTrustLevel = MCPTrustLevel.UNTRUSTED
    risk_tier: RiskTier = RiskTier.HIGH
    registered_by: str = "admin"
    transport: str = "stdio"  # Phase 9: stdio strictly
    lifecycle_state: MCPServerState = MCPServerState.REGISTERED
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        if not self.server_id or not self.server_id.strip():
            raise ValueError("server_id must not be empty")
        if not SEMVER_REGEX.match(self.version):
            raise ValueError(f"Invalid SemVer string for MCP Server: '{self.version}'")
        if not self.command or not self.command.strip():
            raise ValueError("command must not be empty")
        if self.transport != "stdio":
            raise ValueError(f"Unsupported transport '{self.transport}': Phase 9 permits 'stdio' only.")

    def to_dict(self) -> dict[str, Any]:
        return {
            "server_id": self.server_id,
            "version": self.version,
            "command": self.command,
            "args": list(self.args),
            "env_allowlist": list(self.env_allowlist),
            "trust_level": self.trust_level.value,
            "risk_tier": self.risk_tier.value,
            "registered_by": self.registered_by,
            "transport": self.transport,
            "lifecycle_state": self.lifecycle_state.value,
            "metadata": self.metadata,
            "created_at": self.created_at.isoformat(),
        }


class MCPServerRegistry:
    """Registry for managing connected and configured MCP server definitions."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._servers: dict[str, MCPServerRegistration] = {}

    def register_server(self, server: MCPServerRegistration) -> MCPServerRegistration:
        """Register an MCP server definition."""
        with self._lock:
            if server.server_id in self._servers:
                existing = self._servers[server.server_id]
                if existing.lifecycle_state == MCPServerState.REVOKED:
                    raise ValueError(f"Cannot re-register revoked MCP server '{server.server_id}'.")
            self._servers[server.server_id] = server
            return server

    def get_server(self, server_id: str) -> MCPServerRegistration:
        """Retrieve an MCP server registration."""
        with self._lock:
            if server_id not in self._servers:
                raise KeyError(f"MCP server '{server_id}' is not registered.")
            return self._servers[server_id]

    def list_servers(self, trust_level: MCPTrustLevel | None = None) -> list[MCPServerRegistration]:
        """List registered MCP servers."""
        with self._lock:
            res: list[MCPServerRegistration] = []
            for s in self._servers.values():
                if trust_level is None or s.trust_level == trust_level:
                    res.append(s)
            return sorted(res, key=lambda x: x.server_id)

    def set_server_state(self, server_id: str, new_state: MCPServerState) -> MCPServerRegistration:
        """Update lifecycle state of an MCP server."""
        with self._lock:
            server = self.get_server(server_id)
            if server.lifecycle_state == MCPServerState.REVOKED and new_state != MCPServerState.REVOKED:
                raise ValueError(f"Cannot update state of revoked MCP server '{server_id}'.")

            updated = MCPServerRegistration(
                server_id=server.server_id,
                version=server.version,
                command=server.command,
                args=server.args,
                env_allowlist=server.env_allowlist,
                trust_level=server.trust_level,
                risk_tier=server.risk_tier,
                registered_by=server.registered_by,
                transport=server.transport,
                lifecycle_state=new_state,
                metadata=server.metadata,
                created_at=server.created_at,
            )
            self._servers[server_id] = updated
            return updated

