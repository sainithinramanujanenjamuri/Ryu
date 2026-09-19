"""Plan Reconciler: The convergence loop executing Monitor -> Adapter -> Kernel CAS.

spec §4 (Monitor + Adapter), ROADMAP Phase 4, ADR-0008, OPEN-008 — Phase 4
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol

from ryu.pulse_bus.pulse import Pulse, Severity

from core.orchestrator.adapter import Adapter
from core.orchestrator.monitor import Monitor
from core.plans.delta import PlanDelta

logger = logging.getLogger(__name__)


class KernelPlanAuthority(Protocol):
    def commit_plan_delta(
        self, delta: PlanDelta, proposal_id: str | None = None
    ) -> tuple[bool, int, str | None]: ...

    def get_plan_version(self) -> int: ...


class PulsePublisher(Protocol):
    def publish(self, pulse: Pulse) -> Pulse: ...


@dataclass
class ReconcileResult:
    """Outcome of a single reconciliation cycle."""

    reconciled: bool
    action_taken: str  # "none", "retried", "reassigned", "pruned", "escalated", "rebased"
    plan_version: int
    details: dict[str, Any]


class PlanReconciler:
    """Coordinates execution convergence by matching actual Monitor state to desired plan.

    Invariants:
    - Never mutates plan state directly; all changes must pass Kernel Plan CAS.
    - Stale proposals are rejected by the Kernel with `plan.version.superseded`.
    - Bounded rebase attempts (default 3) per ADR-0003.
    - Terminal errors (permission denied, budget exceeded) escalate immediately (Law 6).
    """

    def __init__(
        self,
        space_id: str,
        monitor: Monitor,
        adapter: Adapter,
        kernel: KernelPlanAuthority,
        bus: PulsePublisher | None = None,
        max_rebases: int = 3,
    ) -> None:
        self.space_id = space_id
        self.monitor = monitor
        self.adapter = adapter
        self.kernel = kernel
        self.bus = bus
        self.max_rebases = max_rebases

    def reconcile_task_failure(
        self,
        task_id: str,
        error_class: str,
        message: str = "",
        correlation_id: str | None = None,
    ) -> ReconcileResult:
        """Handle task failure per the §10 Failure Taxonomy and escalate or replan."""
        current_ver = self.kernel.get_plan_version()

        # 1. Transient Failures: Bounded retry
        if error_class.startswith("transient."):
            attempts = self.monitor.state.task_attempts.get(task_id, 0)
            if attempts < 3:
                # Local retry with same idempotency key
                new_attempt = attempts + 1
                self.monitor.state.task_attempts[task_id] = new_attempt
                if self.bus is not None:
                    retry_pulse = Pulse(
                        id=f"pulse-task-retried-{task_id}-{new_attempt}",
                        space_id=self.space_id,
                        type="task.retried",
                        severity=Severity.WARNING,
                        source="reconciler",
                        correlation_id=correlation_id or f"corr-{self.space_id}-{task_id}",
                        payload={
                            "task_id": task_id,
                            "attempt": new_attempt,
                            "error_class": error_class,
                        },
                        timestamp=datetime.now(timezone.utc),
                    )
                    self.bus.publish(retry_pulse)

                return ReconcileResult(
                    reconciled=True,
                    action_taken="retried",
                    plan_version=current_ver,
                    details={"attempt": new_attempt, "error_class": error_class},
                )
            else:
                # Retries exhausted -> Reassign to fallback role via PlanDelta
                delta = self.adapter.propose_reassignment(
                    base_version=current_ver,
                    target_node_id=task_id,
                    new_assignee="agent-fallback-replan",
                )
                success, new_ver = self.commit_delta_with_rebase(delta)
                return ReconcileResult(
                    reconciled=success,
                    action_taken="reassigned",
                    plan_version=new_ver,
                    details={"retries_exhausted": True, "target": task_id},
                )

        # 2. Terminal Failures requiring immediate escalation (no retries)
        if error_class in ("terminal.permission_denied", "terminal.budget_exceeded"):
            return ReconcileResult(
                reconciled=False,
                action_taken="escalated",
                plan_version=current_ver,
                details={
                    "escalation_target": "human_gate",
                    "error_class": error_class,
                    "reason": message,
                },
            )

        # 3. Terminal Failures that can be adapted (e.g. terminal.not_found -> remove optional node)
        delta = self.adapter.propose_node_removal(
            base_version=current_ver,
            target_node_id=task_id,
            reason=f"terminal_failure_{error_class}",
        )
        success, new_ver = self.commit_delta_with_rebase(delta)
        return ReconcileResult(
            reconciled=success,
            action_taken="pruned",
            plan_version=new_ver,
            details={"error_class": error_class, "pruned_node": task_id},
        )

    def commit_delta_with_rebase(
        self,
        delta: PlanDelta,
    ) -> tuple[bool, int]:
        """Commit a PlanDelta to the Kernel Plan CAS, automatically rebasing if superseded.

        Returns:
            (success: bool, final_version: int)
        """
        success, current_ver, _ = self.kernel.commit_plan_delta(delta)
        if success:
            return True, delta.resulting_version

        # Stale delta! Perform bounded rebasing up to self.max_rebases
        active_delta = delta
        for rebase_idx in range(1, self.max_rebases + 1):
            latest_ver = self.kernel.get_plan_version()
            rebased_delta = PlanDelta(
                space_id=self.space_id,
                base_version=latest_ver,
                resulting_version=latest_ver + 1,
                ops=active_delta.ops,
                delta_id=f"{active_delta.delta_id}-rebase-{rebase_idx}",
            )
            success, new_ver, _ = self.kernel.commit_plan_delta(rebased_delta)
            if success:
                return True, rebased_delta.resulting_version
            active_delta = rebased_delta

        return False, self.kernel.get_plan_version()
