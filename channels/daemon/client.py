"""Local Channel Daemon HTTP Client.

Provides client-side access to the Local Channel Daemon over loopback HTTP/SSE.
Performs client-side token-hmac-v1 cryptographic signing so that raw human
approver secrets are never transmitted to or persisted by the daemon.

spec §2, §4, ADR-0026, CONTRACT APP-002, APP-006 — Phase 8.5
"""

from __future__ import annotations

import json
import secrets
import time
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from channels.approval.auth import (
    ApproverDecisionSubmission,
    compute_token_hmac_v1_signature,
)


class DaemonClientError(Exception):
    """Raised when an HTTP or protocol error occurs while contacting the daemon."""

    def __init__(self, message: str, status_code: int = 500, response_body: Any = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.response_body = response_body


class DaemonClient:
    """Client for interacting with the local channel daemon adapter."""

    def __init__(self, base_url: str = "http://127.0.0.1:8420", auth_token: str | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.auth_token = auth_token

    def _request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        json_data: dict[str, Any] | None = None,
    ) -> Any:
        url = f"{self.base_url}{path}"
        if params:
            clean_params = {k: v for k, v in params.items() if v is not None}
            if clean_params:
                url = f"{url}?{urlencode(clean_params)}"

        headers: dict[str, str] = {
            "Accept": "application/json",
        }
        if self.auth_token:
            headers["Authorization"] = f"Bearer {self.auth_token}"

        body_bytes = None
        if json_data is not None:
            headers["Content-Type"] = "application/json; charset=utf-8"
            body_bytes = json.dumps(json_data).encode("utf-8")

        req = Request(url=url, data=body_bytes, headers=headers, method=method)
        try:
            with urlopen(req, timeout=5.0) as resp:
                resp_bytes = resp.read()
                if not resp_bytes:
                    return None
                return json.loads(resp_bytes.decode("utf-8"))
        except HTTPError as e:
            err_body = None
            try:
                raw_err = e.read()
                err_body = json.loads(raw_err.decode("utf-8"))
            except Exception:
                pass
            err_msg = err_body.get("error", str(e)) if isinstance(err_body, dict) else str(e)
            raise DaemonClientError(f"Daemon request failed ({e.code}): {err_msg}", status_code=e.code, response_body=err_body)
        except Exception as e:
            raise DaemonClientError(f"Failed to connect to daemon at {self.base_url}: {e}")

    def health(self) -> dict[str, Any]:
        """Check daemon health."""
        return self._request("GET", "/api/v1/health")  # type: ignore

    def list_spaces(self) -> list[dict[str, Any]]:
        """List active spaces."""
        res = self._request("GET", "/api/v1/spaces")
        return res.get("spaces", []) if isinstance(res, dict) else []

    def get_attention(self, space_id: str) -> dict[str, Any]:
        """Get attention budget and saturation metrics for space."""
        return self._request("GET", f"/api/v1/spaces/{space_id}/attention")  # type: ignore

    def list_approvals(
        self,
        space_id: str,
        status: str | None = None,
        queue_state: str | None = None,
    ) -> list[dict[str, Any]]:
        """List approvals for space."""
        params = {"status": status, "queue_state": queue_state}
        res = self._request("GET", f"/api/v1/spaces/{space_id}/approvals", params=params)
        return res.get("approvals", []) if isinstance(res, dict) else []

    def get_approval(self, space_id: str, approval_id: str) -> dict[str, Any] | None:
        """Inspect specific approval request."""
        try:
            return self._request("GET", f"/api/v1/spaces/{space_id}/approvals/{approval_id}")  # type: ignore
        except DaemonClientError as e:
            if e.status_code == 404:
                return None
            raise

    def submit_decision(self, submission: ApproverDecisionSubmission) -> dict[str, Any]:
        """
        Submit pre-computed decision signature to daemon.
        Note: The raw secret key is never sent.
        """
        payload = {
            "approver_id": submission.approver_id,
            "timestamp": submission.timestamp,
            "nonce": submission.nonce,
            "decision": submission.decision,
            "plan_version": submission.plan_version,
            "capability_request_hash": submission.capability_request_hash,
            "signature": submission.signature,
        }
        return self._request(  # type: ignore
            "POST",
            f"/api/v1/spaces/{submission.space_id}/approvals/{submission.approval_id}/decision",
            json_data=payload,
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
    ) -> dict[str, Any]:
        """
        Client-side signing convenience: computes HMAC-SHA256 signature locally
        and dispatches only the cryptographic signature to the daemon.
        The raw token_secret never leaves the local caller.
        """
        decision_upper = decision.strip().upper()
        if decision_upper not in ("APPROVE", "REJECT"):
            raise ValueError(f"Decision must be 'APPROVE' or 'REJECT', got '{decision}'")

        eff_nonce = nonce if nonce is not None else secrets.token_hex(16)
        eff_timestamp = timestamp if timestamp is not None else int(time.time())

        # Sign locally
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

    def list_tasks(self, space_id: str) -> list[dict[str, Any]]:
        """List tasks for space."""
        res = self._request("GET", f"/api/v1/spaces/{space_id}/tasks")
        return res.get("tasks", []) if isinstance(res, dict) else []

    def get_audit(
        self,
        space_id: str,
        limit: int = 50,
        pulse_type: str | None = None,
    ) -> list[dict[str, Any]]:
        """Query audit log for space."""
        params = {"limit": limit, "type": pulse_type}
        res = self._request("GET", f"/api/v1/spaces/{space_id}/audit", params=params)
        return res.get("events", []) if isinstance(res, dict) else []

    def send_prompt(
        self,
        space_id: str,
        prompt: str,
        command_id: str | None = None,
        params: dict[str, Any] | None = None,
        constraints: list[str] | None = None,
    ) -> dict[str, Any]:
        """Submit a prompt or goal instruction to a space via the local daemon."""
        payload: dict[str, Any] = {
            "prompt": prompt,
        }
        if command_id:
            payload["command_id"] = command_id
        if params:
            payload["params"] = params
        if constraints:
            payload["constraints"] = constraints
        return self._request("POST", f"/api/v1/spaces/{space_id}/prompt", json_data=payload)  # type: ignore

