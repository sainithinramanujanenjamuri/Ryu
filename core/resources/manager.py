"""RYU AI Resource Manager implementation.

Enforces deterministic ownership of contested resources inside Spaces.
Queue with lease model, atomic allocation, anti-starvation aging, and crash recovery.
spec §9 (Resource Manager), §16 (Lease), ROADMAP Phase 3, ADR-0005, ADR-0006.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any

from ryu.pulse_bus.bus import PulseBus
from ryu.pulse_bus.pulse import Pulse, Severity

from core.resources.clock import Clock, SystemClock
from core.resources.identity import Resource, ResourceIdentity
from core.resources.lease import Lease, LeaseManager, LeaseState
from core.resources.queue import QueueDiscipline, ResourceQueue
from core.resources.store import InMemoryResourceStore, ResourceStore


@dataclass
class ResourceAcquisitionResult:
    """Outcome of an acquire() call on ResourceManager."""

    granted: bool
    lease: Lease | None = None
    queue_position: int | None = None
    reason: str | None = None
    cached: bool = False


class ResourceManager:
    """Deterministic, thread-safe manager for contested and capacity resources."""

    def __init__(
        self,
        bus: PulseBus | Any,
        clock: Clock | None = None,
        store: ResourceStore | None = None,
        default_discipline: QueueDiscipline = QueueDiscipline.FIFO,
        max_bypasses: int = 5,
    ) -> None:
        self.bus = bus
        self.clock = clock or SystemClock()
        self.store: ResourceStore = store or InMemoryResourceStore()
        self.default_discipline = default_discipline
        self.max_bypasses = max_bypasses

        self._lock = threading.RLock()
        self._lease_manager = LeaseManager(clock=self.clock)
        self._resources: dict[str, Resource] = {}
        self._queues: dict[str, ResourceQueue] = {}

    def register_resource(self, resource: Resource) -> None:
        """Register a resource descriptor with the manager."""
        with self._lock:
            handle = resource.handle
            self._resources[handle] = resource
            if handle not in self._queues:
                self._queues[handle] = ResourceQueue(
                    resource_id=resource.identity,
                    discipline=self.default_discipline,
                )
            self.store.save_resource(resource)

    def get_resource(self, identity: ResourceIdentity | str) -> Resource | None:
        with self._lock:
            handle = identity if isinstance(identity, str) else identity.to_handle()
            return self._resources.get(handle)

    def get_lease(self, lease_token: str) -> Lease | None:
        """Retrieve a lease by its token."""
        with self._lock:
            return self._lease_manager.get_lease(lease_token)

    def list_resources(self, space_id: str | None = None) -> list[Resource]:
        with self._lock:
            if space_id:
                return [r for r in self._resources.values() if r.space_id == space_id]
            return list(self._resources.values())

    def set_queue_discipline(self, identity: ResourceIdentity, discipline: QueueDiscipline) -> None:
        with self._lock:
            handle = identity.to_handle()
            if handle in self._queues:
                self._queues[handle].discipline = discipline

    def acquire(
        self,
        space_id: str,
        requester_id: str,
        identity: ResourceIdentity,
        units: int = 1,
        duration_seconds: float = 60.0,
        priority: int = 0,
        idempotency_key: str | None = None,
        scope: str = "execution",
    ) -> ResourceAcquisitionResult:
        """Request allocation of a resource.

        If capacity is immediately available, returns granted=True with Lease.
        If contested / unavailable, enqueues request, publishes resource.conflict,
        and returns granted=False with queue_position.
        """
        handle = identity.to_handle()

        with self._lock:
            # Sweep any expired leases first
            self._sweep_expirations_locked()

            # Space and resource registration check
            resource = self._resources.get(handle)
            if not resource:
                # Publish resource.denied per contract
                self._publish_pulse(
                    type_="resource.denied",
                    severity="warning",
                    space_id=space_id,
                    payload={"resource_id": handle, "reason": f"Resource {handle} not registered"},
                )
                return ResourceAcquisitionResult(
                    granted=False,
                    reason=f"Resource {handle} not found",
                )

            # Law 1: Everything happens inside a Space
            if resource.space_id != space_id:
                raise PermissionError(
                    f"Cross-space resource access rejected: resource belongs to "
                    f"{resource.space_id}, caller is in {space_id}"
                )

            if units > resource.total_capacity:
                self._publish_pulse(
                    type_="resource.denied",
                    severity="warning",
                    space_id=space_id,
                    payload={
                        "resource_id": handle,
                        "reason": (
                            f"Requested units {units} exceeds total "
                            f"capacity {resource.total_capacity}"
                        ),
                    },
                )
                return ResourceAcquisitionResult(
                    granted=False,
                    reason=(
                        f"Requested units {units} exceeds total "
                        f"capacity {resource.total_capacity}"
                    ),
                )

            # Check Idempotency (ADR-0006)
            if idempotency_key:
                existing_lease = self._lease_manager.get_by_idempotency(
                    space_id, requester_id, idempotency_key
                )
                if existing_lease and existing_lease.is_valid(self.clock.now()):
                    return ResourceAcquisitionResult(
                        granted=True,
                        lease=existing_lease,
                        cached=True,
                    )

            # Publish resource.requested
            self._publish_pulse(
                type_="resource.requested",
                severity="info",
                space_id=space_id,
                payload={
                    "resource_id": handle,
                    "requester_id": requester_id,
                    "scope": scope,
                },
            )

            queue = self._queues.setdefault(
                handle, ResourceQueue(identity, self.default_discipline)
            )

            # If there is already a queue, new requests must queue up behind it (FIFO/priority)
            if len(queue) == 0 and resource.available_capacity >= units:
                # Immediate allocation
                resource.allocate(units)
                lease = self._lease_manager.issue_lease(
                    resource_id=identity,
                    space_id=space_id,
                    requester_id=requester_id,
                    duration_seconds=duration_seconds,
                    units=units,
                    idempotency_key=idempotency_key,
                )
                self.store.save_lease(lease)
                self.store.save_resource(resource)

                # Publish resource.granted
                self._publish_pulse(
                    type_="resource.granted",
                    severity="info",
                    space_id=space_id,
                    payload={
                        "resource_id": handle,
                        "lease_token": lease.lease_token,
                        "expiry": lease.expiry.isoformat(),
                    },
                )
                return ResourceAcquisitionResult(granted=True, lease=lease)

            # Contested: Must queue
            queued_req, queue_pos = queue.enqueue(
                space_id=space_id,
                requester_id=requester_id,
                units=units,
                priority=priority,
                duration_seconds=duration_seconds,
                idempotency_key=idempotency_key,
                requested_at=self.clock.now(),
            )

            # Publish resource.conflict with accurate queue_position (RESOURCE-006)
            self._publish_pulse(
                type_="resource.conflict",
                severity="warning",
                space_id=space_id,
                payload={
                    "resource_id": handle,
                    "queue_position": queue_pos,
                },
            )

            return ResourceAcquisitionResult(
                granted=False,
                queue_position=queue_pos,
                reason="resource_contested",
            )

    def release(self, space_id: str, requester_id: str, lease_token: str) -> bool:
        """Release a held lease explicitly and service queued requests."""
        with self._lock:
            lease = self._lease_manager.get_lease(lease_token)
            if not lease:
                raise KeyError(f"Lease {lease_token} not found")

            if lease.space_id != space_id:
                raise PermissionError(
                    f"Cross-space lease release rejected: lease in {lease.space_id}, "
                    f"caller in {space_id}"
                )

            if lease.requester_id != requester_id:
                raise PermissionError(
                    f"Unauthorized lease release: held by {lease.requester_id}, "
                    f"attempted by {requester_id}"
                )

            if lease.state == LeaseState.RELEASED:
                return True  # Idempotent release

            was_active = (lease.state == LeaseState.ACTIVE)

            handle = lease.resource_id.to_handle()
            resource = self._resources.get(handle)

            # Release lease
            self._lease_manager.release_lease(space_id, requester_id, lease_token)
            self.store.update_lease_state(lease_token, LeaseState.RELEASED)

            if resource and was_active:
                resource.deallocate(lease.units)
                self.store.save_resource(resource)

            # Publish resource.released
            self._publish_pulse(
                type_="resource.released",
                severity="info",
                space_id=space_id,
                payload={
                    "resource_id": handle,
                    "lease_token": lease_token,
                },
            )

            # Process pending queue for this resource if capacity was just released
            if was_active:
                self._process_queue_locked(handle)
            return True

    def renew(
        self,
        space_id: str,
        requester_id: str,
        lease_token: str,
        extension_seconds: float = 60.0,
    ) -> Lease:
        """Renew an active lease before expiration."""
        with self._lock:
            self._sweep_expirations_locked()
            lease = self._lease_manager.renew_lease(
                space_id=space_id,
                requester_id=requester_id,
                lease_token=lease_token,
                extension_seconds=extension_seconds,
            )
            self.store.update_lease_state(
                lease_token=lease_token,
                state=lease.state,
                expiry=lease.expiry,
                renewed_count=lease.renewed_count,
            )
            return lease

    def revoke(self, space_id: str, lease_token: str, reason: str = "revoked_by_space") -> bool:
        """Revoke a lease authoritatively (Space authority)."""
        with self._lock:
            lease = self._lease_manager.get_lease(lease_token)
            if not lease:
                raise KeyError(f"Lease {lease_token} not found")

            if lease.space_id != space_id:
                raise PermissionError("Cross-space lease revocation rejected")

            if lease.state != LeaseState.ACTIVE:
                return False

            handle = lease.resource_id.to_handle()
            resource = self._resources.get(handle)

            self._lease_manager.revoke_lease(space_id, lease_token)
            self.store.update_lease_state(lease_token, LeaseState.REVOKED)

            if resource:
                resource.deallocate(lease.units)
                self.store.save_resource(resource)

            self._publish_pulse(
                type_="resource.denied",
                severity="warning",
                space_id=space_id,
                payload={
                    "resource_id": handle,
                    "reason": f"Lease {lease_token} revoked: {reason}",
                },
            )

            self._process_queue_locked(handle)
            return True

    def cancel_request(
        self, space_id: str, requester_id: str, identity: ResourceIdentity, request_id: str
    ) -> bool:
        """Cancel a pending request in the queue."""
        with self._lock:
            handle = identity.to_handle()
            queue = self._queues.get(handle)
            if not queue:
                return False
            return queue.cancel(
                request_id=request_id, requester_id=requester_id, space_id=space_id
            )

    def check_expirations(self) -> list[Lease]:
        """Manually trigger expiration sweep across all resources."""
        with self._lock:
            return self._sweep_expirations_locked()

    def _sweep_expirations_locked(self) -> list[Lease]:
        """Sweep expired leases, reclaim capacity, and process queues."""
        expired = self._lease_manager.sweep_expirations()
        affected_resources: set[str] = set()

        for lease in expired:
            self.store.update_lease_state(lease.lease_token, LeaseState.EXPIRED)
            handle = lease.resource_id.to_handle()
            resource = self._resources.get(handle)
            if resource:
                resource.deallocate(lease.units)
                self.store.save_resource(resource)
                affected_resources.add(handle)

            # Publish resource.denied due to expiration
            self._publish_pulse(
                type_="resource.denied",
                severity="warning",
                space_id=lease.space_id,
                payload={
                    "resource_id": handle,
                    "reason": f"Lease {lease.lease_token} expired at {lease.expiry.isoformat()}",
                },
            )

        for handle in affected_resources:
            self._process_queue_locked(handle)

        return expired

    def _process_queue_locked(self, handle: str) -> None:
        """Check queue and grant resources to waiting requests if capacity is available."""
        resource = self._resources.get(handle)
        queue = self._queues.get(handle)
        if not resource or not queue:
            return

        while resource.available_capacity > 0:
            next_req = queue.pop_next(
                available_capacity=resource.available_capacity,
                max_bypasses=self.max_bypasses,
            )
            if not next_req:
                break

            # Allocate
            resource.allocate(next_req.units)
            lease = self._lease_manager.issue_lease(
                resource_id=resource.identity,
                space_id=next_req.space_id,
                requester_id=next_req.requester_id,
                duration_seconds=next_req.duration_seconds,
                units=next_req.units,
                idempotency_key=next_req.idempotency_key,
            )
            self.store.save_lease(lease)
            self.store.save_resource(resource)

            # Publish resource.granted
            self._publish_pulse(
                type_="resource.granted",
                severity="info",
                space_id=next_req.space_id,
                payload={
                    "resource_id": handle,
                    "lease_token": lease.lease_token,
                    "expiry": lease.expiry.isoformat(),
                },
            )

    def recover_from_store(self, space_id: str | None = None) -> None:
        """Crash recovery: restore resource and lease state from durable store."""
        with self._lock:
            # 1. Restore resources
            stored_resources = self.store.list_resources(space_id)
            for r in stored_resources:
                # Reset allocated capacity; will re-tally from active leases
                r.allocated_capacity = 0
                self._resources[r.handle] = r
                if r.handle not in self._queues:
                    self._queues[r.handle] = ResourceQueue(r.identity, self.default_discipline)

            # 2. Restore leases
            all_leases = self.store.list_all_leases(space_id)
            now = self.clock.now()

            for lease in all_leases:
                self._lease_manager._leases[lease.lease_token] = lease
                if lease.idempotency_key:
                    self._lease_manager._idempotency_index[
                        (lease.space_id, lease.requester_id, lease.idempotency_key)
                    ] = lease.lease_token

                # Check if active lease expired during downtime
                if lease.state == LeaseState.ACTIVE:
                    if now >= lease.expiry:
                        lease.state = LeaseState.EXPIRED
                        self.store.update_lease_state(lease.lease_token, LeaseState.EXPIRED)
                    else:
                        # Re-tally allocated capacity
                        handle = lease.resource_id.to_handle()
                        res = self._resources.get(handle)
                        if res:
                            res.allocate(lease.units)

    def _publish_pulse(
        self,
        type_: str,
        severity: str | Severity,
        space_id: str,
        payload: dict[str, Any],
        correlation_id: str | None = None,
    ) -> None:
        """Publish a lifecycle pulse to the bus."""
        pulse = Pulse(
            type=type_,
            severity=Severity(severity),
            space_id=space_id,
            source="resource_manager",
            correlation_id=correlation_id or f"corr-res-{space_id}",
            payload=payload,
            timestamp=self.clock.now(),
        )
        self.bus.publish(pulse)
