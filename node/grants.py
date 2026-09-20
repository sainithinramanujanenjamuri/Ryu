"""Device Grant Lifecycle Manager.

Materializes derived execution credentials for Worker ↔ Node capability execution
against active ResourceManager leases.
spec §11 (Node Runtime), §16 (Lease & Grants), CONTRACT_MATRIX NODE-003, NODE-004
ADR-0017, ADR-0018

INVARIANT:
DeviceGrantManager is strictly a credential materializer, NOT an independent authority.
Authority flow: Space Kernel / Admission Control -> ResourceManager ->
                Lease -> DeviceGrantManager -> DeviceGrant.
"""

from __future__ import annotations

import secrets
import threading
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from ryu.pulse_bus.bus import PulseBus
from ryu.pulse_bus.pulse import Pulse, Severity

from core.resources.lease import Lease, LeaseState
from core.resources.manager import ResourceManager
from node.contract import (
    DeviceGrant,
    GrantExpiredError,
    GrantInvalidError,
    GrantRevokedError,
    GrantState,
    LeaseInvalidError,
    LeaseNotFoundError,
    RiskTier,
)
from node.registry import NodeRegistry


class DeviceGrantManager:
    """Manages the creation, verification, and revocation lifecycle of DeviceGrants.

    Enforces:
    - Mandatory active backing Lease validation in ResourceManager
    - Strict Space and Worker isolation
    - Cryptographic HMAC-SHA256 signing using node pairing secrets
    - Deterministic 5-state lifecycle: GRANTED -> BOUND -> RELEASED | REVOKED | EXPIRED
    - Dual invalidation flow: lease revocation automatically invalidates grant
    - Schema-compliant Pulse publication on grant and revocation events
    """

    def __init__(
        self,
        registry: NodeRegistry,
        resource_manager: ResourceManager,
        bus: PulseBus | None = None,
    ) -> None:
        self.registry = registry
        self.resource_manager = resource_manager
        self.bus = bus
        self._lock = threading.RLock()
        self._grants: dict[str, DeviceGrant] = {}
        self._revoked_tokens: set[str] = set()
        self._revoked_grant_ids: set[str] = set()

    def create_grant(
        self,
        space_id: str,
        worker_id: str,
        node_id: str,
        device_id: str,
        capability: str,
        lease_token: str,
        risk_tier: RiskTier = RiskTier.LOW,
        duration_seconds: float = 300.0,
    ) -> DeviceGrant:
        """Materialize a signed DeviceGrant backed by an active ResourceManager lease.

        Raises:
            LeaseNotFoundError: if the specified lease does not exist.
            LeaseInvalidError: if the lease is inactive, expired, or mismatched.
            GrantInvalidError: if node is unregistered or pairing secret is missing.
        """
        with self._lock:
            # 1. Authoritative Backing Lease Verification
            lease = self._get_backing_lease(lease_token)
            if not lease:
                raise LeaseNotFoundError(
                    f"Cannot issue DeviceGrant: backing lease '{lease_token}' "
                    "not found in ResourceManager."
                )

            # Check Space isolation
            if lease.space_id != space_id:
                raise LeaseInvalidError(
                    f"Cross-space lease violation: lease space '{lease.space_id}' "
                    f"!= target space '{space_id}'."
                )

            # Check Worker identity
            if lease.requester_id != worker_id:
                raise LeaseInvalidError(
                    f"Lease requester mismatch: lease held by '{lease.requester_id}', "
                    f"grant requested for '{worker_id}'."
                )

            # Check Lease validity and expiry
            now = datetime.now(timezone.utc)
            if not lease.is_valid(now):
                raise LeaseInvalidError(
                    f"Backing lease '{lease_token}' is not active or has expired "
                    f"(state={lease.state.value})."
                )

            # Check Resource binding
            if (
                lease.resource_id.provider_id != node_id
                or lease.resource_id.instance_id != device_id
            ):
                raise LeaseInvalidError(
                    f"Lease resource mismatch: lease binds to '{lease.resource_id.to_handle()}', "
                    f"requested node '{node_id}' and device '{device_id}'."
                )

            # 2. Node & Secret Verification
            node = self.registry.get_node(node_id)
            if not node:
                raise GrantInvalidError(f"Target node '{node_id}' not found in registry.")

            secret = self.registry.get_pairing_secret(node_id)
            if not secret:
                raise GrantInvalidError(
                    f"Target node '{node_id}' does not have an established pairing secret."
                )

            # 3. Derive Grant
            grant_id = f"grant-{uuid.uuid4().hex[:12]}"
            nonce = secrets.token_hex(16)
            revocation_token = f"rev-{uuid.uuid4().hex[:16]}"
            issued_at_dt = now
            # Expiry cannot exceed backing lease expiry
            max_expiry_dt = issued_at_dt + timedelta(seconds=duration_seconds)
            expiry_dt = min(lease.expiry, max_expiry_dt)

            issued_at = issued_at_dt.isoformat()
            expiry = expiry_dt.isoformat()

            grant = DeviceGrant(
                grant_id=grant_id,
                space_id=space_id,
                worker_id=worker_id,
                node_id=node_id,
                device_id=device_id,
                capability=capability,
                lease_token=lease_token,
                nonce=nonce,
                issued_at=issued_at,
                expiry=expiry,
                risk_tier=risk_tier,
                grant_schema_version="1.0",
                revocation_token=revocation_token,
                state=GrantState.GRANTED,
            )

            # Cryptographically sign canonical payload
            grant.sign(secret)

            self._grants[grant_id] = grant

            # 4. Emit node.capability.granted Pulse
            if self.bus:
                self._publish_pulse(
                    type_="node.capability.granted",
                    severity=Severity.INFO,
                    space_id=space_id,
                    payload={
                        "node_id": node_id,
                        "capability": capability,
                        "risk_tier": risk_tier.value,
                        "expiry": expiry,
                    },
                )

            return grant

    def revoke_grant(self, grant_id: str, revoked_by: str) -> None:
        """Revoke an active grant authoritatively and invalidate backing lease.

        Emits node.capability.revoked Pulse.
        """
        with self._lock:
            grant = self._grants.get(grant_id)
            if not grant:
                raise GrantInvalidError(f"Grant '{grant_id}' not found.")

            if grant.state == GrantState.REVOKED:
                return  # Idempotent

            grant.state = GrantState.REVOKED
            self._revoked_tokens.add(grant.revocation_token)
            self._revoked_grant_ids.add(grant_id)

            # Revoke backing lease in ResourceManager
            try:
                self.resource_manager.revoke(
                    space_id=grant.space_id,
                    lease_token=grant.lease_token,
                    reason=f"grant_revoked_by_{revoked_by}",
                )
            except Exception:
                pass

            # Emit node.capability.revoked Pulse
            if self.bus:
                self._publish_pulse(
                    type_="node.capability.revoked",
                    severity=Severity.WARNING,
                    space_id=grant.space_id,
                    payload={
                        "node_id": grant.node_id,
                        "capability": grant.capability,
                        "revoked_by": revoked_by,
                    },
                )

    def get_grant(self, grant_id: str) -> DeviceGrant | None:
        """Retrieve grant by grant_id."""
        with self._lock:
            return self._grants.get(grant_id)

    def is_revoked(self, identifier: str) -> bool:
        """Check if grant_id or revocation_token is revoked."""
        with self._lock:
            return identifier in self._revoked_grant_ids or identifier in self._revoked_tokens

    def validate_grant_active(
        self, grant_id: str, current_time: datetime | None = None
    ) -> DeviceGrant:
        """Check if a grant is active, unrevoked, and backed by a valid lease.

        Raises:
            GrantRevokedError: if grant or backing lease is revoked.
            GrantExpiredError: if grant or backing lease is expired.
            GrantInvalidError: if grant is in an invalid state or missing.
        """
        with self._lock:
            grant = self._grants.get(grant_id)
            if not grant:
                raise GrantInvalidError(f"Grant '{grant_id}' not found.")

            if grant_id in self._revoked_grant_ids or grant.state == GrantState.REVOKED:
                raise GrantRevokedError(f"Grant '{grant_id}' has been revoked.")

            if grant.state == GrantState.RELEASED:
                raise GrantInvalidError(f"Grant '{grant_id}' has already been released.")

            now = current_time or datetime.now(timezone.utc)

            # Check grant timestamp expiry
            if grant.is_expired(now):
                grant.state = GrantState.EXPIRED
                raise GrantExpiredError(f"Grant '{grant_id}' has expired.")

            # Check backing lease in ResourceManager
            lease = self._get_backing_lease(grant.lease_token)
            if not lease:
                grant.state = GrantState.EXPIRED
                raise GrantExpiredError(f"Backing lease for grant '{grant_id}' no longer exists.")

            if lease.state == LeaseState.REVOKED:
                grant.state = GrantState.REVOKED
                self._revoked_grant_ids.add(grant_id)
                raise GrantRevokedError(f"Backing lease for grant '{grant_id}' was revoked.")

            if lease.state == LeaseState.RELEASED or not lease.is_valid(now):
                grant.state = GrantState.EXPIRED
                raise GrantExpiredError(
                    f"Backing lease for grant '{grant_id}' is no longer active "
                    f"({lease.state.value})."
                )

            return grant

    def _get_backing_lease(self, lease_token: str) -> Lease | None:
        """Retrieve lease from ResourceManager or its underlying LeaseManager/Store."""
        # Try ResourceManager helper if present
        if hasattr(self.resource_manager, "get_lease"):
            lease = self.resource_manager.get_lease(lease_token)
            if lease:
                return lease

        # Check internal _lease_manager
        if hasattr(self.resource_manager, "_lease_manager"):
            lease = self.resource_manager._lease_manager.get_lease(lease_token)
            if lease:
                return lease

        # Fallback to store
        if hasattr(self.resource_manager, "store"):
            return self.resource_manager.store.get_lease(lease_token)

        return None

    def _publish_pulse(
        self,
        type_: str,
        severity: Severity,
        space_id: str,
        payload: dict[str, Any],
    ) -> None:
        if not self.bus:
            return
        pulse = Pulse(
            type=type_,
            severity=severity,
            space_id=space_id,
            source="node_grant_manager",
            correlation_id=f"corr-grant-{space_id}",
            payload=payload,
            timestamp=datetime.now(timezone.utc),
        )
        self.bus.publish(pulse)
