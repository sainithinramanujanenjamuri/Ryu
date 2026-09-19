"""Space Orchestrator: Thin coordinator sequencing the 5 cognitive modules.

spec §4 (Space Orchestrator), ROADMAP Phase 4, ORCH-001 — Phase 4
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol

from ryu.pulse_bus.pulse import Pulse, Severity

from core.orchestrator.adapter import Adapter
from core.orchestrator.goal_analyzer import Command, GoalAnalyzer, GoalSpec
from core.orchestrator.monitor import Monitor
from core.orchestrator.planner import Planner
from core.orchestrator.reconciler import PlanReconciler, ReconcileResult
from core.orchestrator.team_builder import AssignmentTable, TeamBuilder
from core.plans.delta import PlanDelta
from core.plans.task_graph import TaskGraph
from core.resources.identity import ResourceIdentity
from core.resources.manager import ResourceAcquisitionResult, ResourceManager
from core.space.kernel import SpaceKernel

logger = logging.getLogger(__name__)


class SubscriptionHandle(Protocol):
    def unsubscribe(self) -> None: ...


class SubscribableBus(Protocol):
    def publish(self, pulse: Pulse) -> Pulse: ...

    def subscribe(self, callback: Any, pulse_type: str | None = None) -> Any: ...

    def unsubscribe(self, subscription: Any) -> None: ...


@dataclass(frozen=True)
class OrchestratorSession:
    """Active orchestration state for a specific Command."""

    command_id: str
    space_id: str
    goal_spec: GoalSpec
    task_graph: TaskGraph
    assignments: AssignmentTable
    created_at: datetime = datetime.now(timezone.utc)


class SpaceOrchestrator:
    """Thin coordinator for Space execution.

    Invariants (ADR-0008, §4):
    - Owns sequencing only; holds no duplicate policy, admission, or resource logic.
    - Constitutional subordination:
      - Plan Authority -> Space Kernel Plan CAS
      - Budget & Admission Authority -> Space Kernel Admission Control
      - Human Gate Authority -> Space Kernel Approver
      - Hardware/Lease Authority -> Resource Manager
      - Isolation Authority -> Space Kernel
    """

    def __init__(
        self,
        space_id: str,
        kernel: SpaceKernel,
        resource_mgr: ResourceManager,
        bus: SubscribableBus,
    ) -> None:
        self.space_id = space_id
        self.kernel = kernel
        self.resource_mgr = resource_mgr
        self.bus = bus

        # Verify Space alignment
        if kernel.space_id != space_id:
            raise PermissionError(
                f"Kernel space_id '{kernel.space_id}' does not match orchestrator '{space_id}'"
            )

        # Decomposed 5 cognitive sub-modules
        self.goal_analyzer = GoalAnalyzer(bus=self.bus)
        self.planner = Planner()
        self.team_builder = TeamBuilder(bus=self.bus)
        self.monitor = Monitor(space_id=space_id)
        self.adapter = Adapter(space_id=space_id, bus=self.bus)
        self.reconciler = PlanReconciler(
            space_id=space_id,
            monitor=self.monitor,
            adapter=self.adapter,
            kernel=self.kernel,
            bus=self.bus,
        )

        self._lock = threading.RLock()
        self._sessions: dict[str, OrchestratorSession] = {}

        # Wire monitor to receive all Space pulses
        self._subscription = self.bus.subscribe(self.monitor.handle_pulse)

    def submit_goal(self, command: Command) -> OrchestratorSession:
        """Process a human command into a GoalSpec, TaskGraph, and Team Assignment.

        Idempotency (ADR-0007):
        Duplicate submissions with the same command_id return the existing session
        without creating duplicate plans or emitting duplicate pulses.
        """
        with self._lock:
            # 1. Space Isolation Boundary Check (Law 1, SPACE-001)
            if command.space_id != self.space_id:
                raise PermissionError(
                    f"Cross-space goal submission rejected: command space '{command.space_id}' "
                    f"does not match orchestrator space '{self.space_id}'."
                )

            # 2. Idempotency check: return existing session if already planned
            if command.command_id in self._sessions:
                return self._sessions[command.command_id]

            # 3. Goal Analysis (Pure transformation -> goal.defined)
            goal_spec = self.goal_analyzer.analyze_goal(command)

            # 4. Planning (Proposed DAG -> uncommitted)
            proposed_plan = self.planner.plan_goal(goal_spec)

            # 5. Initialize / Synchronize Plan in Space Kernel
            # Note: SpaceKernel initializes an empty plan at plan_version 1. We register the nodes.
            kernel_graph = self.kernel.get_task_graph()
            if not kernel_graph.nodes:
                kernel_graph.nodes = list(proposed_plan.task_graph.nodes)

            # Publish plan.created Pulse
            correlation = command.correlation_id or f"corr-{command.command_id}"
            self.bus.publish(
                Pulse(
                    id=f"pulse-plan-created-{command.command_id}-v1",
                    space_id=self.space_id,
                    type="plan.created",
                    severity=Severity.INFO,
                    source="orchestrator",
                    correlation_id=correlation,
                    payload={
                        "plan_version": 1,
                        "task_count": len(proposed_plan.task_graph.nodes),
                    },
                    timestamp=datetime.now(timezone.utc),
                )
            )

            # 6. Team Building (Map nodes to roles -> task.assigned)
            assignments = self.team_builder.build_team(kernel_graph, goal_spec)

            # 7. Store and return session
            session = OrchestratorSession(
                command_id=command.command_id,
                space_id=self.space_id,
                goal_spec=goal_spec,
                task_graph=kernel_graph,
                assignments=assignments,
            )
            self._sessions[command.command_id] = session
            return session

    def request_resource_lease(
        self,
        requester_id: str,
        identity: ResourceIdentity,
        units: int = 1,
        duration_seconds: float = 60.0,
        idempotency_key: str | None = None,
    ) -> ResourceAcquisitionResult:
        """Request a hardware resource lease from the Resource Manager.

        Invariants (Law 2):
        The Orchestrator cannot grant leases. It must request them from the Resource Manager.
        """
        return self.resource_mgr.acquire(
            space_id=self.space_id,
            requester_id=requester_id,
            identity=identity,
            units=units,
            duration_seconds=duration_seconds,
            idempotency_key=idempotency_key,
        )

    def release_resource_lease(
        self,
        requester_id: str,
        lease_token: str,
    ) -> bool:
        """Release a held lease via the Resource Manager."""
        return self.resource_mgr.release(
            space_id=self.space_id,
            requester_id=requester_id,
            lease_token=lease_token,
        )

    def propose_plan_delta(self, delta: PlanDelta) -> tuple[bool, int]:
        """Submit a PlanDelta proposal to the Space Kernel's Plan CAS with rebase."""
        return self.reconciler.commit_delta_with_rebase(delta)

    def reconcile_failure(
        self,
        task_id: str,
        error_class: str,
        message: str = "",
        correlation_id: str | None = None,
    ) -> ReconcileResult:
        """Reconcile a task failure through the Reconciler."""
        return self.reconciler.reconcile_task_failure(
            task_id=task_id,
            error_class=error_class,
            message=message,
            correlation_id=correlation_id,
        )

    def get_session(self, command_id: str) -> OrchestratorSession | None:
        with self._lock:
            return self._sessions.get(command_id)

    def close(self) -> None:
        """Clean up bus subscriptions."""
        if hasattr(self.bus, "unsubscribe"):
            self.bus.unsubscribe(self._subscription)
