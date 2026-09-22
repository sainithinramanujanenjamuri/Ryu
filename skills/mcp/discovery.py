"""MCP Tool Discovery and Tools Layer ingestion service.

Maps MCP tools into the Tools Layer under 'mcp.<server_id>.<tool_name>' (REG-006)
and registers them into the ToolRegistry (ADR-0030).

spec §5 (Extensibility Layer), §16 (Component Contracts),
docs/CONTRACT_MATRIX.md REG-006, ADR-0030 — Phase 9
"""

from __future__ import annotations

from typing import Any

import jsonschema  # type: ignore[import-untyped]

from skills.contract import SkillError
from skills.mcp.client import MCPClient
from skills.model import (
    RiskTier,
    SkillLifecycleState,
    ToolRegistration,
    compute_sha256_hash,
)
from skills.registry import DefaultHmacVerifier, SkillRegistry


class MCPDiscoveryService:
    """
    Discovers, normalizes, and registers MCP tools into the RYU Tools Layer.

    Enforces:
    - DISCOVERY != AUTHORIZATION (ADR-0030).
    - Namespace isolation: mcp.<server_id>.<tool_name> (REG-006).
    - JSON Schema validation of input schemas.
    - Default HIGH risk tier for third-party MCP tools.
    """

    def __init__(
        self,
        registry: SkillRegistry,
        signer: DefaultHmacVerifier | None = None,
    ) -> None:
        self.registry = registry
        self.signer = signer or DefaultHmacVerifier()

    def discover_and_register(
        self,
        client: MCPClient,
        server_version: str = "1.0.0",
        default_risk_tier: RiskTier = RiskTier.HIGH,
        registered_by: str = "mcp_discovery_service",
    ) -> list[ToolRegistration]:
        """
        Query connected MCP server tools, validate schemas, and register them.

        Returns list of newly registered ToolRegistration contracts.
        """
        server_id = client.config.server_id
        discovered_tools = client.list_tools()

        registered: list[ToolRegistration] = []
        for t in discovered_tools:
            # 1. Namespace binding (REG-006)
            namespaced_tool_id = f"mcp.{server_id}.{t.name}"
            capability_name = f"mcp.{server_id}.{t.name}"

            # 2. Schema Validation
            input_schema = t.input_schema or {"type": "object"}
            self._validate_tool_schema(input_schema, namespaced_tool_id)

            # 3. Content Hashing (REG-002)
            payload_dict = {
                "name": t.name,
                "namespaced_id": namespaced_tool_id,
                "description": t.description,
                "inputSchema": input_schema,
            }
            content_hash = compute_sha256_hash(payload_dict)

            # 4. Supply-Chain Signing (REG-003)
            sig = self.signer.sign(
                identifier=namespaced_tool_id,
                version=server_version,
                content_hash=content_hash,
                risk_tier=default_risk_tier,
                registered_by=registered_by,
            )

            # 5. Create ToolRegistration contract
            tool_reg = ToolRegistration(
                tool_id=namespaced_tool_id,
                version=server_version,
                content_hash=content_hash,
                signature=sig,
                capability=capability_name,
                risk_tier=default_risk_tier,
                registered_by=registered_by,
                source_type="mcp",
                server_id=server_id,
                description=t.description,
                input_schema=input_schema,
                output_schema={"type": "object"},
                lifecycle_state=SkillLifecycleState.ENABLED,
            )

            # 6. Save in Registry
            saved = self.registry.register_tool(tool_reg, payload=payload_dict)
            registered.append(saved)

        return registered

    def _validate_tool_schema(self, schema: dict[str, Any], tool_id: str) -> None:
        """Ensure input schema conforms to valid JSON Schema Draft 2020-12."""
        try:
            jsonschema.Draft202012Validator.check_schema(schema)
        except jsonschema.exceptions.SchemaError as exc:
            raise SkillError(
                error_class="terminal.schema_violation",
                message=f"MCP tool '{tool_id}' declared an invalid input schema: {exc.message}",
                retryable=False,
            )

