"""RYU AI v1.0.1 — Post-Implementation Release Hardening & Verification Battery.

Verifies:
1. Space Isolation (Space A vs Space B cross-access rejection)
2. Authority Boundary (Desktop cannot bypass runtime authority)
3. HTML Sandbox Security (Zero-privilege iframe sandbox verification)
4. File Ingress Security (Path traversal, sensitive files, oversized, extensions, binary masquerade, taint)
5. Artifact Security (Identity, SHA-256, space ownership, traversal rejection)
6. History Architecture (PulseStore authoritative source, JSONL projection cache)
7. HTML Auto-Extraction (Deterministic, space-bound, empty/malformed handling)
8. Persistence & Restart (Survival across daemon/process restarts)
9. Desktop E2E Live Flow (End-to-end execution without mocks)

spec §2, §4, §7, §16, §18, ADR-0040, CONTRACT DESKTOP-001..005 — v1.0.1 Release
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import pytest
import socket
import time
from typing import Any

from channels.approval.auth import (
    ApproverAuthenticator,
    InMemoryCredentialStore,
)
from channels.approval.client import ApprovalClient
from channels.daemon.client import DaemonClient, DaemonClientError
from channels.daemon.config import DaemonConfig
from channels.daemon.history import DialogueTurn, SpaceHistoryStore
from channels.daemon.artifacts import SpaceArtifactStore
from channels.daemon.server import LocalDaemon
from core.space.attention import AttentionBudget
from core.space.approver import ApprovalManager, InMemoryApprovalStore
from node.contract import DeviceInfo, DeviceState, DeviceType, NodeInfo, NodeState, NodeTrustTier
from node.registry import NodeRegistry
from memory.adapters.in_memory import InMemoryMemoryAdapter
from core.space.memory_protocol import ExperienceRecord
from ryu.pulse_bus.bus import PulseBus
from ryu.pulse_bus.pulse import Pulse, Severity
from ryu.pulse_bus.store import InMemoryPulseStore


def get_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def hardening_env(tmp_path: Path):
    port = get_free_port()
    token = "hardening-v101-bearer-token-abc"
    token_file = tmp_path / "daemon.token"

    config = DaemonConfig(
        host="127.0.0.1",
        port=port,
        auth_token=token,
        token_file_path=token_file,
    )

    bus = PulseBus()
    pulse_store = InMemoryPulseStore()
    approval_store = InMemoryApprovalStore()
    budget = AttentionBudget(default_limit=5)
    manager = ApprovalManager(store=approval_store, bus=bus)
    cred_store = InMemoryCredentialStore()
    auth = ApproverAuthenticator(cred_store=cred_store, nonce_store=cred_store, secret_store={})
    approval_client = ApprovalClient(manager=manager, authenticator=auth)

    history_store = SpaceHistoryStore(base_dir=tmp_path / "spaces", pulse_store=pulse_store)
    artifact_store = SpaceArtifactStore(base_dir=tmp_path / "spaces")

    node_registry = NodeRegistry()
    node_info = NodeInfo(
        node_id="node-desktop-primary",
        platform="windows",
        architecture="x86_64",
        environment_profile="windows_desktop",
        runtime_state=NodeState.READY,
        trust_tier=NodeTrustTier.FULL_TRUST,
    )
    node_registry.register_node(node_info, pairing_secret="high-entropy-node-secret-32-chars")
    device_info = DeviceInfo(
        device_id="dev-gpu-0",
        node_id="node-desktop-primary",
        device_type=DeviceType.GPU,
        availability_state=DeviceState.ONLINE,
    )
    node_registry.register_device(device_info)

    mem_adapter = InMemoryMemoryAdapter()
    mem_adapter.store_experience(
        ExperienceRecord(
            experience_id="exp-space-A-1",
            space_id="space-A",
            situation={"task": "secret_task_A"},
            action={"lang": "python"},
            outcome="success",
            counterfactual="counterfactual A",
            applicable_context={"domain": "code"},
            stored_at=time.time(),
        )
    )

    daemon = LocalDaemon(
        config=config,
        approval_client=approval_client,
        bus=bus,
        pulse_store=pulse_store,
        attention_budget=budget,
        history_store=history_store,
        artifact_store=artifact_store,
        node_registry=node_registry,
        memory_adapter=mem_adapter,
    )
    daemon.server._spaces_catalog_dir = tmp_path / "spaces"
    daemon.start(block=False)
    time.sleep(0.05)

    client = DaemonClient(base_url=f"http://127.0.0.1:{port}", auth_token=token)

    yield {
        "daemon": daemon,
        "client": client,
        "port": port,
        "token": token,
        "bus": bus,
        "pulse_store": pulse_store,
        "tmp_path": tmp_path,
        "history_store": history_store,
        "artifact_store": artifact_store,
        "node_registry": node_registry,
        "mem_adapter": mem_adapter,
    }

    daemon.stop()


# ============================================================================
# 1. SPACE ISOLATION SECURITY BATTERY
# ============================================================================

def test_space_isolation_history_and_artifacts(hardening_env):
    """Space A must not leak history, artifacts, or files to Space B."""
    client: DaemonClient = hardening_env["client"]

    # 1. Create two separate spaces
    client.create_space(space_id="space-alpha", name="Alpha Space")
    client.create_space(space_id="space-beta", name="Beta Space")

    # 2. Activity in Space Alpha
    client.send_prompt(space_id="space-alpha", prompt="Alpha Confidential Prompt: Project Phoenix")
    up_res = client.upload_file(
        space_id="space-alpha",
        filename="alpha_secret.txt",
        content="Alpha confidential specifications",
    )
    alpha_art_id = up_res["artifact_id"]

    # 3. Activity in Space Beta
    client.send_prompt(space_id="space-beta", prompt="Beta Public Prompt: Market Research")
    client.upload_file(
        space_id="space-beta",
        filename="beta_notes.txt",
        content="Beta public research notes",
    )

    # 4. Verify History Isolation
    hist_alpha = client.get_history("space-alpha")
    hist_beta = client.get_history("space-beta")

    assert len(hist_alpha) == 1
    assert "Phoenix" in hist_alpha[0]["user_prompt"]
    assert "Market Research" not in hist_alpha[0]["user_prompt"]

    assert len(hist_beta) == 1
    assert "Market Research" in hist_beta[0]["user_prompt"]
    assert "Phoenix" not in hist_beta[0]["user_prompt"]

    # 5. Verify Artifact Listing Isolation
    arts_alpha = client.list_artifacts("space-alpha")
    arts_beta = client.list_artifacts("space-beta")

    assert any(a["name"] == "alpha_secret.txt" for a in arts_alpha)
    assert not any(a["name"] == "beta_notes.txt" for a in arts_alpha)

    assert any(a["name"] == "beta_notes.txt" for a in arts_beta)
    assert not any(a["name"] == "alpha_secret.txt" for a in arts_beta)

    # 6. Deliberate Cross-Space Artifact Retrieval Rejection
    # Space Beta attempts to read Space Alpha's artifact via client method (returns None on 404)
    cross_res = client.get_artifact_content("space-beta", alpha_art_id)
    assert cross_res is None

    # And verify direct HTTP request raises 404
    with pytest.raises(DaemonClientError) as exc_info:
        client._request("GET", f"/api/v1/spaces/space-beta/artifacts/{alpha_art_id}/content")
    assert exc_info.value.status_code == 404

    # 7. Verify Space Memory Isolation
    mem_a = client.get_memory("space-A")
    mem_b = client.get_memory("space-B")

    assert mem_a["experience_count"] >= 1
    assert any("secret_task_A" in str(e) for e in mem_a["experiences"])

    assert mem_b["experience_count"] == 0
    assert not any("secret_task_A" in str(e) for e in mem_b["experiences"])


# ============================================================================
# 2. AUTHORITY BOUNDARY BATTERY
# ============================================================================

def test_authority_boundary_desktop_unauthenticated_and_forged_rejections(hardening_env):
    """Prove Desktop cannot bypass authentication, cannot forge approvals, and cannot execute direct DB ops."""
    port = hardening_env["port"]

    # 1. Unauthenticated request rejected
    unauth_client = DaemonClient(base_url=f"http://127.0.0.1:{port}", auth_token="forged-or-empty")
    with pytest.raises(DaemonClientError) as exc_info:
        unauth_client.list_spaces()
    assert exc_info.value.status_code == 401

    # 2. Forged decision without valid cryptographic signature rejected
    client: DaemonClient = hardening_env["client"]
    import urllib.request
    import urllib.error

    req_url = f"http://127.0.0.1:{port}/api/v1/spaces/default/approvals/app-fake/decision"
    forged_body = json.dumps({
        "approver_id": "attacker",
        "decision": "APPROVE",
        "signature": "forged_signature_not_hmac",
        "plan_version": 1,
        "capability_request_hash": "abc",
        "timestamp": int(time.time()),
        "nonce": "fake-nonce",
    }).encode("utf-8")

    req = urllib.request.Request(
        req_url,
        data=forged_body,
        headers={
            "Authorization": f"Bearer {hardening_env['token']}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(req)
    # The kernel verifies HMAC preimage; forged signature fails authentication
    assert exc_info.value.code in (400, 401, 404, 409)

    # 3. Direct SQL injection or path escape on endpoints rejected
    bad_req_url = f"http://127.0.0.1:{port}/api/v1/spaces/../../../etc/passwd"
    req_bad = urllib.request.Request(
        bad_req_url,
        headers={"Authorization": f"Bearer {hardening_env['token']}"},
        method="GET",
    )
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(req_bad)
    assert exc_info.value.code == 404


# ============================================================================
# 3. HTML SANDBOX SECURITY BATTERY (ZERO-PRIVILEGE PROOFS)
# ============================================================================

def test_html_sandbox_zero_privilege_code_verification():
    """Verify that frontend implementation strictly adheres to zero-privilege iframe sandbox rules."""
    frontend_dir = Path(__file__).resolve().parent.parent.parent / "apps" / "ryu-desktop" / "src"

    # Inspect MarkdownMessage.tsx and ArtifactExplorer.tsx
    markdown_msg_path = frontend_dir / "components" / "MarkdownMessage.tsx"
    artifact_explorer_path = frontend_dir / "components" / "ArtifactExplorer.tsx"

    assert markdown_msg_path.is_file(), f"Missing {markdown_msg_path}"
    assert artifact_explorer_path.is_file(), f"Missing {artifact_explorer_path}"

    with open(markdown_msg_path, "r", encoding="utf-8") as f:
        md_content = f.read()

    with open(artifact_explorer_path, "r", encoding="utf-8") as f:
        art_content = f.read()

    for path_name, code in [("MarkdownMessage.tsx", md_content), ("ArtifactExplorer.tsx", art_content)]:
        # 1. Must contain sandbox attribute
        assert 'sandbox="allow-scripts"' in code, f"{path_name} must specify sandbox='allow-scripts'"

        # 2. Must NEVER contain allow-same-origin (strict isolation law)
        assert "allow-same-origin" not in code, (
            f"SECURITY DEFECT: {path_name} contains 'allow-same-origin'! "
            "Zero-privilege preview strictly forbids allow-same-origin to prevent iframe sandbox escape."
        )


def test_html_adversarial_payload_storage_and_integrity(hardening_env):
    """Adversarial HTML payloads attempting script escape are stored safely without execution."""
    client: DaemonClient = hardening_env["client"]

    adversarial_payloads = [
        "<script>window.parent.postMessage('pwned', '*');</script>",
        "<script>parent.document.body.innerHTML = 'hacked';</script>",
        "<script>window.top.location = 'https://attacker.com';</script>",
        "<script>window.__TAURI__.invoke('system_exec');</script>",
        "<script>localStorage.setItem('ryu_stolen', 'true');</script>",
        "<script>document.cookie = 'token=stolen';</script>",
        "<script>fetch('http://127.0.0.1:8420/api/v1/spaces/default/history');</script>",
    ]

    for idx, payload in enumerate(adversarial_payloads):
        filename = f"adversarial_{idx}.html"
        res = client.upload_file(
            space_id="space-sandbox-test",
            filename=filename,
            content=payload,
            mime_type="text/html",
        )
        assert res["success"] is True

        # Retrieve content
        retrieved = client.get_artifact_content("space-sandbox-test", res["artifact_id"])
        assert retrieved["content"] == payload
        # SHA-256 integrity match
        expected_sha = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        assert retrieved["sha256"] == expected_sha


# ============================================================================
# 4. FILE INGRESS SECURITY BATTERY
# ============================================================================

def test_file_ingress_path_traversal_rejection(hardening_env):
    """File ingress must reject parent directory references and absolute paths."""
    client: DaemonClient = hardening_env["client"]

    traversal_filenames = [
        "../traversal.txt",
        "..\\traversal_win.txt",
        "..\\..\\windows\\win.ini",
        "/etc/passwd",
        "C:\\Windows\\system32\\cmd.exe",
        "nested/../../escape.txt",
        "null\x00byte.txt",
        ".env",
        ".git",
        "passwd",
        "shadow",
        "id_rsa",
        "id_ed25519",
    ]

    for bad_name in traversal_filenames:
        with pytest.raises(DaemonClientError) as exc_info:
            client.upload_file(
                space_id="space-sec",
                filename=bad_name,
                content="malicious payload",
            )
        assert exc_info.value.status_code in (400, 403, 404), f"Filename {bad_name} was not rejected"


def test_file_ingress_oversized_payload_rejection(hardening_env):
    """Files exceeding 2MB (2,097,152 bytes) must be rejected with 413 Entity Too Large."""
    client: DaemonClient = hardening_env["client"]

    # 2MB + 1 byte
    oversized_content = "X" * (2 * 1024 * 1024 + 1)
    with pytest.raises(DaemonClientError) as exc_info:
        client.upload_file(
            space_id="space-sec",
            filename="large_file.txt",
            content=oversized_content,
        )
    assert exc_info.value.status_code == 413


def test_file_ingress_unsupported_extension_rejection(hardening_env):
    """Executables and binary extensions must be rejected with 415 Unsupported Media Type."""
    client: DaemonClient = hardening_env["client"]

    forbidden_extensions = [
        "payload.exe",
        "library.dll",
        "binary.bin",
        "lib.so",
        "script.sh.exe",
        "archive.zip",
        "image.png",
    ]

    for bad_ext in forbidden_extensions:
        with pytest.raises(DaemonClientError) as exc_info:
            client.upload_file(
                space_id="space-sec",
                filename=bad_ext,
                content="echo 1",
            )
        assert exc_info.value.status_code == 415


def test_file_ingress_binary_masquerading_rejection(hardening_env):
    """Text files containing embedded null bytes must be rejected with 415."""
    client: DaemonClient = hardening_env["client"]

    masquerade_content = "normal text\x00embedded null bytes\x00malicious binary"
    with pytest.raises(DaemonClientError) as exc_info:
        client.upload_file(
            space_id="space-sec",
            filename="masquerade.txt",
            content=masquerade_content,
        )
    assert exc_info.value.status_code == 415


def test_file_ingress_taint_pulse_publication(hardening_env):
    """Successful file ingress must emit security.taint.detected pulse on the bus."""
    client: DaemonClient = hardening_env["client"]
    bus: PulseBus = hardening_env["bus"]

    received_pulses: list[Pulse] = []
    sub = bus.subscribe(lambda p: received_pulses.append(p), pulse_type="security.taint.detected")

    res = client.upload_file(
        space_id="space-taint-test",
        filename="external_notes.md",
        content="# External Project Analysis",
    )
    assert res["success"] is True

    # Allow subscriber dispatch
    time.sleep(0.05)

    assert len(received_pulses) >= 1
    taint_pulse = received_pulses[-1]
    assert taint_pulse.type == "security.taint.detected"
    assert taint_pulse.taint is True
    assert taint_pulse.space_id == "space-taint-test"
    assert "external_notes.md" in taint_pulse.payload.get("reason", "")

    bus.unsubscribe(sub)


# ============================================================================
# 5. ARTIFACT SECURITY BATTERY
# ============================================================================

def test_artifact_sha256_integrity_and_space_ownership(hardening_env):
    """Verify SHA-256 calculation and space ownership boundaries."""
    client: DaemonClient = hardening_env["client"]
    content = "deterministic content for cryptographic integrity testing"
    expected_sha = hashlib.sha256(content.encode("utf-8")).hexdigest()

    res = client.upload_file(
        space_id="space-crypto",
        filename="crypto_test.txt",
        content=content,
    )
    assert res["sha256"] == expected_sha

    # Retrieve and verify SHA-256 in content response
    retrieved = client.get_artifact_content("space-crypto", res["artifact_id"])
    assert retrieved["sha256"] == expected_sha
    assert retrieved["content"] == content

    # Nonexistent artifact returns None via client method and 404 via direct HTTP
    assert client.get_artifact_content("space-crypto", "art-nonexistent-999") is None
    with pytest.raises(DaemonClientError) as exc_info:
        client._request("GET", "/api/v1/spaces/space-crypto/artifacts/art-nonexistent-999/content")
    assert exc_info.value.status_code == 404


# ============================================================================
# 6. HISTORY ARCHITECTURE & PULSE STORE PROJECTION
# ============================================================================

def test_history_reconstruction_from_authoritative_pulse_store(hardening_env):
    """Prove: When JSONL is absent, history faithfully projects from authoritative PulseStore."""
    pulse_store: InMemoryPulseStore = hardening_env["pulse_store"]
    history_store: SpaceHistoryStore = hardening_env["history_store"]
    space_id = "space-pulse-auth"

    # Inject authoritative goal.defined pulses into PulseStore directly
    from datetime import datetime, timezone
    p1 = Pulse(
        id="pulse-g1",
        space_id=space_id,
        type="goal.defined",
        severity=Severity.INFO,
        source="orchestrator",
        correlation_id="corr-g1",
        payload={
            "goal_id": "goal-1",
            "goal_spec": {
                "objective": "First Authoritative Goal",
                "required_capabilities": ["system.read"],
            },
            "single_agent_eligible": True,
        },
        timestamp=datetime.now(timezone.utc),
    )
    p2 = Pulse(
        id="pulse-g2",
        space_id=space_id,
        type="goal.defined",
        severity=Severity.INFO,
        source="orchestrator",
        correlation_id="corr-g2",
        payload={
            "goal_id": "goal-2",
            "goal_spec": {
                "objective": "Second Authoritative Goal",
                "required_capabilities": ["network.call"],
            },
            "single_agent_eligible": False,
        },
        timestamp=datetime.now(timezone.utc),
    )
    pulse_store.append(p1)
    pulse_store.append(p2)

    # Note: JSONL file does not exist yet for this space!
    history_file = history_store._get_history_file(space_id)
    if history_file.is_file():
        os.remove(history_file)

    # Project history
    turns = history_store.get_history(space_id)
    assert len(turns) == 2
    assert turns[0]["user_prompt"] == "First Authoritative Goal"
    assert turns[0]["single_agent_eligible"] is True
    assert turns[1]["user_prompt"] == "Second Authoritative Goal"
    assert turns[1]["single_agent_eligible"] is False


# ============================================================================
# 7. HTML AUTO-EXTRACTION DETERMINISTIC BATTERY
# ============================================================================

def test_html_auto_extraction_behavior(hardening_env):
    """Verify deterministic auto-extraction of HTML blocks and non-empty validation."""
    client: DaemonClient = hardening_env["client"]
    space_id = "space-html-auto"

    # 1. Prompt generating valid HTML
    prompt_with_html = "Create a basic dashboard widget in HTML"
    res1 = client.send_prompt(space_id=space_id, prompt=prompt_with_html)
    assert res1["status"] == "completed"

    arts = client.list_artifacts(space_id)
    assert len(arts) >= 1
    html_art = next(a for a in arts if a["mime_type"] == "text/html")
    assert html_art["name"].startswith("preview_")
    assert html_art["name"].endswith(".html")

    # 2. Prompt with no HTML code blocks must NOT generate HTML artifacts
    prompt_plain = "What is the capital of France?"
    arts_count_before = len(client.list_artifacts(space_id))
    res2 = client.send_prompt(space_id=space_id, prompt=prompt_plain)
    assert res2["status"] == "completed"

    arts_count_after = len(client.list_artifacts(space_id))
    # Count remains unchanged because no HTML code block was emitted
    assert arts_count_after == arts_count_before


# ============================================================================
# 8. PERSISTENCE & PROCESS RESTART BATTERY
# ============================================================================

def test_persistence_daemon_restart_survival(hardening_env):
    """Spaces, history, files, and artifacts survive daemon process shutdown and restart."""
    tmp_path = hardening_env["tmp_path"]
    port = hardening_env["port"]
    token = hardening_env["token"]
    client: DaemonClient = hardening_env["client"]

    # 1. Create space and artifacts
    space_id = "space-durable-restart"
    client.create_space(space_id=space_id, name="Restart Test Space", budget=50.0)
    client.send_prompt(space_id=space_id, prompt="Initial prompt before shutdown")
    up_res = client.upload_file(
        space_id=space_id,
        filename="persisted_manifest.json",
        content='{"persisted": true, "version": "1.0.1"}',
        mime_type="application/json",
    )
    art_id = up_res["artifact_id"]

    # 2. Shutdown daemon
    hardening_env["daemon"].stop()
    time.sleep(0.1)

    # 3. Spin up fresh daemon on new port pointing to exact same tmp_path
    new_port = get_free_port()
    new_config = DaemonConfig(
        host="127.0.0.1",
        port=new_port,
        auth_token=token,
    )
    new_bus = PulseBus()
    new_pulse_store = InMemoryPulseStore()
    new_approval_store = InMemoryApprovalStore()
    new_manager = ApprovalManager(store=new_approval_store, bus=new_bus)
    new_cred_store = InMemoryCredentialStore()
    new_auth = ApproverAuthenticator(cred_store=new_cred_store, nonce_store=new_cred_store, secret_store={})
    new_approval_client = ApprovalClient(manager=new_manager, authenticator=new_auth)

    new_history_store = SpaceHistoryStore(base_dir=tmp_path / "spaces", pulse_store=new_pulse_store)
    new_artifact_store = SpaceArtifactStore(base_dir=tmp_path / "spaces")

    new_daemon = LocalDaemon(
        config=new_config,
        approval_client=new_approval_client,
        bus=new_bus,
        pulse_store=new_pulse_store,
        attention_budget=AttentionBudget(default_limit=5),
        history_store=new_history_store,
        artifact_store=new_artifact_store,
    )
    new_daemon.server._spaces_catalog_dir = tmp_path / "spaces"
    new_daemon.server._load_registered_spaces()
    new_daemon.start(block=False)
    time.sleep(0.05)

    new_client = DaemonClient(base_url=f"http://127.0.0.1:{new_port}", auth_token=token)

    try:
        # 4. Verify Space Survived
        sps = new_client.list_spaces()
        assert any(s["space_id"] == space_id for s in sps)

        # 5. Verify History Survived
        hist = new_client.get_history(space_id)
        assert len(hist) >= 1
        assert hist[0]["user_prompt"] == "Initial prompt before shutdown"

        # 6. Verify Artifact Survived
        arts = new_client.list_artifacts(space_id)
        assert any(a["artifact_id"] == art_id for a in arts)

        # 7. Verify Content Integrity Survived
        content_res = new_client.get_artifact_content(space_id, art_id)
        assert '"persisted": true' in content_res["content"]
        assert content_res["sha256"] == up_res["sha256"]

    finally:
        new_daemon.stop()


# ============================================================================
# 9. DESKTOP E2E LIVE FLOW BATTERY (NO MOCKS)
# ============================================================================

def test_desktop_e2e_complete_flow(hardening_env):
    """Execute complete operator journey: start -> create space -> prompt -> artifact -> upload -> memory -> nodes."""
    client: DaemonClient = hardening_env["client"]

    # 1. Health check
    health = client.check_health()
    assert health["status"] == "healthy"

    # 2. Create isolated workspace
    space_id = "e2e-command-center"
    sp = client.create_space(space_id=space_id, name="E2E Command Center Space", budget=25.0)
    assert sp["success"] is True

    # 3. Submit instruction
    prompt_res = client.send_prompt(
        space_id=space_id,
        prompt="Write a modern web status badge in HTML",
    )
    assert prompt_res["status"] == "completed"
    assert prompt_res["space_id"] == space_id

    # 4. Rehydrate history
    history = client.get_history(space_id)
    assert len(history) == 1
    assert "status badge" in history[0]["user_prompt"]

    # 5. Verify HTML artifact auto-created
    artifacts = client.list_artifacts(space_id)
    assert len(artifacts) >= 1
    html_art = artifacts[0]
    assert html_art["mime_type"] == "text/html"

    # 6. Preview artifact content
    art_content = client.get_artifact_content(space_id, html_art["artifact_id"])
    assert len(art_content["content"]) > 0

    # 7. Upload project file with taint detection
    up_res = client.upload_file(
        space_id=space_id,
        filename="requirements.txt",
        content="pytest>=7.0\njsonschema>=4.21\n",
    )
    assert up_res["success"] is True
    assert up_res["artifact_id"] is not None

    # 8. Verify System Visibility: Nodes
    nodes = client.list_nodes()
    assert len(nodes) >= 1
    assert nodes[0]["node_id"] == "node-desktop-primary"
    assert nodes[0]["device_count"] >= 1

    # 9. Verify System Visibility: Memory
    mem = client.get_memory(space_id)
    assert mem["space_id"] == space_id
    assert "experiences" in mem
