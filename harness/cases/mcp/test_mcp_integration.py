"""Harness integration tests for Model Context Protocol (MCP) subsystem.

Contracts verified:
- REG-006: MCP tools mapped into Tools Layer under mcp.<server_id>.<tool_name>
- MCP-001: Server handshake and initialization
- MCP-002: Tool discovery and schema normalization
- MCP-003: Sandboxed tool invocation through Worker and taint marking

spec §5 (Extensibility Layer), §16, docs/CONTRACT_MATRIX.md REG-006, MCP-001..003
"""

import sys
import pytest
from unittest.mock import MagicMock

from ryu.pulse_bus.bus import PulseBus
from skills.mcp.client import MCPClient
from skills.mcp.discovery import MCPDiscoveryService
from skills.mcp.server_registry import MCPServerRegistration, MCPTrustLevel
from skills.model import RiskTier
from skills.registry import DefaultHmacVerifier, SkillRegistry
from workers.contract import ExecutionRequest
from workers.mcp.worker import MCPWorker

INTEGRATION_SERVER_CODE = """
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
                "serverInfo": {"name": "integration-server", "version": "1.0.0"}
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
                        "name": "calculate_hash",
                        "description": "Calculates data checksum",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "algorithm": {"type": "string"},
                                "data": {"type": "string"}
                            },
                            "required": ["algorithm", "data"]
                        }
                    }
                ]
            }
        }
        sys.stdout.write(json.dumps(res) + "\\n")
        sys.stdout.flush()
    elif method == "tools/call":
        params = req.get("params", {})
        args = params.get("arguments", {})
        res = {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "hash": "checksum-12345",
                "algo": args.get("algorithm", "sha256")
            }
        }
        sys.stdout.write(json.dumps(res) + "\\n")
        sys.stdout.flush()
"""


@pytest.fixture
def mcp_server_config():
    return MCPServerRegistration(
        server_id="crypto_service",
        version="1.0.0",
        command=sys.executable,
        args=("-c", INTEGRATION_SERVER_CODE),
        trust_level=MCPTrustLevel.UNTRUSTED,
        risk_tier=RiskTier.HIGH,
        registered_by="admin",
    )


def test_reg_006_and_mcp_001_002_handshake_and_discovery(mcp_server_config):
    """MCP-001, MCP-002, REG-006: Server handshake and tool namespacing."""
    verifier = DefaultHmacVerifier("int-key")
    registry = SkillRegistry(verifier=verifier)
    discovery = MCPDiscoveryService(registry=registry, signer=verifier)

    with MCPClient(mcp_server_config, default_timeout=5.0) as client:
        assert client.is_running  # MCP-001: process started, handshake succeeded

        tools = discovery.discover_and_register(client, server_version="1.0.0")
        assert len(tools) == 1

        # REG-006: Tool namespace mcp.<server_id>.<tool_name>
        tool = tools[0]
        assert tool.tool_id == "mcp.crypto_service.calculate_hash"
        assert tool.server_id == "crypto_service"
        assert tool.risk_tier == RiskTier.HIGH

        # Verify queryable from ToolRegistry
        saved = registry.get_tool("mcp.crypto_service.calculate_hash", "1.0.0")
        assert saved.capability == "mcp.crypto_service.calculate_hash"


def test_mcp_003_worker_invocation_and_taint(mcp_server_config):
    """MCP-003: Sandboxed tool invocation through MCPWorker emits pulses with taint: True."""
    bus = MagicMock(spec=PulseBus)

    with MCPClient(mcp_server_config, default_timeout=5.0) as client:
        worker = MCPWorker(
            server_config=mcp_server_config,
            client=client,
            bus=bus,
        )

        exec_req = ExecutionRequest(
            request_id="exec-req-1",
            correlation_id="corr-mcp-1",
            space_id="default-space",
            worker_id="mcp-worker-crypto_service",
            capability="mcp.crypto_service.calculate_hash",
            arguments={
                "tool_name": "calculate_hash",
                "parameters": {"algorithm": "sha256", "data": "test-data"},
            },
        )

        result = worker.execute(exec_req)
        assert result.is_success
        assert result.status == "ok"
        assert result.output_data == {"hash": "checksum-12345", "algo": "sha256"}
        assert result.taint is True  # Invariant: external tool result is tainted

        # Pulse verification: worker.tool.called and worker.tool.succeeded
        calls = [c[0][0] for c in bus.publish.call_args_list]
        called_pulse = next(p for p in calls if p.type == "worker.tool.called")
        succeeded_pulse = next(p for p in calls if p.type == "worker.tool.succeeded")

        assert called_pulse.payload["tool_id"] == "mcp-worker-crypto_service"
        assert called_pulse.payload["capability"] == "mcp.crypto_service.calculate_hash"
        assert succeeded_pulse.payload["tool_id"] == "mcp-worker-crypto_service"
        assert succeeded_pulse.taint is True
