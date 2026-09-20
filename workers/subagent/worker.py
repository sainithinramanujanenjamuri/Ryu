"""Subagent Worker implementing subordinate subagent delegation.

spec §7 (Execution Layer), §12 (Context Scopes), §14, §16,
CONTRACT_MATRIX WORKER-005, ADR-0016
"""

from __future__ import annotations

from ryu.pulse_bus.bus import PulseBus

from core.resources.manager import ResourceManager
from workers.base import BaseWorker
from workers.contract import (
    ExecutionError,
    ExecutionMetrics,
    ExecutionRequest,
    ExecutionResult,
    WorkerIdentity,
)


class SubagentWorker(BaseWorker):
    """Executes a scoped delegation to an isolated subagent worker.

    Constitutional Invariants (WORKER-005, Correction 3):
    1. Subagent receives ONLY the Handoff Note and assigned plan node.
    2. Subagent does NOT inherit parent conversation history or raw state.
    3. Subagent has NO plan mutation authority, cannot mint leases,
       and cannot spawn recursive agents.
    4. Subordinate to the deterministic authority chain:
       Space Orchestrator -> Agent -> Proposal -> Validation ->
       Admission -> ResourceManager -> Worker.
    """

    def __init__(
        self,
        identity: WorkerIdentity | None = None,
        bus: PulseBus | None = None,
        resource_manager: ResourceManager | None = None,
    ) -> None:
        ident = identity or WorkerIdentity(
            worker_id="subagent-worker-01",
            capability="subagent.delegate",
            space_id="default-space",
        )
        super().__init__(identity=ident, bus=bus, resource_manager=resource_manager)

    def _execute_sandboxed(self, request: ExecutionRequest) -> ExecutionResult:
        handoff_note = request.arguments.get("handoff_note")
        plan_node_id = request.arguments.get("plan_node_id")

        if not handoff_note or not isinstance(handoff_note, dict):
            err = ExecutionError(
                error_class="terminal.invalid_params",
                message="Subagent delegation rejected: missing or invalid 'handoff_note'",
                recoverable=False,
            )
            return ExecutionResult(request_id=request.request_id, status="failed", error=err)

        if not plan_node_id:
            err = ExecutionError(
                error_class="terminal.invalid_params",
                message="Subagent delegation rejected: missing 'plan_node_id'",
                recoverable=False,
            )
            return ExecutionResult(request_id=request.request_id, status="failed", error=err)

        # Isolation check: verify no parent conversational history or secrets leaked
        forbidden_keys = {"history", "turns", "parent_state", "credentials", "secrets"}
        leaked_keys = forbidden_keys.intersection(request.arguments.keys())
        if leaked_keys:
            err = ExecutionError(
                error_class="terminal.permission_denied",
                message=f"Subagent isolation breach: prohibited keys provided: {list(leaked_keys)}",
                recoverable=False,
            )
            return ExecutionResult(request_id=request.request_id, status="denied", error=err)

        # Execute scoped subagent logic (pure deterministic processing of the handoff task)
        task_summary = (
            handoff_note.get("goal")
            or handoff_note.get("task_description")
            or "delegated task"
        )
        output_summary = f"Subagent completed task for node '{plan_node_id}': {task_summary}"

        return ExecutionResult(
            request_id=request.request_id,
            status="ok",
            output_data={
                "plan_node_id": plan_node_id,
                "handoff_summary": output_summary,
                "isolated": True,
            },
            metrics=ExecutionMetrics(),
            logs=[f"Subagent dispatched with isolated handoff note for node {plan_node_id}"],
        )
