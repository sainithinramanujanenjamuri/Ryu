"""Unit tests for MCP JSON-RPC 2.0 protocol models."""

from skills.mcp.protocol import (
    JSONRPC_INTERNAL_ERROR,
    JsonRpcRequest,
    JsonRpcResponse,
    MCPToolDefinition,
)


def test_jsonrpc_request_serialization():
    req = JsonRpcRequest(id=1, method="tools/list", params={"cursor": "abc"})
    d = req.to_dict()
    assert d["jsonrpc"] == "2.0"
    assert d["id"] == 1
    assert d["method"] == "tools/list"
    assert d["params"] == {"cursor": "abc"}

    raw = req.to_json()
    assert '"method":"tools/list"' in raw


def test_jsonrpc_response_deserialization():
    raw_success = {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {"tools": [{"name": "calculator"}]},
    }
    resp = JsonRpcResponse.from_dict(raw_success)
    assert not resp.is_error
    assert resp.result["tools"][0]["name"] == "calculator"

    raw_error = {
        "jsonrpc": "2.0",
        "id": 2,
        "error": {"code": JSONRPC_INTERNAL_ERROR, "message": "Failed internal operation"},
    }
    resp_err = JsonRpcResponse.from_dict(raw_error)
    assert resp_err.is_error
    assert resp_err.error is not None
    assert resp_err.error.code == JSONRPC_INTERNAL_ERROR
    assert resp_err.error.message == "Failed internal operation"


def test_mcp_tool_definition():
    tool_raw = {
        "name": "echo",
        "description": "Echoes back input",
        "inputSchema": {
            "type": "object",
            "properties": {"msg": {"type": "string"}},
            "required": ["msg"],
        },
    }
    td = MCPToolDefinition.from_dict(tool_raw)
    assert td.name == "echo"
    assert td.description == "Echoes back input"
    assert td.input_schema["properties"]["msg"]["type"] == "string"

