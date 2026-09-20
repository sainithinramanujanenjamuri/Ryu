"""Adversarial Security Test Suite for Phase 8 CLI Channel and Human Gates.

Verifies all 22 security threat vectors (SEC-01 through SEC-22).
spec §2, §4, §16, ROADMAP Phase 8 — Phase 8
"""

import os
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from ryu.pulse_bus.bus import PulseBus
from ryu.pulse_bus.config import PostgresConfig
from ryu.pulse_bus.pulse import Pulse, Severity
from ryu.pulse_bus.reject import PulseRejectedError
from ryu.pulse_bus.validator import PulseValidator

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
from channels.approval.client import ApprovalClient
from channels.cli.terminal import capture_input
from core.space.approver import (
    ApprovalManager,
    InMemoryApprovalStore,
)


@pytest.fixture
def sec_env():
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
    cred_bob = ApproverCredentialRecord(
        approver_id="bob",
        token_id="tok-bob-1",
        secret_ref="secret://approver/bob-key",
        created_at=now,
        expires_at=now + timedelta(days=30),
    )
    cred_store.register_credential(cred_alice)
    cred_store.register_credential(cred_bob)

    bus = PulseBus()
    store = InMemoryApprovalStore()
    mgr = ApprovalManager(bus=bus, store=store)
    mgr.set_space_approver("space-sec", "alice")
    client = ApprovalClient(mgr, auth)

    return {
        "auth": auth,
        "cred_store": cred_store,
        "secret_store": secret_store,
        "mgr": mgr,
        "client": client,
        "bus": bus,
        "store": store,
    }


# SEC-01: Forged HMAC signature rejected
def test_sec_01_forged_hmac_signature_rejected(sec_env):
    auth = sec_env["auth"]
    sub = ApproverDecisionSubmission(
        approver_id="alice",
        timestamp=int(time.time()),
        nonce="1" * 32,
        space_id="space-sec",
        approval_id=str(uuid.uuid4()),
        decision="APPROVE",
        plan_version=1,
        capability_request_hash="f" * 64,
        signature="deadbeef" * 8,
    )
    with pytest.raises(InvalidSignatureError):
        auth.verify_submission(sub)


# SEC-02: Tampered pre-image fields invalidates signature
def test_sec_02_tampered_preimage_fields(sec_env):
    auth = sec_env["auth"]
    now_ts = int(time.time())
    app_id = str(uuid.uuid4())
    nonce = "2" * 32
    req_hash = "a" * 64

    sig = compute_token_hmac_v1_signature(
        secret_key_bytes=b"alice_secret_token_123",
        approver_id="alice",
        timestamp=now_ts,
        nonce=nonce,
        space_id="space-sec",
        approval_id=app_id,
        decision="APPROVE",
        plan_version=1,
        capability_request_hash=req_hash,
    )

    # Tamper decision from APPROVE to REJECT
    tampered_sub = ApproverDecisionSubmission(
        approver_id="alice",
        timestamp=now_ts,
        nonce=nonce,
        space_id="space-sec",
        approval_id=app_id,
        decision="REJECT",
        plan_version=1,
        capability_request_hash=req_hash,
        signature=sig,
    )
    with pytest.raises(InvalidSignatureError):
        auth.verify_submission(tampered_sub)


# SEC-03: Wire protocol downgrade attack rejected
def test_sec_03_protocol_downgrade_rejected(sec_env):
    auth = sec_env["auth"]
    sub = ApproverDecisionSubmission(
        approver_id="alice",
        timestamp=int(time.time()),
        nonce="3" * 32,
        space_id="space-sec",
        approval_id=str(uuid.uuid4()),
        decision="APPROVE",
        plan_version=1,
        capability_request_hash="a" * 64,
        signature="0" * 64,
        protocol="token-cleartext-v0",
    )
    with pytest.raises(MalformedAuthenticationPayloadError):
        auth.verify_submission(sub)


# SEC-04: Clock skew injection past +60s rejected
def test_sec_04_clock_skew_future_rejected(sec_env):
    auth = sec_env["auth"]
    now_ts = int(time.time())
    sub = ApproverDecisionSubmission(
        approver_id="alice",
        timestamp=now_ts + 65,
        nonce="4" * 32,
        space_id="space-sec",
        approval_id=str(uuid.uuid4()),
        decision="APPROVE",
        plan_version=1,
        capability_request_hash="a" * 64,
        signature="0" * 64,
    )
    with pytest.raises(ClockSkewError):
        auth.verify_submission(sub, current_time=now_ts)


# SEC-05: Clock skew injection past -60s rejected
def test_sec_05_clock_skew_past_rejected(sec_env):
    auth = sec_env["auth"]
    now_ts = int(time.time())
    sub = ApproverDecisionSubmission(
        approver_id="alice",
        timestamp=now_ts - 65,
        nonce="5" * 32,
        space_id="space-sec",
        approval_id=str(uuid.uuid4()),
        decision="APPROVE",
        plan_version=1,
        capability_request_hash="a" * 64,
        signature="0" * 64,
    )
    with pytest.raises(ClockSkewError):
        auth.verify_submission(sub, current_time=now_ts)


# SEC-06: Nonce replay attack rejected
def test_sec_06_nonce_replay_rejected(sec_env):
    auth = sec_env["auth"]
    now_ts = int(time.time())
    app_id = str(uuid.uuid4())
    nonce = "6" * 32
    req_hash = "b" * 64

    sig = compute_token_hmac_v1_signature(
        secret_key_bytes=b"alice_secret_token_123",
        approver_id="alice",
        timestamp=now_ts,
        nonce=nonce,
        space_id="space-sec",
        approval_id=app_id,
        decision="APPROVE",
        plan_version=1,
        capability_request_hash=req_hash,
    )
    sub = ApproverDecisionSubmission(
        approver_id="alice",
        timestamp=now_ts,
        nonce=nonce,
        space_id="space-sec",
        approval_id=app_id,
        decision="APPROVE",
        plan_version=1,
        capability_request_hash=req_hash,
        signature=sig,
    )
    assert auth.verify_submission(sub) is True
    with pytest.raises(ReplayDetectedError):
        auth.verify_submission(sub)


# SEC-07: Nonce case sensitivity bypass prevented
def test_sec_07_nonce_case_sensitivity_canonicalized(sec_env):
    auth = sec_env["auth"]
    now_ts = int(time.time())
    app_id = str(uuid.uuid4())
    nonce_upper = "7A7B7C7D7E7F" + ("0" * 20)
    req_hash = "c" * 64

    sig = compute_token_hmac_v1_signature(
        secret_key_bytes=b"alice_secret_token_123",
        approver_id="alice",
        timestamp=now_ts,
        nonce=nonce_upper,
        space_id="space-sec",
        approval_id=app_id,
        decision="APPROVE",
        plan_version=1,
        capability_request_hash=req_hash,
    )
    sub1 = ApproverDecisionSubmission(
        approver_id="alice",
        timestamp=now_ts,
        nonce=nonce_upper,
        space_id="space-sec",
        approval_id=app_id,
        decision="APPROVE",
        plan_version=1,
        capability_request_hash=req_hash,
        signature=sig,
    )
    assert auth.verify_submission(sub1) is True

    # Replay with lowercase version of same nonce must be detected
    sub2 = ApproverDecisionSubmission(
        approver_id="alice",
        timestamp=now_ts,
        nonce=nonce_upper.lower(),
        space_id="space-sec",
        approval_id=str(uuid.uuid4()),
        decision="APPROVE",
        plan_version=1,
        capability_request_hash=req_hash,
        signature=sig,
    )
    with pytest.raises(ReplayDetectedError):
        auth.verify_submission(sub2)


# SEC-08: Unknown approver rejected
def test_sec_08_unknown_approver_rejected(sec_env):
    auth = sec_env["auth"]
    sub = ApproverDecisionSubmission(
        approver_id="mallory",
        timestamp=int(time.time()),
        nonce="8" * 32,
        space_id="space-sec",
        approval_id=str(uuid.uuid4()),
        decision="APPROVE",
        plan_version=1,
        capability_request_hash="d" * 64,
        signature="0" * 64,
    )
    with pytest.raises(UnknownApproverError):
        auth.verify_submission(sub)


# SEC-09: Revoked approver credential rejected
def test_sec_09_revoked_approver_rejected(sec_env):
    auth = sec_env["auth"]
    cred_store = sec_env["cred_store"]
    cred_store.revoke_credential("bob", reason="Compromised device")

    sub = ApproverDecisionSubmission(
        approver_id="bob",
        timestamp=int(time.time()),
        nonce="9" * 32,
        space_id="space-sec",
        approval_id=str(uuid.uuid4()),
        decision="APPROVE",
        plan_version=1,
        capability_request_hash="e" * 64,
        signature="0" * 64,
    )
    with pytest.raises(TokenRevokedError):
        auth.verify_submission(sub)


# SEC-10: Expired approver credential rejected
def test_sec_10_expired_approver_rejected(sec_env):
    auth = sec_env["auth"]
    cred_store = sec_env["cred_store"]
    now = datetime.now(timezone.utc)
    cred_store.register_credential(
        ApproverCredentialRecord(
            approver_id="eve",
            token_id="tok-eve",
            secret_ref="secret://approver/eve-key",
            created_at=now - timedelta(days=2),
            expires_at=now - timedelta(days=1),
        )
    )
    sub = ApproverDecisionSubmission(
        approver_id="eve",
        timestamp=int(time.time()),
        nonce="a" * 32,
        space_id="space-sec",
        approval_id=str(uuid.uuid4()),
        decision="APPROVE",
        plan_version=1,
        capability_request_hash="e" * 64,
        signature="0" * 64,
    )
    with pytest.raises(TokenExpiredError):
        auth.verify_submission(sub)


# SEC-11: SecretStore fail-closed resolution
def test_sec_11_secret_store_fail_closed(sec_env):
    auth = sec_env["auth"]
    cred_store = sec_env["cred_store"]
    now = datetime.now(timezone.utc)
    # Register credential pointing to missing secret ref
    cred_store.register_credential(
        ApproverCredentialRecord(
            approver_id="dave",
            token_id="tok-dave",
            secret_ref="secret://approver/missing-key",
            created_at=now,
            expires_at=now + timedelta(days=1),
        )
    )
    sub = ApproverDecisionSubmission(
        approver_id="dave",
        timestamp=int(time.time()),
        nonce="b" * 32,
        space_id="space-sec",
        approval_id=str(uuid.uuid4()),
        decision="APPROVE",
        plan_version=1,
        capability_request_hash="e" * 64,
        signature="0" * 64,
    )
    from channels.approval.auth import AuthenticationServiceUnavailableError
    with pytest.raises(AuthenticationServiceUnavailableError):
        auth.verify_submission(sub)


# SEC-12: Space approver identity mismatch rejected
def test_sec_12_space_approver_mismatch_rejected(sec_env):
    auth = sec_env["auth"]
    now_ts = int(time.time())
    nonce = "c" * 32
    app_id = str(uuid.uuid4())
    req_hash = "1" * 64

    sig = compute_token_hmac_v1_signature(
        secret_key_bytes=b"bob_secret_token_456",
        approver_id="bob",
        timestamp=now_ts,
        nonce=nonce,
        space_id="space-sec",
        approval_id=app_id,
        decision="APPROVE",
        plan_version=1,
        capability_request_hash=req_hash,
    )
    sub = ApproverDecisionSubmission(
        approver_id="bob",
        timestamp=now_ts,
        nonce=nonce,
        space_id="space-sec",
        approval_id=app_id,
        decision="APPROVE",
        plan_version=1,
        capability_request_hash=req_hash,
        signature=sig,
    )
    # bob is a valid approver, but space-sec designates alice
    with pytest.raises(PermissionError):
        auth.verify_submission(sub, expected_space_approver_id="alice")


# SEC-13: Newline / control character injection in pre-image rejected
def test_sec_13_newline_injection_rejected():
    with pytest.raises(MalformedAuthenticationPayloadError):
        compute_token_hmac_v1_signature(
            secret_key_bytes=b"key",
            approver_id="alice",
            timestamp=123,
            nonce="d" * 32,
            space_id="space\nattack",
            approval_id=str(uuid.uuid4()),
            decision="APPROVE",
            plan_version=1,
            capability_request_hash="2" * 64,
        )


# SEC-14: Plan version mismatch during capability consumption rejected
def test_sec_14_plan_version_mismatch_rejected(sec_env):
    mgr = sec_env["mgr"]
    app_id = str(uuid.uuid4())
    req_hash = "3" * 64

    mgr.request_approval(
        request_id=app_id,
        space_id="space-sec",
        capability="fs.write",
        plan_version=1,
        capability_request_hash=req_hash,
    )
    mgr.resolve(app_id, approved=True, approver_id="alice")

    # Current plan bumped to version 2 -> consumption fails
    consumed = mgr.consume_approval(
        approval_id=app_id,
        current_plan_version=2,
        capability_request_hash=req_hash,
    )
    assert consumed is False


# SEC-15: Capability request hash mismatch during capability consumption rejected
def test_sec_15_capability_request_hash_mismatch_rejected(sec_env):
    mgr = sec_env["mgr"]
    app_id = str(uuid.uuid4())
    req_hash = "4" * 64

    mgr.request_approval(
        request_id=app_id,
        space_id="space-sec",
        capability="net.connect",
        plan_version=1,
        capability_request_hash=req_hash,
    )
    mgr.resolve(app_id, approved=True, approver_id="alice")

    # Hash mismatch -> consumption fails
    consumed = mgr.consume_approval(
        approval_id=app_id,
        current_plan_version=1,
        capability_request_hash="wrong" + ("0" * 59),
    )
    assert consumed is False


# SEC-16: Direct unauthenticated emission of security.grant.approved rejected by bus
def test_sec_16_direct_grant_approved_pulse_rejected():
    validator = PulseValidator()
    pulse = Pulse(
        id="forged-approval-pulse",
        space_id="space-sec",
        type="security.grant.approved",
        severity=Severity.INFO,
        source="rogue_agent",  # Unauthorized source!
        timestamp=datetime.now(timezone.utc),
        payload={
            "request_id": "app-forged",
            "capability": "shell.exec",
            "risk_tier": "high",
            "approver_id": "alice",
            "expiry": datetime.now(timezone.utc).isoformat(),
        },
        correlation_id="corr-sec-16",
    )
    with pytest.raises(PulseRejectedError) as exc:
        validator.validate(pulse.type, pulse.payload, source=pulse.source)
    assert "unauthorized_pulse_source" in str(exc.value)


# SEC-17: Direct unauthenticated emission of security.taint.cleared rejected by bus
def test_sec_17_direct_taint_cleared_pulse_rejected():
    validator = PulseValidator()
    pulse = Pulse(
        id="forged-taint-cleared",
        space_id="space-sec",
        type="security.taint.cleared",
        severity=Severity.INFO,
        source="rogue_worker",  # Unauthorized source!
        timestamp=datetime.now(timezone.utc),
        payload={
            "correlation_id": "corr-123",
            "approver_id": "alice",
            "scope": "execution",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
        correlation_id="corr-sec-17",
    )
    with pytest.raises(PulseRejectedError) as exc:
        validator.validate(pulse.type, pulse.payload, source=pulse.source)
    assert "unauthorized_pulse_source" in str(exc.value)


# SEC-18: Double consumption of approval fails (single-use CAS)
def test_sec_18_double_consumption_rejected(sec_env):
    mgr = sec_env["mgr"]
    app_id = str(uuid.uuid4())
    req_hash = "5" * 64

    mgr.request_approval(
        request_id=app_id,
        space_id="space-sec",
        capability="fs.write",
        plan_version=1,
        capability_request_hash=req_hash,
    )
    mgr.resolve(app_id, approved=True, approver_id="alice")

    # First consumption succeeds
    first = mgr.consume_approval(app_id, 1, req_hash)
    assert first is True

    # Second consumption fails
    second = mgr.consume_approval(app_id, 1, req_hash)
    assert second is False


# SEC-19: Consumption of non-approved approval rejected
def test_sec_19_consumption_of_non_approved_rejected(sec_env):
    mgr = sec_env["mgr"]
    app_id = str(uuid.uuid4())
    req_hash = "6" * 64

    mgr.request_approval(
        request_id=app_id,
        space_id="space-sec",
        capability="fs.write",
        plan_version=1,
        capability_request_hash=req_hash,
    )
    # In PENDING state
    assert mgr.consume_approval(app_id, 1, req_hash) is False

    # In DENIED state
    mgr.resolve(app_id, approved=False, approver_id="alice")
    assert mgr.consume_approval(app_id, 1, req_hash) is False


# SEC-20: Piped / relayed CLI input tagged as tainted
def test_sec_20_piped_cli_input_tagged_tainted():
    import io
    fake_stream = io.StringIO("APPROVE\n")
    _, is_tainted = capture_input("prompt: ", in_stream=fake_stream)
    assert is_tainted is True


# SEC-21: Database pulse immutability trigger prevents UPDATE / DELETE
@pytest.mark.skipif(
    os.environ.get("RYU_INTEGRATION_TESTS") != "1",
    reason="Integration tests disabled; set RYU_INTEGRATION_TESTS=1",
)
def test_sec_21_database_pulse_immutability():
    import psycopg2
    cfg = PostgresConfig()
    conn = psycopg2.connect(
        host=cfg.host, port=cfg.port, dbname=cfg.db, user=cfg.user, password=cfg.password
    )
    with conn:
        with conn.cursor() as cur:
            p_id = f"pulse-audit-{int(time.time())}"
            cur.execute(
                """
                INSERT INTO pulses (id, space_id, type, severity, source, timestamp, payload, correlation_id)
                VALUES (%s, 'space-audit', 'runtime.started', 'info', 'test', NOW(), '{}', 'c-1');
                """,
                (p_id,),
            )
            # Try to UPDATE -> trigger raises exception
            with pytest.raises(psycopg2.DatabaseError) as exc:
                cur.execute("UPDATE pulses SET severity = 'error' WHERE id = %s;", (p_id,))
            assert "Audit Immutability Violation" in str(exc.value)


# SEC-22: Tampered decision_signature in approval record causes admission check to fail
def test_sec_22_tampered_decision_signature_fails_admission(sec_env):
    mgr = sec_env["mgr"]
    app_id = str(uuid.uuid4())
    req_hash = "7" * 64

    mgr.request_approval(
        request_id=app_id,
        space_id="space-sec",
        capability="shell.exec",
        plan_version=1,
        capability_request_hash=req_hash,
    )
    mgr.resolve(app_id, approved=True, approver_id="alice")

    req = mgr.get_request(app_id)
    assert req is not None
    # Tamper with decision signature
    req.decision_signature = "bad" + ("0" * 61)

    # Attempting to consume with tampered signature MUST fail
    consumed = mgr.consume_approval(
        approval_id=app_id,
        current_plan_version=1,
        capability_request_hash=req_hash,
    )
    assert consumed is False
