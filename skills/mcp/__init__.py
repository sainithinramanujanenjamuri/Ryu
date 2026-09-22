"""Model Context Protocol (MCP) subsystem for RYU AI.

Extensibility wire protocol layer.
spec §5, docs/CONTRACT_MATRIX.md REG-006, ADR-0029, ADR-0030 — Phase 9
"""

from __future__ import annotations

from skills.mcp.client import MCPClient
from skills.mcp.discovery import MCPDiscoveryService
from skills.mcp.protocol import (
    MCP_PROTOCOL_VERSION,
    JsonRpcError,
    JsonRpcRequest,
    JsonRpcResponse,
    MCPToolDefinition,
)
from skills.mcp.server_registry import (
    MCPServerRegistration,
    MCPServerRegistry,
    MCPServerState,
    MCPTrustLevel,
)

__all__ = [
    "JsonRpcError",
    "JsonRpcRequest",
    "JsonRpcResponse",
    "MCPClient",
    "MCPDiscoveryService",
    "MCPServerRegistration",
    "MCPServerRegistry",
    "MCPServerState",
    "MCPToolDefinition",
    "MCPTrustLevel",
    "MCP_PROTOCOL_VERSION",
]

