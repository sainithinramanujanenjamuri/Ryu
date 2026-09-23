"""Node and Device Registry for RYU AI.

Canonical inventory and metadata store for compute nodes and their hosted devices.
spec §11 (Node Runtime), §16 (Lease), CONTRACT_MATRIX NODE-001..NODE-003
ADR-0017, ADR-0018

INVARIANT:
NodeRegistry is strictly an inventory and metadata repository.
NodeRegistry != Resource authority
NodeRegistry != Lease authority
NodeRegistry != Capability authority
NodeRegistry != Grant authority
"""

from __future__ import annotations

import re
import threading

from core.resources.identity import Resource, ResourceIdentity
from core.resources.manager import ResourceManager
from node.contract import (
    DeviceInfo,
    DeviceNotFoundError,
    DeviceState,
    NodeInfo,
    NodeRegistrationError,
    NodeState,
    NodeTrustTier,
)

# Valid node ID pattern: alphanumeric, hyphen, underscore, 3-64 chars
NODE_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{3,64}$")


class NodeRegistry:
    """Thread-safe canonical repository for registered nodes and devices.

    Enforces:
    - Node ID format validation (blocks forged/malformed IDs)
    - Conflict rejection on duplicate node registration with mismatched metadata
    - Device registration and state transitions
    - Export of hardware capacity into Space-scoped ResourceManager without holding authority
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._nodes: dict[str, NodeInfo] = {}
        self._pairing_secrets: dict[str, str] = {}
        self._devices: dict[str, DeviceInfo] = {}
        # Maps node_id -> list of device_ids
        self._node_devices: dict[str, list[str]] = {}

    def register_node(self, info: NodeInfo, pairing_secret: str) -> None:
        """Register a node with its cryptographic pairing secret.

        Raises:
            NodeRegistrationError: if node_id is malformed or duplicate registration conflicts.
        """
        if not info.node_id or not NODE_ID_PATTERN.match(info.node_id):
            raise NodeRegistrationError(
                f"Invalid node_id format: '{info.node_id}'. "
                "Must be 3-64 alphanumeric, hyphen, or underscore."
            )

        if not pairing_secret or len(pairing_secret) < 16:
            raise NodeRegistrationError(
                "Pairing secret must be high entropy (at least 16 characters)."
            )

        with self._lock:
            existing = self._nodes.get(info.node_id)
            if existing:
                # If already registered, check for metadata conflicts
                if (
                    existing.platform != info.platform
                    or existing.architecture != info.architecture
                    or existing.environment_profile != info.environment_profile
                ):
                    raise NodeRegistrationError(
                        f"Duplicate registration conflict for node '{info.node_id}': "
                        f"existing platform={existing.platform}/{existing.architecture}, "
                        f"new platform={info.platform}/{info.architecture}."
                    )
                # Ensure pairing secret has not changed fraudulently
                if self._pairing_secrets.get(info.node_id) != pairing_secret:
                    raise NodeRegistrationError(
                        f"Pairing secret mismatch for pre-registered node '{info.node_id}'."
                    )
                # Update mutable attributes (state, labels, capabilities)
                existing.runtime_state = info.runtime_state
                existing.capabilities = list(info.capabilities)
                existing.labels = dict(info.labels)
                return

            self._nodes[info.node_id] = info
            self._pairing_secrets[info.node_id] = pairing_secret
            if info.node_id not in self._node_devices:
                self._node_devices[info.node_id] = []

    def get_node(self, node_id: str) -> NodeInfo | None:
        """Retrieve node info by node_id."""
        with self._lock:
            return self._nodes.get(node_id)

    def list_nodes(self) -> list[NodeInfo]:
        """List all registered nodes."""
        with self._lock:
            return list(self._nodes.values())

    def list_nodes_by_platform(self, platform: str) -> list[NodeInfo]:
        """List nodes matching a given platform name (e.g. 'windows', 'linux')."""
        with self._lock:
            return [n for n in self._nodes.values() if n.platform == platform]

    def list_nodes_by_tier(self, tier: NodeTrustTier) -> list[NodeInfo]:
        """List nodes matching a given trust tier."""
        with self._lock:
            return [n for n in self._nodes.values() if n.trust_tier == tier]

    def get_pairing_secret(self, node_id: str) -> str | None:
        """Retrieve paired secret for node (internal authorized use only)."""
        with self._lock:
            return self._pairing_secrets.get(node_id)

    def set_node_state(self, node_id: str, state: NodeState) -> None:
        """Update node runtime lifecycle state."""
        with self._lock:
            node = self._nodes.get(node_id)
            if not node:
                raise NodeRegistrationError(f"Cannot update state: node '{node_id}' not found.")
            node.runtime_state = state

    def register_device(self, device: DeviceInfo) -> None:
        """Register a hardware or virtual device hosted by a node.

        Raises:
            NodeRegistrationError: if the hosting node is not registered.
        """
        with self._lock:
            if device.node_id not in self._nodes:
                raise NodeRegistrationError(
                    f"Cannot register device '{device.device_id}': "
                    f"hosting node '{device.node_id}' is not registered."
                )

            self._devices[device.device_id] = device
            dev_list = self._node_devices.setdefault(device.node_id, [])
            if device.device_id not in dev_list:
                dev_list.append(device.device_id)

    def get_device(self, device_id: str) -> DeviceInfo | None:
        """Retrieve device info by device_id."""
        with self._lock:
            return self._devices.get(device_id)

    def list_devices(self, node_id: str | None = None) -> list[DeviceInfo]:
        """List devices, optionally filtered by node_id."""
        with self._lock:
            if node_id is not None:
                dev_ids = self._node_devices.get(node_id, [])
                return [self._devices[did] for did in dev_ids if did in self._devices]
            return list(self._devices.values())

    def set_device_state(self, device_id: str, state: DeviceState) -> None:
        """Update device availability state."""
        with self._lock:
            device = self._devices.get(device_id)
            if not device:
                raise DeviceNotFoundError(f"Device '{device_id}' not found in registry.")
            device.availability_state = state

    def sync_resources_to_manager(
        self, resource_manager: ResourceManager, space_id: str
    ) -> None:
        """Export registered node devices as schedulable Resources into a Space's ResourceManager.

        Note: NodeRegistry only registers the capacity description; ResourceManager maintains
        exclusive authoritative control over allocation, queueing, and leases.
        """
        with self._lock:
            for device in self._devices.values():
                identity = ResourceIdentity(
                    resource_type=device.device_type.value,
                    provider_id=device.node_id,
                    instance_id=device.device_id,
                )
                res = Resource(
                    identity=identity,
                    space_id=space_id,
                    total_capacity=device.total_capacity,
                    metadata=dict(device.capability_metadata),
                )
                resource_manager.register_resource(res)
