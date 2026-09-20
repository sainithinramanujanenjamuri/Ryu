"""Approval Domain Client: bridge between CLI commands, harness, and the Approval subsystem.

Provides high-level helpers to sign, authenticate, and submit decision payloads,
and to inspect durable approval state.

spec §4, §16, ROADMAP Phase 8, ADR-0022, ADR-0023 — Phase 8
"""

from __future__ import annotations

import secrets
import time

from channels.approval.auth import (
    ApproverAuthenticator,
    ApproverDecisionSubmission,
    compute_token_hmac_v1_signature,
)
from core.space.approver import (
    ApprovalLifecycleState,
    ApprovalManager,
    ApprovalRequest,
    AttentionQueueState,
    TimeoutClass,
)


class ApprovalClient:
    """Client for interacting with human approval gates."""

    def __init__(
        self,
        manager: ApprovalManager,
        authenticator: ApproverAuthenticator,
    ) -> None:
        self.manager = manager
        self.authenticator = authenticator

    def submit_decision(self, submission: ApproverDecisionSubmission) -> bool:
        """
        Authenticate wire payload and execute atomic CAS transition.
        Returns True if authenticated and transitioned successfully.
        """
        expected_approver = self.manager.get_approver_id(submission.space_id)
        # 1. Verify authentication using wire protocol verifier
        self.authenticator.verify_submission(
            submission=submission,
            expected_space_approver_id=expected_approver,
        )

        # 2. Execute CAS transition in ApprovalManager
        approved = submission.decision == "APPROVE"
        return self.manager.resolve(
            request_id=submission.approval_id,
            approved=approved,
            approver_id=submission.approver_id,
        )

    def sign_and_submit_decision(
        self,
        approver_id: str,
        token_secret: str,
        space_id: str,
        approval_id: str,
        decision: str,
        plan_version: int,
        capability_request_hash: str,
        nonce: str | None = None,
        timestamp: int | None = None,
    ) -> bool:
        """
        Convenience method for client/CLI: generate canonical nonce, timestamp, signature,
        and submit the decision payload.
        """
        decision_upper = decision.strip().upper()
        if decision_upper not in ("APPROVE", "REJECT"):
            raise ValueError(f"Decision must be 'APPROVE' or 'REJECT', got '{decision}'")

        eff_nonce = nonce if nonce is not None else secrets.token_hex(16)
        eff_timestamp = timestamp if timestamp is not None else int(time.time())

        signature = compute_token_hmac_v1_signature(
            secret_key_bytes=token_secret.encode("utf-8"),
            approver_id=approver_id,
            timestamp=eff_timestamp,
            nonce=eff_nonce,
            space_id=space_id,
            approval_id=approval_id,
            decision=decision_upper,
            plan_version=plan_version,
            capability_request_hash=capability_request_hash,
        )

        submission = ApproverDecisionSubmission(
            approver_id=approver_id,
            timestamp=eff_timestamp,
            nonce=eff_nonce,
            space_id=space_id,
            approval_id=approval_id,
            decision=decision_upper,
            plan_version=plan_version,
            capability_request_hash=capability_request_hash,
            signature=signature,
        )

        return self.submit_decision(submission)

    def get_request(self, approval_id: str) -> ApprovalRequest | None:
        """Retrieve an approval request by ID."""
        return self.manager.get_request(approval_id)

    def list_pending(self, space_id: str) -> list[ApprovalRequest]:
        """List all pending approval requests for a space."""
        return self.manager.store.list_by_space(space_id=space_id, status="pending")

    def list_approvals(
        self,
        space_id: str,
        status: ApprovalLifecycleState | None = None,
        queue_state: AttentionQueueState | None = None,
    ) -> list[ApprovalRequest]:
        """List approval requests filtered by space, status, and queue state."""
        return self.manager.store.list_by_space(
            space_id=space_id, status=status, queue_state=queue_state
        )

    def request_approval(
        self,
        request_id: str,
        space_id: str,
        capability: str,
        timeout_class: TimeoutClass = "default_deny",
        timeout_seconds: float = 30.0,
        goal_id: str = "",
        plan_id: str = "",
        plan_version: int = 1,
        correlation_id: str = "",
        parent_pulse_id: str | None = None,
        requester_id: str = "",
        capability_request_hash: str = "",
        risk_tier: str = "high",
        taint: bool = False,
        summary: str = "",
    ) -> ApprovalRequest:
        """Helper to create an approval request directly via manager."""
        return self.manager.request_approval(
            request_id=request_id,
            space_id=space_id,
            capability=capability,
            timeout_class=timeout_class,
            timeout_seconds=timeout_seconds,
            goal_id=goal_id,
            plan_id=plan_id,
            plan_version=plan_version,
            correlation_id=correlation_id,
            parent_pulse_id=parent_pulse_id,
            requester_id=requester_id,
            capability_request_hash=capability_request_hash,
            risk_tier=risk_tier,
            taint=taint,
            summary=summary,
        )

