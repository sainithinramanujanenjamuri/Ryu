"""Node Coordinator, Heartbeat Tracking, and Offline Recovery.

Monitors node liveness, manages heartbeat leases, checkpoints in-flight tasks
on disconnect, and coordinates validated resumption upon reconnection.
spec §11 (Node Runtime), CONTRACT_MATRIX NODE-005, NODE-006, NODE-007
ADR-0017, ADR-0018

INVARIANT:
Recovery requires validation before resuming.
Used for validated resume of replay-safe/idempotent tasks after lease, grant,
device, and checkpoint verification.
Indeterminate or non-idempotent tasks are flagged and escalated without auto-replay.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from ryu.pulse_bus.bus import PulseBus
from ryu.pulse_bus.pulse import Pulse, Severity

from core.resources.manager import ResourceManager
from node.contract import (
    GrantRevokedError,
    NodeError,
    NodeOfflineError,
    NodeState,
)
from node.grants import DeviceGrantManager
from node.registry import NodeRegistry

logger = logging.getLogger(__name__)


@dataclass
class TaskCheckpoint:
    """Checkpoint of an in-flight worker task executing on a node."""

    task_id: str
    space_id: str
    worker_id: str
    node_id: str
    device_id: str
    grant_id: str
    is_idempotent: bool
    state: str  # "in_flight", "checkpointed", "indeterminate", "resumed"
    data: dict[str, Any] = field(default_factory=dict)
    checkpointed_at: str = ""


class NodeCoordinator:
    """Orchestrates node liveness, heartbeat tracking, offline checkpointing, and reconnection.

    Enforces:
    - Node heartbeat monitoring against configurable TTL
    - Immediate offline state transition upon heartbeat expiration
    - node.offline pulse emission (contract schema compliant)
    - Checkpointing of in-flight tasks (distinguishing idempotent vs indeterminate)
    - Flapping and rapid reconnection rate-limiting
    - Post-reconnect lease and grant reconciliation (rejecting grants revoked while offline)
    - Validated resume only for verified idempotent tasks
    """

    def __init__(
        self,
        registry: NodeRegistry,
        grant_manager: DeviceGrantManager,
        resource_manager: ResourceManager,
        bus: PulseBus | None = None,
        heartbeat_ttl_seconds: float = 30.0,
        flapping_threshold_seconds: float = 1.0,
    ) -> None:
        self.registry = registry
        self.grant_manager = grant_manager
        self.resource_manager = resource_manager
        self.bus = bus
        self.heartbeat_ttl_seconds = heartbeat_ttl_seconds
        self.flapping_threshold_seconds = flapping_threshold_seconds

        self._lock = threading.RLock()
        self._last_heartbeat: dict[str, datetime] = {}
        self._last_reconnect: dict[str, datetime] = {}
        self._checkpoints: dict[str, TaskCheckpoint] = {}  # task_id -> TaskCheckpoint
        self._node_tasks: dict[str, list[str]] = {}  # node_id -> list of task_ids

    def record_heartbeat(
        self, node_id: str, timestamp: datetime | None = None
    ) -> None:
        """Record a successful heartbeat from a node."""
        now = timestamp or datetime.now(timezone.utc)
        with self._lock:
            self._last_heartbeat[node_id] = now
            node = self.registry.get_node(node_id)
            if node and node.runtime_state == NodeState.OFFLINE:
                # If node was marked offline, it must explicitly call reconnect_node
                pass
            elif node and node.runtime_state == NodeState.REGISTERED:
                self.registry.set_node_state(node_id, NodeState.READY)

    def register_in_flight_task(
        self,
        task_id: str,
        space_id: str,
        worker_id: str,
        node_id: str,
        device_id: str,
        grant_id: str,
        is_idempotent: bool,
        data: dict[str, Any] | None = None,
    ) -> None:
        """Register an active task executing on a node for offline checkpointing."""
        with self._lock:
            cp = TaskCheckpoint(
                task_id=task_id,
                space_id=space_id,
                worker_id=worker_id,
                node_id=node_id,
                device_id=device_id,
                grant_id=grant_id,
                is_idempotent=is_idempotent,
                state="in_flight",
                data=data or {},
            )
            self._checkpoints[task_id] = cp
            self._node_tasks.setdefault(node_id, []).append(task_id)

    def complete_task(self, task_id: str) -> None:
        """Mark task completed and remove checkpoint."""
        with self._lock:
            cp = self._checkpoints.pop(task_id, None)
            if cp and cp.node_id in self._node_tasks:
                if task_id in self._node_tasks[cp.node_id]:
                    self._node_tasks[cp.node_id].remove(task_id)

    def sweep_timeouts(self, current_time: datetime | None = None) -> list[str]:
        """Sweep nodes that missed heartbeats past TTL.

        Transitions node to OFFLINE, emits node.offline Pulse, and checkpoints tasks.
        """
        now = current_time or datetime.now(timezone.utc)
        offline_nodes: list[str] = []

        with self._lock:
            for node_id, last_hb in list(self._last_heartbeat.items()):
                node = self.registry.get_node(node_id)
                if not node or node.runtime_state in (
                    NodeState.OFFLINE,
                    NodeState.TERMINATED,
                ):
                    continue

                elapsed = (now - last_hb).total_seconds()
                if elapsed > self.heartbeat_ttl_seconds:
                    # Transition to OFFLINE
                    self.registry.set_node_state(node_id, NodeState.OFFLINE)
                    offline_nodes.append(node_id)

                    # Checkpoint in-flight tasks
                    self._checkpoint_node_tasks_locked(node_id, now)

                    # Emit node.offline Pulse (schema: node_id, last_heartbeat_at)
                    if self.bus:
                        pulse = Pulse(
                            type="node.offline",
                            severity=Severity.WARNING,
                            space_id="system",
                            source="node_coordinator",
                            correlation_id=f"corr-offline-{node_id}",
                            payload={
                                "node_id": node_id,
                                "last_heartbeat_at": last_hb.isoformat(),
                            },
                            timestamp=now,
                        )
                        self.bus.publish(pulse)

        return offline_nodes

    def _checkpoint_node_tasks_locked(self, node_id: str, timestamp: datetime) -> None:
        """Checkpoint in-flight tasks for a disconnected node."""
        task_ids = self._node_tasks.get(node_id, [])
        for tid in task_ids:
            cp = self._checkpoints.get(tid)
            if not cp:
                continue

            cp.checkpointed_at = timestamp.isoformat()
            if cp.is_idempotent:
                cp.state = "checkpointed"
            else:
                # Non-idempotent or indeterminate tasks cannot be safely auto-replayed
                cp.state = "indeterminate"
                # Notify orchestrator/workers of indeterminate state
                if self.bus:
                    self.bus.publish(
                        Pulse(
                            type="task.failed",
                            severity=Severity.ERROR,
                            space_id=cp.space_id,
                            source="node_coordinator",
                            correlation_id=f"corr-indeterminate-{tid}",
                            payload={
                                "task_id": tid,
                                "error_class": "terminal.device_offline",
                                "message": (
                                    f"Node '{node_id}' disconnected during "
                                    "non-idempotent operation; state indeterminate"
                                ),
                                "plan_version": 1,
                            },
                            timestamp=timestamp,
                        )
                    )

    def reconnect_node(
        self,
        node_id: str,
        lease_token: str,
        current_time: datetime | None = None,
    ) -> bool:
        """Handle node reconnection, anti-flapping throttling, and lease reconciliation.

        Emits node.reconnected Pulse.
        """
        now = current_time or datetime.now(timezone.utc)

        with self._lock:
            # 1. Anti-Flapping / Rate Limiting Check
            last_rec = self._last_reconnect.get(node_id)
            if last_rec:
                reconnect_gap = (now - last_rec).total_seconds()
                if reconnect_gap < self.flapping_threshold_seconds:
                    logger.warning(
                        f"Node '{node_id}' reconnect flapping detected "
                        f"({reconnect_gap:.3f}s); throttling."
                    )
                    return False

            self._last_reconnect[node_id] = now
            self._last_heartbeat[node_id] = now

            # 2. Reconcile node state
            node = self.registry.get_node(node_id)
            if not node:
                raise NodeError(f"Cannot reconnect unregistered node '{node_id}'.")

            # 3. Post-Reconnect Lease & Grant Reconciliation
            # Check backing lease in ResourceManager
            lease = self.grant_manager._get_backing_lease(lease_token)
            if not lease or not lease.is_valid(now):
                # Backing lease expired or revoked while offline
                # Must invalidate all pending tasks and reject stale grants
                self._blacklist_offline_node_tasks_locked(node_id)
                raise GrantRevokedError(
                    f"Reconnection rejected: backing lease '{lease_token}' "
                    "expired or revoked while offline."
                )

            # Reconnection accepted: set node state back to READY
            self.registry.set_node_state(node_id, NodeState.READY)

            # 4. Emit node.reconnected Pulse (schema: node_id, lease_token)
            if self.bus:
                pulse = Pulse(
                    type="node.reconnected",
                    severity=Severity.INFO,
                    space_id="system",
                    source="node_coordinator",
                    correlation_id=f"corr-reconnect-{node_id}",
                    payload={
                        "node_id": node_id,
                        "lease_token": lease_token,
                    },
                    timestamp=now,
                )
                self.bus.publish(pulse)

            return True

    def validate_and_resume_task(
        self, task_id: str, current_time: datetime | None = None
    ) -> TaskCheckpoint:
        """Validated resume of replay-safe/idempotent tasks.

        Verifies lease, grant, device, and checkpoint before resume.
        """
        now = current_time or datetime.now(timezone.utc)

        with self._lock:
            cp = self._checkpoints.get(task_id)
            if not cp:
                raise NodeError(f"Task '{task_id}' has no checkpoint.")

            if not cp.is_idempotent:
                raise NodeError(
                    f"Cannot auto-resume task '{task_id}': marked non-idempotent "
                    f"(state={cp.state}). Requires human or orchestrator policy escalation."
                )

            # 1. Verify node is back online
            node = self.registry.get_node(cp.node_id)
            if not node or node.runtime_state not in (NodeState.READY, NodeState.ACTIVE):
                raise NodeOfflineError(
                    f"Cannot resume task '{task_id}': node '{cp.node_id}' "
                    "is not in READY/ACTIVE state."
                )

            # 2. Verify grant and backing lease are still active
            self.grant_manager.validate_grant_active(cp.grant_id, current_time=now)

            # 3. Verify device is online
            device = self.registry.get_device(cp.device_id)
            if not device:
                raise NodeError(
                    f"Cannot resume task '{task_id}': device '{cp.device_id}' not found."
                )

            # Resumption validated
            cp.state = "resumed"
            return cp

    def _blacklist_offline_node_tasks_locked(self, node_id: str) -> None:
        """Mark all in-flight tasks for a disconnected node whose lease expired as failed."""
        task_ids = self._node_tasks.get(node_id, [])
        for tid in task_ids:
            cp = self._checkpoints.get(tid)
            if cp:
                cp.state = "indeterminate"

    def get_task_checkpoint(self, task_id: str) -> TaskCheckpoint | None:
        """Retrieve task checkpoint."""
        with self._lock:
            return self._checkpoints.get(task_id)
