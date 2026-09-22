#!/usr/bin/env python3
"""Interactive demonstration of the Phase 8 Human Gate and CLI Channel.

Demonstrates:
1. Setting up an Approver Identity with HMAC-SHA256 Token credentials.
2. Generating a high-risk capability approval request in a Space.
3. Querying the pending approval gate via `ryu approval list`.
4. Inspecting the formatted ANSI card via `ryu approval inspect`.
5. Human operator approving the gate with `token-hmac-v1` cryptographic signature.
6. Verification of the atomic CAS state transition to APPROVED and resolved queue state.

Usage:
    python scripts/demo_approval.py
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import time
import uuid

from channels.approval.auth import (
    ApproverAuthenticator,
    ApproverCredentialRecord,
    InMemoryCredentialStore,
)
from channels.approval.client import ApprovalClient
from channels.cli import CLIContext, main
from core.space.approver import (
    ApprovalManager,
    ApprovalRequest,
    InMemoryApprovalStore,
)


def run_demo() -> None:
    print("======================================================================")
    print("       RYU AI — Phase 8 CLI Channel & Human Gate Interactive Demo     ")
    print("======================================================================\n")

    # 1. Initialize hermetic credential and approval stores
    cred_store = InMemoryCredentialStore()
    approver_id = "operator_alice"
    token_secret = "alice_super_secret_token_123"
    secret_ref = "secret://approver/alice-key"

    now_dt = datetime.now(timezone.utc)
    cred_store.register_credential(
        ApproverCredentialRecord(
            approver_id=approver_id,
            token_id="tok-alice-01",
            secret_ref=secret_ref,
            created_at=now_dt,
            expires_at=now_dt + timedelta(days=1),
        )
    )
    secret_store = {secret_ref: token_secret}
    authenticator = ApproverAuthenticator(
        cred_store=cred_store,
        nonce_store=cred_store,
        secret_store=secret_store,
    )

    approval_store = InMemoryApprovalStore()
    manager = ApprovalManager(store=approval_store)
    manager.set_space_approver("space_prod", approver_id)

    client = ApprovalClient(manager=manager, authenticator=authenticator)
    ctx = CLIContext(approval_client=client)

    # 2. Simulate an Agent requesting a high-risk capability
    app_id = str(uuid.uuid4())
    print(f"[*] Simulating high-risk capability dispatch requiring human gate: {app_id}")
    req = ApprovalRequest(
        request_id=app_id,
        space_id="space_prod",
        capability="device.shell.execute",
        approver_id=approver_id,
        timeout_class="default_deny",
        timeout_seconds=300.0,
        status="pending",
        queue_state="active",
        created_at=time.time(),
        expires_at=time.time() + 300.0,
        goal_id="goal-release-v2",
        plan_id="plan-release-step",
        plan_version=1,
        correlation_id="corr-release-42",
        parent_pulse_id="pulse-root-1",
        requester_id="worker-deployer-1",
        capability_request_hash="d5a6b8c9e0f1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0b1c2d3e4f5a6b7",
        risk_tier="CRITICAL",
        taint=False,
        summary="Deploy production migration script and restart database services",
    )
    approval_store.save(req)

    # 3. CLI Command: Status
    print("\n--- STEP 1: Check Runtime Status (CLI: ryu status) ---")
    main(["status"], ctx)

    # 4. CLI Command: List Approvals
    print("\n--- STEP 2: List Active Gates (CLI: ryu approval list) ---")
    main(["approval", "list", "--space-id", "space_prod"], ctx)

    # 5. CLI Command: Inspect Gate
    print("\n--- STEP 3: Inspect Human Gate Card (CLI: ryu approval inspect) ---")
    main(["approval", "inspect", app_id], ctx)

    # 6. CLI Command: Approve Gate with token-hmac-v1
    print("\n--- STEP 4: Human Approves Gate with HMAC Signature (CLI: ryu approval approve) ---")
    print(f"Approver: {approver_id}")
    print(f"Token:    {token_secret}")
    main([
        "approval", "approve", app_id,
        "--approver", approver_id,
        "--token", token_secret,
        "--space-id", "space_prod",
        "--plan-version", "1",
    ], ctx)

    # 7. CLI Command: Verify CAS resolution
    print("\n--- STEP 5: Verify Gate Resolution (CLI: ryu approval list) ---")
    main(["approval", "list", "--space-id", "space_prod"], ctx)

    print("\n======================================================================")
    print("                    Demo Completed Successfully!                      ")
    print("======================================================================")


if __name__ == "__main__":
    run_demo()
