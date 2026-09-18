"""Human Approval Manager: deterministic approver_id and timeout policies.

Every human-gated operation resolves to a single approver_id (docs/Architecture §4, §16).
Timeout classes enforce default_deny (high-risk grants/taint) vs default_hold (budget).

spec §4 (Space Kernel), §10 (Approval tiers), §16 (security.grant.denied) — Phase 2
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal, Protocol

from ryu.pulse_bus.pulse import Pulse, Severity


class PulsePublisher(Protocol):
    def publish(self, pulse: Pulse) -> Pulse: ...


TimeoutClass = Literal["default_deny", "default_hold"]
ApprovalStatus = Literal["pending", "approved", "denied", "held"]


@dataclass
class ApprovalRequest:
    """Represents a human approval gate request."""
    request_id: str
    space_id: str
    capability: str
    approver_id: str
    timeout_class: TimeoutClass = "default_deny"
    timeout_seconds: float = 30.0
    status: ApprovalStatus = "pending"
    created_at: float = field(default_factory=time.time)
    resolved_at: float | None = None


class ApprovalManager:
    """
    Manages human approval lifecycle, deterministic approver mapping, and timeout policies.
    """

    def __init__(
        self,
        default_approver_id: str = "human_operator",
        bus: PulsePublisher | None = None,
    ) -> None:
        self.default_approver_id = default_approver_id
        self.bus = bus
        self._lock = threading.Lock()
        self._requests: dict[str, ApprovalRequest] = {}
        self._space_approvers: dict[str, str] = {}

    def set_space_approver(self, space_id: str, approver_id: str) -> None:
        """Assign the single deterministic approver_id for a Space."""
        with self._lock:
            self._space_approvers[space_id] = approver_id

    def get_approver_id(self, space_id: str) -> str:
        """Retrieve the authoritative single approver_id for a Space."""
        with self._lock:
            return self._space_approvers.get(space_id, self.default_approver_id)

    def request_approval(
        self,
        request_id: str,
        space_id: str,
        capability: str,
        timeout_class: TimeoutClass = "default_deny",
        timeout_seconds: float = 30.0,
    ) -> ApprovalRequest:
        """Create a new human approval gate request."""
        approver_id = self.get_approver_id(space_id)
        req = ApprovalRequest(
            request_id=request_id,
            space_id=space_id,
            capability=capability,
            approver_id=approver_id,
            timeout_class=timeout_class,
            timeout_seconds=timeout_seconds,
        )
        with self._lock:
            self._requests[request_id] = req
        return req

    def get_request(self, request_id: str) -> ApprovalRequest | None:
        """Retrieve an approval request by ID."""
        with self._lock:
            return self._requests.get(request_id)

    def resolve(
        self,
        request_id: str,
        approved: bool,
        approver_id: str | None = None,
    ) -> bool:
        """
        Record a human response.

        Returns:
            True if state was transitioned, False if request not found or not pending.
        """
        with self._lock:
            req = self._requests.get(request_id)
            if req is None or req.status not in ("pending", "held"):
                return False

            req.resolved_at = time.time()
            if approved:
                req.status = "approved"
            else:
                req.status = "denied"
                if self.bus is not None:
                    self.bus.publish(
                        Pulse(
                            id=f"grant-denied-{req.space_id}-{request_id}",
                            space_id=req.space_id,
                            type="security.grant.denied",
                            severity=Severity.WARNING,
                            source="approval_manager",
                            timestamp=datetime.now(timezone.utc),
                            payload={
                                "request_id": request_id,
                                "capability": req.capability,
                                "reason": f"Rejected by approver {approver_id or req.approver_id}",
                            },
                            taint=False,
                            correlation_id=f"corr-approval-{req.space_id}",
                            parent_pulse_id=None,
                        )
                    )
            return True

    def check_timeout(self, request_id: str, current_time: float | None = None) -> ApprovalStatus:
        """
        Evaluate timeout policy on a request.

        For default_deny: transitions to 'denied' and publishes security.grant.denied.
        For default_hold: transitions to 'held' and execution remains paused.
        """
        now = current_time if current_time is not None else time.time()
        with self._lock:
            req = self._requests.get(request_id)
            if req is None:
                return "denied"
            if req.status != "pending":
                return req.status

            if now - req.created_at >= req.timeout_seconds:
                if req.timeout_class == "default_deny":
                    req.status = "denied"
                    req.resolved_at = now
                    if self.bus is not None:
                        self.bus.publish(
                            Pulse(
                                id=f"grant-denied-timeout-{req.space_id}-{request_id}",
                                space_id=req.space_id,
                                type="security.grant.denied",
                                severity=Severity.WARNING,
                                source="approval_manager",
                                timestamp=datetime.now(timezone.utc),
                                payload={
                                    "request_id": request_id,
                                    "capability": req.capability,
                                    "reason": "Approval request timed out (default_deny)",
                                },
                                taint=False,
                                correlation_id=f"corr-approval-{req.space_id}",
                                parent_pulse_id=None,
                            )
                        )
                    return "denied"
                else:  # default_hold
                    req.status = "held"
                    return "held"

            return "pending"

