"""Node Runtime and Device Binding Execution Boundary.

Device-side grant verification, hardware binding, and local audit logging.
spec §11 (Node Runtime), CONTRACT_MATRIX NODE-002, NODE-003, NODE-004, NODE-008
ADR-0017, ADR-0018, ADR-0019

INVARIANT:
NodeRuntime is strictly an execution boundary, NOT an authority boundary.
Device bindings require valid Space-scoped, lease-backed DeviceGrants with
cryptographic HMAC-SHA256 signatures.
"""

from __future__ import annotations

import threading
import uuid
from datetime import datetime, timezone

from ryu.pulse_bus.bus import PulseBus
from ryu.pulse_bus.pulse import Pulse, Severity

from node.audit import DeviceAuditLog
from node.bridge import RustNodeBridge
from node.contract import (
    AuditCorruptionError,
    DeviceBinding,
    DeviceBindingError,
    DeviceGrant,
    DeviceNotFoundError,
    DeviceState,
    DeviceUnavailableError,
    GrantExpiredError,
    GrantInvalidError,
    GrantRevokedError,
    GrantState,
    NodeError,
    NodeState,
    NodeTrustTier,
    RestrictedNodePolicy,
)
from node.policy.engine import DevicePolicyEngine
from node.registry import NodeRegistry


class NodeRuntime:
    """Device-side execution and grant enforcement boundary on the node.

    Enforces:
    - Target node ID matching (cross-node grant rejection)
    - Space isolation matching (cross-space grant rejection)
    - Worker identity matching
    - Cryptographic HMAC-SHA256 signature verification in constant time
    - Revocation checking (in-flight revocation halts execution)
    - Replay protection (released or expired grants cannot be re-bound)
    - Exclusive device binding table
    - Local append-only audit logging with hash chain verification
    """

    def __init__(
        self,
        node_id: str,
        shared_secret: str,
        registry: NodeRegistry,
        audit_log: DeviceAuditLog,
        bridge: RustNodeBridge | None = None,
        bus: PulseBus | None = None,
        trust_tier: NodeTrustTier = NodeTrustTier.FULL_TRUST,
        policy: RestrictedNodePolicy | None = None,
    ) -> None:
        self.node_id = node_id
        self.shared_secret = shared_secret
        self.registry = registry
        self.audit_log = audit_log
        self.bridge = bridge
        self.bus = bus
        self.trust_tier = trust_tier
        self.policy = policy

        self._lock = threading.RLock()
        self._active_bindings: dict[str, DeviceBinding] = {}
        self._binding_grants: dict[str, DeviceGrant] = {}
        self._device_bindings: dict[str, str] = {}  # device_id -> binding_id
        self._revocation_blacklist: set[str] = set()
        self._released_grants: set[str] = set()

    def sync_revocations(self, tokens_or_grant_ids: set[str] | list[str]) -> None:
        """Update local revocation blacklist with revoked tokens or grant IDs."""
        with self._lock:
            self._revocation_blacklist.update(tokens_or_grant_ids)

    def bind_device(
        self,
        worker_id: str,
        space_id: str,
        grant: DeviceGrant,
        device_id: str,
        current_time: datetime | None = None,
    ) -> DeviceBinding:
        """Verify grant and bind device for authorized execution.

        Raises:
            NodeError: if node is draining, offline, or terminated.
            DeviceNotFoundError: if device is not registered.
            DeviceUnavailableError: if device is busy, failed, or offline.
            DeviceBindingError: if device is already actively bound.
            GrantInvalidError: if grant is cross-node, cross-space, replayed, or forged.
            GrantRevokedError: if grant has been revoked.
            GrantExpiredError: if grant has expired.
            AuditCorruptionError: if audit log tampering is detected.
        """
        now = current_time or datetime.now(timezone.utc)
        now_iso = now.isoformat()

        with self._lock:
            # 1. Node Lifecycle State Verification
            node = self.registry.get_node(self.node_id)
            if not node:
                raise NodeError(f"Node '{self.node_id}' is not registered.")
            if node.runtime_state in (
                NodeState.DRAINING,
                NodeState.OFFLINE,
                NodeState.TERMINATED,
            ):
                raise NodeError(
                    f"Node '{self.node_id}' is in state {node.runtime_state.value}; "
                    "rejecting new bindings."
                )

            # 2. Audit Log Integrity Check (Pre-execution gate)
            is_valid, count, err_msg = self.audit_log.verify_chain()
            if not is_valid:
                if self.bus:
                    self._publish_taint_pulse(
                        space_id=space_id,
                        reason=f"Audit chain corruption detected: {err_msg}",
                    )
                raise AuditCorruptionError(
                    f"Audit log tampering detected on node '{self.node_id}': {err_msg}"
                )

            # 3. Target Node Verification
            if grant.node_id != self.node_id:
                raise GrantInvalidError(
                    f"Cross-node grant rejected: grant target node '{grant.node_id}' "
                    f"!= '{self.node_id}'."
                )

            # 4. Space Isolation Verification
            if grant.space_id != space_id:
                raise GrantInvalidError(
                    f"Cross-space grant rejected: grant space '{grant.space_id}' "
                    f"!= caller space '{space_id}'."
                )

            # 5. Worker Identity Verification
            if grant.worker_id != worker_id:
                raise GrantInvalidError(
                    f"Worker mismatch: grant worker '{grant.worker_id}' "
                    f"!= caller worker '{worker_id}'."
                )

            # 6. Device Identity Verification
            if grant.device_id != device_id:
                raise GrantInvalidError(
                    f"Device mismatch: grant device '{grant.device_id}' "
                    f"!= target device '{device_id}'."
                )

            device = self.registry.get_device(device_id)
            if not device or device.node_id != self.node_id:
                raise DeviceNotFoundError(
                    f"Device '{device_id}' does not exist on node '{self.node_id}'."
                )

            # 7. Device Availability Verification
            if device_id in self._device_bindings:
                existing_bid = self._device_bindings[device_id]
                raise DeviceBindingError(
                    f"Device '{device_id}' is already actively bound to binding '{existing_bid}'."
                )

            if device.availability_state != DeviceState.ONLINE:
                raise DeviceUnavailableError(
                    f"Device '{device_id}' is not online (state={device.availability_state.value})."
                )

            # 8. Grant State & Expiry Verification
            if (
                grant.grant_id in self._revocation_blacklist
                or grant.revocation_token in self._revocation_blacklist
                or grant.state == GrantState.REVOKED
            ):
                self._record_audit(
                    space_id=space_id,
                    event_type="BIND_DENIED",
                    grant_id=grant.grant_id,
                    device_id=device_id,
                    op_id="bind",
                    result="DENIED: Grant revoked",
                    timestamp=now_iso,
                )
                raise GrantRevokedError(f"Grant '{grant.grant_id}' has been revoked.")

            if grant.grant_id in self._released_grants or grant.state == GrantState.RELEASED:
                self._record_audit(
                    space_id=space_id,
                    event_type="BIND_DENIED",
                    grant_id=grant.grant_id,
                    device_id=device_id,
                    op_id="bind",
                    result="DENIED: Grant already released (replay)",
                    timestamp=now_iso,
                )
                raise GrantInvalidError(
                    f"Grant '{grant.grant_id}' has already been released; reuse rejected."
                )

            if grant.state == GrantState.EXPIRED or grant.is_expired(now):
                grant.state = GrantState.EXPIRED
                self._record_audit(
                    space_id=space_id,
                    event_type="BIND_DENIED",
                    grant_id=grant.grant_id,
                    device_id=device_id,
                    op_id="bind",
                    result="DENIED: Grant expired",
                    timestamp=now_iso,
                )
                raise GrantExpiredError(f"Grant '{grant.grant_id}' has expired.")

            # 9. Cryptographic HMAC-SHA256 Signature Verification
            if not grant.verify(self.shared_secret):
                self._record_audit(
                    space_id=space_id,
                    event_type="BIND_DENIED",
                    grant_id=grant.grant_id,
                    device_id=device_id,
                    op_id="bind",
                    result="DENIED: Invalid HMAC signature",
                    timestamp=now_iso,
                )
                raise GrantInvalidError(
                    f"Cryptographic signature verification failed for grant '{grant.grant_id}'."
                )

            # 10. MDM / Restricted-Node Policy Evaluation
            # INVARIANT: MDM_ALLOW != Authentication.
            # MDM policy enforcement is an additional local policy constraint.
            # MDM_ALLOW alone is NOT sufficient.
            # valid_DeviceGrant alone is NOT sufficient on a Restricted node if MDM denies.
            # MDM_DENY -> binding denied.
            if node.trust_tier == NodeTrustTier.RESTRICTED or self.trust_tier == NodeTrustTier.RESTRICTED:
                effective_policy = self.policy or node.policy
                permitted, reason = DevicePolicyEngine.evaluate(
                    trust_tier=NodeTrustTier.RESTRICTED,
                    policy=effective_policy,
                    capability=grant.capability,
                )
                if not permitted:
                    self._record_audit(
                        space_id=space_id,
                        event_type="BIND_DENIED",
                        grant_id=grant.grant_id,
                        device_id=device_id,
                        op_id="bind",
                        result=f"DENIED: MDM policy restriction ({reason})",
                        timestamp=now_iso,
                    )
                    raise GrantInvalidError(
                        f"MDM policy violation on node '{self.node_id}': {reason}"
                    )

            # 11. Perform Binding
            binding_id = f"bind-{uuid.uuid4().hex[:12]}"
            binding = DeviceBinding(
                binding_id=binding_id,
                grant_id=grant.grant_id,
                node_id=self.node_id,
                device_id=device_id,
                space_id=space_id,
                worker_id=worker_id,
                bound_at=now_iso,
            )

            self._active_bindings[binding_id] = binding
            self._binding_grants[binding_id] = grant
            self._device_bindings[device_id] = binding_id
            self.registry.set_device_state(device_id, DeviceState.BUSY)
            grant.state = GrantState.BOUND

            # 11. Append to Audit Log
            self._record_audit(
                space_id=space_id,
                event_type="DEVICE_BOUND",
                grant_id=grant.grant_id,
                device_id=device_id,
                op_id=binding_id,
                result="BOUND_SUCCESS",
                timestamp=now_iso,
            )

            return binding

    def release_device(
        self, binding_id: str, current_time: datetime | None = None
    ) -> None:
        """Release an active device binding."""
        now = current_time or datetime.now(timezone.utc)
        now_iso = now.isoformat()

        with self._lock:
            binding = self._active_bindings.get(binding_id)
            if not binding or not binding.is_active:
                return  # Idempotent release

            binding.is_active = False
            binding.released_at = now_iso

            # Update grant state to RELEASED
            grant = self._binding_grants.get(binding_id)
            if grant:
                grant.state = GrantState.RELEASED
                self._released_grants.add(grant.grant_id)

            # Remove from device table
            if self._device_bindings.get(binding.device_id) == binding_id:
                del self._device_bindings[binding.device_id]

            # Set device state back to ONLINE
            try:
                self.registry.set_device_state(binding.device_id, DeviceState.ONLINE)
            except Exception:
                pass

            # Append to Audit Log
            self._record_audit(
                space_id=binding.space_id,
                event_type="DEVICE_RELEASED",
                grant_id=binding.grant_id,
                device_id=binding.device_id,
                op_id=binding_id,
                result="RELEASED_SUCCESS",
                timestamp=now_iso,
            )

    def revoke_in_flight(self, grant_id: str) -> None:
        """Enforce mid-call revocation (NODE-004): immediate halt and release."""
        now_iso = datetime.now(timezone.utc).isoformat()
        with self._lock:
            self._revocation_blacklist.add(grant_id)

            # Terminate active bindings for this grant
            affected_bindings = [
                b for b in self._active_bindings.values()
                if b.grant_id == grant_id and b.is_active
            ]
            for binding in affected_bindings:
                binding.is_active = False
                binding.released_at = now_iso
                if self._device_bindings.get(binding.device_id) == binding.binding_id:
                    del self._device_bindings[binding.device_id]
                try:
                    self.registry.set_device_state(binding.device_id, DeviceState.ONLINE)
                except Exception:
                    pass

                self._record_audit(
                    space_id=binding.space_id,
                    event_type="GRANT_REVOKED",
                    grant_id=grant_id,
                    device_id=binding.device_id,
                    op_id=binding.binding_id,
                    result="HALTED_MID_CALL",
                    timestamp=now_iso,
                )

    def get_active_binding(self, binding_id: str) -> DeviceBinding | None:
        """Retrieve active binding by binding_id."""
        with self._lock:
            b = self._active_bindings.get(binding_id)
            return b if (b and b.is_active) else None

    def _record_audit(
        self,
        space_id: str,
        event_type: str,
        grant_id: str,
        device_id: str,
        op_id: str,
        result: str,
        timestamp: str,
    ) -> None:
        try:
            self.audit_log.append(
                node_id=self.node_id,
                space_id=space_id,
                event_type=event_type,
                grant_id=grant_id,
                device_id=device_id,
                operation_id=op_id,
                result=result,
                timestamp=timestamp,
            )
        except Exception:
            pass

    def _publish_taint_pulse(self, space_id: str, reason: str) -> None:
        if not self.bus:
            return
        corr_id = f"corr-taint-{space_id}"
        pulse = Pulse(
            type="security.taint.detected",
            severity=Severity.WARNING,
            space_id=space_id,
            source="node_runtime",
            correlation_id=corr_id,
            payload={
                "correlation_id": corr_id,
                "source_pulse_id": f"node:{self.node_id}",
                "reason": reason,
            },
            timestamp=datetime.now(timezone.utc),
        )
        self.bus.publish(pulse)
