"""Unit tests for DeviceGrantManager: authority boundaries, lease validation, and dual invalidation.

CONTRACT_MATRIX NODE-003, NODE-004, RESOURCE-002..RESOURCE-004
ADR-0017, ADR-0018
"""

from __future__ import annotations

import pytest
from ryu.pulse_bus.bus import PulseBus

from core.resources.identity import ResourceIdentity
from core.resources.manager import ResourceManager
from core.resources.store import InMemoryResourceStore
from node.contract import (
    DeviceInfo,
    DeviceType,
    GrantRevokedError,
    GrantState,
    LeaseInvalidError,
    LeaseNotFoundError,
    NodeInfo,
    RiskTier,
)
from node.grants import DeviceGrantManager
from node.registry import NodeRegistry


@pytest.fixture
def setup_env():
    bus = PulseBus()
    rm = ResourceManager(bus=bus, store=InMemoryResourceStore())
    registry = NodeRegistry()
    secret = "high-entropy-node-secret-32bytes"

    node = NodeInfo(
        node_id="node-test-01",
        platform="windows",
        architecture="x86_64",
        environment_profile="windows_host",
    )
    registry.register_node(node, secret)

    device = DeviceInfo(
        device_id="gpu-0",
        node_id="node-test-01",
        device_type=DeviceType.GPU,
        total_capacity=1,
    )
    registry.register_device(device)
    registry.sync_resources_to_manager(rm, space_id="space-main")

    gm = DeviceGrantManager(registry=registry, resource_manager=rm, bus=bus)
    return bus, rm, registry, gm, secret


def test_grant_creation_requires_active_lease(setup_env) -> None:
    bus, rm, registry, gm, secret = setup_env

    # 1. Attempt to create grant without presenting lease -> MUST FAIL
    with pytest.raises(LeaseNotFoundError, match="backing lease 'fake-token' not found"):
        gm.create_grant(
            space_id="space-main",
            worker_id="worker-01",
            node_id="node-test-01",
            device_id="gpu-0",
            capability="gpu.cuda",
            lease_token="fake-token",
        )

    # 2. Acquire real lease from ResourceManager
    res_ident = ResourceIdentity("gpu", "node-test-01", "gpu-0")
    acq = rm.acquire(
        space_id="space-main",
        requester_id="worker-01",
        identity=res_ident,
        units=1,
        duration_seconds=600.0,
    )
    assert acq.granted is True
    assert acq.lease is not None
    real_lease_token = acq.lease.lease_token

    # 3. Create grant with valid lease -> SUCCESS
    grant = gm.create_grant(
        space_id="space-main",
        worker_id="worker-01",
        node_id="node-test-01",
        device_id="gpu-0",
        capability="gpu.cuda",
        lease_token=real_lease_token,
        risk_tier=RiskTier.LOW,
    )
    assert grant.grant_id.startswith("grant-")
    assert grant.signature != ""
    assert grant.state == GrantState.GRANTED
    assert grant.verify(secret) is True


def test_grant_creation_space_and_worker_isolation(setup_env) -> None:
    bus, rm, registry, gm, secret = setup_env

    res_ident = ResourceIdentity("gpu", "node-test-01", "gpu-0")
    acq = rm.acquire(
        space_id="space-main",
        requester_id="worker-01",
        identity=res_ident,
        units=1,
    )
    assert acq.lease is not None
    lease_token = acq.lease.lease_token

    # Cross-space attempt
    with pytest.raises(LeaseInvalidError, match="Cross-space lease violation"):
        gm.create_grant(
            space_id="space-other",
            worker_id="worker-01",
            node_id="node-test-01",
            device_id="gpu-0",
            capability="gpu.cuda",
            lease_token=lease_token,
        )

    # Wrong worker attempt
    with pytest.raises(LeaseInvalidError, match="Lease requester mismatch"):
        gm.create_grant(
            space_id="space-main",
            worker_id="worker-imposter",
            node_id="node-test-01",
            device_id="gpu-0",
            capability="gpu.cuda",
            lease_token=lease_token,
        )


def test_dual_invalidation_flow(setup_env) -> None:
    bus, rm, registry, gm, secret = setup_env

    res_ident = ResourceIdentity("gpu", "node-test-01", "gpu-0")
    acq = rm.acquire(
        space_id="space-main",
        requester_id="worker-01",
        identity=res_ident,
        units=1,
    )
    assert acq.lease is not None
    lease_token = acq.lease.lease_token

    grant = gm.create_grant(
        space_id="space-main",
        worker_id="worker-01",
        node_id="node-test-01",
        device_id="gpu-0",
        capability="gpu.cuda",
        lease_token=lease_token,
    )

    # Revoking the grant must also revoke the backing lease in ResourceManager
    gm.revoke_grant(grant.grant_id, revoked_by="security_officer")
    assert grant.state == GrantState.REVOKED

    # Backing lease should now be revoked in ResourceManager
    with pytest.raises(GrantRevokedError, match="has been revoked"):
        gm.validate_grant_active(grant.grant_id)


def test_lease_revocation_invalidates_grant(setup_env) -> None:
    bus, rm, registry, gm, secret = setup_env

    res_ident = ResourceIdentity("gpu", "node-test-01", "gpu-0")
    acq = rm.acquire(
        space_id="space-main",
        requester_id="worker-01",
        identity=res_ident,
        units=1,
    )
    assert acq.lease is not None
    lease_token = acq.lease.lease_token

    grant = gm.create_grant(
        space_id="space-main",
        worker_id="worker-01",
        node_id="node-test-01",
        device_id="gpu-0",
        capability="gpu.cuda",
        lease_token=lease_token,
    )

    # Authoritatively revoke lease directly at ResourceManager
    rm.revoke(space_id="space-main", lease_token=lease_token, reason="budget_exhausted")

    # Grant validation must detect that backing lease is revoked
    with pytest.raises(GrantRevokedError, match="Backing lease .* was revoked"):
        gm.validate_grant_active(grant.grant_id)
