"""Contracts, data models, enums, and exceptions for RYU Node Runtime.

Space-Centric Cognitive Architecture (SCCA) — Phase 7
spec §11 (Node Runtime), §16 (Lease & Grants), CONTRACT_MATRIX NODE-001..NODE-008
ADR-0017, ADR-0018, ADR-0019, ADR-0020
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class RiskTier(str, Enum):
    """Capability risk tier classification."""

    LOW = "low"
    HIGH = "high"


class NodeState(str, Enum):
    """Lifecycle states of a physical or virtual compute node."""

    REGISTERED = "registered"
    READY = "ready"
    ACTIVE = "active"
    DRAINING = "draining"
    OFFLINE = "offline"
    TERMINATED = "terminated"


class DeviceType(str, Enum):
    """Types of devices enumerated and managed on a node."""

    CPU = "cpu"
    GPU = "gpu"
    STORAGE = "storage"
    NETWORK = "network"
    SCREEN = "screen"
    TERMINAL = "terminal"
    FILESYSTEM = "filesystem"


class DeviceState(str, Enum):
    """Availability and health state of a device on a node."""

    DISCOVERED = "discovered"
    ONLINE = "online"
    BUSY = "busy"
    OFFLINE = "offline"
    FAILED = "failed"


class GrantState(str, Enum):
    """Deterministic 5-state lifecycle for a DeviceGrant."""

    GRANTED = "granted"
    BOUND = "bound"
    RELEASED = "released"
    REVOKED = "revoked"
    EXPIRED = "expired"


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class NodeError(Exception):
    """Base exception for all Node Runtime errors."""


class NodeRegistrationError(NodeError):
    """Error raised on invalid or conflicting node registration."""


class DeviceError(NodeError):
    """Base exception for device-level errors."""


class DeviceNotFoundError(DeviceError):
    """Error raised when a referenced device does not exist on the node."""


class DeviceUnavailableError(DeviceError):
    """Error raised when a device is busy, offline, or failed."""


class DeviceBindingError(DeviceError):
    """Error raised when device binding fails or conflicts."""


class GrantError(NodeError):
    """Base exception for device capability grant errors."""


class GrantInvalidError(GrantError):
    """Error raised when a grant is malformed, cross-space, cross-node, or tampered."""


class GrantExpiredError(GrantError):
    """Error raised when a grant or its backing lease has expired."""


class GrantRevokedError(GrantError):
    """Error raised when a grant or its backing lease has been revoked."""


class LeaseNotFoundError(GrantError):
    """Error raised when attempting to create a grant without a backing lease."""


class LeaseInvalidError(GrantError):
    """Error raised when backing lease is inactive, expired, or mismatched."""


class AuditCorruptionError(NodeError):
    """Error raised when audit log SHA-256 hash chaining verification fails."""


class RustBridgeError(NodeError):
    """Error raised on Rust ryu-node bridge invocation failure or unauthorized command."""


class NodeOfflineError(NodeError):
    """Error raised when an operation requires an active node, but node is offline."""


# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------


def compute_grant_signature(payload: str, secret: str) -> str:
    """Compute HMAC-SHA256 signature for canonical grant payload."""
    h = hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256)
    return h.hexdigest()


def verify_grant_signature(payload: str, signature: str, secret: str) -> bool:
    """Verify HMAC-SHA256 signature in constant time."""
    expected = compute_grant_signature(payload, secret)
    return hmac.compare_digest(expected, signature)


@dataclass
class NodeInfo:
    """Canonical descriptor of a registered compute node."""

    node_id: str
    platform: str
    architecture: str
    environment_profile: str
    runtime_state: NodeState = NodeState.REGISTERED
    cpu_cores: int = 1
    memory_total_bytes: int = 0
    storage_total_bytes: int = 0
    capabilities: list[str] = field(default_factory=list)
    labels: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "platform": self.platform,
            "architecture": self.architecture,
            "environment_profile": self.environment_profile,
            "runtime_state": self.runtime_state.value,
            "cpu_cores": self.cpu_cores,
            "memory_total_bytes": self.memory_total_bytes,
            "storage_total_bytes": self.storage_total_bytes,
            "capabilities": list(self.capabilities),
            "labels": dict(self.labels),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> NodeInfo:
        return cls(
            node_id=data["node_id"],
            platform=data["platform"],
            architecture=data["architecture"],
            environment_profile=data.get("environment_profile", "unknown"),
            runtime_state=NodeState(data.get("runtime_state", NodeState.REGISTERED.value)),
            cpu_cores=data.get("cpu_cores", 1),
            memory_total_bytes=data.get("memory_total_bytes", 0),
            storage_total_bytes=data.get("storage_total_bytes", 0),
            capabilities=list(data.get("capabilities", [])),
            labels=dict(data.get("labels", {})),
        )


@dataclass
class DeviceInfo:
    """Canonical descriptor of a hardware or virtual device on a node."""

    device_id: str
    node_id: str
    device_type: DeviceType
    capability_metadata: dict[str, str] = field(default_factory=dict)
    availability_state: DeviceState = DeviceState.ONLINE
    total_capacity: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "device_id": self.device_id,
            "node_id": self.node_id,
            "device_type": self.device_type.value,
            "capability_metadata": dict(self.capability_metadata),
            "availability_state": self.availability_state.value,
            "total_capacity": self.total_capacity,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DeviceInfo:
        return cls(
            device_id=data["device_id"],
            node_id=data["node_id"],
            device_type=DeviceType(data["device_type"]),
            capability_metadata=dict(data.get("capability_metadata", {})),
            availability_state=DeviceState(
                data.get("availability_state", DeviceState.ONLINE.value)
            ),
            total_capacity=data.get("total_capacity", 1),
        )


@dataclass
class DeviceGrant:
    """Authoritatively derived execution credential for Worker ↔ Node execution.

    Materialized by DeviceGrantManager against an active ResourceManager Lease.
    """

    grant_id: str
    space_id: str
    worker_id: str
    node_id: str
    device_id: str
    capability: str
    lease_token: str
    nonce: str
    issued_at: str
    expiry: str
    risk_tier: RiskTier
    grant_schema_version: str = "1.0"
    revocation_token: str = ""
    signature: str = ""
    state: GrantState = GrantState.GRANTED

    def canonical_payload(self) -> str:
        """Construct canonical pipe-delimited payload matching Rust GrantVerifier."""
        return (
            f"{self.grant_schema_version}|{self.grant_id}|{self.space_id}|{self.worker_id}|"
            f"{self.node_id}|{self.device_id}|{self.capability}|{self.lease_token}|"
            f"{self.nonce}|{self.issued_at}|{self.expiry}|{self.risk_tier.value}"
        )

    def sign(self, secret: str) -> None:
        """Compute and set HMAC signature using the paired node secret."""
        self.signature = compute_grant_signature(self.canonical_payload(), secret)

    def verify(self, secret: str) -> bool:
        """Verify HMAC signature in constant time against the paired node secret."""
        if not self.signature:
            return False
        return verify_grant_signature(self.canonical_payload(), self.signature, secret)

    def is_expired(self, current_time: datetime | None = None) -> bool:
        """Check if grant expiry timestamp has elapsed."""
        now = current_time or datetime.now(timezone.utc)
        try:
            exp = datetime.fromisoformat(self.expiry)
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=timezone.utc)
            return now >= exp
        except Exception:
            return True

    def to_dict(self) -> dict[str, Any]:
        return {
            "grant_id": self.grant_id,
            "space_id": self.space_id,
            "worker_id": self.worker_id,
            "node_id": self.node_id,
            "device_id": self.device_id,
            "capability": self.capability,
            "lease_token": self.lease_token,
            "nonce": self.nonce,
            "issued_at": self.issued_at,
            "expiry": self.expiry,
            "risk_tier": self.risk_tier.value,
            "grant_schema_version": self.grant_schema_version,
            "revocation_token": self.revocation_token,
            "signature": self.signature,
            "state": self.state.value,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DeviceGrant:
        return cls(
            grant_id=data["grant_id"],
            space_id=data["space_id"],
            worker_id=data["worker_id"],
            node_id=data["node_id"],
            device_id=data["device_id"],
            capability=data["capability"],
            lease_token=data["lease_token"],
            nonce=data["nonce"],
            issued_at=data["issued_at"],
            expiry=data["expiry"],
            risk_tier=RiskTier(data["risk_tier"]),
            grant_schema_version=data.get("grant_schema_version", "1.0"),
            revocation_token=data.get("revocation_token", ""),
            signature=data.get("signature", ""),
            state=GrantState(data.get("state", GrantState.GRANTED.value)),
        )


@dataclass
class DeviceBinding:
    """Active execution binding between an authorized Worker, Grant, and physical Device."""

    binding_id: str
    grant_id: str
    node_id: str
    device_id: str
    space_id: str
    worker_id: str
    bound_at: str
    released_at: str | None = None
    is_active: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "binding_id": self.binding_id,
            "grant_id": self.grant_id,
            "node_id": self.node_id,
            "device_id": self.device_id,
            "space_id": self.space_id,
            "worker_id": self.worker_id,
            "bound_at": self.bound_at,
            "released_at": self.released_at,
            "is_active": self.is_active,
        }


@dataclass
class NodeHealthReport:
    """Health and resource utilization metrics reported by a node."""

    node_id: str
    status: str
    cpu_percent: float
    memory_used_bytes: int
    memory_total_bytes: int
    uptime_seconds: int
    active_bindings_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "status": self.status,
            "cpu_percent": self.cpu_percent,
            "memory_used_bytes": self.memory_used_bytes,
            "memory_total_bytes": self.memory_total_bytes,
            "uptime_seconds": self.uptime_seconds,
            "active_bindings_count": self.active_bindings_count,
        }


@dataclass
class AuditRecord:
    """Device-local append-only audit record protected by SHA-256 hash chaining."""

    seq: int
    timestamp: str
    node_id: str
    space_id: str
    event_type: str
    grant_id: str
    device_id: str
    operation_id: str
    result: str
    prev_hash: str
    record_hash: str

    def compute_hash(self) -> str:
        """Compute SHA-256 over record components matching Rust DeviceAuditLogger."""
        payload = (
            f"{self.seq}|{self.timestamp}|{self.node_id}|{self.space_id}|"
            f"{self.event_type}|{self.grant_id}|{self.device_id}|"
            f"{self.operation_id}|{self.result}|{self.prev_hash}"
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "timestamp": self.timestamp,
            "node_id": self.node_id,
            "space_id": self.space_id,
            "event_type": self.event_type,
            "grant_id": self.grant_id,
            "device_id": self.device_id,
            "operation_id": self.operation_id,
            "result": self.result,
            "prev_hash": self.prev_hash,
            "record_hash": self.record_hash,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AuditRecord:
        return cls(
            seq=data["seq"],
            timestamp=data["timestamp"],
            node_id=data["node_id"],
            space_id=data["space_id"],
            event_type=data["event_type"],
            grant_id=data["grant_id"],
            device_id=data["device_id"],
            operation_id=data["operation_id"],
            result=data["result"],
            prev_hash=data["prev_hash"],
            record_hash=data["record_hash"],
        )
