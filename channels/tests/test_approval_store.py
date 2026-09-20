"""Unit and integration tests for ApprovalStore and decision signatures.

spec §4, §16, ROADMAP Phase 8, ADR-0022, ADR-0024 — Phase 8
"""

import os
import time
from datetime import datetime, timedelta, timezone

import pytest
from ryu.pulse_bus.config import PostgresConfig

from channels.approval.auth import ApproverCredentialRecord
from channels.approval.store import PostgresApprovalStore
from core.space.approver import (
    ApprovalRequest,
    InMemoryApprovalStore,
    compute_decision_signature,
    verify_decision_signature,
)


def test_in_memory_store_save_and_get():
    store = InMemoryApprovalStore()
    req = ApprovalRequest(
        request_id="app-1",
        space_id="space-alpha",
        capability="fs.write",
        approver_id="alice",
    )
    store.save(req)

    retrieved = store.get("app-1")
    assert retrieved is not None
    assert retrieved.request_id == "app-1"
    assert retrieved.space_id == "space-alpha"
    assert retrieved.status == "pending"


def test_in_memory_store_cas_transitions():
    store = InMemoryApprovalStore()
    req = ApprovalRequest(
        request_id="app-cas-1",
        space_id="space-alpha",
        capability="net.connect",
        approver_id="alice",
    )
    store.save(req)

    # Invalid CAS: expected "approved", but current is "pending"
    assert store.transition_cas("app-cas-1", expected_status="approved", new_status="denied") is False

    # Valid CAS: pending -> approved
    now = time.time()
    assert store.transition_cas(
        "app-cas-1",
        expected_status="pending",
        new_status="approved",
        approver_id="alice",
        resolved_at=now,
        signature="sig123",
    ) is True

    updated = store.get("app-cas-1")
    assert updated is not None
    assert updated.status == "approved"
    assert updated.queue_state == "resolved"
    assert updated.decision_signature == "sig123"

    # Subsequent CAS: approved -> consumed
    assert store.transition_cas(
        "app-cas-1",
        expected_status="approved",
        new_status="consumed",
    ) is True

    consumed = store.get("app-cas-1")
    assert consumed is not None
    assert consumed.status == "consumed"
    assert consumed.consumed_at is not None


def test_decision_signature_tamper_evidence():
    key = b"kernel-secret-key-12345678901234"
    now = time.time()
    req = ApprovalRequest(
        request_id="app-sig-1",
        space_id="space-alpha",
        capability="shell.exec",
        approver_id="bob",
        status="approved",
        resolved_at=now,
        plan_version=2,
        capability_request_hash="c" * 64,
    )

    sig = compute_decision_signature(
        kernel_key_bytes=key,
        space_id=req.space_id,
        approval_id=req.approval_id,
        status=req.status,
        approver_id=req.approver_id,
        resolved_at=now,
        plan_version=req.plan_version,
        capability_request_hash=req.capability_request_hash,
    )
    req.decision_signature = sig

    # Verification succeeds with untampered record
    assert verify_decision_signature(key, req) is True

    # Tampering with status
    req.status = "denied"
    assert verify_decision_signature(key, req) is False
    req.status = "approved"

    # Tampering with plan_version
    req.plan_version = 3
    assert verify_decision_signature(key, req) is False
    req.plan_version = 2

    # Tampering with capability_request_hash
    req.capability_request_hash = "d" * 64
    assert verify_decision_signature(key, req) is False


@pytest.mark.skipif(
    os.environ.get("RYU_INTEGRATION_TESTS") != "1",
    reason="Integration tests disabled; set RYU_INTEGRATION_TESTS=1",
)
def test_postgres_approval_store_integration():
    cfg = PostgresConfig()
    store = PostgresApprovalStore(cfg)

    app_id = f"pg-app-{int(time.time())}"
    req = ApprovalRequest(
        request_id=app_id,
        space_id="space-pg",
        capability="device.gpu",
        approver_id="alice",
        capability_request_hash="e" * 64,
        plan_version=1,
    )

    # 1. Save
    store.save(req)

    # 2. Get
    loaded = store.get(app_id)
    assert loaded is not None
    assert loaded.request_id == app_id
    assert loaded.status == "pending"

    # 3. List
    listed = store.list_by_space("space-pg", status="pending")
    assert any(a.approval_id == app_id for a in listed)

    # 4. CAS Transition
    now = time.time()
    assert store.transition_cas(
        approval_id=app_id,
        expected_status="pending",
        new_status="approved",
        approver_id="alice",
        resolved_at=now,
        signature="pg-sig-123",
    ) is True

    loaded2 = store.get(app_id)
    assert loaded2 is not None
    assert loaded2.status == "approved"
    assert loaded2.queue_state == "resolved"

    # 5. Nonce consumption
    nonce = f"nonce{int(time.time())}"[:32].ljust(32, "0")
    # Need approver credentials registered for FK constraint
    now_dt = datetime.now(timezone.utc)
    cred = ApproverCredentialRecord(
        approver_id="alice",
        token_id="tok-alice-pg",
        secret_ref="secret://approver/alice-key",
        created_at=now_dt,
        expires_at=now_dt + timedelta(days=30),
    )
    store.register_credential(cred)

    consumed1 = store.consume_nonce(nonce, "alice", now)
    assert consumed1 is True

    # Replay
    consumed2 = store.consume_nonce(nonce, "alice", now)
    assert consumed2 is False

