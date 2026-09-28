"""RYU AI Space Orchestrator subsystem.

Implements the thin coordinator and decomposed cognitive sub-modules per
docs/Architecture §4, §16, and ROADMAP.md Phase 4.
"""

from core.orchestrator.adapter import Adapter
from core.orchestrator.dispatch_model import (
    CrossSpaceViolationError,
    DeterministicDispatcher,
    DispatchAction,
    DispatchAttempt,
    DispatchDecision,
    GoalEvaluatorProtocol,
    SpaceKernelAuthorityProtocol,
    TaskDispatcherProtocol,
    TaskPipelineResult,
    VerifiedExecutionEvidence,
    WorkerInvokerProtocol,
    compute_dispatch_idempotency_key,
)
from core.orchestrator.goal_analyzer import Command, GoalAnalyzer, GoalSpec
from core.orchestrator.monitor import Monitor, TimelineState
from core.orchestrator.orchestrator import OrchestratorSession, SpaceOrchestrator
from core.orchestrator.planner import Planner, ProposedPlan
from core.orchestrator.reconciler import PlanReconciler, ReconcileResult
from core.orchestrator.team_builder import AssignmentTable, TaskAssignment, TeamBuilder

__all__ = [
    "Adapter",
    "AssignmentTable",
    "Command",
    "CrossSpaceViolationError",
    "DeterministicDispatcher",
    "DispatchAction",
    "DispatchAttempt",
    "DispatchDecision",
    "GoalAnalyzer",
    "GoalEvaluatorProtocol",
    "GoalSpec",
    "Monitor",
    "OrchestratorSession",
    "PlanReconciler",
    "Planner",
    "ProposedPlan",
    "ReconcileResult",
    "SpaceKernelAuthorityProtocol",
    "SpaceOrchestrator",
    "TaskAssignment",
    "TaskDispatcherProtocol",
    "TaskPipelineResult",
    "TeamBuilder",
    "TimelineState",
    "VerifiedExecutionEvidence",
    "WorkerInvokerProtocol",
    "compute_dispatch_idempotency_key",
]

