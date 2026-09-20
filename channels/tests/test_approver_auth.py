"""Unit tests for token-hmac-v1 wire protocol and ApproverAuthenticator.

spec §4, §16, ROADMAP Phase 8, CONTRACT_MATRIX HUMAN-001, CLI-002, ADR-0023 — Phase 8
"""

import time
import uuid
import pytest
from datetime import datetime, timedelta, timezone

from channels.approval.auth import (
    ApproverAuthenticator,
    ApproverCredentialRecord,
    ApproverDecisionSubmission,
    ClockSkewError,
    InMemoryCredentialStore,
    InvalidSignatureError,
    MalformedAuthenticationPayloadError,
    ReplayDetectedError,
    TokenExpiredError,
    TokenRevokedError,
    UnknownApproverError,
    compute_token_hmac_v1_signature,
)


@pytest.fixture
def auth_setup():
    cred_store = InMemoryCredentialStore()
    nonce_store = cred_store
    secret_store = {
        "secret://approver/alice-key": "alice_secret_token_123",
        "secret://approver/bob-key": "bob_secret_token_456",
    }
    auth = ApproverAuthenticator(
        cred_store=cred_store,
        nonce_store=nonce_store,
        secret_store=secret_store,
        clock_skew_seconds=60.0,
    )

    now = datetime.now(timezone.utc)
    cred_alice = ApproverCredentialRecord(
        approver_id="alice",
        token_id="tok-alice-1",
        secret_ref="secret://approver/alice-key",
        created_at=now,
        expires_at=now + timedelta(days=30),
    )
    cred_store.register_credential(cred_alice)

    return auth, cred_store, secret_store


def test_compute_signature_canonical_preimage():
    sig = compute_token_hmac_v1_signature(
        secret_key_bytes=b"my_secret",
        approver_id="alice",
        timestamp=1700000000,
        nonce="0123456789abcdef0123456789abcdef",
        space_id="space-1",
        approval_id="11111111-1111-1111-1111-111111111111",
        decision="APPROVE",
        plan_version=2,
        capability_request_hash="a" * 64,
    )
    assert len(sig) == 64
    assert sig.islower()


def test_newline_injection_rejected():
    with pytest.raises(MalformedAuthenticationPayloadError):
        compute_token_hmac_v1_signature(
            secret_key_bytes=b"my_secret",
            approver_id="alice\nevil:true",
            timestamp=1700000000,
            nonce="0123456789abcdef0123456789abcdef",
            space_id="space-1",
            approval_id="11111111-1111-1111-1111-111111111111",
            decision="APPROVE",
            plan_version=1,
            capability_request_hash="a" * 64,
        )


def test_verify_submission_success(auth_setup):
    auth, _, secret_store = auth_setup
    now_ts = int(time.time())
    nonce = "a1b2c3d4e5f60718293a4b5c6d7e8f90"
    app_id = str(uuid.uuid4())
    req_hash = "f" * 64

    sig = compute_token_hmac_v1_signature(
        secret_key_bytes=b"alice_secret_token_123",
        approver_id="alice",
        timestamp=now_ts,
        nonce=nonce,
        space_id="space-1",
        approval_id=app_id,
        decision="APPROVE",
        plan_version=1,
        capability_request_hash=req_hash,
    )

    sub = ApproverDecisionSubmission(
        approver_id="alice",
        timestamp=now_ts,
        nonce=nonce,
        space_id="space-1",
        approval_id=app_id,
        decision="APPROVE",
        plan_version=1,
        capability_request_hash=req_hash,
        signature=sig,
    )

    assert auth.verify_submission(sub, expected_space_approver_id="alice") is True


def test_clock_skew_bounds(auth_setup):
    auth, _, _ = auth_setup
    now_ts = int(time.time())
    app_id = str(uuid.uuid4())
    req_hash = "f" * 64

    # Timestamp 75s in the past (> 60s)
    old_ts = now_ts - 75
    sub_old = ApproverDecisionSubmission(
        approver_id="alice",
        timestamp=old_ts,
        nonce="11112222333344445555666677778888",
        space_id="space-1",
        approval_id=app_id,
        decision="APPROVE",
        plan_version=1,
        capability_request_hash=req_hash,
        signature="0" * 64,
    )
    with pytest.raises(ClockSkewError):
        auth.verify_submission(sub_old, current_time=now_ts)

    # Timestamp 75s in the future (> 60s)
    future_ts = now_ts + 75
    sub_future = ApproverDecisionSubmission(
        approver_id="alice",
        timestamp=future_ts,
        nonce="22223333444455556666777788889999",
        space_id="space-1",
        approval_id=app_id,
        decision="APPROVE",
        plan_version=1,
        capability_request_hash=req_hash,
        signature="0" * 64,
    )
    with pytest.raises(ClockSkewError):
        auth.verify_submission(sub_future, current_time=now_ts)


def test_replay_nonce_rejection(auth_setup):
    auth, _, _ = auth_setup
    now_ts = int(time.time())
    nonce = "33334444555566667777888899990000"
    app_id = str(uuid.uuid4())
    req_hash = "f" * 64

    sig = compute_token_hmac_v1_signature(
        secret_key_bytes=b"alice_secret_token_123",
        approver_id="alice",
        timestamp=now_ts,
        nonce=nonce,
        space_id="space-1",
        approval_id=app_id,
        decision="APPROVE",
        plan_version=1,
        capability_request_hash=req_hash,
    )

    sub = ApproverDecisionSubmission(
        approver_id="alice",
        timestamp=now_ts,
        nonce=nonce,
        space_id="space-1",
        approval_id=app_id,
        decision="APPROVE",
        plan_version=1,
        capability_request_hash=req_hash,
        signature=sig,
    )

    # First submission succeeds
    assert auth.verify_submission(sub) is True

    # Replay with same nonce raises ReplayDetectedError
    with pytest.raises(ReplayDetectedError):
        auth.verify_submission(sub)


def test_revoked_token_rejection(auth_setup):
    auth, cred_store, _ = auth_setup
    now_ts = int(time.time())
    nonce = "44445555666677778888999900001111"
    app_id = str(uuid.uuid4())
    req_hash = "f" * 64

    # Revoke Alice
    cred_store.revoke_credential("alice", reason="Compromised token")

    sub = ApproverDecisionSubmission(
        approver_id="alice",
        timestamp=now_ts,
        nonce=nonce,
        space_id="space-1",
        approval_id=app_id,
        decision="APPROVE",
        plan_version=1,
        capability_request_hash=req_hash,
        signature="0" * 64,
    )

    with pytest.raises(TokenRevokedError):
        auth.verify_submission(sub)


def test_expired_token_rejection(auth_setup):
    auth, cred_store, _ = auth_setup
    now_ts = int(time.time())
    now = datetime.now(timezone.utc)
    app_id = str(uuid.uuid4())
    req_hash = "f" * 64

    # Register expired credential for Charlie
    cred_charlie = ApproverCredentialRecord(
        approver_id="charlie",
        token_id="tok-charlie-1",
        secret_ref="secret://approver/charlie-key",
        created_at=now - timedelta(days=10),
        expires_at=now - timedelta(days=1),
    )
    cred_store.register_credential(cred_charlie)

    sub = ApproverDecisionSubmission(
        approver_id="charlie",
        timestamp=now_ts,
        nonce="55556666777788889999000011112222",
        space_id="space-1",
        approval_id=app_id,
        decision="APPROVE",
        plan_version=1,
        capability_request_hash=req_hash,
        signature="0" * 64,
    )

    with pytest.raises(TokenExpiredError):
        auth.verify_submission(sub)


def test_unknown_approver_rejection(auth_setup):
    auth, _, _ = auth_setup
    now_ts = int(time.time())
    sub = ApproverDecisionSubmission(
        approver_id="unknown_user",
        timestamp=now_ts,
        nonce="66667777888899990000111122223333",
        space_id="space-1",
        approval_id=str(uuid.uuid4()),
        decision="APPROVE",
        plan_version=1,
        capability_request_hash="f" * 64,
        signature="0" * 64,
    )

    with pytest.raises(UnknownApproverError):
        auth.verify_submission(sub)


def test_tampered_signature_rejection(auth_setup):
    auth, _, _ = auth_setup
    now_ts = int(time.time())
    sub = ApproverDecisionSubmission(
        approver_id="alice",
        timestamp=now_ts,
        nonce="77778888999900001111222233334444",
        space_id="space-1",
        approval_id=str(uuid.uuid4()),
        decision="APPROVE",
        plan_version=1,
        capability_request_hash="f" * 64,
        signature="bad" + ("0" * 61),
    )

    with pytest.raises(InvalidSignatureError):
        auth.verify_submission(sub)

