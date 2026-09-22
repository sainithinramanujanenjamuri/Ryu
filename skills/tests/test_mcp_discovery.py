"""Unit tests for MCP tool discovery, schema normalization, and ToolRegistry ingestion."""

import sys

import pytest

from skills.contract import SkillError
from skills.mcp.client import MCPClient
from skills.mcp.discovery import MCPDiscoveryService
from skills.mcp.server_registry import MCPServerRegistration, MCPTrustLevel
from skills.model import RiskTier
from skills.registry import DefaultHmacVerifier, SkillRegistry

MOCK_DISCOVERY_SERVER_CODE = """
import sys
import json

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
                "serverInfo": {"name": "discovery-server", "version": "1.0.0"}
            }
        }
        sys.stdout.write(json.dumps(res) + "\\n")
        sys.stdout.flush()
    elif method == "notifications/initialized":
        pass
    elif method == "tools/list":
        res = {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "tools": [
                    {
                        "name": "search_database",
                        "description": "Searches records in DB",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "query": {"type": "string"},
                                "limit": {"type": "integer"}
                            },
                            "required": ["query"]
                        }
                    },
                    {
                        "name": "export_csv",
                        "description": "Exports rows to CSV format",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "rows": {"type": "array"}
                            }
                        }
                    }
                ]
            }
        }
        sys.stdout.write(json.dumps(res) + "\\n")
        sys.stdout.flush()
"""

MALFORMED_SCHEMA_SERVER_CODE = """
import sys
import json

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
                "serverInfo": {"name": "bad-schema-server", "version": "1.0.0"}
            }
        }
        sys.stdout.write(json.dumps(res) + "\\n")
        sys.stdout.flush()
    elif method == "notifications/initialized":
        pass
    elif method == "tools/list":
        res = {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "tools": [
                    {
                        "name": "broken_tool",
                        "description": "Has invalid schema type",
                        "inputSchema": {
                            "type": "not-a-valid-json-schema-type"
                        }
                    }
                ]
            }
        }
        sys.stdout.write(json.dumps(res) + "\\n")
        sys.stdout.flush()
"""


def test_discover_and_register_tools():
    verifier = DefaultHmacVerifier("disc-secret")
    registry = SkillRegistry(verifier=verifier)
    service = MCPDiscoveryService(registry=registry, signer=verifier)

    server_config = MCPServerRegistration(
        server_id="analytics_db",
        version="1.2.0",
        command=sys.executable,
        args=("-c", MOCK_DISCOVERY_SERVER_CODE),
        trust_level=MCPTrustLevel.UNTRUSTED,
        risk_tier=RiskTier.HIGH,
        registered_by="admin",
    )

    with MCPClient(server_config, default_timeout=5.0) as client:
        tools = service.discover_and_register(client, server_version="1.2.0")
        assert len(tools) == 2

        # Verify namespacing: mcp.<server_id>.<tool_name> (REG-006)
        tool_ids = [t.tool_id for t in tools]
        assert "mcp.analytics_db.search_database" in tool_ids
        assert "mcp.analytics_db.export_csv" in tool_ids

        # Verify risk tier defaults to HIGH
        assert all(t.risk_tier == RiskTier.HIGH for t in tools)

        # Verify registered in registry
        saved = registry.get_tool("mcp.analytics_db.search_database", "1.2.0")
        assert saved.capability == "mcp.analytics_db.search_database"
        assert saved.server_id == "analytics_db"


def test_discover_tools_rejects_malformed_schema():
    verifier = DefaultHmacVerifier("disc-secret")
    registry = SkillRegistry(verifier=verifier)
    service = MCPDiscoveryService(registry=registry, signer=verifier)

    server_config = MCPServerRegistration(
        server_id="bad_server",
        version="1.0.0",
        command=sys.executable,
        args=("-c", MALFORMED_SCHEMA_SERVER_CODE),
        trust_level=MCPTrustLevel.UNTRUSTED,
        risk_tier=RiskTier.HIGH,
        registered_by="admin",
    )

    with MCPClient(server_config, default_timeout=5.0) as client:
        with pytest.raises(SkillError, match="invalid input schema"):
            service.discover_and_register(client, server_version="1.0.0")

