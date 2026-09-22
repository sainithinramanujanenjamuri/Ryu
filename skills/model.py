"""Skill and Tool data models and supply-chain contracts.

spec §5 (Extensibility Layer), §7 (Skills Layer), §9 (Cross-Cutting Services),
§16 (Component Contracts), docs/CONTRACT_MATRIX.md REG-001..REG-005,
ROADMAP Phase 9, ADR-0027, ADR-0028 — Phase 9
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

SEMVER_REGEX = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-((?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)(?:\.(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))*))?"
    r"(?:\+([0-9a-zA-Z-]+(?:\.[0-9a-zA-Z-]+)*))?$"
)

HASH_HEX_REGEX = re.compile(r"^[0-9a-f]{64}$")


class SkillLifecycleState(str, Enum):
    """Lifecycle states for registered Skills and Tools."""

    REGISTERED = "REGISTERED"   # Manifest registered and verified
    VALIDATED = "VALIDATED"     # Contracts and schemas validated
    ENABLED = "ENABLED"         # Active and available for Space assignment
    DISABLED = "DISABLED"       # Temporarily suspended; cannot be invoked
    REVOKED = "REVOKED"         # Permanently invalidated due to security breach/revocation


class RiskTier(str, Enum):
    """Capability risk tiers (contracts/registry/capability-risks.json)."""

    LOW = "low"     # Read-only, sandboxed, no external side-effects
    HIGH = "high"   # Modifying, subprocess, device access; requires fresh human approval


def compute_sha256_hash(payload: bytes | str | dict[str, Any]) -> str:
    """Compute deterministic SHA-256 hash of a payload."""
    if isinstance(payload, bytes):
        raw_bytes = payload
    elif isinstance(payload, str):
        raw_bytes = payload.encode("utf-8")
    elif isinstance(payload, dict):
        raw_bytes = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    else:
        raise TypeError(f"Unsupported payload type for hashing: {type(payload)}")
    return hashlib.sha256(raw_bytes).hexdigest()


@dataclass(frozen=True)
class SkillRegistration:
    """
    Canonical Skill Registration contract matching docs/Architecture §16.

    Spaces reference skill_id@version, never @latest (REG-005).
    risk_tier binds to content_hash and is immutable without human security.grant.approved (REG-004).
    """

    skill_id: str
    version: str
    content_hash: str
    signature: str
    capabilities: tuple[str, ...]
    risk_tier: RiskTier
    registered_by: str
    description: str = ""
    input_schema: dict[str, Any] = field(default_factory=dict)
    output_schema: dict[str, Any] = field(default_factory=dict)
    lifecycle_state: SkillLifecycleState = SkillLifecycleState.REGISTERED
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        if not self.skill_id or not self.skill_id.strip():
            raise ValueError("skill_id must not be empty")
        if not SEMVER_REGEX.match(self.version):
            raise ValueError(f"Invalid SemVer string for Skill: '{self.version}'")
        if not HASH_HEX_REGEX.match(self.content_hash):
            raise ValueError(f"Invalid content_hash format (must be 64-char hex SHA-256): '{self.content_hash}'")
        if not self.signature or not self.signature.strip():
            raise ValueError("signature must not be empty")
        if not self.registered_by or not self.registered_by.strip():
            raise ValueError("registered_by must not be empty")

    @property
    def versioned_id(self) -> str:
        return f"{self.skill_id}@{self.version}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "skill_id": self.skill_id,
            "version": self.version,
            "versioned_id": self.versioned_id,
            "content_hash": self.content_hash,
            "signature": self.signature,
            "capabilities": list(self.capabilities),
            "risk_tier": self.risk_tier.value,
            "registered_by": self.registered_by,
            "description": self.description,
            "input_schema": self.input_schema,
            "output_schema": self.output_schema,
            "lifecycle_state": self.lifecycle_state.value,
            "metadata": self.metadata,
            "created_at": self.created_at.isoformat(),
        }


@dataclass(frozen=True)
class ToolRegistration:
    """
    Canonical Tool Registration contract matching docs/Architecture §16.

    MCP tools are namespaced as mcp.<server_id>.<tool_name> (REG-006).
    """

    tool_id: str
    version: str
    content_hash: str
    signature: str
    capability: str
    risk_tier: RiskTier
    registered_by: str
    source_type: str = "native"     # "native" | "mcp" | "plugin"
    server_id: str | None = None
    description: str = ""
    input_schema: dict[str, Any] = field(default_factory=dict)
    output_schema: dict[str, Any] = field(default_factory=dict)
    lifecycle_state: SkillLifecycleState = SkillLifecycleState.ENABLED
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        if not self.tool_id or not self.tool_id.strip():
            raise ValueError("tool_id must not be empty")
        if not SEMVER_REGEX.match(self.version):
            raise ValueError(f"Invalid SemVer string for Tool: '{self.version}'")
        if not HASH_HEX_REGEX.match(self.content_hash):
            raise ValueError(f"Invalid content_hash format (must be 64-char hex SHA-256): '{self.content_hash}'")
        if not self.capability or not self.capability.strip():
            raise ValueError("capability must not be empty")
        if not self.signature or not self.signature.strip():
            raise ValueError("signature must not be empty")
        if not self.registered_by or not self.registered_by.strip():
            raise ValueError("registered_by must not be empty")

    @property
    def versioned_id(self) -> str:
        return f"{self.tool_id}@{self.version}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_id": self.tool_id,
            "version": self.version,
            "versioned_id": self.versioned_id,
            "content_hash": self.content_hash,
            "signature": self.signature,
            "capability": self.capability,
            "risk_tier": self.risk_tier.value,
            "registered_by": self.registered_by,
            "source_type": self.source_type,
            "server_id": self.server_id,
            "description": self.description,
            "input_schema": self.input_schema,
            "output_schema": self.output_schema,
            "lifecycle_state": self.lifecycle_state.value,
            "metadata": self.metadata,
            "created_at": self.created_at.isoformat(),
        }

