"""Resource lease lifecycle management.

spec §9 (Resource Manager), §16 (Lease), CONTRACT_MATRIX RESOURCE-002..RESOURCE-004 — Phase 3
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

from core.resources.clock import Clock, SystemClock
from core.resources.identity import ResourceIdentity


class LeaseState(str, Enum):
    """Lifecycle states for a resource lease."""

    ACTIVE = "active"
    EXPIRED = "expired"
    RELEASED = "released"
    REVOKED = "revoked"


@dataclass
class Lease:
    """Authoritative grant lease issued by the ResourceManager."""

    lease_token: str
    resource_id: ResourceIdentity
    space_id: str
    requester_id: str
    acquired_at: datetime
    expiry: datetime
    units: int = 1
    idempotency_key: str | None = None
    state: LeaseState = LeaseState.ACTIVE
    renewed_count: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    def is_valid(self, at_time: datetime) -> bool:
        """A lease is valid only if active and before its expiration."""
        return self.state == LeaseState.ACTIVE and at_time < self.expiry

    def to_dict(self) -> dict[str, Any]:
        """Serialize matching the contract schema in grants.json."""
        return {
            "lease_token": self.lease_token,
            "resource_id": self.resource_id.to_dict(),
            "space_id": self.space_id,
            "requester_id": self.requester_id,
            "acquired_at": self.acquired_at.isoformat(),
            "expiry": self.expiry.isoformat(),
            "units": self.units,
            "idempotency_key": self.idempotency_key,
            "state": self.state.value,
            "renewed_count": self.renewed_count,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Lease:
        """Reconstruct Lease from dict representation."""
        acquired_at = datetime.fromisoformat(data["acquired_at"])
        if acquired_at.tzinfo is None:
            acquired_at = acquired_at.replace(tzinfo=timezone.utc)

        expiry = datetime.fromisoformat(data["expiry"])
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)

        return cls(
            lease_token=data["lease_token"],
            resource_id=ResourceIdentity.from_dict(data["resource_id"]),
            space_id=data["space_id"],
            requester_id=data["requester_id"],
            acquired_at=acquired_at,
            expiry=expiry,
            units=data.get("units", 1),
            idempotency_key=data.get("idempotency_key"),
            state=LeaseState(data.get("state", LeaseState.ACTIVE.value)),
            renewed_count=data.get("renewed_count", 0),
            metadata=data.get("metadata", {}),
        )


class LeaseManager:
    """Manages active leases, validations, renewals, releases, and expiration sweeps."""

    def __init__(self, clock: Clock | None = None) -> None:
        self._clock: Clock = clock or SystemClock()
        self._leases: dict[str, Lease] = {}
        self._idempotency_index: dict[tuple[str, str, str], str] = {}

    @property
    def clock(self) -> Clock:
        return self._clock

    def issue_lease(
        self,
        resource_id: ResourceIdentity,
        space_id: str,
        requester_id: str,
        duration_seconds: float = 60.0,
        units: int = 1,
        idempotency_key: str | None = None,
        lease_token: str | None = None,
    ) -> Lease:
        """Issue a new active lease for a resource."""
        now = self._clock.now()
        token = lease_token or f"lease-{uuid.uuid4()}"
        expiry = now + timedelta(seconds=duration_seconds)

        lease = Lease(
            lease_token=token,
            resource_id=resource_id,
            space_id=space_id,
            requester_id=requester_id,
            acquired_at=now,
            expiry=expiry,
            units=units,
            idempotency_key=idempotency_key,
            state=LeaseState.ACTIVE,
            renewed_count=0,
        )
        self._leases[token] = lease
        if idempotency_key:
            self._idempotency_index[(space_id, requester_id, idempotency_key)] = token

        return lease

    def get_lease(self, lease_token: str) -> Lease | None:
        return self._leases.get(lease_token)

    def get_by_idempotency(
        self, space_id: str, requester_id: str, idempotency_key: str
    ) -> Lease | None:
        token = self._idempotency_index.get((space_id, requester_id, idempotency_key))
        if token:
            return self._leases.get(token)
        return None

    def renew_lease(
        self,
        space_id: str,
        requester_id: str,
        lease_token: str,
        extension_seconds: float = 60.0,
    ) -> Lease:
        """Renew an existing active lease.

        Enforces:
        - Space boundary matching (PermissionError on cross-space)
        - Holder identity matching (PermissionError on wrong holder)
        - Active state and non-expired check (ValueError if already expired)
        """
        lease = self._leases.get(lease_token)
        if not lease:
            raise KeyError(f"Lease {lease_token} not found")

        if lease.space_id != space_id:
            raise PermissionError(
                f"Cross-space lease renewal rejected: lease belongs to {lease.space_id}, "
                f"caller is in {space_id}"
            )

        if lease.requester_id != requester_id:
            raise PermissionError(
                f"Unauthorized lease renewal: lease held by {lease.requester_id}, "
                f"attempted by {requester_id}"
            )

        now = self._clock.now()
        if not lease.is_valid(now):
            raise ValueError(
                f"Cannot renew lease {lease_token}: lease is in state '{lease.state.value}' "
                "or already expired"
            )

        # Extend expiry from either current expiry or now (whichever is later)
        base_time = max(now, lease.expiry)
        lease.expiry = base_time + timedelta(seconds=extension_seconds)
        lease.renewed_count += 1
        return lease

    def release_lease(
        self,
        space_id: str,
        requester_id: str,
        lease_token: str,
    ) -> Lease:
        """Release a held lease explicitly.

        Enforces:
        - Space boundary matching
        - Holder identity matching
        - Transition to RELEASED state
        """
        lease = self._leases.get(lease_token)
        if not lease:
            raise KeyError(f"Lease {lease_token} not found")

        if lease.space_id != space_id:
            raise PermissionError(
                f"Cross-space lease release rejected: lease belongs to {lease.space_id}, "
                f"caller is in {space_id}"
            )

        if lease.requester_id != requester_id:
            raise PermissionError(
                f"Unauthorized lease release: lease held by {lease.requester_id}, "
                f"attempted by {requester_id}"
            )

        # Idempotent if already released
        if lease.state != LeaseState.RELEASED:
            lease.state = LeaseState.RELEASED

        return lease

    def revoke_lease(
        self,
        space_id: str,
        lease_token: str,
    ) -> Lease:
        """Revoke a lease authoritatively (Space authority)."""
        lease = self._leases.get(lease_token)
        if not lease:
            raise KeyError(f"Lease {lease_token} not found")

        if lease.space_id != space_id:
            raise PermissionError(
                f"Cross-space lease revocation rejected: lease belongs to {lease.space_id}, "
                f"caller is in {space_id}"
            )

        lease.state = LeaseState.REVOKED
        return lease

    def sweep_expirations(self) -> list[Lease]:
        """Scan all active leases and mark those past their expiry as EXPIRED."""
        now = self._clock.now()
        expired: list[Lease] = []
        for lease in self._leases.values():
            if lease.state == LeaseState.ACTIVE and now >= lease.expiry:
                lease.state = LeaseState.EXPIRED
                expired.append(lease)
        return expired

    def get_active_leases_for_resource(self, resource_id: ResourceIdentity) -> list[Lease]:
        now = self._clock.now()
        return [
            item for item in self._leases.values()
            if item.resource_id == resource_id and item.is_valid(now)
        ]

    def list_all_leases(self, space_id: str | None = None) -> list[Lease]:
        if space_id:
            return [item for item in self._leases.values() if item.space_id == space_id]
        return list(self._leases.values())
