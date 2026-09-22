"""Unit tests for MCPWorker execution and pulse emission."""

import pytest
from unittest.mock import MagicMock

from ryu.pulse_bus.bus import PulseBus
from skills.contract import SkillError
from skills.mcp.server_registry import MCPServerRegistration, MCPTrustLevel
from skills.model import RiskTier
from workers.contract import ExecutionLimits, ExecutionRequest, WorkerIdentity
from workers.mcp.worker import MCPWorker


@pytest.fixture
def mock_server_config():
    return MCPServerRegistration(
        server_id="test_mcp_server",
        version="1.0.0",
        command="dummy",
        trust_level=MCPTrustLevel.UNTRUSTED,
        risk_tier=RiskTier.HIGH,
        registered_by="admin",
    )


@pytest.fixture
def mock_mcp_client():
    client = MagicMock()
    client.call_tool.return_value = {"content": [{"type": "text", "text": "result_from_tool"}]}
    return client


def test_mcp_worker_successful_execution(mock_server_config, mock_mcp_client):
    worker = MCPWorker(
        server_config=mock_server_config,
        client=mock_mcp_client,
    )

    req = ExecutionRequest(
        request_id="exec-1",
        correlation_id="corr-1",
        space_id="default-space",
        worker_id="mcp-worker-test_mcp_server",
        capability="mcp.test_mcp_server.echo",
        arguments={"tool_name": "echo", "parameters": {"msg": "hello"}},
    )

    result = worker.execute(req)
    assert result.is_success
    assert result.status == "ok"
    assert result.taint is True  # ADR-0032: Tool output stamped taint: True
    assert result.output_data == {"content": [{"type": "text", "text": "result_from_tool"}]}
    assert result.metrics.duration_seconds >= 0.0

    mock_mcp_client.call_tool.assert_called_once_with(
        name="echo",
        arguments={"msg": "hello"},
        timeout=30.0,
    )


def test_mcp_worker_tool_failure(mock_server_config):
    client = MagicMock()
    client.call_tool.side_effect = SkillError(
        error_class="transient.timeout",
        message="Tool execution timed out",
        retryable=True,
    )

    worker = MCPWorker(
        server_config=mock_server_config,
        client=client,
    )

    req = ExecutionRequest(
        request_id="exec-2",
        correlation_id="corr-1",
        space_id="default-space",
        worker_id="mcp-worker-test_mcp_server",
        capability="mcp.test_mcp_server.slow_tool",
        arguments={"parameters": {}},
    )

    result = worker.execute(req)
    assert not result.is_success
    assert result.status == "failed"
    assert result.error is not None
    assert result.error.error_class == "transient.timeout"
    assert result.error.retryable is True


def test_mcp_worker_emits_pulses(mock_server_config, mock_mcp_client):
    bus = MagicMock(spec=PulseBus)
    worker = MCPWorker(
        server_config=mock_server_config,
        client=mock_mcp_client,
        bus=bus,
    )

    req = ExecutionRequest(
        request_id="exec-3",
        correlation_id="corr-3",
        space_id="default-space",
        worker_id="mcp-worker-test_mcp_server",
        capability="mcp.test_mcp_server.test_tool",
        arguments={"parameters": {}},
    )

    result = worker.execute(req)
    assert result.is_success

    # Verify bus published worker.tool.called and worker.tool.succeeded
    published_types = [call[0][0].type for call in bus.publish.call_args_list]
    assert "worker.tool.called" in published_types
    assert "worker.tool.succeeded" in published_types
