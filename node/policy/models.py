"""Data models for device-side MDM policy enforcement.

CONTRACT_MATRIX NODE-012, ADR-0039 — Phase 11
INVARIANT:
MDM_ALLOW != Authentication.
MDM policy enforcement is an additional local policy constraint.
It does not authenticate a DeviceGrant, establish Space membership,
establish node identity, create a lease, or authorize a capability by itself.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class NodeTrustTier(str, Enum):
    """Trust tier classification of a compute node."""

    FULL_TRUST = "full_trust"
    RESTRICTED = "restricted"


@dataclass
class RestrictedNodePolicy:
    """Device-side capability allow-list policy enforced on RESTRICTED nodes."""

    policy_id: str
    allowed_capabilities: set[str] = field(default_factory=set)
    denied_capabilities: set[str] = field(default_factory=set)
    allowed_storage_paths: list[str] = field(default_factory=list)
    allow_terminal_exec: bool = False

    def is_capability_permitted(self, capability: str) -> tuple[bool, str | None]:
        """Evaluate if capability is permitted under this local policy.

        Returns (True, None) if permitted, or (False, error_reason) if denied.
        """
        if capability in self.denied_capabilities:
            return False, f"Capability '{capability}' explicitly denied by device MDM policy '{self.policy_id}'."

        if self.allowed_capabilities and capability not in self.allowed_capabilities:
            return False, f"Capability '{capability}' not in allow-list for device MDM policy '{self.policy_id}'."

        return True, None

    def to_dict(self) -> dict[str, Any]:
        return {
            "policy_id": self.policy_id,
            "allowed_capabilities": sorted(list(self.allowed_capabilities)),
            "denied_capabilities": sorted(list(self.denied_capabilities)),
            "allowed_storage_paths": list(self.allowed_storage_paths),
            "allow_terminal_exec": self.allow_terminal_exec,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RestrictedNodePolicy:
        return cls(
            policy_id=data["policy_id"],
            allowed_capabilities=set(data.get("allowed_capabilities", [])),
            denied_capabilities=set(data.get("denied_capabilities", [])),
            allowed_storage_paths=list(data.get("allowed_storage_paths", [])),
            allow_terminal_exec=data.get("allow_terminal_exec", False),
        )

