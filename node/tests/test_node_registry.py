"""Unit tests for NodeRegistry.

Covers inventory, format validation, duplicate detection, and resource sync.
CONTRACT_MATRIX NODE-001, NODE-003, RESOURCE-001
ADR-0017, ADR-0018
"""

from __future__ import annotations

import pytest
from ryu.pulse_bus.bus import PulseBus

from core.resources.manager import ResourceManager
from core.resources.store import InMemoryResourceStore
from node.contract import (
    DeviceInfo,
    DeviceType,
    NodeInfo,
    NodeRegistrationError,
    NodeState,
)
from node.registry import NodeRegistry


def test_register_node_success() -> None:
    registry = NodeRegistry()
    node = NodeInfo(
        node_id="node-alpha-01",
        platform="windows",
        architecture="x86_64",
        environment_profile="windows_host",
        runtime_state=NodeState.REGISTERED,
    )
    secret = "high-entropy-pairing-secret-32bytes"

    registry.register_node(node, secret)
    retrieved = registry.get_node("node-alpha-01")
    assert retrieved is not None
    assert retrieved.platform == "windows"
    assert registry.get_pairing_secret("node-alpha-01") == secret


def test_register_node_invalid_id_format() -> None:
    registry = NodeRegistry()
    secret = "high-entropy-pairing-secret-32bytes"

    # Too short
    with pytest.raises(NodeRegistrationError, match="Invalid node_id format"):
        registry.register_node(
            NodeInfo(
                node_id="no", platform="linux", architecture="x86_64",
                environment_profile="wsl2"
            ),
            secret,
        )

    # Disallowed characters / path traversal attempt
    with pytest.raises(NodeRegistrationError, match="Invalid node_id format"):
        registry.register_node(
            NodeInfo(
                node_id="../../etc/passwd", platform="linux", architecture="x86_64",
                environment_profile="wsl2"
            ),
            secret,
        )

    # Whitespace
    with pytest.raises(NodeRegistrationError, match="Invalid node_id format"):
        registry.register_node(
            NodeInfo(
                node_id="node alpha", platform="linux", architecture="x86_64",
                environment_profile="wsl2"
            ),
            secret,
        )


def test_register_node_weak_secret() -> None:
    registry = NodeRegistry()
    node = NodeInfo(
        node_id="node-alpha-01",
        platform="windows",
        architecture="x86_64",
        environment_profile="windows_host",
    )
    with pytest.raises(NodeRegistrationError, match="high entropy"):
        registry.register_node(node, "weak")


def test_register_node_duplicate_conflict() -> None:
    registry = NodeRegistry()
    secret = "high-entropy-pairing-secret-32bytes"
    node1 = NodeInfo(
        node_id="node-alpha-01",
        platform="windows",
        architecture="x86_64",
        environment_profile="windows_host",
    )
    registry.register_node(node1, secret)

    # Conflict: different platform
    node2 = NodeInfo(
        node_id="node-alpha-01",
        platform="linux",
        architecture="x86_64",
        environment_profile="wsl2",
    )
    with pytest.raises(NodeRegistrationError, match="Duplicate registration conflict"):
        registry.register_node(node2, secret)


def test_device_registration_and_listing() -> None:
    registry = NodeRegistry()
    secret = "high-entropy-pairing-secret-32bytes"
    node = NodeInfo(
        node_id="node-alpha-01",
        platform="windows",
        architecture="x86_64",
        environment_profile="windows_host",
    )
    registry.register_node(node, secret)

    dev_gpu = DeviceInfo(
        device_id="gpu-0",
        node_id="node-alpha-01",
        device_type=DeviceType.GPU,
        total_capacity=1,
    )
    dev_cpu = DeviceInfo(
        device_id="cpu-0",
        node_id="node-alpha-01",
        device_type=DeviceType.CPU,
        total_capacity=16,
    )

    registry.register_device(dev_gpu)
    registry.register_device(dev_cpu)

    devices = registry.list_devices("node-alpha-01")
    assert len(devices) == 2
    assert registry.get_device("gpu-0") is not None
    assert registry.get_device("non-existent") is None


def test_device_registration_unregistered_node() -> None:
    registry = NodeRegistry()
    dev = DeviceInfo(
        device_id="gpu-0",
        node_id="unregistered-node",
        device_type=DeviceType.GPU,
    )
    match_msg = "hosting node 'unregistered-node' is not registered"
    with pytest.raises(NodeRegistrationError, match=match_msg):
        registry.register_device(dev)


def test_sync_resources_to_manager() -> None:
    registry = NodeRegistry()
    secret = "high-entropy-pairing-secret-32bytes"
    node = NodeInfo(
        node_id="node-alpha-01",
        platform="windows",
        architecture="x86_64",
        environment_profile="windows_host",
    )
    registry.register_node(node, secret)

    dev_gpu = DeviceInfo(
        device_id="gpu-0",
        node_id="node-alpha-01",
        device_type=DeviceType.GPU,
        total_capacity=1,
        capability_metadata={"vram": "12GB"},
    )
    registry.register_device(dev_gpu)

    bus = PulseBus()
    rm = ResourceManager(bus=bus, store=InMemoryResourceStore())

    # Sync to Space
    registry.sync_resources_to_manager(rm, space_id="space-main")

    res = rm.get_resource("gpu/node-alpha-01/gpu-0")
    assert res is not None
    assert res.total_capacity == 1
    assert res.space_id == "space-main"
    assert res.metadata["vram"] == "12GB"
