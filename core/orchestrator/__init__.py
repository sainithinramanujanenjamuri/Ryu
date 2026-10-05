"""RYU AI Space Orchestrator subsystem.

Implements the thin coordinator and decomposed cognitive sub-modules per
docs/Architecture §4, §16, and ROADMAP.md Phase 4.
"""

from core.orchestrator.adapter import Adapter
from core.orchestrator.dispatch_model import (
    ConvergenceDecision,
    ConvergenceEngine,
    ConvergenceProposal,
    CrossSpaceViolationError,
    DeterministicDispatcher,
    DeterministicGoalEvaluator,
    DispatchAction,
    DispatchAttempt,
    DispatchDecision,
    DispatchExecutionResult,
    EvidenceStatus,
    EvidenceType,
    EvidenceVerificationResult,
    GoalEvaluationResult,
    GoalEvaluationStatus,
    GoalEvaluatorProtocol,
    SpaceKernelAuthorityProtocol,
    TaskCompletionResult,
    TaskDispatcherProtocol,
    TaskExecutionRequest,
    TaskExecutionResult,
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
from core.orchestrator.scheduler import (
    ConcurrentDAGScheduler,
    SchedulerConfig,
    TaskCandidate,
)
from core.orchestrator.team_builder import AssignmentTable, TaskAssignment, TeamBuilder

__all__ = [
    "Adapter",
    "AssignmentTable",
    "Command",
    "ConcurrentDAGScheduler",
    "ConvergenceDecision",
    "ConvergenceEngine",
    "ConvergenceProposal",
    "CrossSpaceViolationError",
    "DeterministicDispatcher",
    "DeterministicGoalEvaluator",
    "DispatchAction",
    "DispatchAttempt",
    "DispatchDecision",
    "DispatchExecutionResult",
    "EvidenceStatus",
    "EvidenceType",
    "EvidenceVerificationResult",
    "GoalAnalyzer",
    "GoalEvaluationResult",
    "GoalEvaluationStatus",
    "GoalEvaluatorProtocol",
    "GoalSpec",
    "Monitor",
    "OrchestratorSession",
    "PlanReconciler",
    "Planner",
    "ProposedPlan",
    "ReconcileResult",
    "SchedulerConfig",
    "SpaceKernelAuthorityProtocol",
    "SpaceOrchestrator",
    "TaskAssignment",
    "TaskCandidate",
    "TaskCompletionResult",
    "TaskDispatcherProtocol",
    "TaskExecutionRequest",
    "TaskExecutionResult",
    "TaskPipelineResult",
    "TeamBuilder",
    "TimelineState",
    "VerifiedExecutionEvidence",
    "WorkerInvokerProtocol",
    "compute_dispatch_idempotency_key",
]

