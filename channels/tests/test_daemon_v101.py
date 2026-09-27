"""Comprehensive test suite for RYU AI v1.0.1 Daemon Capability Exposure.

Verifies WP-1 through WP-6:
- WP-1: Conversation History Rehydration
- WP-2: Space Lifecycle & Switching
- WP-4: Space Artifact Explorer & Retrieval
- WP-5: Sandboxed File Ingress & Taint
- WP-6: System Visibility (Nodes & Memory)

spec §2, §4, §7, §11, ADR-0040, CONTRACT DESKTOP-001..005 — v1.0.1
"""

from __future__ import annotations

import json
from pathlib import Path
import pytest
import socket
import time

from channels.approval.auth import (
    ApproverAuthenticator,
    ApproverCredentialRecord,
    InMemoryCredentialStore,
)
from channels.approval.client import ApprovalClient
from channels.daemon.client import DaemonClient, DaemonClientError
from channels.daemon.config import DaemonConfig
from channels.daemon.history import SpaceHistoryStore
from channels.daemon.artifacts import SpaceArtifactStore
from channels.daemon.server import LocalDaemon
from core.space.attention import AttentionBudget
from core.space.approver import ApprovalManager, InMemoryApprovalStore
from node.contract import DeviceInfo, DeviceState, DeviceType, NodeInfo, NodeState, NodeTrustTier
from node.registry import NodeRegistry
from memory.adapters.in_memory import InMemoryMemoryAdapter
from core.space.memory_protocol import ExperienceRecord
from ryu.pulse_bus.bus import PulseBus


def get_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def setup_daemon_v101(tmp_path: Path):
    port = get_free_port()
    token = "test-v101-bearer-token-xyz"
    token_file = tmp_path / "daemon.token"

    config = DaemonConfig(
        host="127.0.0.1",
        port=port,
        auth_token=token,
        token_file_path=token_file,
    )

    # Setup core subsystems
    bus = PulseBus()
    approval_store = InMemoryApprovalStore()
    budget = AttentionBudget(default_limit=5)
    manager = ApprovalManager(store=approval_store, bus=bus)
    cred_store = InMemoryCredentialStore()
    auth = ApproverAuthenticator(cred_store=cred_store, nonce_store=cred_store, secret_store={})
    approval_client = ApprovalClient(manager=manager, authenticator=auth)

    # Isolated storage roots
    history_store = SpaceHistoryStore(base_dir=tmp_path / "spaces")
    artifact_store = SpaceArtifactStore(base_dir=tmp_path / "spaces")

    # Wire NodeRegistry
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

    # Wire MemoryAdapter
    from datetime import datetime, timezone
    mem_adapter = InMemoryMemoryAdapter()
    mem_adapter.store_experience(
        ExperienceRecord(
            experience_id="exp-101",
            space_id="space-test",
            situation={"task": "code_gen"},
            action={"lang": "python"},
            outcome="success",
            counterfactual="use async execution",
            applicable_context={"domain": "code"},
            stored_at=datetime.now(timezone.utc),
        )
    )

    daemon = LocalDaemon(
        config=config,
        approval_client=approval_client,
        bus=bus,
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
        "tmp_path": tmp_path,
        "node_registry": node_registry,
        "mem_adapter": mem_adapter,
    }

    daemon.stop()


# ============================================================================
# WP-1: CONVERSATION HISTORY REHYDRATION TESTS (DESKTOP-002)
# ============================================================================

def test_history_empty_space(setup_daemon_v101):
    """Empty space must return empty turns list without failure."""
    client: DaemonClient = setup_daemon_v101["client"]
    turns = client.get_history("space-empty")
    assert isinstance(turns, list)
    assert len(turns) == 0


def test_history_rehydration_after_prompts(setup_daemon_v101):
    """Prompts submitted to space are recorded durably and rehydrated chronologically."""
    client: DaemonClient = setup_daemon_v101["client"]
    space_id = "space-hist-1"

    # Send 2 consecutive prompts
    res1 = client.send_prompt(space_id=space_id, prompt="Hello RYU, what is your purpose?")
    assert res1["status"] == "completed"

    res2 = client.send_prompt(space_id=space_id, prompt="Write a quick Python hello world script")
    assert res2["status"] == "completed"

    # Retrieve history
    history = client.get_history(space_id=space_id)
    assert len(history) == 2
    assert history[0]["user_prompt"] == "Hello RYU, what is your purpose?"
    assert history[1]["user_prompt"] == "Write a quick Python hello world script"
    assert history[0]["timestamp"] <= history[1]["timestamp"]
    assert history[0]["status"] == "completed"
    assert history[1]["status"] == "completed"


def test_history_space_isolation(setup_daemon_v101):
    """Dialogue turns in space-A must never leak into space-B."""
    client: DaemonClient = setup_daemon_v101["client"]
    client.send_prompt(space_id="space-A", prompt="Secret prompt for space A only")
    client.send_prompt(space_id="space-B", prompt="Public prompt for space B")

    hist_a = client.get_history("space-A")
    hist_b = client.get_history("space-B")

    assert len(hist_a) == 1
    assert hist_a[0]["user_prompt"] == "Secret prompt for space A only"

    assert len(hist_b) == 1
    assert hist_b[0]["user_prompt"] == "Public prompt for space B"


def test_history_unauthorized_rejection(setup_daemon_v101):
    """History endpoint must reject requests without valid Bearer token."""
    unauth_client = DaemonClient(base_url=f"http://127.0.0.1:{setup_daemon_v101['port']}", auth_token="wrong-token")
    with pytest.raises(DaemonClientError) as exc_info:
        unauth_client.get_history("space-test")
    assert exc_info.value.status_code == 401


# ============================================================================
# WP-2: SPACE LIFECYCLE & SWITCHING TESTS (DESKTOP-001)
# ============================================================================

def test_space_lifecycle_create_and_list(setup_daemon_v101):
    """Users can create spaces via daemon which appear in space listings."""
    client: DaemonClient = setup_daemon_v101["client"]

    # Initial list contains default
    sps = client.list_spaces()
    assert any(s["space_id"] == "default" for s in sps)

    # Create new space
    res = client.create_space(space_id="project-alpha", name="Project Alpha", budget=100.0)
    assert res["success"] is True
    assert res["space"]["space_id"] == "project-alpha"
    assert res["space"]["name"] == "Project Alpha"

    # List spaces again
    sps_after = client.list_spaces()
    assert any(s["space_id"] == "project-alpha" for s in sps_after)

    # Inspect space
    sp = client.get_space("project-alpha")
    assert sp is not None
    assert sp["name"] == "Project Alpha"


def test_space_duplicate_creation_conflict(setup_daemon_v101):
    """Attempting to create duplicate space_id must fail with 409 Conflict."""
    client: DaemonClient = setup_daemon_v101["client"]
    client.create_space(space_id="unique-space-1", name="Unique Space")

    with pytest.raises(DaemonClientError) as exc_info:
        client.create_space(space_id="unique-space-1", name="Duplicate")
    assert exc_info.value.status_code == 409


def test_space_invalid_id_rejection(setup_daemon_v101):
    """Malformed space IDs must be rejected with 400 Bad Request."""
    client: DaemonClient = setup_daemon_v101["client"]
    with pytest.raises(DaemonClientError) as exc_info:
        client.create_space(space_id="bad/space/name", name="Bad Space")
    assert exc_info.value.status_code == 400


# ============================================================================
# WP-4: ARTIFACT EXPLORER & RETRIEVAL TESTS (DESKTOP-004)
# ============================================================================

def test_artifact_registration_and_retrieval(setup_daemon_v101):
    """Registered artifacts can be listed and retrieved safely."""
    client: DaemonClient = setup_daemon_v101["client"]
    server = setup_daemon_v101["daemon"].server
    space_id = "space-artifacts"

    # Register an artifact via server's artifact_store
    server.artifact_store.register_artifact(
        space_id=space_id,
        name="report.txt",
        content="Final audit report contents.",
        mime_type="text/plain",
    )

    artifacts = client.list_artifacts(space_id)
    assert len(artifacts) == 1
    art = artifacts[0]
    assert art["name"] == "report.txt"
    assert art["mime_type"] == "text/plain"
    assert art["size_bytes"] == len("Final audit report contents.")
    assert len(art["sha256"]) == 64  # Valid SHA-256 digest

    # Retrieve content
    content_res = client.get_artifact_content(space_id, art["artifact_id"])
    assert content_res is not None
    assert content_res["content"] == "Final audit report contents."
    assert content_res["mime_type"] == "text/plain"


def test_artifact_auto_registration_from_html_prompt(setup_daemon_v101):
    """Prompts producing HTML code blocks automatically register preview artifacts."""
    client: DaemonClient = setup_daemon_v101["client"]
    space_id = "space-html-gen"

    prompt = "Generate a simple HTML landing page with a header and button"
    res = client.send_prompt(space_id=space_id, prompt=prompt)
    assert res["status"] == "completed"

    artifacts = client.list_artifacts(space_id)
    assert len(artifacts) >= 1
    html_art = artifacts[0]
    assert html_art["mime_type"] == "text/html"
    assert html_art["name"].endswith(".html")


# ============================================================================
# WP-5: SANDBOXED FILE INGRESS & TAINT TESTS (DESKTOP-005)
# ============================================================================

def test_sandboxed_file_ingress_success(setup_daemon_v101):
    """Valid text file ingress creates sandboxed file, computes hash, and emits taint."""
    client: DaemonClient = setup_daemon_v101["client"]
    bus = setup_daemon_v101["bus"]
    space_id = "space-ingress"

    taint_pulses = []
    bus.subscribe(lambda p: taint_pulses.append(p) if getattr(p, "type", "") == "security.taint.detected" else None)

    content = "def calculate_sum(a, b):\n    return a + b\n"
    res = client.upload_file(
        space_id=space_id,
        filename="calculator.py",
        content=content,
        mime_type="text/x-python",
    )

    assert res["success"] is True
    assert res["filename"] == "calculator.py"
    assert res["bytes_written"] == len(content.encode("utf-8"))
    assert len(res["sha256"]) == 64
    assert res["artifact_id"]

    # Verify taint pulse was emitted
    assert len(taint_pulses) == 1
    assert taint_pulses[0].taint is True
    assert taint_pulses[0].space_id == space_id


def test_file_ingress_path_traversal_rejection(setup_daemon_v101):
    """Filenames attempting directory traversal must be rejected with 400."""
    client: DaemonClient = setup_daemon_v101["client"]
    with pytest.raises(DaemonClientError) as exc_info:
        client.upload_file(
            space_id="space-sec",
            filename="../../etc/passwd.txt",
            content="root:x:0:0::/root:/bin/bash",
        )
    assert exc_info.value.status_code == 400


def test_file_ingress_unsupported_extension_rejection(setup_daemon_v101):
    """Binary or unsupported file extensions (.exe, .bin) must be rejected with 415."""
    client: DaemonClient = setup_daemon_v101["client"]
    with pytest.raises(DaemonClientError) as exc_info:
        client.upload_file(
            space_id="space-sec",
            filename="malicious.exe",
            content="MZ....binary",
        )
    assert exc_info.value.status_code == 415


def test_file_ingress_size_limit_rejection(setup_daemon_v101):
    """Files exceeding 2MB policy limit must be rejected with 413."""
    client: DaemonClient = setup_daemon_v101["client"]
    oversized_content = "X" * (2 * 1024 * 1024 + 10)  # > 2MB
    with pytest.raises(DaemonClientError) as exc_info:
        client.upload_file(
            space_id="space-sec",
            filename="large_data.txt",
            content=oversized_content,
        )
    assert exc_info.value.status_code == 413


# ============================================================================
# WP-6: SYSTEM VISIBILITY TESTS (NODES & MEMORY)
# ============================================================================

def test_nodes_visibility(setup_daemon_v101):
    """GET /api/v1/nodes returns connected physical/virtual nodes and devices."""
    client: DaemonClient = setup_daemon_v101["client"]
    nodes = client.list_nodes()
    assert len(nodes) == 1
    node = nodes[0]
    assert node["node_id"] == "node-desktop-primary"
    assert node["platform"] == "windows"
    assert node["runtime_state"] == "ready"
    assert node["trust_tier"] == "full_trust"
    assert len(node["devices"]) == 1
    assert node["devices"][0]["device_id"] == "dev-gpu-0"


def test_space_memory_visibility(setup_daemon_v101):
    """GET /api/v1/spaces/{id}/memory returns space experiences and insights."""
    client: DaemonClient = setup_daemon_v101["client"]
    mem = client.get_memory("space-test")
    assert mem["space_id"] == "space-test"
    assert mem["experience_count"] == 1
    assert mem["experiences"][0]["experience_id"] == "exp-101"
    assert mem["experiences"][0]["outcome"] == "success"
