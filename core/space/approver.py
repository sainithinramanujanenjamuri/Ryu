"""Human Approval Manager: deterministic approver_id, CAS lifecycle, and timeout policies.

Every human-gated operation resolves to a single authenticated approver_id (docs/Architecture §4, §16).
Timeout classes enforce default_deny (high-risk grants/taint) vs default_hold (budget).

spec §4 (Space Kernel), §10 (Approval tiers), §16 (security.grant.approved/denied),
ROADMAP Phase 8, ADR-0022, ADR-0024 — Phase 8
"""

from __future__ import annotations

import hashlib
import hmac
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal, Protocol

from ryu.pulse_bus.pulse import Pulse, Severity


class PulsePublisher(Protocol):
    def publish(self, pulse: Pulse) -> Pulse: ...


TimeoutClass = Literal["default_deny", "default_hold"]
ApprovalLifecycleState = Literal[
    "pending", "approved", "denied", "expired", "held", "consumed"
]
ApprovalStatus = ApprovalLifecycleState  # Backwards compatibility alias
AttentionQueueState = Literal["active", "queued", "resolved"]


@dataclass
class ApprovalRequest:
    """Represents a durable human approval gate request."""

    request_id: str
    space_id: str
    capability: str
    approver_id: str
    timeout_class: TimeoutClass = "default_deny"
    timeout_seconds: float = 30.0
    status: ApprovalLifecycleState = "pending"
    queue_state: AttentionQueueState = "queued"
    created_at: float = field(default_factory=time.time)
    expires_at: float = 0.0
    resolved_at: float | None = None
    consumed_at: float | None = None

    # Lineage and Execution Context Binding
    goal_id: str = ""
    plan_id: str = ""
    plan_version: int = 1
    correlation_id: str = ""
    parent_pulse_id: str | None = None
    requester_id: str = ""
    capability_request_hash: str = ""
    risk_tier: str = "high"
    taint: bool = False
    summary: str = ""

    # Decision Integrity
    resolution_reason: str | None = None
    decision_signature: str | None = None
    decision_key_version: int = 1

    def __post_init__(self) -> None:
        if not self.expires_at and self.created_at:
            self.expires_at = self.created_at + self.timeout_seconds

    @property
    def approval_id(self) -> str:
        """Alias for request_id to satisfy E2E contract naming."""
        return self.request_id


def compute_decision_signature(
    kernel_key_bytes: bytes,
    space_id: str,
    approval_id: str,
    status: str,
    approver_id: str,
    resolved_at: float,
    plan_version: int,
    capability_request_hash: str,
    key_version: int = 1,
) -> str:
    """
    Compute canonical tamper-evident HMAC-SHA256 signature for persisted approval record.
    Uses Space Kernel internal secret (kernel_hmac_secret).
    """
    payload = (
        f"ryu-decision-v1\n"
        f"{space_id}\n"
        f"{approval_id}\n"
        f"{status}\n"
        f"{approver_id}\n"
        f"{resolved_at:.6f}\n"
        f"{plan_version}\n"
        f"{capability_request_hash.lower()}\n"
        f"{key_version}"
    )
    return hmac.new(kernel_key_bytes, payload.encode("utf-8"), hashlib.sha256).hexdigest().lower()


def verify_decision_signature(kernel_key_bytes: bytes, req: ApprovalRequest) -> bool:
    """Verify constant-time integrity of an approved or denied ApprovalRequest record."""
    if not req.decision_signature or req.resolved_at is None:
        return False
    expected = compute_decision_signature(
        kernel_key_bytes=kernel_key_bytes,
        space_id=req.space_id,
        approval_id=req.approval_id,
        status=req.status,
        approver_id=req.approver_id or "",
        resolved_at=req.resolved_at,
        plan_version=req.plan_version,
        capability_request_hash=req.capability_request_hash,
        key_version=req.decision_key_version,
    )
    return hmac.compare_digest(expected, req.decision_signature)


class ApprovalStore(Protocol):
    """Protocol for durable or in-memory approval storage."""

    def save(self, req: ApprovalRequest) -> None: ...

    def get(self, approval_id: str) -> ApprovalRequest | None: ...

    def list_by_space(
        self,
        space_id: str,
        status: ApprovalLifecycleState | None = None,
        queue_state: AttentionQueueState | None = None,
    ) -> list[ApprovalRequest]: ...

    def transition_cas(
        self,
        approval_id: str,
        expected_status: ApprovalLifecycleState,
        new_status: ApprovalLifecycleState,
        approver_id: str | None = None,
        resolved_at: float | None = None,
        reason: str | None = None,
        signature: str | None = None,
        key_version: int = 1,
    ) -> bool: ...

    def update_queue_state(
        self, approval_id: str, new_queue_state: AttentionQueueState
    ) -> bool: ...


class InMemoryApprovalStore:
    """Thread-safe in-memory store for ApprovalRequests."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._requests: dict[str, ApprovalRequest] = {}

    def save(self, req: ApprovalRequest) -> None:
        with self._lock:
            self._requests[req.approval_id] = req

    def get(self, approval_id: str) -> ApprovalRequest | None:
        with self._lock:
            return self._requests.get(approval_id)

    def list_by_space(
        self,
        space_id: str,
        status: ApprovalLifecycleState | None = None,
        queue_state: AttentionQueueState | None = None,
    ) -> list[ApprovalRequest]:
        with self._lock:
            results = [r for r in self._requests.values() if r.space_id == space_id]
            if status is not None:
                results = [r for r in results if r.status == status]
            if queue_state is not None:
                results = [r for r in results if r.queue_state == queue_state]
            return sorted(results, key=lambda r: r.created_at)

    def transition_cas(
        self,
        approval_id: str,
        expected_status: ApprovalLifecycleState,
        new_status: ApprovalLifecycleState,
        approver_id: str | None = None,
        resolved_at: float | None = None,
        reason: str | None = None,
        signature: str | None = None,
        key_version: int = 1,
    ) -> bool:
        with self._lock:
            req = self._requests.get(approval_id)
            if req is None or req.status != expected_status:
                return False
            req.status = new_status
            if approver_id is not None:
                req.approver_id = approver_id
            if resolved_at is not None:
                req.resolved_at = resolved_at
            if reason is not None:
                req.resolution_reason = reason
            if signature is not None:
                req.decision_signature = signature
                req.decision_key_version = key_version
            if new_status in ("approved", "denied", "expired", "consumed"):
                req.queue_state = "resolved"
            if new_status == "consumed":
                req.consumed_at = resolved_at or time.time()
            return True

    def update_queue_state(
        self, approval_id: str, new_queue_state: AttentionQueueState
    ) -> bool:
        with self._lock:
            req = self._requests.get(approval_id)
            if req is None:
                return False
            req.queue_state = new_queue_state
            return True


class ApprovalManager:
    """
    Manages human approval lifecycle, deterministic approver mapping, and timeout policies.
    Enforces atomic CAS transitions and publishes authoritative approval events.
    """

    def __init__(
        self,
        default_approver_id: str = "human_operator",
        bus: PulsePublisher | None = None,
        store: ApprovalStore | None = None,
        secret_store: Any | None = None,
    ) -> None:
        self.default_approver_id = default_approver_id
        self.bus = bus
        self.store = store or InMemoryApprovalStore()
        self.secret_store = secret_store
        self._lock = threading.Lock()
        self._space_approvers: dict[str, str] = {}
        self._signing_keys: dict[str, bytes] = {}

    def set_space_approver(self, space_id: str, approver_id: str) -> None:
        """Assign the single deterministic approver_id for a Space."""
        with self._lock:
            self._space_approvers[space_id] = approver_id

    def get_approver_id(self, space_id: str) -> str:
        """Retrieve the authoritative single approver_id for a Space."""
        with self._lock:
            return self._space_approvers.get(space_id, self.default_approver_id)

    def set_decision_signing_key(self, space_id: str, key_bytes: bytes) -> None:
        """Set the Space Kernel HMAC authority secret for signing approval decisions."""
        with self._lock:
            self._signing_keys[space_id] = key_bytes

    def get_decision_signing_key(self, space_id: str) -> bytes:
        """Retrieve the Space Kernel HMAC authority secret."""
        with self._lock:
            if space_id in self._signing_keys:
                return self._signing_keys[space_id]
            # Fallback deterministic key derived from space_id for test hermeticity
            derived = hashlib.sha256(f"kernel-signing-key-{space_id}".encode("utf-8")).digest()
            self._signing_keys[space_id] = derived
            return derived

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
        """Create a new human approval gate request in PENDING state."""
        approver_id = self.get_approver_id(space_id)
        now = time.time()
        req = ApprovalRequest(
            request_id=request_id,
            space_id=space_id,
            capability=capability,
            approver_id=approver_id,
            timeout_class=timeout_class,
            timeout_seconds=timeout_seconds,
            status="pending",
            queue_state="queued",
            created_at=now,
            expires_at=now + timeout_seconds,
            goal_id=goal_id,
            plan_id=plan_id,
            plan_version=plan_version,
            correlation_id=correlation_id or f"corr-approval-{space_id}-{request_id}",
            parent_pulse_id=parent_pulse_id,
            requester_id=requester_id,
            capability_request_hash=capability_request_hash,
            risk_tier=risk_tier,
            taint=taint,
            summary=summary or f"Approval request for {capability}",
        )
        self.store.save(req)
        return req

    def get_request(self, request_id: str) -> ApprovalRequest | None:
        """Retrieve an approval request by ID."""
        return self.store.get(request_id)

    def resolve(
        self,
        request_id: str,
        approved: bool,
        approver_id: str | None = None,
        reason: str | None = None,
    ) -> bool:
        """
        Record an authenticated human response via atomic CAS transition.
        Generates decision_signature and publishes authoritative approval Pulses.
        """
        req = self.store.get(request_id)
        if req is None or req.status not in ("pending", "held"):
            return False

        effective_approver = approver_id or req.approver_id or self.get_approver_id(req.space_id)
        now = time.time()
        new_status: ApprovalLifecycleState = "approved" if approved else "denied"

        # Compute tamper-evident decision_signature
        kernel_key = self.get_decision_signing_key(req.space_id)
        sig = compute_decision_signature(
            kernel_key_bytes=kernel_key,
            space_id=req.space_id,
            approval_id=req.approval_id,
            status=new_status,
            approver_id=effective_approver,
            resolved_at=now,
            plan_version=req.plan_version,
            capability_request_hash=req.capability_request_hash,
            key_version=req.decision_key_version,
        )

        success = self.store.transition_cas(
            approval_id=request_id,
            expected_status=req.status,
            new_status=new_status,
            approver_id=effective_approver,
            resolved_at=now,
            reason=reason,
            signature=sig,
            key_version=req.decision_key_version,
        )

        if success:
            updated_req = self.store.get(request_id)
            if updated_req:
                self._publish_approval_pulse(updated_req, approved=approved, reason=reason)
        return success

    def check_timeout(
        self, request_id: str, current_time: float | None = None, as_expired: bool = False
    ) -> ApprovalLifecycleState:
        """
        Evaluate timeout policy on a request.
        For default_deny: transitions PENDING -> 'denied' (or 'expired' if as_expired=True) and publishes security.grant.denied.
        For default_hold: transitions PENDING -> 'held' and execution remains paused.
        """
        now = current_time if current_time is not None else time.time()
        req = self.store.get(request_id)
        if req is None:
            return "denied"
        if req.status != "pending":
            return req.status

        if now >= req.expires_at:
            if req.timeout_class == "default_deny":
                new_status: ApprovalLifecycleState = "expired" if as_expired else "denied"
                kernel_key = self.get_decision_signing_key(req.space_id)
                sig = compute_decision_signature(
                    kernel_key_bytes=kernel_key,
                    space_id=req.space_id,
                    approval_id=req.approval_id,
                    status=new_status,
                    approver_id="system_timeout",
                    resolved_at=now,
                    plan_version=req.plan_version,
                    capability_request_hash=req.capability_request_hash,
                    key_version=req.decision_key_version,
                )
                if self.store.transition_cas(
                    approval_id=request_id,
                    expected_status="pending",
                    new_status=new_status,
                    approver_id="system_timeout",
                    resolved_at=now,
                    reason=f"Approval request timed out ({new_status})",
                    signature=sig,
                ):
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
                                    "reason": f"Approval request timed out ({new_status})",
                                },
                                taint=False,
                                correlation_id=req.correlation_id,
                                parent_pulse_id=req.parent_pulse_id,
                            )
                        )
                return new_status
            else:  # default_hold
                self.store.transition_cas(
                    approval_id=request_id,
                    expected_status="pending",
                    new_status="held",
                    reason="Paused awaiting budget replenishment / human continuation (default_hold)",
                )
                return "held"

        return "pending"

    def expire(self, request_id: str, current_time: float | None = None) -> bool:
        """Explicitly transition an expired request from PENDING to EXPIRED."""
        res = self.check_timeout(request_id, current_time=current_time, as_expired=True)
        return res == "expired"

    def consume_approval(
        self,
        approval_id: str,
        current_plan_version: int,
        capability_request_hash: str,
    ) -> bool:
        """
        Validate and atomically consume an approved ApprovalRequest for capability execution.
        Enforces single-use CAS semantics and plan version binding.
        """
        req = self.store.get(approval_id)
        if req is None or req.status != "approved" or req.consumed_at is not None:
            return False

        if req.plan_version != current_plan_version:
            return False

        if (
            req.capability_request_hash
            and capability_request_hash
            and req.capability_request_hash.lower() != capability_request_hash.lower()
        ):
            return False

        # Verify decision_signature integrity
        kernel_key = self.get_decision_signing_key(req.space_id)
        if not verify_decision_signature(kernel_key, req):
            return False

        now = time.time()
        with self._lock:
            if req.consumed_at is not None:
                return False
            success = self.store.transition_cas(
                approval_id=approval_id,
                expected_status="approved",
                new_status="consumed",
                resolved_at=now,
            )
            if success:
                req.consumed_at = now
                req.status = "consumed"
            return success

    def _publish_approval_pulse(
        self, req: ApprovalRequest, approved: bool, reason: str | None = None
    ) -> None:
        """
        Authoritative publisher for security approval pulses.
        Pulse source is strictly 'approval_manager'.
        """
        if self.bus is None:
            return

        pulse_type = "security.grant.approved" if approved else "security.grant.denied"
        severity = Severity.INFO if approved else Severity.WARNING
        if approved:
            exp_dt = datetime.fromtimestamp(req.expires_at, tz=timezone.utc)
            payload: dict[str, Any] = {
                "request_id": req.request_id,
                "capability": req.capability,
                "risk_tier": req.risk_tier if req.risk_tier in ("low", "high") else "high",
                "approver_id": req.approver_id or self.get_approver_id(req.space_id),
                "expiry": exp_dt.isoformat(),
            }
        else:
            payload = {
                "request_id": req.request_id,
                "capability": req.capability,
                "reason": reason or "Approval denied by human operator",
            }

        self.bus.publish(
            Pulse(
                id=f"{pulse_type.replace('.', '-')}-{req.space_id}-{req.request_id}",
                space_id=req.space_id,
                type=pulse_type,
                severity=severity,
                source="approval_manager",
                timestamp=datetime.now(timezone.utc),
                payload=payload,
                taint=False,
                correlation_id=req.correlation_id,
                parent_pulse_id=req.parent_pulse_id,
            )
        )
