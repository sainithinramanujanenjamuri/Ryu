"""Chaos and fault injection tests for MCP Subprocess Runtime.

Fault scenarios verified:
- CHAOS-MCP-001: Subprocess crashes immediately upon spawn / initialize
- CHAOS-MCP-002: Tool execution hangs beyond timeout (watchdog enforcement)
- CHAOS-MCP-003: Subprocess returns malformed / corrupted JSON
- CHAOS-MCP-006: Subprocess crashes mid-execution (SIGSEGV/exit emulation)

spec §5, §16, ADR-0029, ADR-0031 — Phase 9
"""

import sys
import pytest

from skills.contract import SkillError
from skills.mcp.client import MCPClient
from skills.mcp.server_registry import MCPServerRegistration, MCPTrustLevel
from skills.model import RiskTier

CRASH_ON_INIT_CODE = """
import sys
# Exits immediately with error status code
sys.exit(1)
"""

HANG_TOOL_CODE = """
import sys
import json
import time

for line in sys.stdin:
    if not line.strip():
        continue
    req = json.loads(line)
    method = req.get("method")
    req_id = req.get("id")

    if method == "initialize":
        sys.stdout.write(json.dumps({
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "serverInfo": {"name": "hang-server", "version": "1.0.0"}
            }
        }) + "\\n")
        sys.stdout.flush()
    elif method == "notifications/initialized":
        pass
    elif method == "tools/list":
        sys.stdout.write(json.dumps({
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {"tools": [{"name": "sleep_forever", "inputSchema": {}}]}
        }) + "\\n")
        sys.stdout.flush()
    elif method == "tools/call":
        # Simulates a hung or deadlocked tool
        time.sleep(30)
"""

CORRUPTED_JSON_CODE = """
import sys
import json

for line in sys.stdin:
    if not line.strip():
        continue
    req = json.loads(line)
    method = req.get("method")
    req_id = req.get("id")

    if method == "initialize":
        sys.stdout.write(json.dumps({
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "serverInfo": {"name": "corrupt-server", "version": "1.0.0"}
            }
        }) + "\\n")
        sys.stdout.flush()
    elif method == "notifications/initialized":
        pass
    elif method == "tools/call":
        # Return broken non-JSON garbage
        sys.stdout.write("<<<INTERNAL PANIC SEGMENTATION FAULT NON-JSON>>>\\n")
        sys.stdout.flush()
"""

CRASH_MID_CALL_CODE = """
import sys
import json

for line in sys.stdin:
    if not line.strip():
        continue
    req = json.loads(line)
    method = req.get("method")
    req_id = req.get("id")

    if method == "initialize":
        sys.stdout.write(json.dumps({
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "serverInfo": {"name": "crash-server", "version": "1.0.0"}
            }
        }) + "\\n")
        sys.stdout.flush()
    elif method == "notifications/initialized":
        pass
    elif method == "tools/call":
        # Simulates sudden process crash
        sys.exit(139)
"""


def test_chaos_mcp_001_crash_on_init():
    """CHAOS-MCP-001: Subprocess crash during initialize fails cleanly."""
    cfg = MCPServerRegistration(
        server_id="crash_init_server",
        version="1.0.0",
        command=sys.executable,
        args=("-c", CRASH_ON_INIT_CODE),
        trust_level=MCPTrustLevel.UNTRUSTED,
        risk_tier=RiskTier.HIGH,
        registered_by="test",
    )

    client = MCPClient(cfg, default_timeout=2.0)
    with pytest.raises(SkillError) as exc_info:
        client.start()
    assert exc_info.value.error_class == "terminal.process_crash"
    assert not client.is_running


def test_chaos_mcp_002_tool_call_timeout_watchdog():
    """CHAOS-MCP-002: Watchdog terminates hung tool process upon timeout expiry."""
    cfg = MCPServerRegistration(
        server_id="hang_server",
        version="1.0.0",
        command=sys.executable,
        args=("-c", HANG_TOOL_CODE),
        trust_level=MCPTrustLevel.UNTRUSTED,
        risk_tier=RiskTier.HIGH,
        registered_by="test",
    )

    client = MCPClient(cfg, default_timeout=0.5)
    with client:
        with pytest.raises(SkillError) as exc_info:
            client.call_tool("sleep_forever", {}, timeout=0.4)
        assert exc_info.value.error_class == "transient.timeout"
        assert exc_info.value.retryable is True

    # Confirm process was terminated and not orphaned
    assert not client.is_running


def test_chaos_mcp_003_corrupted_json_handling():
    """CHAOS-MCP-003: Non-JSON protocol garbage is caught as terminal protocol violation."""
    cfg = MCPServerRegistration(
        server_id="corrupt_server",
        version="1.0.0",
        command=sys.executable,
        args=("-c", CORRUPTED_JSON_CODE),
        trust_level=MCPTrustLevel.UNTRUSTED,
        risk_tier=RiskTier.HIGH,
        registered_by="test",
    )

    with MCPClient(cfg, default_timeout=3.0) as client:
        with pytest.raises(SkillError) as exc_info:
            client.call_tool("any", {})
        assert exc_info.value.error_class == "terminal.protocol_violation"


def test_chaos_mcp_006_crash_mid_call():
    """CHAOS-MCP-006: Subprocess crashing mid-call is handled as process crash."""
    cfg = MCPServerRegistration(
        server_id="crash_mid_call",
        version="1.0.0",
        command=sys.executable,
        args=("-c", CRASH_MID_CALL_CODE),
        trust_level=MCPTrustLevel.UNTRUSTED,
        risk_tier=RiskTier.HIGH,
        registered_by="test",
    )

    with MCPClient(cfg, default_timeout=3.0) as client:
        with pytest.raises(SkillError) as exc_info:
            client.call_tool("any", {})
        assert exc_info.value.error_class == "terminal.process_crash"

