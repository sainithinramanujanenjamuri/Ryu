"""Unit tests for Lease and LeaseManager lifecycle.

spec §9 (Resource Manager), §16 (Lease), CONTRACT_MATRIX RESOURCE-002..RESOURCE-004 — Phase 3
"""

from datetime import datetime, timezone

import pytest

from core.resources.clock import FakeClock
from core.resources.identity import ResourceIdentity
from core.resources.lease import LeaseManager, LeaseState


def test_lease_issuance_and_expiration() -> None:
    clock = FakeClock(datetime(2026, 6, 1, 10, 0, 0, tzinfo=timezone.utc))
    mgr = LeaseManager(clock=clock)
    res_id = ResourceIdentity("gpu", "node-1", "cuda-0")

    lease = mgr.issue_lease(
        resource_id=res_id,
        space_id="space-alpha",
        requester_id="agent-1",
        duration_seconds=30.0,
    )

    assert lease.lease_token.startswith("lease-")
    assert lease.state == LeaseState.ACTIVE
    assert lease.is_valid(clock.now())
    assert lease.renewed_count == 0

    # Advance clock by 10s: still valid
    clock.advance(10.0)
    assert lease.is_valid(clock.now())

    # Advance clock past expiry (remaining 21s)
    clock.advance(21.0)
    assert not lease.is_valid(clock.now())

    # Sweep marks it EXPIRED
    expired = mgr.sweep_expirations()
    assert len(expired) == 1
    assert expired[0].lease_token == lease.lease_token
    assert lease.state == LeaseState.EXPIRED


def test_lease_renewal_rules() -> None:
    clock = FakeClock(datetime(2026, 6, 1, 10, 0, 0, tzinfo=timezone.utc))
    mgr = LeaseManager(clock=clock)
    res_id = ResourceIdentity("gpu", "node-1", "cuda-0")

    lease = mgr.issue_lease(
        resource_id=res_id,
        space_id="space-alpha",
        requester_id="agent-1",
        duration_seconds=60.0,
    )

    # 1. Happy path renewal
    clock.advance(30.0)
    renewed = mgr.renew_lease(
        space_id="space-alpha",
        requester_id="agent-1",
        lease_token=lease.lease_token,
        extension_seconds=60.0,
    )
    assert renewed.renewed_count == 1
    assert renewed.expiry == datetime(2026, 6, 1, 10, 2, 0, tzinfo=timezone.utc)

    # 2. Wrong holder renewal rejected
    with pytest.raises(PermissionError, match="Unauthorized lease renewal"):
        mgr.renew_lease(
            space_id="space-alpha",
            requester_id="imposter-agent",
            lease_token=lease.lease_token,
        )

    # 3. Cross-space renewal rejected
    with pytest.raises(PermissionError, match="Cross-space lease renewal rejected"):
        mgr.renew_lease(
            space_id="other-space",
            requester_id="agent-1",
            lease_token=lease.lease_token,
        )

    # 4. Expired lease cannot be resurrected
    clock.advance(150.0)  # past new expiry
    assert not lease.is_valid(clock.now())
    with pytest.raises(ValueError, match="Cannot renew lease"):
        mgr.renew_lease(
            space_id="space-alpha",
            requester_id="agent-1",
            lease_token=lease.lease_token,
        )


def test_lease_release_and_revocation() -> None:
    clock = FakeClock(datetime(2026, 6, 1, 10, 0, 0, tzinfo=timezone.utc))
    mgr = LeaseManager(clock=clock)
    res_id = ResourceIdentity("cpu", "host", "slot-1")

    lease = mgr.issue_lease(
        resource_id=res_id,
        space_id="space-alpha",
        requester_id="agent-1",
        duration_seconds=60.0,
    )

    # Wrong holder release rejected
    with pytest.raises(PermissionError, match="Unauthorized lease release"):
        mgr.release_lease("space-alpha", "agent-2", lease.lease_token)

    # Cross-space release rejected
    with pytest.raises(PermissionError, match="Cross-space lease release rejected"):
        mgr.release_lease("space-beta", "agent-1", lease.lease_token)

    # Happy path release
    released = mgr.release_lease("space-alpha", "agent-1", lease.lease_token)
    assert released.state == LeaseState.RELEASED
    assert not released.is_valid(clock.now())

    # Repeated release is idempotent
    released_again = mgr.release_lease("space-alpha", "agent-1", lease.lease_token)
    assert released_again.state == LeaseState.RELEASED


def test_lease_revocation() -> None:
    clock = FakeClock(datetime(2026, 6, 1, 10, 0, 0, tzinfo=timezone.utc))
    mgr = LeaseManager(clock=clock)
    res_id = ResourceIdentity("model", "cloud", "gpt-4")

    lease = mgr.issue_lease(
        resource_id=res_id,
        space_id="space-alpha",
        requester_id="agent-1",
        duration_seconds=60.0,
    )

    # Cross-space revocation rejected
    with pytest.raises(PermissionError, match="Cross-space lease revocation rejected"):
        mgr.revoke_lease("space-beta", lease.lease_token)

    # Authorized revocation
    revoked = mgr.revoke_lease("space-alpha", lease.lease_token)
    assert revoked.state == LeaseState.REVOKED
    assert not revoked.is_valid(clock.now())

