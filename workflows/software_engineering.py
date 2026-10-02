"""Software Engineering Workflow — Phase 14.8.

Bounded, evidence-driven integration layer connecting Phase 14.2–14.7 capabilities
into a closed-loop software engineering execution workflow under ADR-0044.

Authority Boundaries (AGENTS.md §5, §6, SCCA Laws 1–6):
- SoftwareEngineeringWorkflow is a COORDINATOR / DELEGATOR, NOT an authority.
- CANNOT directly mutate plans: all plan changes submit PlanDelta via SpaceKernel.commit_plan_delta() (CAS).
- CANNOT directly modify repositories: all mutations route through AtomicPatchApplicator via RepositoryWorker.
- CANNOT execute arbitrary commands: tests strictly route through sandboxed TestRunnerWorker.
- CANNOT declare goals satisfied: goal satisfaction evaluated by DeterministicGoalEvaluator.
- CANNOT authorize capabilities or grant resources: coordinates with AdmissionControl / ResourceManager.
- CANNOT bypass SpaceKernel: all operations scoped to Space (Law 1).
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from ryu.pulse_bus.bus import PulseBus
from ryu.pulse_bus.pulse import Pulse, Severity

from core.orchestrator.dispatch_model import (
    ConvergenceDecision,
    ConvergenceEngine,
    ConvergenceProposal,
    DeterministicGoalEvaluator,
    EvidenceStatus,
    EvidenceType,
    GoalEvaluationResult,
    GoalEvaluationStatus,
    VerifiedExecutionEvidence,
)
from core.orchestrator.goal_analyzer import GoalSpec
from core.plans.delta import PlanDelta
from core.space.kernel import SpaceKernel
from core.space.repair_protocol import (
    MAX_REPAIR_ITERATIONS,
    RepairDiagnostic,
    RepairLoopHistory,
    RepairProposal,
    classify_failure,
    compute_repair_fingerprint,
    normalize_failure_trace,
    validate_repair_proposal,
)
from core.space.research_protocol import (
    ResearchSynthesis,
    SynthesisStatus,
)
from workers.contract import ExecutionRequest, WorkerIdentity
from workers.repository.worker import RepositoryWorker
from workers.research.synthesis import ResearchSynthesizer
from workers.research.worker import ResearchWorker
from workers.test_runner.worker import TestRunnerWorker

logger = logging.getLogger(__name__)


class WorkflowStatus(str, Enum):
    """Lifecycle status of the integrated software engineering workflow."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    ESCALATED = "ESCALATED"
    ABORTED = "ABORTED"
    INTERRUPTED = "INTERRUPTED"


@dataclass
class WorkflowExecutionResult:
    """Immutable outcome of an integrated software engineering workflow run."""

    workflow_id: str
    space_id: str
    goal_id: str
    status: WorkflowStatus
    initial_plan_version: int
    final_plan_version: int
    tasks_executed: list[str]
    evidence_collected: list[VerifiedExecutionEvidence]
    goal_evaluation: GoalEvaluationResult | None
    convergence_proposal: ConvergenceProposal | None
    repair_attempts: int
    patches_applied: list[str]
    tainted: bool
    provenance_chain: list[dict[str, Any]]
    checkpoints_created: list[str]
    error: str | None = None
    recorded_events: list[dict[str, Any]] = field(default_factory=list)
    research_synthesis: dict[str, Any] | None = None
    contradictions_detected: list[dict[str, Any]] = field(default_factory=list)


class SoftwareEngineeringWorkflow:
    """Bounded, evidence-driven closed-loop software engineering coordinator (Phase 14.8).

    Orchestrates the lifecycle:
      Human Goal -> Space Preconditions -> (Optional Research & Synthesis) ->
      Repository Inspection -> (Initial Patch) -> Sandboxed Test Execution ->
      Evidence Verification -> (Bounded Test-Repair Loop) ->
      Deterministic Goal Evaluation -> Convergence Engine -> Plan CAS.
    """

    def __init__(
        self,
        space_id: str,
        kernel: SpaceKernel,
        bus: PulseBus | None = None,
        engine: ConvergenceEngine | None = None,
        goal_evaluator: DeterministicGoalEvaluator | None = None,
        res_mgr: Any | None = None,
        research_worker: ResearchWorker | None = None,
        synthesizer: ResearchSynthesizer | None = None,
        repo_worker: RepositoryWorker | None = None,
        test_runner_worker: TestRunnerWorker | None = None,
        replay_mode: bool = False,
    ) -> None:
        if not space_id or not space_id.strip():
            raise ValueError("space_id must not be empty (SCCA Law 1)")
        self.space_id = space_id
        self.kernel = kernel
        self.bus = bus or getattr(kernel, "bus", None) or PulseBus()
        self.kernel.verify_space_identity(self.space_id)

        self.engine = engine or ConvergenceEngine(space_id=self.space_id, bus=self.bus, replay_mode=replay_mode)
        self.goal_evaluator = goal_evaluator or DeterministicGoalEvaluator()
        self.res_mgr = res_mgr
        self.replay_mode = replay_mode

        # Initialize workers if not injected
        self.research_worker = research_worker or ResearchWorker(
            identity=WorkerIdentity(worker_id=f"research-{self.space_id}", capability="research.retrieve", space_id=self.space_id),
            bus=self.bus,
        )
        self.synthesizer = synthesizer or ResearchSynthesizer()
        self.repo_worker = repo_worker or RepositoryWorker(
            identity=WorkerIdentity(worker_id=f"repo-{self.space_id}", capability="repository.*", space_id=self.space_id)
        )
        self.test_runner_worker = test_runner_worker or TestRunnerWorker(
            identity=WorkerIdentity(worker_id=f"runner-{self.space_id}", capability="test.execute", space_id=self.space_id)
        )

        # Workflow tracking state
        self.workflow_id = f"wf-{self.space_id}-{uuid.uuid4().hex[:8]}"
        self.tainted = False
        self.provenance_chain: list[dict[str, Any]] = []
        self.applied_patches: list[str] = []
        self.repair_history = RepairLoopHistory(space_id=self.space_id, task_id="repair-loop")
        self.recorded_events: list[dict[str, Any]] = []
        self.checkpoints: list[str] = []
        self.tasks_executed: list[str] = []
        self.evidence_collected: list[VerifiedExecutionEvidence] = []
        self.contradictions_detected: list[dict[str, Any]] = []
        self.last_synthesis: ResearchSynthesis | None = None

    def execute_goal(
        self,
        goal_spec: GoalSpec | Any,
        repo_dir: str | Path,
        context: dict[str, Any] | None = None,
    ) -> WorkflowExecutionResult:
        """Execute a human-defined software engineering goal through the closed loop.

        Args:
            goal_spec: Authoritative GoalSpec (Human-defined goal boundary).
            repo_dir: Path to the target repository root.
            context: Additional execution context and metadata.

        Returns:
            WorkflowExecutionResult with auditable evidence, provenance, and final status.
        """
        ctx = context or {}
        repo_path = Path(repo_dir).resolve()
        initial_plan_version = self.kernel.get_plan_version()

        # 1. Enforce Space Boundary (Law 1, Law 5)
        goal_space = getattr(goal_spec, "space_id", "")
        if goal_space != self.space_id:
            raise PermissionError(
                f"Cross-space workflow access denied: workflow is bound to '{self.space_id}', "
                f"received goal for space '{goal_space}'"
            )
        self.kernel.verify_space_identity(self.space_id)

        # Check cross-space evidence isolation (SCCA Law 1, SPACE-001, ADV-13)
        for ev in self.evidence_collected:
            if ev.space_id != self.space_id:
                raise PermissionError(
                    f"Cross-space evidence leakage detected: evidence space '{ev.space_id}' "
                    f"does not match workflow space '{self.space_id}'"
                )

        # 2. Check Resource / Budget Constraints (Law 2, Section 22)
        if hasattr(self.kernel, "admission"):
            adm = self.kernel.admission
            initial = getattr(adm, "_initial_budgets", {}).get(self.space_id, 0.0)
            remaining = adm.get_remaining_budget(self.space_id)
            policies = getattr(adm, "_policies", {})
            policy = policies.get(self.space_id, "hard_stop")
            if initial > 0.0 and policy == "hard_stop" and remaining <= 0:
                self._publish_pulse(
                    "space.budget.exceeded",
                    payload={
                        "policy_mode": "hard_stop",
                        "remaining_budget": float(remaining),
                        "window_id": f"win-{self.space_id}",
                    },
                    severity=Severity.CRITICAL,
                )
                return self._build_result(
                    goal_spec,
                    WorkflowStatus.FAILED,
                    initial_plan_version,
                    error=f"Space budget exceeded: remaining={remaining}",
                )

        # 3. Publish goal.defined pulse and record provenance
        goal_id = getattr(goal_spec, "goal_id", f"goal-{uuid.uuid4().hex[:6]}")
        goal_dict = goal_spec.to_dict() if hasattr(goal_spec, "to_dict") else asdict(goal_spec) if hasattr(goal_spec, "__dataclass_fields__") else {"objective": str(getattr(goal_spec, "objective", ""))}
        self._publish_pulse(
            "goal.defined",
            payload={
                "goal_id": goal_id,
                "goal_spec": goal_dict,
                "single_agent_eligible": getattr(goal_spec, "single_agent_eligible", True),
            },
            severity=Severity.INFO,
        )
        self._record_provenance("goal.defined", {"goal_id": goal_id, "objective": getattr(goal_spec, "objective", "")})

        # 4. Initialize Plan in SpaceKernel if plan version is 0
        if self.kernel.get_plan_version() == 0:
            initial_delta = PlanDelta(
                space_id=self.space_id,
                base_version=0,
                resulting_version=1,
                ops=[
                    {"op": "add", "target_node_id": "task-inspect", "capability": "repository.inspect", "state": "ready"},
                    {"op": "add", "target_node_id": "task-test", "capability": "test.execute", "state": "pending", "dependencies": ["task-inspect"]},
                ],
            )
            ok, new_ver, err = self.kernel.commit_plan_delta(initial_delta)
            if not ok:
                return self._build_result(goal_spec, WorkflowStatus.FAILED, initial_plan_version, error=f"Initial plan creation failed: {err}")

        # 5. Optional Research & Synthesis Stage (Phase 14.2 & Phase 14.7)
        research_sources = ctx.get("research_sources") or getattr(goal_spec, "metadata", {}).get("research_sources", [])
        research_query = ctx.get("research_query") or getattr(goal_spec, "metadata", {}).get("research_query", "")
        if research_sources or research_query:
            res_status, res_err = self._execute_research_stage(goal_id, research_query, research_sources)
            if not res_status:
                return self._build_result(goal_spec, WorkflowStatus.FAILED, initial_plan_version, error=res_err)
            self._save_checkpoint("after-research")

        # 6. Repository Inspection Stage (Phase 14.3)
        inspect_ok, inspect_err = self._execute_inspect_stage(repo_path)
        if not inspect_ok:
            return self._build_result(goal_spec, WorkflowStatus.FAILED, initial_plan_version, error=inspect_err)
        self._save_checkpoint("after-inspect")

        # 7. Initial Patch Application Stage (Phase 14.4 - if specified in goal or context)
        initial_patch = ctx.get("initial_patch") or getattr(goal_spec, "metadata", {}).get("initial_patch", None)
        if initial_patch:
            patch_id = ctx.get("patch_id") or getattr(goal_spec, "metadata", {}).get("patch_id") or f"patch-initial-{uuid.uuid4().hex[:6]}"
            target_files = ctx.get("target_files") or getattr(goal_spec, "metadata", {}).get("target_files", [])
            patch_ok, patch_err = self._execute_patch_stage(repo_path, initial_patch, patch_id, target_files)
            if not patch_ok:
                return self._build_result(goal_spec, WorkflowStatus.FAILED, initial_plan_version, error=patch_err)
            self._save_checkpoint("after-initial-patch")

        # 8. Test Execution Stage (Phase 14.5)
        test_args = ctx.get("test_args") or getattr(goal_spec, "metadata", {}).get("test_args", ["-v"])
        test_runner = ctx.get("test_runner") or getattr(goal_spec, "metadata", {}).get("test_runner", "pytest")
        test_ok, test_ev, test_data = self._execute_test_stage(repo_path, test_runner, test_args, task_id="task-test")
        self.evidence_collected.append(test_ev)

        # 9. Bounded Test-Repair Loop (Phase 14.6 - if test failed)
        if test_ev.exit_code != 0:
            repair_status, final_ev, repair_err = self._execute_repair_loop(
                goal_spec=goal_spec,
                repo_path=repo_path,
                initial_test_data=test_data,
                test_runner=test_runner,
                test_args=test_args,
                context=ctx,
            )
            if final_ev:
                self.evidence_collected.append(final_ev)
            goal_eval_res = self.goal_evaluator.evaluate(goal_spec, self.evidence_collected)
            if repair_status == WorkflowStatus.ESCALATED:
                return self._build_result(
                    goal_spec,
                    WorkflowStatus.ESCALATED,
                    initial_plan_version,
                    error=repair_err,
                    goal_eval=goal_eval_res,
                )
            if repair_status != WorkflowStatus.COMPLETED:
                return self._build_result(
                    goal_spec,
                    repair_status,
                    initial_plan_version,
                    error=repair_err,
                    goal_eval=goal_eval_res,
                )

        # 10. Deterministic Goal Evaluation (Section 14, Slice G)
        # External claims / LLM claims cannot override deterministic exit codes
        advisory_model_claim = ctx.get("advisory_model_assertion")
        if advisory_model_claim:
            # Model assertion is recorded as passive telemetry, never overriding deterministic evidence
            self._record_provenance("advisory_model_claim", {"claim": str(advisory_model_claim)})

        # Check cross-space evidence isolation (SCCA Law 1, SPACE-001, ADV-13)
        for ev in self.evidence_collected:
            if ev.space_id != self.space_id:
                raise PermissionError(
                    f"Cross-space evidence leakage detected: evidence space '{ev.space_id}' "
                    f"does not match workflow space '{self.space_id}'"
                )

        goal_eval_res = self.goal_evaluator.evaluate(goal_spec, self.evidence_collected)

        # 11. Final Convergence Decision via ConvergenceEngine (Section 15)
        convergence_proposal = self.engine.evaluate_and_propose(
            kernel=self.kernel,
            goal_spec=goal_spec,
            evidence=self.evidence_collected,
        )

        final_status = WorkflowStatus.COMPLETED if goal_eval_res.status == GoalEvaluationStatus.SATISFIED else WorkflowStatus.FAILED
        if convergence_proposal.decision == ConvergenceDecision.ESCALATE:
            final_status = WorkflowStatus.ESCALATED
        elif convergence_proposal.decision == ConvergenceDecision.ABORT:
            final_status = WorkflowStatus.ABORTED

        return self._build_result(
            goal_spec,
            final_status,
            initial_plan_version,
            goal_eval=goal_eval_res,
            proposal=convergence_proposal,
        )

    def _execute_research_stage(
        self,
        goal_id: str,
        query: str,
        sources: list[str],
    ) -> tuple[bool, str | None]:
        """Execute research retrieval and synthesis preserving taint and contradictions."""
        task_id = "task-research"
        self.tasks_executed.append(task_id)
        plan_ver = self.kernel.get_plan_version()

        retrieval_results = []
        for src in sources:
            req = ExecutionRequest(
                request_id=f"req-res-{uuid.uuid4().hex[:6]}",
                correlation_id=f"corr-res-{uuid.uuid4().hex[:6]}",
                space_id=self.space_id,
                worker_id=f"research-{self.space_id}",
                capability="research.retrieve",
                task_id=task_id,
                arguments={"query": query or "software investigation", "sources": [src]},
            )
            res = self.research_worker.execute(req)
            if res.status != "ok":
                return False, f"Research retrieval failed: {res.error}"
            retrieval_results.append(res)
            self.tainted = True  # External research introduces mandatory taint

            # Publish research.retrieved pulse
            prov_id = f"prov-src-{hashlib.sha256(src.encode()).hexdigest()[:8]}"
            self._publish_pulse(
                "research.retrieved",
                payload={
                    "source_location": src,
                    "source_type": "documentation",
                    "content_hash": hashlib.sha256(str(res.output_data).encode()).hexdigest(),
                    "provenance_id": prov_id,
                    "task_id": task_id,
                    "plan_version": plan_ver,
                },
                severity=Severity.INFO,
                taint=True,
            )

        # Synthesize via ResearchSynthesizer
        try:
            # Flatten raw retrieved items into ResearchResult objects if present
            raw_items = []
            for r in retrieval_results:
                if r.output_data and "results" in r.output_data:
                    raw_items.extend(r.output_data["results"])

            synthesis = self.synthesizer.synthesize(
                results=raw_items,
                space_id=self.space_id,
                task_id=task_id,
                plan_version=plan_ver,
                query=query,
            )
            self.last_synthesis = synthesis

            if synthesis.status == SynthesisStatus.CONTRADICTION:
                for conflict in synthesis.conflicts:
                    conflict_dict = {
                        "source_a": conflict.source_a_provenance_id,
                        "source_b": conflict.source_b_provenance_id,
                        "topic": conflict.topic,
                        "conflict_summary": f"Statement A: {conflict.statement_a} vs Statement B: {conflict.statement_b}",
                        "task_id": task_id,
                        "plan_version": plan_ver,
                    }
                    self.contradictions_detected.append(conflict_dict)
                    self._publish_pulse(
                        "research.conflict_detected",
                        payload=conflict_dict,
                        severity=Severity.WARNING,
                        taint=True,
                    )
            self._record_provenance("research.synthesized", {"synthesis_id": synthesis.synthesis_id, "taint": True})
            return True, None
        except Exception as e:
            return False, f"Research synthesis error: {e}"

    def _execute_inspect_stage(self, repo_path: Path) -> tuple[bool, str | None]:
        """Execute read-only repository inspection (REPO-001)."""
        task_id = "task-inspect"
        self.tasks_executed.append(task_id)

        req = ExecutionRequest(
            request_id=f"req-insp-{uuid.uuid4().hex[:6]}",
            correlation_id=f"corr-insp-{uuid.uuid4().hex[:6]}",
            space_id=self.space_id,
            worker_id=f"repo-{self.space_id}",
            capability="repository.inspect",
            task_id=task_id,
            arguments={"action": "inspect_tree", "repository_root": str(repo_path)},
        )
        res = self.repo_worker.execute(req)
        if res.status != "ok":
            return False, f"Repository inspection failed: {res.error}"

        self._record_provenance("repository.inspected", {"repo_path": str(repo_path), "status": res.status})
        return True, None

    def _execute_patch_stage(
        self,
        repo_path: Path,
        patch_diff: str,
        patch_id: str,
        target_files: list[str],
    ) -> tuple[bool, str | None]:
        """Execute atomic patch application via AtomicPatchApplicator (PATCH-001..005)."""
        task_id = f"task-patch-{patch_id}"
        self.tasks_executed.append(task_id)

        # Idempotency check: prevent duplicate patch application
        if patch_id in self.applied_patches:
            return True, None

        plan_ver = self.kernel.get_plan_version()
        req = ExecutionRequest(
            request_id=f"req-patch-{uuid.uuid4().hex[:6]}",
            correlation_id=f"corr-patch-{uuid.uuid4().hex[:6]}",
            space_id=self.space_id,
            worker_id=f"repo-{self.space_id}",
            capability="repository.patch",
            task_id=task_id,
            arguments={
                "action": "apply_patch",
                "repository_root": str(repo_path),
                "diff_text": patch_diff,
                "patch": patch_diff,
                "patch_id": patch_id,
                "target_files": target_files,
            },
        )
        res = self.repo_worker.execute(req)
        if res.status != "ok" or not res.output_data or res.output_data.get("state") not in ("applied", "verified"):
            return False, f"Patch application failed: {res.error or res.output_data}"

        self.applied_patches.append(patch_id)
        diff_lines = len(patch_diff.splitlines())
        before_hashes = res.output_data.get("before_hashes", {})
        after_hashes = res.output_data.get("after_hashes", {})

        self._publish_pulse(
            "repo.patch_applied",
            payload={
                "patch_id": patch_id,
                "target_files": target_files,
                "changed_line_count": diff_lines,
                "before_hashes": before_hashes,
                "after_hashes": after_hashes,
                "task_id": task_id,
                "plan_version": plan_ver,
            },
            severity=Severity.INFO,
            taint=self.tainted,
        )
        self._record_provenance("repo.patch_applied", {"patch_id": patch_id, "target_files": target_files})
        return True, None

    def _execute_test_stage(
        self,
        repo_path: Path,
        runner: str,
        test_args: list[str],
        task_id: str,
    ) -> tuple[bool, VerifiedExecutionEvidence, dict[str, Any]]:
        """Execute sandboxed test runner and extract structured evidence (EVIDENCE-001)."""
        self.tasks_executed.append(task_id)
        plan_ver = self.kernel.get_plan_version()

        req = ExecutionRequest(
            request_id=f"req-test-{uuid.uuid4().hex[:6]}",
            correlation_id=f"corr-test-{uuid.uuid4().hex[:6]}",
            space_id=self.space_id,
            worker_id=f"runner-{self.space_id}",
            capability="test.execute",
            task_id=task_id,
            arguments={
                "runner": runner,
                "arguments": test_args,
                "repository_root": str(repo_path),
            },
        )
        res = self.test_runner_worker.execute(req)
        out = res.output_data or {}
        exit_code = out.get("exit_code", -1 if res.status != "ok" else 0)
        total_tests = out.get("total_tests", 1)
        passed_tests = out.get("passed_tests", 0 if exit_code != 0 else total_tests)
        failed_tests = out.get("failed_tests", total_tests if exit_code != 0 else 0)
        skipped_tests = out.get("skipped_tests", 0)
        duration = float(out.get("duration_seconds", 0.1))

        # Emit test.executed pulse
        self._publish_pulse(
            "test.executed",
            payload={
                "total_tests": total_tests,
                "passed_tests": passed_tests,
                "failed_tests": failed_tests,
                "skipped_tests": skipped_tests,
                "exit_code": exit_code,
                "duration_seconds": duration,
                "task_id": task_id,
                "plan_version": plan_ver,
            },
            severity=Severity.INFO if exit_code == 0 else Severity.WARNING,
            taint=self.tainted,
        )

        ev = VerifiedExecutionEvidence(
            task_id=task_id,
            evidence_type=EvidenceType.PROCESS_EXIT.value,
            verified=True,
            exit_code=exit_code,
            duration_seconds=duration,
            status=EvidenceStatus.VERIFIED.value if exit_code == 0 else EvidenceStatus.INVALID.value,
            space_id=self.space_id,
            plan_version=plan_ver,
            tainted=self.tainted,
        )
        self._record_provenance("test.executed", {"task_id": task_id, "exit_code": exit_code})
        return exit_code == 0, ev, out

    def _execute_repair_loop(
        self,
        goal_spec: GoalSpec | Any,
        repo_path: Path,
        initial_test_data: dict[str, Any],
        test_runner: str,
        test_args: list[str],
        context: dict[str, Any],
    ) -> tuple[WorkflowStatus, VerifiedExecutionEvidence | None, str | None]:
        """Execute bounded test-repair loop up to MAX_REPAIR_ITERATIONS (REPAIR-001..004)."""
        current_test_data = initial_test_data
        final_evidence = None

        repair_patches = context.get("repair_patches") or getattr(goal_spec, "metadata", {}).get("repair_patches", [])

        while True:
            cur_iter = self.repair_history.current_iteration + 1

            # Check repair ceiling (REPAIR-001)
            if self.repair_history.is_limit_exceeded() or cur_iter > MAX_REPAIR_ITERATIONS:
                # Propose ESCALATE via ConvergenceEngine
                self.engine.evaluate_and_propose(
                    kernel=self.kernel,
                    goal_spec=goal_spec,
                    evidence=self.evidence_collected,
                    failed_task_id=f"repair-task-iter-{cur_iter - 1}",
                    error_class="repair_ceiling_exceeded",
                )
                return WorkflowStatus.ESCALATED, final_evidence, f"Repair ceiling exceeded (max {MAX_REPAIR_ITERATIONS} iterations reached)"

            # Step 1: Normalize failure trace & compute fingerprint (REPAIR-002)
            stdout_text = current_test_data.get("stdout", "") or current_test_data.get("error_message", "")
            stderr_text = current_test_data.get("stderr", "")
            exit_code = int(current_test_data.get("exit_code", 1))
            norm_trace = normalize_failure_trace(stdout_text or "AssertionError: test failure")
            failed_task_id = "task-test"
            fp = compute_repair_fingerprint(self.space_id, failed_task_id, norm_trace)

            # Check for repeated failure fingerprint or oscillation (REPAIR-002)
            if self.repair_history.is_loop_detected(fp):
                self.engine.evaluate_and_propose(
                    kernel=self.kernel,
                    goal_spec=goal_spec,
                    evidence=self.evidence_collected,
                    failed_task_id=failed_task_id,
                    error_class="repeated_failure_fingerprint",
                )
                return WorkflowStatus.ESCALATED, final_evidence, f"Repeated failure fingerprint or oscillation detected: {fp}"

            # Step 2: Formulate RepairDiagnostic
            diag = RepairDiagnostic(
                space_id=self.space_id,
                plan_version=self.kernel.get_plan_version(),
                task_id=failed_task_id,
                failure_class=classify_failure(exit_code=exit_code, stdout=stdout_text, stderr=stderr_text),
                normalized_failure_trace=norm_trace,
                failure_fingerprint=fp,
            )

            # Step 3: Formulate RepairProposal
            # Pick next repair patch from context or generate bounded proposal
            patch_idx = cur_iter - 1
            if patch_idx < len(repair_patches):
                patch_spec = repair_patches[patch_idx]
                patch_diff = patch_spec.get("patch", "")
                patch_id = patch_spec.get("patch_id", f"repair-patch-{cur_iter}")
                target_files = tuple(patch_spec.get("target_files", ["calc.py"]))
            else:
                # Fallback proposal
                patch_diff = context.get("repair_patch", "")
                patch_id = f"repair-patch-{cur_iter}"
                target_files = tuple(context.get("target_files", ["calc.py"]))

            repair_prop = RepairProposal(
                space_id=self.space_id,
                plan_version=self.kernel.get_plan_version(),
                task_id=failed_task_id,
                failure_fingerprint=fp,
                iteration=cur_iter,
                target_files=target_files,
                proposed_patch=patch_diff,
                patch_id=patch_id,
            )
            validate_repair_proposal(repair_prop)

            # Record iteration into durable history
            self.repair_history = self.repair_history.record_iteration(fingerprint=fp, patch_id=patch_id)

            # Step 4: Propose REPLAN via ConvergenceEngine
            prop = self.engine.evaluate_and_propose(
                kernel=self.kernel,
                goal_spec=goal_spec,
                evidence=self.evidence_collected,
                failed_task_id=failed_task_id,
                error_class="assertion_failure",
                repair_diagnostic=diag,
                repair_proposal=repair_prop,
            )

            if prop.decision == ConvergenceDecision.ESCALATE:
                return WorkflowStatus.ESCALATED, final_evidence, "ConvergenceEngine requested escalation during repair"
            if prop.decision != ConvergenceDecision.REPLAN:
                return WorkflowStatus.FAILED, final_evidence, f"Unexpected convergence decision: {prop.decision}"

            # Step 5: Commit PlanDelta via CAS
            ok, new_ver, err = self.engine.apply_proposal(prop, self.kernel)
            if not ok:
                return WorkflowStatus.FAILED, final_evidence, f"PlanDelta CAS commit failed during repair: {err}"

            # Step 6: Apply proposed repair patch to repository
            patch_ok, patch_err = self._execute_patch_stage(repo_path, patch_diff, patch_id, list(target_files))
            if not patch_ok:
                return WorkflowStatus.FAILED, final_evidence, f"Failed applying repair patch: {patch_err}"

            self._save_checkpoint(f"after-repair-patch-iter-{cur_iter}")

            # Step 7: Retest via TestRunnerWorker
            retest_task = f"retest-task-iter-{cur_iter}"
            test_ok, final_evidence, current_test_data = self._execute_test_stage(
                repo_path=repo_path,
                runner=test_runner,
                test_args=test_args,
                task_id=retest_task,
            )

            if test_ok:
                # Retest passed! Repair succeeded.
                return WorkflowStatus.COMPLETED, final_evidence, None

            # Retest failed — continue loop to next iteration or ceiling

    def resume_from_checkpoint(
        self,
        checkpoint_data: dict[str, Any],
        goal_spec: GoalSpec | Any,
        repo_dir: str | Path,
        context: dict[str, Any] | None = None,
    ) -> WorkflowExecutionResult:
        """Resume workflow execution from a durable checkpoint (KERNEL-006, Section 18)."""
        incoming_space = checkpoint_data.get("space_id", "")
        self.kernel.verify_space_identity(incoming_space)

        # Restore SpaceKernel authoritative state
        self.kernel.restore_checkpoint(checkpoint_data)

        # Restore workflow metadata
        self.applied_patches = list(checkpoint_data.get("metadata", {}).get("applied_patches", []))
        self.provenance_chain = list(checkpoint_data.get("metadata", {}).get("provenance_chain", []))
        self.tainted = bool(checkpoint_data.get("metadata", {}).get("tainted", False))

        # Re-execute or continue workflow deterministically
        return self.execute_goal(goal_spec, repo_dir, context=context)

    def replay(
        self,
        recorded_events: list[dict[str, Any]],
        goal_spec: GoalSpec | Any,
    ) -> WorkflowExecutionResult:
        """Deterministic replay of recorded execution trace without real I/O side effects (Section 19).

        Invariants:
        - ZERO live network requests.
        - ZERO disk mutations or patches applied.
        - ZERO subprocess test executions.
        - Verifies control-flow equivalence against recorded trace.
        """
        self.kernel.verify_space_identity(self.space_id)
        replayed_evidence: list[VerifiedExecutionEvidence] = []
        replayed_patches: list[str] = []

        for evt in recorded_events:
            event_type = evt.get("type", "")
            payload = evt.get("payload", {})
            if event_type == "repo.patch_applied":
                replayed_patches.append(payload.get("patch_id", ""))
            elif event_type == "test.executed":
                ev = VerifiedExecutionEvidence(
                    task_id=payload.get("task_id", "replayed-task"),
                    evidence_type=EvidenceType.PROCESS_EXIT.value,
                    verified=True,
                    exit_code=payload.get("exit_code", 0),
                    duration_seconds=max(float(payload.get("duration_seconds", 0.1)), 0.1),
                    status=EvidenceStatus.VERIFIED.value if payload.get("exit_code", 0) == 0 else EvidenceStatus.INVALID.value,
                    space_id=self.space_id,
                    plan_version=payload.get("plan_version", 1),
                    tainted=bool(evt.get("taint", False)),
                )
                replayed_evidence.append(ev)

        # Re-evaluate goal deterministically
        goal_eval = self.goal_evaluator.evaluate(goal_spec, replayed_evidence)
        proposal = self.engine.evaluate_and_propose(
            kernel=self.kernel,
            goal_spec=goal_spec,
            evidence=replayed_evidence,
        )

        status = WorkflowStatus.COMPLETED if goal_eval.status == GoalEvaluationStatus.SATISFIED else WorkflowStatus.FAILED
        if proposal.decision == ConvergenceDecision.ESCALATE:
            status = WorkflowStatus.ESCALATED

        return WorkflowExecutionResult(
            workflow_id=f"replay-{self.workflow_id}",
            space_id=self.space_id,
            goal_id=getattr(goal_spec, "goal_id", "unknown"),
            status=status,
            initial_plan_version=1,
            final_plan_version=self.kernel.get_plan_version(),
            tasks_executed=[e.task_id for e in replayed_evidence],
            evidence_collected=replayed_evidence,
            goal_evaluation=goal_eval,
            convergence_proposal=proposal,
            repair_attempts=0,
            patches_applied=replayed_patches,
            tainted=any(e.tainted for e in replayed_evidence),
            provenance_chain=[{"stage": "replay", "events_count": len(recorded_events)}],
            checkpoints_created=[],
            recorded_events=recorded_events,
        )

    def _save_checkpoint(self, stage_label: str) -> str:
        """Create a durable checkpoint of kernel state and workflow progress."""
        cid = f"chk-{self.space_id}-{stage_label}-{len(self.checkpoints) + 1}"
        chk_data = self.kernel.create_checkpoint(cid)
        chk_data["metadata"] = {
            "workflow_id": self.workflow_id,
            "applied_patches": list(self.applied_patches),
            "provenance_chain": list(self.provenance_chain),
            "tainted": self.tainted,
            "stage_label": stage_label,
        }
        self.checkpoints.append(cid)
        return cid

    def _publish_pulse(
        self,
        pulse_type: str,
        payload: dict[str, Any],
        severity: Severity = Severity.INFO,
        taint: bool = False,
        correlation_id: str | None = None,
    ) -> Pulse:
        """Publish a typed pulse through the configured pulse bus."""
        p = Pulse(
            space_id=self.space_id,
            type=pulse_type,
            source=f"workflow.{self.workflow_id}",
            payload=payload,
            severity=severity,
            correlation_id=correlation_id or f"corr-{self.workflow_id}",
            taint=taint or self.tainted,
        )
        self.bus.publish(p)
        self.recorded_events.append({"type": pulse_type, "payload": copy.deepcopy(payload), "taint": p.taint})
        return p

    def _record_provenance(self, stage: str, data: dict[str, Any]) -> None:
        """Append an entry to the cryptographic provenance log."""
        prev_hash = self.provenance_chain[-1]["record_hash"] if self.provenance_chain else "0" * 64
        record_content = f"{stage}:{json.dumps(data, sort_keys=True)}:{prev_hash}"
        record_hash = hashlib.sha256(record_content.encode("utf-8")).hexdigest()
        self.provenance_chain.append({
            "stage": stage,
            "data": data,
            "prev_hash": prev_hash,
            "record_hash": record_hash,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "space_id": self.space_id,
            "taint": self.tainted,
        })

    def _build_result(
        self,
        goal_spec: Any,
        status: WorkflowStatus,
        initial_ver: int,
        error: str | None = None,
        goal_eval: GoalEvaluationResult | None = None,
        proposal: ConvergenceProposal | None = None,
    ) -> WorkflowExecutionResult:
        """Construct the authoritative workflow execution result."""
        return WorkflowExecutionResult(
            workflow_id=self.workflow_id,
            space_id=self.space_id,
            goal_id=getattr(goal_spec, "goal_id", "goal-unknown"),
            status=status,
            initial_plan_version=initial_ver,
            final_plan_version=self.kernel.get_plan_version(),
            tasks_executed=list(self.tasks_executed),
            evidence_collected=list(self.evidence_collected),
            goal_evaluation=goal_eval,
            convergence_proposal=proposal,
            repair_attempts=self.repair_history.current_iteration,
            patches_applied=list(self.applied_patches),
            tainted=self.tainted,
            provenance_chain=list(self.provenance_chain),
            checkpoints_created=list(self.checkpoints),
            error=error,
            recorded_events=list(self.recorded_events),
            contradictions_detected=list(self.contradictions_detected),
        )
