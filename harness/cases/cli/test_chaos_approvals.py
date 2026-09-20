"""Chaos Failure Scenarios for Phase 8 CLI Channel and Human Gates.

Verifies all 9 chaos failure scenarios (C-01 through C-09).
spec §2, §4, §16, ROADMAP Phase 8 — Phase 8
"""

import concurrent.futures
import io
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from channels.approval.auth import (
    ApproverAuthenticator,
    ApproverCredentialRecord,
    ApproverDecisionSubmission,
    ClockSkewError,
    InMemoryCredentialStore,
)
from channels.approval.client import ApprovalClient
from channels.cli import EXIT_SYNTAX_ERROR, CLIContext, main
from core.space.approver import (
    ApprovalManager,
    ApprovalRequest,
    InMemoryApprovalStore,
)
from core.space.attention import AttentionBudget


@pytest.fixture
def chaos_env():
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
    cred_store.register_credential(
        ApproverCredentialRecord(
            approver_id="alice",
            token_id="tok-alice-1",
            secret_ref="secret://approver/alice-key",
            created_at=now,
            expires_at=now + timedelta(days=30),
        )
    )
    cred_store.register_credential(
        ApproverCredentialRecord(
            approver_id="bob",
            token_id="tok-bob-1",
            secret_ref="secret://approver/bob-key",
            created_at=now,
            expires_at=now + timedelta(days=30),
        )
    )

    store = InMemoryApprovalStore()
    mgr = ApprovalManager(store=store)
    mgr.set_space_approver("space-chaos", "alice")
    client = ApprovalClient(mgr, auth)

    return {
        "auth": auth,
        "cred_store": cred_store,
        "secret_store": secret_store,
        "mgr": mgr,
        "store": store,
        "client": client,
    }


# C-01: Disconnect / unresponsiveness during prompt (timeout fires, default_deny enforced)
def test_c01_disconnect_prompt_timeout(chaos_env):
    mgr = chaos_env["mgr"]
    app_id = str(uuid.uuid4())

    req = mgr.request_approval(
        request_id=app_id,
        space_id="space-chaos",
        capability="fs.delete",
        timeout_class="default_deny",
        timeout_seconds=5.0,
    )
    assert req.status == "pending"

    # Simulate unresponsiveness / elapsed time past timeout
    future_time = req.created_at + 10.0
    status = mgr.check_timeout(app_id, current_time=future_time, as_expired=True)

    assert status == "expired"
    updated = mgr.get_request(app_id)
    assert updated is not None
    assert updated.status == "expired"
    assert updated.queue_state == "resolved"


# C-02: Process crash during pending approval (state preserved from store)
def test_c02_process_crash_state_preservation(chaos_env):
    store = chaos_env["store"]
    app_id = str(uuid.uuid4())

    # Create approval before crash
    req = ApprovalRequest(
        request_id=app_id,
        space_id="space-chaos",
        capability="net.bind",
        approver_id="alice",
        status="pending",
        queue_state="queued",
    )
    store.save(req)

    # Simulate restart by creating a new ApprovalManager with same store
    new_mgr = ApprovalManager(store=store)
    recovered = new_mgr.get_request(app_id)

    assert recovered is not None
    assert recovered.request_id == app_id
    assert recovered.capability == "net.bind"
    assert recovered.status == "pending"


# C-03: Concurrent resolution collision (race between 2 approvers: exactly one succeeds)
def test_c03_concurrent_resolution_collision(chaos_env):
    mgr = chaos_env["mgr"]
    app_id = str(uuid.uuid4())

    mgr.request_approval(
        request_id=app_id,
        space_id="space-chaos",
        capability="shell.exec",
    )

    results = []

    def attempt_resolve(approved: bool, approver: str):
        res = mgr.resolve(app_id, approved=approved, approver_id=approver)
        results.append((approver, res))

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        f1 = executor.submit(attempt_resolve, True, "alice")
        f2 = executor.submit(attempt_resolve, False, "bob")
        f1.result()
        f2.result()

    successes = [r for r in results if r[1] is True]
    failures = [r for r in results if r[1] is False]

    # Exactly one CAS transition succeeds
    assert len(successes) == 1
    assert len(failures) == 1


# C-04: Timeout race condition (resolution submitted simultaneously with timeout check)
def test_c04_timeout_race_condition(chaos_env):
    mgr = chaos_env["mgr"]
    app_id = str(uuid.uuid4())

    mgr.request_approval(
        request_id=app_id,
        space_id="space-chaos",
        capability="fs.write",
        timeout_seconds=0.01,
    )
    time.sleep(0.02)

    results = []

    def resolve_op():
        res = mgr.resolve(app_id, approved=True, approver_id="alice")
        results.append(("resolve", res))

    def timeout_op():
        res = mgr.check_timeout(app_id, as_expired=True)
        results.append(("timeout", res))

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        f1 = executor.submit(resolve_op)
        f2 = executor.submit(timeout_op)
        f1.result()
        f2.result()

    final_req = mgr.get_request(app_id)
    assert final_req is not None
    # Terminal status must be either approved or expired/denied, never corrupted or pending
    assert final_req.status in ("approved", "expired", "denied")


# C-05: Clock skew during transient NTP jump rejected safely
def test_c05_ntp_jump_rejected(chaos_env):
    auth = chaos_env["auth"]
    now_ts = int(time.time())
    app_id = str(uuid.uuid4())

    # Transient jump 300s into future
    jumped_ts = now_ts + 300
    sub = ApproverDecisionSubmission(
        approver_id="alice",
        timestamp=jumped_ts,
        nonce="c" * 32,
        space_id="space-chaos",
        approval_id=app_id,
        decision="APPROVE",
        plan_version=1,
        capability_request_hash="f" * 64,
        signature="0" * 64,
    )
    with pytest.raises(ClockSkewError):
        auth.verify_submission(sub, current_time=now_ts)


# C-06: Ingestion of malformed JSON payload into CLI gracefully handled
def test_c06_cli_malformed_arguments():
    out = io.StringIO()
    err = io.StringIO()
    ctx = CLIContext(out_stream=out, err_stream=err)

    # Malformed arguments (missing subcommands, invalid parameters)
    code = main(["approval", "approve", "app-123", "--unknown-flag"], ctx=ctx)
    assert code == EXIT_SYNTAX_ERROR
    assert "usage error" in err.getvalue().lower()


# C-07: Attention budget limit <= 0 rejected with ValueError
def test_c07_attention_budget_zero_limit_rejected():
    budget = AttentionBudget()
    with pytest.raises(ValueError):
        budget.set_limit("space-chaos", 0)

    with pytest.raises(ValueError):
        budget.set_limit("space-chaos", -5)


# C-08: Database connection loss during approval save / CAS fails safely
def test_c08_database_connection_loss_fail_closed():
    from unittest.mock import MagicMock

    from ryu.pulse_bus.config import PostgresConfig

    from channels.approval.store import PostgresApprovalStore

    store = PostgresApprovalStore(PostgresConfig())
    # Mock connection failure
    store._get_conn = MagicMock(side_effect=Exception("Database connection lost (5432)"))

    req = ApprovalRequest(
        request_id="app-c08",
        space_id="space-chaos",
        capability="fs.write",
        approver_id="alice",
    )
    with pytest.raises(Exception) as exc:
        store.save(req)
    assert "Database connection lost" in str(exc.value)


# C-09: Sudden plan version bump while approval is pending
def test_c09_plan_version_bump_invalidates_consumption(chaos_env):
    mgr = chaos_env["mgr"]
    app_id = str(uuid.uuid4())
    req_hash = "9" * 64

    # Request issued under plan_version = 1
    mgr.request_approval(
        request_id=app_id,
        space_id="space-chaos",
        capability="device.gpu",
        plan_version=1,
        capability_request_hash=req_hash,
    )
    # Human approves it
    mgr.resolve(app_id, approved=True, approver_id="alice")

    # In the meantime, Space Kernel committed a new plan delta (bumped to version 2)
    current_plan_version = 2

    # Consumption must be rejected because approval was granted for obsolete plan version 1
    consumed = mgr.consume_approval(
        approval_id=app_id,
        current_plan_version=current_plan_version,
        capability_request_hash=req_hash,
    )
    assert consumed is False
