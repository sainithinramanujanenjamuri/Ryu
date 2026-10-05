"""Admission Control subsystem: pre-dispatch budget enforcement and capability check.

The Space Kernel is the authoritative pre-dispatch enforcement point (docs/Architecture §4, §16).
Every CapabilityRequest is evaluated before dispatching to workers or tools.

spec §4 (Admission Control), §16 (CapabilityRequest/Response), KERNEL-001/002/003 — Phase 2
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol

from ryu.pulse_bus.pulse import Pulse, Severity

from core.capabilities.windows import EscalationWindowManager


class PulsePublisher(Protocol):
    def publish(self, pulse: Pulse) -> Pulse: ...


@dataclass(frozen=True)
class CapabilityRequest:
    """CapabilityRequest contract matching docs/Architecture §16."""
    requester_id: str
    space_id: str
    capability: str
    params: dict[str, Any] = field(default_factory=dict)
    timeout: float = 30.0
    budget: float = 0.0
    idempotency_key: str = ""


@dataclass(frozen=True)
class CapabilityResponse:
    """CapabilityResponse contract matching docs/Architecture §16."""
    status: str  # "ok" | "failed" | "denied"
    result: Any = None
    error: str | None = None
    cost: float = 0.0


class AdmissionController:
    """
    Enforces Space-level pre-dispatch budget and capability admission invariants.

    Budget denial is never an exception; it returns CapabilityResponse(status="denied")
    and publishes typed space.budget.exceeded Pulses per Law 6.
    """

    def __init__(
        self,
        bus: PulsePublisher | None = None,
        windows: EscalationWindowManager | None = None,
    ) -> None:
        self.bus = bus
        self.windows = windows or EscalationWindowManager()
        self._lock = threading.Lock()
        self._budgets: dict[str, float] = {}
        self._initial_budgets: dict[str, float] = {}
        self._policies: dict[str, str] = {}  # "hard_stop" | "approval_required" | "degraded"
        self._soft_thresholds: dict[str, float] = {}  # ratio 0.0 to 1.0 (default 0.80)
        self._reservations: dict[tuple[str, str], float] = {}  # (space_id, reservation_id) -> amount (SCHED-004)

    def set_budget(
        self,
        space_id: str,
        budget: float,
        policy_mode: str = "hard_stop",
        soft_threshold: float = 0.80,
    ) -> None:
        """Configure budget and enforcement policy for a Space."""
        with self._lock:
            self._budgets[space_id] = float(budget)
            self._initial_budgets[space_id] = float(budget)
            self._policies[space_id] = policy_mode
            self._soft_thresholds[space_id] = soft_threshold

    def get_remaining_budget(self, space_id: str) -> float:
        """Return the current remaining budget for a Space."""
        with self._lock:
            return self._budgets.get(space_id, 0.0)

    def get_reserved_budget(self, space_id: str) -> float:
        """Return the sum of active budget reservations for a Space (SCHED-004)."""
        with self._lock:
            return sum(amt for (sp, _), amt in self._reservations.items() if sp == space_id)

    def reserve_budget(self, space_id: str, amount: float, reservation_id: str) -> bool:
        """Atomically pre-reserve budget for a task (SCHED-004)."""
        with self._lock:
            current = self._budgets.get(space_id, 0.0)
            if amount > 0 and current < amount:
                return False
            if amount > 0:
                self._budgets[space_id] = current - amount
                self._reservations[(space_id, reservation_id)] = amount
            return True

    def release_reservation(self, space_id: str, reservation_id: str) -> bool:
        """Release a pre-reserved budget back to available balance (e.g. on lease/CAS failure) (SCHED-004)."""
        with self._lock:
            key = (space_id, reservation_id)
            if key in self._reservations:
                amount = self._reservations.pop(key)
                self._budgets[space_id] = self._budgets.get(space_id, 0.0) + amount
                return True
            return False

    def reconcile_reservation(self, space_id: str, reservation_id: str, actual_cost: float) -> None:
        """Reconcile a pre-reserved budget against actual spend upon task completion (SCHED-004)."""
        with self._lock:
            key = (space_id, reservation_id)
            reserved = self._reservations.pop(key, 0.0)
            diff = reserved - actual_cost
            self._budgets[space_id] = max(0.0, self._budgets.get(space_id, 0.0) + diff)

    def replenish_budget(self, space_id: str, amount: float) -> str:
        """Replenish budget and mint a new escalation window."""
        with self._lock:
            current = self._budgets.get(space_id, 0.0)
            self._budgets[space_id] = current + amount
        return self.windows.replenish(space_id, amount)

    def record_spend(self, space_id: str, cost: float) -> None:
        """Deduct execution cost from the Space budget."""
        with self._lock:
            if space_id in self._budgets:
                self._budgets[space_id] = max(0.0, self._budgets[space_id] - cost)

    def check_admission(
        self,
        request: CapabilityRequest,
        approval: Any | None = None,
        is_tainted: bool = False,
        current_plan_version: int | None = None,
    ) -> CapabilityResponse:
        """
        Evaluate CapabilityRequest against budget policy and human approval prior to any dispatch.

        Returns:
            CapabilityResponse with status="ok" if admitted,
            or status="denied" if blocked by budget policy or unapproved high-risk/tainted operation.
        """
        space_id = request.space_id

        # Phase 8: Human Gate verification for high-risk, tainted, or gated capabilities
        is_gated = (
            request.capability.startswith("node.")
            or request.capability.startswith("security.")
            or is_tainted
            or (approval is not None)
        )
        if is_gated:
            if approval is None:
                return CapabilityResponse(
                    status="denied",
                    error="approval_required",
                    cost=0.0,
                )
            if getattr(approval, "status", None) != "approved":
                return CapabilityResponse(
                    status="denied",
                    error=f"approval_{getattr(approval, 'status', 'missing')}",
                    cost=0.0,
                )
            if getattr(approval, "consumed_at", None) is not None:
                return CapabilityResponse(
                    status="denied",
                    error="approval_already_consumed",
                    cost=0.0,
                )
            if current_plan_version is not None and getattr(approval, "plan_version", None) != current_plan_version:
                return CapabilityResponse(
                    status="denied",
                    error="approval_plan_version_mismatch",
                    cost=0.0,
                )
            # Mark approval consumed atomically
            setattr(approval, "consumed_at", time.time())
            setattr(approval, "status", "consumed")

        req_cost = float(getattr(request, "budget", 0.0) or 0.0)
        resv_key: str | None = None

        with self._lock:
            remaining = self._budgets.get(space_id, 0.0)
            initial = self._initial_budgets.get(space_id, 0.0)
            policy = self._policies.get(space_id, "hard_stop")
            soft_thresh = self._soft_thresholds.get(space_id, 0.80)

            # MODE A: hard_stop (budget exhausted or insufficient for requested budget)
            if remaining <= 0 or (req_cost > 0 and remaining < req_cost):
                window_id = self.windows.get_or_create_window(space_id)
                should_escalate = self.windows.should_escalate_budget(space_id, window_id)
                if should_escalate and self.bus is not None:
                    self.bus.publish(
                        Pulse(
                            id=f"budget-exceeded-{space_id}-{window_id}",
                            space_id=space_id,
                            type="space.budget.exceeded",
                            severity=Severity.CRITICAL,
                            source="admission_controller",
                            timestamp=datetime.now(timezone.utc),
                            payload={
                                "policy_mode": "hard_stop",
                                "remaining_budget": remaining,
                                "window_id": window_id,
                            },
                            taint=False,
                            correlation_id=f"corr-budget-{space_id}",
                            parent_pulse_id=None,
                        )
                    )

                return CapabilityResponse(
                    status="denied",
                    error="budget_exhausted",
                    cost=0.0,
                )

            # MODE B: approval_required (soft threshold reached)
            if policy == "approval_required" and initial > 0:
                spent = initial - remaining
                if (spent / initial) >= soft_thresh:
                    window_id = self.windows.get_or_create_window(space_id)
                    should_escalate = self.windows.should_escalate_budget(space_id, window_id)
                    if should_escalate and self.bus is not None:
                        self.bus.publish(
                            Pulse(
                                id=f"budget-warning-{space_id}-{window_id}",
                                space_id=space_id,
                                type="space.budget.exceeded",
                                severity=Severity.WARNING,
                                source="admission_controller",
                                timestamp=datetime.now(timezone.utc),
                                payload={
                                    "policy_mode": "approval_required",
                                    "remaining_budget": remaining,
                                    "window_id": window_id,
                                },
                                taint=False,
                                correlation_id=f"corr-budget-{space_id}",
                                parent_pulse_id=None,
                            )
                        )
                    return CapabilityResponse(
                        status="denied",
                        error="approval_required",
                        cost=0.0,
                    )

            # Atomically reserve budget if requested (SCHED-004)
            if req_cost > 0:
                resv_key = request.idempotency_key or f"resv-{request.requester_id}-{time.time()}"
                self._budgets[space_id] = remaining - req_cost
                self._reservations[(space_id, resv_key)] = req_cost

            # MODE C: degraded mode (or normal admission within budget)
            result_payload: dict[str, Any] = {"degraded": (policy == "degraded")}
            if resv_key is not None:
                result_payload["reservation_id"] = resv_key
            return CapabilityResponse(
                status="ok",
                result=result_payload,
                cost=req_cost,
            )

