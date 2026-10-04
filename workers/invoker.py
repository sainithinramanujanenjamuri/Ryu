"""Runtime Worker Invoker for Phase 12.4.

Implements core.orchestrator.dispatch_model.WorkerInvokerProtocol to bridge
the deterministic Dispatcher in core/ to the concrete Worker Runtime in workers/.

Enforces:
- SCCA Law 1 (Space Isolation): Worker execution strictly bounded to Space.
- SCCA Law 2 (Capability Assignment): Workers execute admitted capabilities under valid lease.
- SCCA Law 6 (Deterministic Containment): Failures contain and escalate deterministically.
- Forward-only Taint: Output inherits input taint and cannot clear it.
- Secret Sanitization: Logs and outputs sanitized prior to return.
"""

from __future__ import annotations

import logging
from pathlib import Path

from ryu.pulse_bus.bus import PulseBus

from core.orchestrator.dispatch_model import (
    TaskExecutionRequest,
    TaskExecutionResult,
)
from core.resources.manager import ResourceManager
from node.runtime import NodeRuntime
from workers.base import BaseWorker, sanitize_text
from workers.browser.worker import BrowserWorker
from workers.contract import (
    ExecutionLimits,
    ExecutionRequest,
    SandboxPolicy,
    WorkerIdentity,
)
from workers.file.worker import FileWorker
from workers.node.worker import NodeWorker
from workers.python.worker import PythonWorker
from workers.repository.worker import RepositoryWorker
from workers.research.worker import ResearchWorker
from workers.shell.worker import ShellWorker
from workers.subagent.worker import SubagentWorker
from workers.test_runner.worker import TestRunnerWorker

logger = logging.getLogger(__name__)


class RuntimeWorkerInvoker:
    """Executes TaskExecutionRequests via concrete sandboxed Workers.

    Satisfies WorkerInvokerProtocol without placing any worker implementation
    or sandbox execution inside core/.
    """

    def __init__(
        self,
        bus: PulseBus | None = None,
        resource_manager: ResourceManager | None = None,
        node_runtime: NodeRuntime | None = None,
        base_working_dir: Path | str | None = None,
        workers: dict[str, BaseWorker] | None = None,
    ) -> None:
        self.bus = bus
        self.resource_manager = resource_manager
        self.node_runtime = node_runtime
        self.base_working_dir = Path(base_working_dir) if base_working_dir else None
        self._workers: dict[str, BaseWorker] = dict(workers or {})

    def register_worker(self, capability: str, worker: BaseWorker) -> None:
        """Register a preconfigured worker instance for an exact or prefix capability."""
        self._workers[capability] = worker

    def get_or_create_worker(self, capability: str, space_id: str) -> BaseWorker | None:
        """Retrieve a registered worker or instantiate a standard capability worker."""
        # 1. Exact match in registered workers
        if capability in self._workers:
            return self._workers[capability]

        # 2. Prefix match in registered workers (e.g. "mcp.server.*")
        for cap, w in self._workers.items():
            if cap.endswith(".*") and capability.startswith(cap[:-1]):
                return w

        # 3. Standard built-in workers instantiated for this space
        if capability.startswith("python.") or capability == "python":
            return PythonWorker(
                identity=WorkerIdentity(
                    worker_id=f"python-worker-{space_id}",
                    capability=capability,
                    space_id=space_id,
                ),
                bus=self.bus,
                resource_manager=self.resource_manager,
                base_working_dir=self.base_working_dir,
            )

        if capability.startswith("terminal.") or capability.startswith("shell."):
            return ShellWorker(
                identity=WorkerIdentity(
                    worker_id=f"shell-worker-{space_id}",
                    capability=capability,
                    space_id=space_id,
                ),
                bus=self.bus,
                resource_manager=self.resource_manager,
                base_working_dir=self.base_working_dir,
            )

        if capability.startswith("file.") or capability == "file.*":
            return FileWorker(
                identity=WorkerIdentity(
                    worker_id=f"file-worker-{space_id}",
                    capability="file.*",
                    space_id=space_id,
                ),
                bus=self.bus,
                resource_manager=self.resource_manager,
            )

        if capability.startswith("browser."):
            return BrowserWorker(
                identity=WorkerIdentity(
                    worker_id=f"browser-worker-{space_id}",
                    capability=capability,
                    space_id=space_id,
                ),
                bus=self.bus,
                resource_manager=self.resource_manager,
            )

        if capability.startswith("subagent."):
            return SubagentWorker(
                identity=WorkerIdentity(
                    worker_id=f"subagent-worker-{space_id}",
                    capability=capability,
                    space_id=space_id,
                ),
                bus=self.bus,
                resource_manager=self.resource_manager,
            )

        if capability.startswith("research.") or capability == "research":
            return ResearchWorker(
                identity=WorkerIdentity(
                    worker_id=f"research-worker-{space_id}",
                    capability=capability,
                    space_id=space_id,
                ),
                bus=self.bus,
                resource_manager=self.resource_manager,
                base_working_dir=self.base_working_dir,
            )

        if capability.startswith("repository.") or capability == "repository" or capability.startswith("repo."):
            return RepositoryWorker(
                identity=WorkerIdentity(
                    worker_id=f"repository-worker-{space_id}",
                    capability=capability,
                    space_id=space_id,
                ),
                bus=self.bus,
                resource_manager=self.resource_manager,
                base_working_dir=self.base_working_dir,
            )

        if capability.startswith("test.") or capability == "test" or capability.startswith("test_runner."):
            return TestRunnerWorker(
                identity=WorkerIdentity(
                    worker_id=f"test-runner-worker-{space_id}",
                    capability=capability,
                    space_id=space_id,
                ),
                bus=self.bus,
                resource_manager=self.resource_manager,
                base_working_dir=self.base_working_dir,
            )

        if capability.startswith("node."):
            if self.node_runtime is not None:
                return NodeWorker(
                    node_runtime=self.node_runtime,
                    identity=WorkerIdentity(
                        worker_id=f"node-worker-{space_id}",
                        capability=capability,
                        space_id=space_id,
                    ),
                    bus=self.bus,
                    resource_manager=self.resource_manager,
                )

        return None

    def invoke(self, request: TaskExecutionRequest) -> TaskExecutionResult:
        """Invoke worker under SCCA isolation, lease verification, and sandbox controls."""
        worker = self.get_or_create_worker(request.capability, request.space_id)
        if worker is None:
            return TaskExecutionResult(
                request_id=request.request_id,
                status="denied",
                task_id=request.task_id,
                space_id=request.space_id,
                plan_version=request.plan_version,
                error=f"No worker available for capability '{request.capability}'",
                error_class="terminal.permission_denied",
            )

        # Sandbox policy handling
        sb_policy = request.arguments.get("sandbox_policy")
        if not isinstance(sb_policy, SandboxPolicy):
            sb_policy = SandboxPolicy()

        exec_limits = ExecutionLimits(
            timeout_seconds=request.timeout_seconds,
        )

        exec_req = ExecutionRequest(
            request_id=request.request_id,
            correlation_id=f"corr-{request.space_id}-{request.task_id}",
            space_id=request.space_id,
            worker_id=worker.worker_id,
            capability=request.capability,
            arguments=dict(request.arguments),
            lease_id=request.lease_token,
            is_tainted=request.is_tainted,
            execution_limits=exec_limits,
            sandbox_policy=sb_policy,
            idempotency_key=request.idempotency_key,
            task_id=request.task_id,
            plan_id=request.plan_id,
            plan_version=request.plan_version,
            attempt=request.attempt,
        )

        try:
            res = worker.execute(exec_req)
        except Exception as exc:
            return TaskExecutionResult(
                request_id=request.request_id,
                status="failed",
                task_id=request.task_id,
                space_id=request.space_id,
                plan_version=request.plan_version,
                error=sanitize_text(str(exc)),
                error_class="terminal.invalid_params",
                taint=request.is_tainted,
            )

        # Forward-only taint: if input was tainted or result is tainted, taint is True
        effective_taint = res.taint or request.is_tainted

        # Collect artifact identifiers / paths / metadata
        artifact_refs = [
            art.to_dict() if hasattr(art, "to_dict") else (art.artifact_id or getattr(art, "path", str(art)))
            for art in res.artifacts
        ]

        duration = res.metrics.duration_seconds if res.metrics else 0.0
        err_msg = res.error.message if res.error else None
        err_cls = res.error.error_class if res.error else None
        details = dict(res.error.details) if res.error and res.error.details else {}

        return TaskExecutionResult(
            request_id=res.request_id,
            status=res.status,
            task_id=request.task_id,
            space_id=request.space_id,
            plan_version=request.plan_version,
            output_data=dict(res.output_data) if isinstance(res.output_data, dict) else ({"value": res.output_data} if res.output_data is not None else {}),
            artifacts=artifact_refs,
            taint=effective_taint,
            duration_seconds=duration,
            error=err_msg,
            error_class=err_cls,
            logs=list(res.logs),
            details=details,
        )
