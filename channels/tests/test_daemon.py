"""Unit tests for Local Channel Daemon (adapter, loopback, bearer auth, and secret isolation).

spec §2, §4, ADR-0026, CONTRACT APP-002, APP-003, APP-006 — Phase 8.5
"""

from __future__ import annotations

import hashlib
import socket
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
import pytest

from channels.approval.auth import (
    ApproverAuthenticator,
    ApproverCredentialRecord,
    InMemoryCredentialStore,
)
from channels.approval.client import ApprovalClient
from channels.daemon.client import DaemonClient, DaemonClientError
from channels.daemon.config import DaemonConfig
from channels.daemon.server import LocalDaemon
from ryu.pulse_bus.bus import PulseBus
from core.space.attention import AttentionBudget
from core.space.approver import (
    ApprovalManager,
    ApprovalRequest,
    InMemoryApprovalStore,
)


def get_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def setup_daemon(tmp_path: Path):
    port = get_free_port()
    token = "test-daemon-bearer-token-12345"
    token_file = tmp_path / "daemon.token"

    config = DaemonConfig(
        host="127.0.0.1",
        port=port,
        auth_token=token,
        token_file_path=token_file,
    )

    # Wire approval subsystem
    store = InMemoryApprovalStore()
    budget = AttentionBudget(default_limit=5)
    manager = ApprovalManager(store=store)

    cred_store = InMemoryCredentialStore()
    now = datetime.now(timezone.utc)
    cred_alice = ApproverCredentialRecord(
        approver_id="alice",
        token_id="tok-alice-1",
        secret_ref="secret://approver/alice-key",
        created_at=now,
        expires_at=now + timedelta(days=30),
    )
    cred_store.register_credential(cred_alice)

    secret_store = {
        "secret://approver/alice-key": "alice-secret-key-999",
    }

    authenticator = ApproverAuthenticator(
        cred_store=cred_store,
        nonce_store=cred_store,
        secret_store=secret_store,
    )
    approval_client = ApprovalClient(manager=manager, authenticator=authenticator)

    bus = PulseBus()

    daemon = LocalDaemon(
        config=config,
        approval_client=approval_client,
        bus=bus,
        pulse_store=None,
        attention_budget=budget,
    )
    daemon.start(block=False)
    time.sleep(0.05)  # Allow thread to bind

    client = DaemonClient(base_url=f"http://127.0.0.1:{port}", auth_token=token)

    yield {
        "daemon": daemon,
        "client": client,
        "manager": manager,
        "store": store,
        "budget": budget,
        "auth_token": token,
        "port": port,
        "bus": bus,
        "cred_store": cred_store,
        "secret_store": secret_store,
    }

    daemon.stop()


def test_daemon_loopback_enforcement():
    """Binding to non-loopback host must fail with ValueError (ADR-0026)."""
    with pytest.raises(ValueError, match="Security violation: Daemon must bind strictly to local loopback"):
        DaemonConfig(host="0.0.0.0")

    with pytest.raises(ValueError, match="Security violation: Daemon must bind strictly to local loopback"):
        DaemonConfig(host="192.168.1.50")


def test_daemon_health_unauthenticated(setup_daemon):
    """Health endpoint must be reachable without auth."""
    unauth_client = DaemonClient(base_url=f"http://127.0.0.1:{setup_daemon['port']}", auth_token=None)
    health = unauth_client.health()
    assert health["status"] == "healthy"
    assert health["phase"] == "8.5"


def test_daemon_bearer_auth_rejection(setup_daemon):
    """Endpoints requiring auth must return 401 when token is missing or wrong."""
    unauth_client = DaemonClient(base_url=f"http://127.0.0.1:{setup_daemon['port']}", auth_token="wrong-token")
    with pytest.raises(DaemonClientError) as exc_info:
        unauth_client.list_spaces()
    assert exc_info.value.status_code == 401


def test_daemon_dynamic_attention_budget(setup_daemon):
    """Attention endpoint must reflect dynamic N from runtime AttentionBudget (APP-003)."""
    client: DaemonClient = setup_daemon["client"]
    approval_client: ApprovalClient = setup_daemon["daemon"].server.approval_client

    # Budget concurrency limit is 5
    att = client.get_attention("space-1")
    assert att["concurrency_limit"] == 5
    assert att["active_count"] == 0
    assert att["queued_count"] == 0
    assert att["is_saturated"] is False

    # Seed an active approval via request_approval
    req = approval_client.request_approval(
        request_id="req-1",
        space_id="space-1",
        capability="shell.exec",
        risk_tier="high",
        summary="Run shell command",
    )
    # Activate in attention budget
    setup_daemon["budget"].submit_approval("space-1", req.request_id)
    req.queue_state = "active"
    setup_daemon["store"].save(req)

    att2 = client.get_attention("space-1")
    assert att2["active_count"] == 1
    assert len(att2["active_approvals"]) == 1
    assert att2["active_approvals"][0]["approval_id"] == "req-1"


def test_daemon_approval_submission_flow(setup_daemon):
    """Client-side signed decision can be submitted via daemon and resolved in store."""
    client: DaemonClient = setup_daemon["client"]
    store: InMemoryApprovalStore = setup_daemon["store"]
    manager: ApprovalManager = setup_daemon["manager"]

    manager.set_space_approver("space-alpha", "alice")
    valid_hash = hashlib.sha256(b"req-hash-abc").hexdigest()
    app_id = str(uuid.uuid4())

    req = manager.request_approval(
        request_id=app_id,
        space_id="space-alpha",
        capability="system.reboot",
        risk_tier="high",
        summary="System reboot",
        plan_version=2,
        capability_request_hash=valid_hash,
    )
    req.queue_state = "active"
    store.save(req)

    # Use sign_and_submit_decision on client
    res = client.sign_and_submit_decision(
        approver_id="alice",
        token_secret="alice-secret-key-999",
        space_id="space-alpha",
        approval_id=app_id,
        decision="APPROVE",
        plan_version=2,
        capability_request_hash=valid_hash,
    )
    assert res["success"] is True
    assert res["approval_id"] == app_id

    # Verify state in store transitioned to approved
    updated = store.get(app_id)
    assert updated is not None
    assert updated.status == "approved"


def test_daemon_never_persists_raw_human_token(setup_daemon):
    """Contract APP-006: Local Channel Daemon must NEVER persist, log, cache, or store raw human approver secrets."""
    client: DaemonClient = setup_daemon["client"]
    store: InMemoryApprovalStore = setup_daemon["store"]
    manager: ApprovalManager = setup_daemon["manager"]
    daemon: LocalDaemon = setup_daemon["daemon"]
    cred_store: InMemoryCredentialStore = setup_daemon["cred_store"]
    secret_store: dict = setup_daemon["secret_store"]

    secret_raw = "super-secret-human-key-xyz-789"
    now = datetime.now(timezone.utc)
    cred_bob = ApproverCredentialRecord(
        approver_id="bob",
        token_id="tok-bob-1",
        secret_ref="secret://approver/bob-key",
        created_at=now,
        expires_at=now + timedelta(days=30),
    )
    cred_store.register_credential(cred_bob)
    secret_store["secret://approver/bob-key"] = secret_raw

    manager.set_space_approver("space-sec", "bob")
    valid_hash_sec = hashlib.sha256(b"req-sec").hexdigest()
    app_sec_id = str(uuid.uuid4())

    req = manager.request_approval(
        request_id=app_sec_id,
        space_id="space-sec",
        capability="node.format",
        risk_tier="critical",
        plan_version=1,
        capability_request_hash=valid_hash_sec,
    )
    req.queue_state = "active"
    store.save(req)

    # Client performs signing locally and submits only the signature
    res = client.sign_and_submit_decision(
        approver_id="bob",
        token_secret=secret_raw,
        space_id="space-sec",
        approval_id=app_sec_id,
        decision="APPROVE",
        plan_version=1,
        capability_request_hash=valid_hash_sec,
    )
    assert res["success"] is True

    # Audit the daemon's internal state:
    # 1. Check all daemon attributes
    daemon_dict = repr(daemon.server.__dict__)
    assert secret_raw not in daemon_dict, "Violation: Raw human secret found in daemon server attributes!"

    # 2. Check config
    assert secret_raw not in repr(daemon.config.__dict__), "Violation: Raw human secret found in daemon config!"

    # 3. Check token file on disk
    if daemon.config.token_file_path and daemon.config.token_file_path.is_file():
        file_content = daemon.config.token_file_path.read_text(encoding="utf-8")
        assert secret_raw not in file_content, "Violation: Raw human secret leaked to token file!"


def test_daemon_list_spaces_and_approvals(setup_daemon):
    """Test listing spaces and approvals via daemon API."""
    client: DaemonClient = setup_daemon["client"]
    spaces = client.list_spaces()
    assert len(spaces) >= 1

    approvals = client.list_approvals("space-sec")
    assert isinstance(approvals, list)


def test_daemon_sse_pulse_stream(setup_daemon):
    """Test streaming events via SSE endpoint."""
    port = setup_daemon["port"]
    token = setup_daemon["auth_token"]
    bus = setup_daemon["bus"]

    import urllib.request

    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/v1/spaces/space-stream/events",
        headers={"Authorization": f"Bearer {token}"},
    )

    received_lines: list[str] = []

    def read_stream():
        try:
            with urllib.request.urlopen(req, timeout=2.0) as resp:
                for _ in range(5):
                    line = resp.readline().decode("utf-8")
                    if line:
                        received_lines.append(line.strip())
                    if len(received_lines) >= 2:
                        break
        except Exception:
            pass

    t = threading.Thread(target=read_stream, daemon=True)
    t.start()
    time.sleep(0.1)

    # Initial event should be received
    t.join(timeout=1.0)
    assert any("connected" in l for l in received_lines)


def test_daemon_prompt_endpoint(setup_daemon):
    """Test POST /api/v1/spaces/{space_id}/prompt with SCCA §18 single_agent_eligible evaluation."""
    client: DaemonClient = setup_daemon["client"]

    # Submit natural language prompt
    res = client.send_prompt(space_id="space-prompt-test", prompt="write basic python program")

    assert res["space_id"] == "space-prompt-test"
    assert res["objective"] == "write basic python program"
    assert res["single_agent_eligible"] is True
    assert res["execution_mode"] == "direct_single_agent"
    assert "def main()" in res["response"]
    assert res["status"] == "completed"


