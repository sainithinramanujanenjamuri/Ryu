"""Integration unit tests for MCPClient with a mock stdio python process."""

import sys

import pytest

from skills.contract import SkillError
from skills.mcp.client import MCPClient
from skills.mcp.server_registry import MCPServerRegistration, MCPTrustLevel
from skills.model import RiskTier

MOCK_SERVER_CODE = """
import sys
import json
import time

for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        req = json.loads(line)
    except Exception:
        continue

    method = req.get("method")
    req_id = req.get("id")

    if method == "initialize":
        res = {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "mock-mcp-server", "version": "1.0.0"}
            }
        }
        sys.stdout.write(json.dumps(res) + "\\n")
        sys.stdout.flush()
    elif method == "notifications/initialized":
        pass  # notification, no response
    elif method == "tools/list":
        res = {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "tools": [
                    {
                        "name": "echo",
                        "description": "Echoes back input",
                        "inputSchema": {
                            "type": "object",
                            "properties": {"text": {"type": "string"}},
                            "required": ["text"]
                        }
                    }
                ]
            }
        }
        sys.stdout.write(json.dumps(res) + "\\n")
        sys.stdout.flush()
    elif method == "tools/call":
        args = req.get("params", {}).get("arguments", {})
        tool_name = req.get("params", {}).get("name")
        if tool_name == "echo":
            res = {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"echoed": args.get("text", "")}
            }
        elif tool_name == "hang":
            time.sleep(10)
            res = {"jsonrpc": "2.0", "id": req_id, "result": {}}
        else:
            res = {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32601, "message": "Unknown tool"}
            }
        sys.stdout.write(json.dumps(res) + "\\n")
        sys.stdout.flush()
"""


@pytest.fixture
def mock_server_config():
    return MCPServerRegistration(
        server_id="mock_server",
        version="1.0.0",
        command=sys.executable,
        args=("-c", MOCK_SERVER_CODE),
        trust_level=MCPTrustLevel.UNTRUSTED,
        risk_tier=RiskTier.HIGH,
        registered_by="test",
    )


def test_mcp_client_handshake_and_tools_list(mock_server_config):
    with MCPClient(mock_server_config, default_timeout=5.0) as client:
        assert client.is_running

        tools = client.list_tools()
        assert len(tools) == 1
        assert tools[0].name == "echo"
        assert tools[0].description == "Echoes back input"


def test_mcp_client_call_tool_success(mock_server_config):
    with MCPClient(mock_server_config, default_timeout=5.0) as client:
        result = client.call_tool("echo", {"text": "hello from ryu"})
        assert result.get("echoed") == "hello from ryu"


def test_mcp_client_call_tool_error(mock_server_config):
    with MCPClient(mock_server_config, default_timeout=5.0) as client:
        with pytest.raises(SkillError, match="Unknown tool"):
            client.call_tool("nonexistent", {})


def test_mcp_client_timeout(mock_server_config):
    # Test short timeout
    with MCPClient(mock_server_config, default_timeout=0.5) as client:
        with pytest.raises(SkillError, match="timed out"):
            client.call_tool("hang", {}, timeout=0.3)

