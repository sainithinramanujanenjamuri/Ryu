"""E2E Contract Test Suite for Phase 8 CLI Channel and Human Gates.

Verifies:
- CLI-001 through CLI-008
- HUMAN-001 through HUMAN-005
- REC-006 (Audit Immutability)

spec §2, §4, §16, ROADMAP Phase 8 — Phase 8
"""

import io
import os
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from ryu.pulse_bus.bus import PulseBus
from ryu.pulse_bus.config import PostgresConfig
from ryu.pulse_bus.pulse import Pulse, Severity

from channels.approval.auth import (
    ApproverAuthenticator,
    ApproverCredentialRecord,
    InMemoryCredentialStore,
)
from channels.approval.client import ApprovalClient
from channels.cli import (
    EXIT_SUCCESS,
    CLIContext,
    main,
)
from core.capabilities.admission import CapabilityRequest
from core.orchestrator.orchestrator import SpaceOrchestrator
from core.space.kernel import SpaceKernel


@pytest.fixture
def e2e_env():
    cred_store = InMemoryCredentialStore()
    nonce_store = cred_store
    secret_store = {
        "secret://approver/alice-key": "alice_secret_token_123",
    }
    auth = ApproverAuthenticator(
        cred_store=cred_store,
        nonce_store=nonce_store,
        secret_store=secret_store,
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

    bus = PulseBus()
    kernel = SpaceKernel(
        space_id="space-e2e",
        owner_id="alice",
        bus=bus,
        budget=100.0,
        attention_limit=3,
    )
    client = ApprovalClient(kernel.approval_mgr, auth)

    out = io.StringIO()
    err = io.StringIO()
    ctx = CLIContext(
        approval_client=client,
        bus=bus,
        kernel=kernel,
        out_stream=out,
        err_stream=err,
    )

    return {
        "client": client,
        "mgr": kernel.approval_mgr,
        "auth": auth,
        "bus": bus,
        "kernel": kernel,
        "attention": kernel.attention,
        "admission": kernel.admission,
        "ctx": ctx,
        "out": out,
        "err": err,
    }


# CLI-001: ryu status shows system health and space overview
def test_cli_001_status_command(e2e_env):
    ctx = e2e_env["ctx"]
    out = e2e_env["out"]

    code = main(["status"], ctx=ctx)
    assert code == EXIT_SUCCESS
    val = out.getvalue()
    assert "ONLINE" in val
    assert "space-e2e" in val


# CLI-002: ryu approval approve transitions pending to approved
def test_cli_002_approval_approve_command(e2e_env):
    client = e2e_env["client"]
    ctx = e2e_env["ctx"]
    out = e2e_env["out"]
    app_id = str(uuid.uuid4())
    req_hash = "1" * 64

    client.request_approval(
        request_id=app_id,
        space_id="space-e2e",
        capability="fs.read",
        plan_version=1,
        capability_request_hash=req_hash,
    )

    code = main(
        [
            "approval",
            "approve",
            app_id,
            "--approver",
            "alice",
            "--token",
            "alice_secret_token_123",
            "--space-id",
            "space-e2e",
            "--plan-version",
            "1",
            "--hash",
            req_hash,
        ],
        ctx=ctx,
    )
    assert code == EXIT_SUCCESS
    assert "approved" in out.getvalue().lower()

    req = client.get_request(app_id)
    assert req is not None
    assert req.status == "approved"
    assert req.queue_state == "resolved"
    assert req.decision_signature is not None


# CLI-003: ryu approval reject transitions pending to denied
def test_cli_003_approval_reject_command(e2e_env):
    client = e2e_env["client"]
    ctx = e2e_env["ctx"]
    out = e2e_env["out"]
    app_id = str(uuid.uuid4())
    req_hash = "2" * 64

    client.request_approval(
        request_id=app_id,
        space_id="space-e2e",
        capability="shell.exec",
        plan_version=1,
        capability_request_hash=req_hash,
    )

    out.seek(0)
    out.truncate(0)
    code = main(
        [
            "approval",
            "reject",
            app_id,
            "--approver",
            "alice",
            "--token",
            "alice_secret_token_123",
            "--reason",
            "Too risky",
            "--space-id",
            "space-e2e",
            "--plan-version",
            "1",
            "--hash",
            req_hash,
        ],
        ctx=ctx,
    )
    assert code == EXIT_SUCCESS
    assert "rejected" in out.getvalue().lower()

    req = client.get_request(app_id)
    assert req is not None
    assert req.status == "denied"
    assert req.queue_state == "resolved"


# CLI-004: ryu approval list filters by space and status
def test_cli_004_approval_list_command(e2e_env):
    client = e2e_env["client"]
    ctx = e2e_env["ctx"]
    out = e2e_env["out"]

    client.request_approval(
        request_id="app-cli-004",
        space_id="space-e2e",
        capability="net.dial",
    )

    out.seek(0)
    out.truncate(0)
    code = main(["approval", "list", "--space-id", "space-e2e"], ctx=ctx)
    assert code == EXIT_SUCCESS
    assert "app-cli-004" in out.getvalue()


# CLI-005: ryu approval inspect displays details and taint status
def test_cli_005_approval_inspect_command(e2e_env):
    client = e2e_env["client"]
    ctx = e2e_env["ctx"]
    out = e2e_env["out"]

    client.request_approval(
        request_id="app-inspect-1",
        space_id="space-e2e",
        capability="node.exec",
        taint=True,
        summary="Execute untrusted node code",
    )

    out.seek(0)
    out.truncate(0)
    code = main(["approval", "inspect", "app-inspect-1"], ctx=ctx)
    assert code == EXIT_SUCCESS
    val = out.getvalue()
    assert "app-inspect-1" in val
    assert "node.exec" in val
    assert "TAINTED" in val


# CLI-006: ryu space list and space inspect
def test_cli_006_space_commands(e2e_env):
    ctx = e2e_env["ctx"]
    out = e2e_env["out"]

    out.seek(0)
    out.truncate(0)
    code = main(["space", "list"], ctx=ctx)
    assert code == EXIT_SUCCESS
    assert "space-e2e" in out.getvalue()

    out.seek(0)
    out.truncate(0)
    code = main(["space", "inspect", "space-e2e"], ctx=ctx)
    assert code == EXIT_SUCCESS
    assert "space-e2e" in out.getvalue()


# CLI-007: ryu task list and task inspect
def test_cli_007_task_commands(e2e_env):
    ctx = e2e_env["ctx"]
    out = e2e_env["out"]

    code = main(["task", "list"], ctx=ctx)
    assert code == EXIT_SUCCESS

    code = main(["task", "inspect", "task-xyz"], ctx=ctx)
    assert code == EXIT_SUCCESS
    assert "task-xyz" in out.getvalue()


# CLI-008: ryu audit stream
def test_cli_008_audit_stream(e2e_env):
    ctx = e2e_env["ctx"]
    bus = e2e_env["bus"]

    # Publish an event
    bus.publish(
        Pulse(
            id="audit-pulse-1",
            space_id="space-e2e",
            type="space.created",
            severity=Severity.INFO,
            source="space_kernel",
            timestamp=datetime.now(timezone.utc),
            payload={"space_id": "space-e2e", "owner_id": "alice"},
            correlation_id="c-audit",
        )
    )

    code = main(["audit", "stream"], ctx=ctx)
    assert code == EXIT_SUCCESS


# HUMAN-001: Space Kernel capability request -> approval gate -> CLI approve -> admitted & consumed
def test_human_001_e2e_capability_approval_flow(e2e_env):
    kernel = e2e_env["kernel"]
    client = e2e_env["client"]
    mgr = e2e_env["mgr"]
    app_id = str(uuid.uuid4())
    req_hash = "3" * 64

    # 1. Gate request created
    req = mgr.request_approval(
        request_id=app_id,
        space_id="space-e2e",
        capability="node.shell",
        plan_version=kernel.get_plan_version(),
        capability_request_hash=req_hash,
    )
    assert req.status == "pending"

    # 2. Before approval: capability request denied
    cap_req = CapabilityRequest(
        requester_id="agent-worker-1",
        space_id="space-e2e",
        capability="node.shell",
    )
    resp_unapproved = kernel.request_capability(
        request=cap_req,
        approval=req,
    )
    assert resp_unapproved.status == "denied"

    # 3. CLI resolves approval
    now_ts = int(time.time())
    nonce = "e" * 32
    client.sign_and_submit_decision(
        approver_id="alice",
        token_secret="alice_secret_token_123",
        space_id="space-e2e",
        approval_id=app_id,
        decision="APPROVE",
        plan_version=kernel.get_plan_version(),
        capability_request_hash=req_hash,
        nonce=nonce,
        timestamp=now_ts,
    )
    assert req.status == "approved"

    # 4. Kernel admits capability request and consumes approval atomically
    resp_approved = kernel.request_capability(
        request=cap_req,
        approval=req,
    )
    assert resp_approved.status == "ok"
    assert req.consumed_at is not None
    assert req.status == "consumed"


# HUMAN-002: Default-deny timeout prevents execution
def test_human_002_default_deny_timeout(e2e_env):
    kernel = e2e_env["kernel"]
    mgr = e2e_env["mgr"]
    app_id = str(uuid.uuid4())

    req = mgr.request_approval(
        request_id=app_id,
        space_id="space-e2e",
        capability="node.write",
        timeout_class="default_deny",
        timeout_seconds=5.0,
    )

    # Time out
    mgr.check_timeout(app_id, current_time=req.created_at + 10.0, as_expired=True)
    assert req.status == "expired"

    cap_req = CapabilityRequest(
        requester_id="agent-1",
        space_id="space-e2e",
        capability="node.write",
    )
    resp = kernel.request_capability(cap_req, approval=req)
    assert resp.status == "denied"


# HUMAN-003: Default-hold timeout pauses execution without auto-approval
def test_human_003_default_hold_timeout(e2e_env):
    kernel = e2e_env["kernel"]
    mgr = e2e_env["mgr"]
    app_id = str(uuid.uuid4())

    req = mgr.request_approval(
        request_id=app_id,
        space_id="space-e2e",
        capability="budget.expand",
        timeout_class="default_hold",
        timeout_seconds=5.0,
    )

    # Timeout evaluates -> transitions to 'held'
    status = mgr.check_timeout(app_id, current_time=req.created_at + 10.0)
    assert status == "held"
    assert req.status == "held"

    # Execution remains paused: capability is DENIED (no auto-approval!)
    cap_req = CapabilityRequest(
        requester_id="agent-1",
        space_id="space-e2e",
        capability="budget.expand",
    )
    resp = kernel.request_capability(cap_req, approval=req)
    assert resp.status == "denied"


# HUMAN-004: Attention saturation pauses orchestrator dispatch
def test_human_004_attention_saturation_pauses_dispatch(e2e_env):
    attention = e2e_env["attention"]
    kernel = e2e_env["kernel"]
    space_id = "space-e2e"

    # Fill attention slots up to limit (3)
    attention.submit_approval(space_id, "gate-1")
    attention.submit_approval(space_id, "gate-2")
    attention.submit_approval(space_id, "gate-3")

    # 4th approval request is queued
    assert attention.submit_approval(space_id, "gate-4") is False
    assert attention.is_saturated(space_id) is True

    # Orchestrator checks saturation
    from core.resources.manager import ResourceManager
    rm = ResourceManager(bus=e2e_env["bus"])
    orch = SpaceOrchestrator(space_id=space_id, kernel=kernel, resource_mgr=rm, bus=e2e_env["bus"])
    assert orch.is_attention_saturated() is True
    assert orch.should_pause_dispatch_for_gates() is True


# HUMAN-005: Attention dequeuing activates next request
def test_human_005_attention_dequeuing_activates_next(e2e_env):
    attention = e2e_env["attention"]
    space_id = "space-e2e"

    attention.set_limit(space_id, 2)
    attention.submit_approval(space_id, "slot-1")
    attention.submit_approval(space_id, "slot-2")
    attention.submit_approval(space_id, "queued-1", priority_class=1)

    assert attention.get_active_count(space_id) == 2
    assert attention.get_queued_count(space_id) == 1

    # Complete slot-1 -> queued-1 activated
    next_req = attention.complete_approval(space_id, "slot-1")
    assert next_req == "queued-1"
    assert attention.get_active_count(space_id) == 2
    assert attention.get_queued_count(space_id) == 0


# REC-006: Audit immutability verification (PostgreSQL trigger)
@pytest.mark.skipif(
    os.environ.get("RYU_INTEGRATION_TESTS") != "1",
    reason="Integration tests disabled; set RYU_INTEGRATION_TESTS=1",
)
def test_rec_006_audit_immutability():
    import psycopg2
    cfg = PostgresConfig()
    conn = psycopg2.connect(
        host=cfg.host, port=cfg.port, dbname=cfg.db, user=cfg.user, password=cfg.password
    )
    with conn:
        with conn.cursor() as cur:
            p_id = f"pulse-rec-006-{int(time.time())}"
            cur.execute(
                """
                INSERT INTO pulses (id, space_id, type, severity, source, timestamp, payload, correlation_id)
                VALUES (%s, 'space-rec', 'runtime.started', 'info', 'test', NOW(), '{}', 'c-rec');
                """,
                (p_id,),
            )
            with pytest.raises(psycopg2.DatabaseError) as exc:
                cur.execute("DELETE FROM pulses WHERE id = %s;", (p_id,))
            assert "Audit Immutability Violation" in str(exc.value)
