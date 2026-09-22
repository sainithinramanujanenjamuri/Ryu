"""Model Context Protocol (MCP) JSON-RPC 2.0 wire models and contracts.

spec §5 (Extensibility Layer), docs/CONTRACT_MATRIX.md REG-006, ADR-0029 — Phase 9
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

MCP_PROTOCOL_VERSION = "2024-11-05"

# JSON-RPC 2.0 Standard Error Codes
JSONRPC_PARSE_ERROR = -32700
JSONRPC_INVALID_REQUEST = -32600
JSONRPC_METHOD_NOT_FOUND = -32601
JSONRPC_INVALID_PARAMS = -32602
JSONRPC_INTERNAL_ERROR = -32603
JSONRPC_TIMEOUT_ERROR = -32000


@dataclass
class JsonRpcError:
    """Standard JSON-RPC 2.0 Error object."""

    code: int
    message: str
    data: Any = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.data is not None:
            d["data"] = self.data
        return d


@dataclass
class JsonRpcRequest:
    """JSON-RPC 2.0 Request message."""

    method: str
    params: dict[str, Any] | None = None
    id: str | int | None = None
    jsonrpc: str = "2.0"

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"jsonrpc": self.jsonrpc, "method": self.method}
        if self.params is not None:
            d["params"] = self.params
        if self.id is not None:
            d["id"] = self.id
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), separators=(",", ":"))


@dataclass
class JsonRpcResponse:
    """JSON-RPC 2.0 Response message."""

    id: str | int | None
    result: Any = None
    error: JsonRpcError | None = None
    jsonrpc: str = "2.0"

    @property
    def is_error(self) -> bool:
        return self.error is not None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"jsonrpc": self.jsonrpc, "id": self.id}
        if self.error is not None:
            d["error"] = self.error.to_dict()
        else:
            d["result"] = self.result
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), separators=(",", ":"))

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> JsonRpcResponse:
        err = None
        if "error" in data and data["error"] is not None:
            err_dict = data["error"]
            err = JsonRpcError(
                code=err_dict.get("code", JSONRPC_INTERNAL_ERROR),
                message=err_dict.get("message", "Unknown error"),
                data=err_dict.get("data"),
            )
        return cls(
            jsonrpc=data.get("jsonrpc", "2.0"),
            id=data.get("id"),
            result=data.get("result"),
            error=err,
        )


@dataclass
class MCPToolDefinition:
    """Discovered MCP Tool metadata."""

    name: str
    description: str = ""
    input_schema: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MCPToolDefinition:
        return cls(
            name=data.get("name", ""),
            description=data.get("description", ""),
            input_schema=data.get("inputSchema", {}) or {},
        )

