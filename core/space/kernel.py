"""Space Kernel: the highest deterministic authority for a Space.

Coordinates Space identity, Admission Control, Budget Windows, Plan Store (CAS),
Approval Manager, and Attention Budget (docs/Architecture §4, §16).
Enforces Space isolation (SPACE-001, SPACE-006) and Taint protection (TAINT-005).

spec §4 (Space Kernel), §16 (Space lifecycle), SPACE-001/006, TAINT-005 — Phase 2
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any, Protocol

from ryu.pulse_bus.pulse import Pulse, Severity

from core.capabilities.admission import (
    AdmissionController,
    CapabilityRequest,
    CapabilityResponse,
)
from core.capabilities.windows import EscalationWindowManager
from core.plans.delta import PlanDelta
from core.plans.plan_store import PlanStore, PlanStoreProtocol
from core.plans.task_graph import TaskGraph, TaskNode
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
        plan_store: PlanStoreProtocol | None = None,
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

        self.plan_store = plan_store if plan_store is not None else PlanStore(bus=self.bus)
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
        approval: Any | None = None,
    ) -> CapabilityResponse:
        """
        Evaluate and admit a capability request.

        Enforces Space isolation, Taint grant protection (TAINT-005), and Admission budget/approval checks.
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

        return self.admission.check_admission(
            request,
            approval=approval,
            is_tainted=is_tainted,
            current_plan_version=self.get_plan_version(),
        )

    def commit_plan_delta(
        self,
        delta: PlanDelta,
        proposal_id: str | None = None,
    ) -> tuple[bool, int, str | None]:
        """Commit a PlanDelta for this Space via atomic CAS."""
        self.verify_space_identity(delta.space_id)
        return self.plan_store.commit_delta(delta, proposal_id=proposal_id)

    def propose_task_transition(
        self,
        task_id: str,
        to_state: str,
        expected_plan_version: int | str | None = None,
        from_state: str | None = None,
        reason: str = "",
        error: str | None = None,
        result_ref: str | None = None,
        proposal_id: str | None = None,
    ) -> tuple[bool, int, str | None]:
        """Propose a task lifecycle transition via atomic CAS PlanDelta.

        Enforces Space isolation, state machine validity, and single-writer CAS.
        Supports both calling conventions:
          1. (task_id, to_state, expected_plan_version, from_state=...) / keyword args
          2. (task_id, from_state, to_state, error=..., reason=...)
        """
        actual_from: str | None = None
        actual_to: str = to_state
        if isinstance(expected_plan_version, str):
            # Called positionally as: propose_task_transition(task_id, from_state, to_state, ...)
            actual_from = to_state
            actual_to = expected_plan_version
            base_ver = self.get_plan_version()
        else:
            actual_to = to_state
            actual_from = from_state
            base_ver = (
                expected_plan_version
                if isinstance(expected_plan_version, int)
                else self.get_plan_version()
            )

        delta = PlanDelta(
            space_id=self.space_id,
            base_version=base_ver,
            resulting_version=base_ver + 1,
            ops=[
                {
                    "op": "transition",
                    "target_node_id": task_id,
                    "to_state": actual_to,
                    "from_state": actual_from,
                    "reason": reason,
                    "error": error,
                    "result_ref": result_ref,
                }
            ],
        )
        return self.commit_plan_delta(delta, proposal_id=proposal_id or task_id)

    def get_task_graph(self, version: int | None = None) -> TaskGraph:
        """Retrieve the authoritative TaskGraph for this Space (current or historical)."""
        return self.plan_store.get_task_graph(self.space_id, version=version)

    def get_historical_task_graph(self, version: int) -> TaskGraph | None:
        """Retrieve an immutable historical TaskGraph snapshot for this Space."""
        return self.plan_store.get_historical_graph(self.space_id, version)

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

    def create_checkpoint(self, checkpoint_id: str | None = None) -> dict[str, Any]:
        """Create a serializable checkpoint of authoritative Space state (KERNEL-006)."""
        now_str = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        cid = checkpoint_id or f"chk-{self.space_id}-{now_str}"
        with self._lock:
            graph = self.get_task_graph()
            nodes_data = [
                {
                    "id": n.id,
                    "capability": n.capability,
                    "params": dict(n.params),
                    "optional": n.optional,
                    "state": n.state,
                }
                for n in graph.nodes
            ]
            checkpoint = {
                "checkpoint_id": cid,
                "space_id": self.space_id,
                "owner_id": self.owner_id,
                "plan_version": self.get_plan_version(),
                "nodes": nodes_data,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            return checkpoint

    def restore_checkpoint(self, checkpoint_data: dict[str, Any]) -> None:
        """Restore authoritative Space state from a checkpoint and emit space.restored."""
        incoming_space = checkpoint_data.get("space_id", "")
        self.verify_space_identity(incoming_space)

        cid = checkpoint_data.get("checkpoint_id", "chk-unknown")
        with self._lock:
            nodes = [
                TaskNode(
                    id=nd["id"],
                    capability=nd["capability"],
                    params=dict(nd.get("params", {})),
                    optional=bool(nd.get("optional", False)),
                    state=nd.get("state", "pending"),
                )
                for nd in checkpoint_data.get("nodes", [])
            ]
            pver = int(checkpoint_data.get("plan_version", 1))

            graph = TaskGraph(
                space_id=self.space_id,
                plan_version=pver,
                nodes=nodes,
            )
            self.plan_store.restore_graph(graph, winning_delta=cid)

            if self.bus is not None:
                self.bus.publish(
                    Pulse(
                        id=f"space-restored-{self.space_id}-{cid}",
                        space_id=self.space_id,
                        type="space.restored",
                        severity=Severity.INFO,
                        source="space_kernel",
                        timestamp=datetime.now(timezone.utc),
                        payload={
                            "space_id": self.space_id,
                            "checkpoint_id": cid,
                        },
                        taint=False,
                        correlation_id=f"corr-restore-{self.space_id}",
                    )
                )
