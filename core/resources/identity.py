"""Resource identity and descriptor definitions.

Resource identity is (resource_type, provider_id, instance_id).
Manager-issued handles are the only authoritative reference.
spec §9 (Resource Manager), §16 (Lease), CONTRACT_MATRIX RESOURCE-001 — Phase 3
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ResourceIdentity:
    """Canonical three-tuple identity for any hardware or software resource."""

    resource_type: str  # e.g., "gpu", "cpu", "node_capability", "model"
    provider_id: str    # e.g., "local-host", "node-windows-1", "cloud-provider"
    instance_id: str    # e.g., "cuda-0", "cpu-core-2", "gpt-4o"

    def __post_init__(self) -> None:
        if not self.resource_type or not self.resource_type.strip():
            raise ValueError("resource_type cannot be empty")
        if not self.provider_id or not self.provider_id.strip():
            raise ValueError("provider_id cannot be empty")
        if not self.instance_id or not self.instance_id.strip():
            raise ValueError("instance_id cannot be empty")

    def to_handle(self) -> str:
        """Return canonical string handle: resource_type/provider_id/instance_id."""
        return f"{self.resource_type}/{self.provider_id}/{self.instance_id}"

    def to_dict(self) -> dict[str, str]:
        """Convert to dict matching schema definition in contracts/registry/grants.json."""
        return {
            "resource_type": self.resource_type,
            "provider_id": self.provider_id,
            "instance_id": self.instance_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, str]) -> ResourceIdentity:
        """Construct ResourceIdentity from a dict."""
        return cls(
            resource_type=data["resource_type"],
            provider_id=data["provider_id"],
            instance_id=data["instance_id"],
        )

    @classmethod
    def from_handle(cls, handle: str) -> ResourceIdentity:
        """Parse canonical string handle into ResourceIdentity."""
        parts = handle.split("/", 2)
        if len(parts) != 3:
            raise ValueError(
                f"Invalid resource handle format: {handle}. Expected type/provider/instance."
            )
        return cls(
            resource_type=parts[0],
            provider_id=parts[1],
            instance_id=parts[2],
        )

    def __str__(self) -> str:
        return self.to_handle()


@dataclass
class Resource:
    """Registered resource managed by the ResourceManager within a Space."""

    identity: ResourceIdentity
    space_id: str
    total_capacity: int = 1
    allocated_capacity: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.total_capacity < 1:
            raise ValueError("total_capacity must be at least 1")
        if self.allocated_capacity < 0:
            raise ValueError("allocated_capacity cannot be negative")
        if self.allocated_capacity > self.total_capacity:
            raise ValueError("allocated_capacity cannot exceed total_capacity")

    @property
    def handle(self) -> str:
        return self.identity.to_handle()

    @property
    def available_capacity(self) -> int:
        return self.total_capacity - self.allocated_capacity

    @property
    def is_available(self) -> bool:
        return self.available_capacity > 0

    def allocate(self, units: int = 1) -> None:
        """Allocate capacity units atomically."""
        if units < 1:
            raise ValueError("Allocation units must be >= 1")
        if self.allocated_capacity + units > self.total_capacity:
            raise ValueError(
                f"Cannot allocate {units} units on {self.handle}: "
                f"only {self.available_capacity} of {self.total_capacity} available."
            )
        self.allocated_capacity += units

    def deallocate(self, units: int = 1) -> None:
        """Release allocated capacity units."""
        if units < 1:
            raise ValueError("Deallocation units must be >= 1")
        if self.allocated_capacity - units < 0:
            raise ValueError(
                f"Cannot deallocate {units} units on {self.handle}: "
                f"only {self.allocated_capacity} allocated."
            )
        self.allocated_capacity -= units
