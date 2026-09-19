"""Unit tests for ResourceIdentity and Resource models.

spec §9 (Resource Manager), CONTRACT_MATRIX RESOURCE-001 — Phase 3
"""

import pytest

from core.resources.identity import Resource, ResourceIdentity


def test_resource_identity_handle_and_parsing() -> None:
    ident = ResourceIdentity(
        resource_type="gpu",
        provider_id="node-windows-1",
        instance_id="cuda-0",
    )
    assert ident.to_handle() == "gpu/node-windows-1/cuda-0"
    assert str(ident) == "gpu/node-windows-1/cuda-0"

    # Dict serialization matching grants.json
    d = ident.to_dict()
    assert d == {
        "resource_type": "gpu",
        "provider_id": "node-windows-1",
        "instance_id": "cuda-0",
    }

    # Reconstruct from dict
    reconstructed = ResourceIdentity.from_dict(d)
    assert reconstructed == ident

    # Reconstruct from handle
    from_h = ResourceIdentity.from_handle("gpu/node-windows-1/cuda-0")
    assert from_h == ident


def test_resource_identity_validation() -> None:
    with pytest.raises(ValueError, match="resource_type cannot be empty"):
        ResourceIdentity(resource_type="", provider_id="prov", instance_id="inst")

    with pytest.raises(ValueError, match="provider_id cannot be empty"):
        ResourceIdentity(resource_type="gpu", provider_id="", instance_id="inst")

    with pytest.raises(ValueError, match="instance_id cannot be empty"):
        ResourceIdentity(resource_type="gpu", provider_id="prov", instance_id="")

    with pytest.raises(ValueError, match="Invalid resource handle format"):
        ResourceIdentity.from_handle("invalid-handle-without-slashes")


def test_resource_capacity_accounting() -> None:
    ident = ResourceIdentity("cpu", "host", "core-0")
    res = Resource(identity=ident, space_id="space-1", total_capacity=4)

    assert res.total_capacity == 4
    assert res.allocated_capacity == 0
    assert res.available_capacity == 4
    assert res.is_available

    # Allocate 2 units
    res.allocate(2)
    assert res.allocated_capacity == 2
    assert res.available_capacity == 2

    # Allocate 2 units (full)
    res.allocate(2)
    assert res.allocated_capacity == 4
    assert res.available_capacity == 0
    assert not res.is_available

    # Over-allocation fails
    with pytest.raises(ValueError, match="Cannot allocate"):
        res.allocate(1)

    # Deallocate
    res.deallocate(2)
    assert res.allocated_capacity == 2
    assert res.available_capacity == 2
    assert res.is_available

    # Deallocating more than allocated fails
    with pytest.raises(ValueError, match="Cannot deallocate"):
        res.deallocate(3)

