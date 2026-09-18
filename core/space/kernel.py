"""Space Kernel: the highest deterministic authority for a Space.

Coordinates Space identity, Admission Control, Budget Windows, Plan Store (CAS),
Approval Manager, and Attention Budget (docs/Architecture §4, §16).
Enforces Space isolation (SPACE-001, SPACE-006) and Taint protection (TAINT-005).

spec §4 (Space Kernel), §16 (Space lifecycle), SPACE-001/006, TAINT-005 — Phase 2
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Protocol

from ryu.pulse_bus.pulse import Pulse, Severity

from core.capabilities.admission import (
    AdmissionController,
    CapabilityRequest,
    CapabilityResponse,
)
from core.capabilities.windows import EscalationWindowManager
from core.plans.delta import PlanDelta
from core.plans.plan_store import PlanStore
from core.plans.task_graph import TaskGraph
from core.security.secrets import SecretStore
from core.space.approver import ApprovalManager, ApprovalRequest, TimeoutClass
from core.space.attention import AttentionBudget


class PulsePublisher(Protocol):
    def publish(self, pulse: Pulse) -> Pulse: ...


class SpaceKernel:
    """
    Space Kernel authority enforcing Space-level invariants.

    Nothing dispatches, plans, or leaks without the Kernel's say (ROADMAP Phase 2).
    """

    def __init__(
        self,
        space_id: str,
        owner_id: str,
        bus: PulsePublisher | None = None,
        secret_store: SecretStore | None = None,
        budget: float = 0.0,
        budget_policy: str = "hard_stop",
        attention_limit: int = 3,
    ) -> None:
        self.space_id = space_id
        self.owner_id = owner_id
        self.bus = bus
        self.secret_store = secret_store or SecretStore()
        self._lock = threading.Lock()

        # Coordinated subsystems
        self.windows = EscalationWindowManager()
        self.admission = AdmissionController(bus=self.bus, windows=self.windows)
        self.admission.set_budget(space_id, budget, policy_mode=budget_policy)

        self.plan_store = PlanStore(bus=self.bus)
        self.plan_store.init_space_plan(space_id)

        self.approval_mgr = ApprovalManager(default_approver_id=owner_id, bus=self.bus)
        self.attention = AttentionBudget(default_limit=attention_limit)

        # Publish space.created lifecycle event
        if self.bus is not None:
            self.bus.publish(
                Pulse(
                    id=f"space-created-{space_id}",
                    space_id=space_id,
                    type="space.created",
                    severity=Severity.INFO,
                    source="space_kernel",
                    timestamp=datetime.now(timezone.utc),
                    payload={
                        "space_id": space_id,
                        "owner_id": owner_id,
                    },
                    taint=False,
                    correlation_id=f"corr-space-{space_id}",
                    parent_pulse_id=None,
                )
            )

    def verify_space_identity(self, incoming_space_id: str) -> None:
        """
        Enforce strict Space identity boundary (SPACE-001, SPACE-006).

        Raises:
            PermissionError if an operation attempts cross-space boundary traversal.
        """
        if incoming_space_id != self.space_id:
            raise PermissionError(
                f"Space isolation violation (SPACE-001): kernel for space '{self.space_id}' "
                f"rejected operation targeted at space '{incoming_space_id}'."
            )

    def request_capability(
        self,
        request: CapabilityRequest,
        is_tainted: bool = False,
    ) -> CapabilityResponse:
        """
        Evaluate and admit a capability request.

        Enforces Space isolation, Taint grant protection (TAINT-005), and Admission budget checks.
        """
        self.verify_space_identity(request.space_id)

        # TAINT-005: Injection canary — tainted payload cannot forge security grants
        if is_tainted and request.capability.startswith("security.grant"):
            if self.bus is not None:
                self.bus.publish(
                    Pulse(
                        id=f"taint-grant-denied-{self.space_id}-{request.requester_id}",
                        space_id=self.space_id,
                        type="security.grant.denied",
                        severity=Severity.CRITICAL,
                        source="space_kernel",
                        timestamp=datetime.now(timezone.utc),
                        payload={
                            "request_id": f"req-{request.requester_id}",
                            "capability": request.capability,
                            "reason": (
                                "Unauthorized grant request derived from tainted execution context "
                                "(TAINT-005)"
                            ),
                        },
                        taint=True,
                        correlation_id=f"corr-taint-{self.space_id}",
                        parent_pulse_id=None,
                    )
                )
            return CapabilityResponse(
                status="denied",
                error="tainted_security_grant_blocked",
                cost=0.0,
            )

        return self.admission.check_admission(request)

    def commit_plan_delta(
        self,
        delta: PlanDelta,
        proposal_id: str | None = None,
    ) -> tuple[bool, int, str | None]:
        """Commit a PlanDelta for this Space via atomic CAS."""
        self.verify_space_identity(delta.space_id)
        return self.plan_store.commit_delta(delta, proposal_id=proposal_id)

    def get_task_graph(self) -> TaskGraph:
        """Retrieve the authoritative TaskGraph for this Space."""
        return self.plan_store.get_task_graph(self.space_id)

    def get_plan_version(self) -> int:
        """Retrieve the authoritative plan_version for this Space."""
        return self.plan_store.get_plan_version(self.space_id)

    def request_approval(
        self,
        request_id: str,
        capability: str,
        timeout_class: TimeoutClass = "default_deny",
        timeout_seconds: float = 30.0,
    ) -> tuple[ApprovalRequest, bool]:
        """
        Submit an approval request through the Space's Attention Budget.

        Returns:
            (ApprovalRequest, is_active: bool) where is_active is False if queued.
        """
        req = self.approval_mgr.request_approval(
            request_id=request_id,
            space_id=self.space_id,
            capability=capability,
            timeout_class=timeout_class,
            timeout_seconds=timeout_seconds,
        )
        is_active = self.attention.submit_approval(self.space_id, request_id)
        return req, is_active

    def resolve_approval(self, request_id: str, approved: bool) -> bool:
        """Resolve an approval and dequeue the next queued request in the attention budget."""
        res = self.approval_mgr.resolve(request_id, approved)
        if res:
            self.attention.complete_approval(self.space_id, request_id)
        return res
