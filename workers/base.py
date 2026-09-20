"""Base Worker implementation and deterministic lifecycle management.

spec §7 (Execution Layer), §10 (Prompt-Injection & Taint Model), §14, §16,
CONTRACT_MATRIX WORKER-001..WORKER-005, TAINT-001, SECRET-004 — Phase 6
ADR-0013, ADR-0014, ADR-0015, ADR-0016
"""

from __future__ import annotations

import logging
import re
import threading
import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any

from ryu.pulse_bus.bus import PulseBus
from ryu.pulse_bus.pulse import Pulse, Severity

from core.resources.manager import ResourceManager
from workers.contract import (
    ExecutionError,
    ExecutionMetrics,
    ExecutionRequest,
    ExecutionResult,
    WorkerIdentity,
    WorkerState,
    map_error_to_failure_taxonomy,
)

logger = logging.getLogger(__name__)

# Secret sanitization pattern (scans for secret:// URIs, tokens, keys)
_SECRET_PATTERN = re.compile(
    r"(secret://[^\s\"'>]+|(?:bearer|token|key|password|secret)[\s:=]+['\"]?([A-Za-z0-9_\-\.]{8,})['\"]?)",
    re.IGNORECASE,
)


def sanitize_text(text: str) -> str:
    """Redact secrets and sensitive tokens from strings."""
    if not text:
        return text

    def _replace(match: re.Match[str]) -> str:
        matched = match.group(0)
        if matched.lower().startswith("secret://"):
            # URI is fine, but if it looks like a resolved credential, mask it
            return matched
        parts = re.split(r"([\s:=]+)", matched, maxsplit=1)
        if len(parts) >= 3:
            return f"{parts[0]}{parts[1]}[REDACTED]"
        return "[REDACTED]"

    return _SECRET_PATTERN.sub(_replace, text)


def sanitize_value(val: Any) -> Any:
    """Recursively sanitize strings inside nested dicts/lists."""
    if isinstance(val, str):
        return sanitize_text(val)
    elif isinstance(val, dict):
        return {k: sanitize_value(v) for k, v in val.items()}
    elif isinstance(val, list):
        return [sanitize_value(v) for v in val]
    return val


class BaseWorker(ABC):
    """Abstract base class for all RYU capability Workers.

    Enforces deterministic lifecycle transitions, mandatory lease validation,
    Space isolation, typed Pulse publication, and non-authority boundaries.
    """

    def __init__(
        self,
        identity: WorkerIdentity,
        bus: PulseBus | None = None,
        resource_manager: ResourceManager | None = None,
    ) -> None:
        self.identity = identity
        self.bus = bus
        self.resource_manager = resource_manager
        self.state = WorkerState.CREATED
        self._lock = threading.RLock()
        self._current_request: ExecutionRequest | None = None
        self._cancelled = False
        self._transition_to(WorkerState.READY)

    @property
    def worker_id(self) -> str:
        return self.identity.worker_id

    @property
    def capability(self) -> str:
        return self.identity.capability

    @property
    def space_id(self) -> str:
        return self.identity.space_id

    def _is_capability_supported(self, requested_capability: str) -> bool:
        """Check if requested capability is supported by this worker."""
        if requested_capability == self.capability:
            return True
        if self.capability.endswith(".*") and requested_capability.startswith(self.capability[:-1]):
            return True
        return False

    def _transition_to(self, new_state: WorkerState) -> None:
        """Atomically transition worker state."""
        with self._lock:
            old_state = self.state
            self.state = new_state
            logger.debug(
                "Worker %s transitioned from %s to %s",
                self.worker_id,
                old_state.value,
                new_state.value,
            )

    def cancel(self) -> None:
        """Signal cancellation for any currently executing task."""
        with self._lock:
            self._cancelled = True
            if self.state in (
                WorkerState.STARTING,
                WorkerState.RUNNING,
                WorkerState.OBSERVING,
            ):
                self._transition_to(WorkerState.CANCELLED)

    def execute(self, request: ExecutionRequest) -> ExecutionResult:
        """Execute capability request under deterministic lifecycle and boundaries."""
        with self._lock:
            if self._cancelled:
                self._transition_to(WorkerState.CANCELLED)
                err = ExecutionError(
                    error_class="terminal.permission_denied",
                    message="Execution cancelled before launch",
                    recoverable=False,
                )
                return ExecutionResult(
                    request_id=request.request_id,
                    status="cancelled",
                    error=err,
                    metrics=ExecutionMetrics(duration_seconds=0.0),
                )
            self._current_request = request
            start_time = datetime.now(timezone.utc)

            # 1. State: ADMITTED
            self._transition_to(WorkerState.ADMITTED)

            # 2. Validate Space Isolation (Law 1)
            if request.space_id != self.space_id:
                err = ExecutionError(
                    error_class="terminal.permission_denied",
                    message=(
                        f"Cross-space worker execution rejected: worker assigned to "
                        f"{self.space_id}, request from {request.space_id}"
                    ),
                    recoverable=False,
                    details={"worker_space": self.space_id, "request_space": request.space_id},
                )
                self._transition_to(WorkerState.FAILED)
                self._publish_failed_pulse(request, err)
                return ExecutionResult(
                    request_id=request.request_id,
                    status="denied",
                    error=err,
                    metrics=ExecutionMetrics(duration_seconds=0.0),
                )

            # 3. Validate Capability Assignment (Law 2)
            if not self._is_capability_supported(request.capability):
                err = ExecutionError(
                    error_class="terminal.permission_denied",
                    message=(
                        f"Worker capability mismatch: worker provides {self.capability}, "
                        f"requested {request.capability}"
                    ),
                    recoverable=False,
                )
                self._transition_to(WorkerState.FAILED)
                self._publish_failed_pulse(request, err)
                return ExecutionResult(
                    request_id=request.request_id,
                    status="denied",
                    error=err,
                    metrics=ExecutionMetrics(duration_seconds=0.0),
                )

            # 4. Validate Mandatory Resource Lease (Law 2 & Phase 3)
            lease_valid, lease_err = self._validate_lease(request)
            if not lease_valid:
                assert lease_err is not None
                self._transition_to(WorkerState.FAILED)
                self._publish_failed_pulse(request, lease_err)
                return ExecutionResult(
                    request_id=request.request_id,
                    status="denied",
                    error=lease_err,
                    metrics=ExecutionMetrics(duration_seconds=0.0),
                )

            # 5. State: STARTING
            self._transition_to(WorkerState.STARTING)
            self._publish_called_pulse(request)

        # 6. State: RUNNING
        self._transition_to(WorkerState.RUNNING)
        try:
            if self._cancelled:
                raise TimeoutError("Execution cancelled before launch")

            # Delegate to specialized implementation
            result = self._execute_sandboxed(request)

            # 7. State: OBSERVING
            self._transition_to(WorkerState.OBSERVING)

            # Measure duration
            duration = (datetime.now(timezone.utc) - start_time).total_seconds()
            result.metrics.duration_seconds = duration

            # Sanitize logs & outputs (SECRET-005)
            result.logs = [sanitize_text(log) for log in result.logs]
            result.output_data = sanitize_value(result.output_data)

            if result.is_success:
                self._transition_to(WorkerState.COMPLETED)
                self._publish_succeeded_pulse(request, result)
            else:
                self._transition_to(WorkerState.FAILED)
                if result.error:
                    self._publish_failed_pulse(request, result.error)

            return result

        except TimeoutError as exc:
            duration = (datetime.now(timezone.utc) - start_time).total_seconds()
            self._transition_to(WorkerState.TIMED_OUT)
            err = ExecutionError(
                error_class="transient.timeout",
                message=sanitize_text(f"Execution timed out: {exc}"),
                recoverable=True,
            )
            self._publish_failed_pulse(request, err)
            return ExecutionResult(
                request_id=request.request_id,
                status="timeout",
                error=err,
                metrics=ExecutionMetrics(duration_seconds=duration),
            )

        except PermissionError as exc:
            duration = (datetime.now(timezone.utc) - start_time).total_seconds()
            self._transition_to(WorkerState.SANDBOX_VIOLATION)
            err = ExecutionError(
                error_class="terminal.permission_denied",
                message=sanitize_text(f"Sandbox violation: {exc}"),
                recoverable=False,
            )
            self._publish_failed_pulse(request, err)
            return ExecutionResult(
                request_id=request.request_id,
                status="violation",
                error=err,
                metrics=ExecutionMetrics(duration_seconds=duration),
            )

        except Exception as exc:
            duration = (datetime.now(timezone.utc) - start_time).total_seconds()
            self._transition_to(WorkerState.FAILED)
            err = ExecutionError(
                error_class="terminal.invalid_params",
                message=sanitize_text(f"Execution failed: {exc}"),
                recoverable=False,
            )
            self._publish_failed_pulse(request, err)
            return ExecutionResult(
                request_id=request.request_id,
                status="failed",
                error=err,
                metrics=ExecutionMetrics(duration_seconds=duration),
            )
        finally:
            self._cleanup(request)

    def _validate_lease(
        self, request: ExecutionRequest
    ) -> tuple[bool, ExecutionError | None]:
        """Validate lease existence, space ownership, and active state."""
        # If no resource manager is attached, lease is optional (e.g. standalone test)
        if self.resource_manager is None:
            if not request.lease_id:
                # If resource requirements were requested but no lease was provided:
                if request.resource_requirements:
                    return False, ExecutionError(
                        error_class="terminal.permission_denied",
                        message="Resource requirements specified but no lease_id provided",
                        recoverable=False,
                    )
            return True, None

        # When resource manager is present, lease validation is mandatory
        if not request.lease_id:
            return False, ExecutionError(
                error_class="terminal.permission_denied",
                message="Execution rejected: missing required resource lease",
                recoverable=False,
            )

        lease = self.resource_manager.get_lease(request.lease_id)
        if lease is None:
            return False, ExecutionError(
                error_class="terminal.permission_denied",
                message=f"Execution rejected: lease {request.lease_id} not found",
                recoverable=False,
            )

        if lease.space_id != request.space_id:
            return False, ExecutionError(
                error_class="terminal.permission_denied",
                message=(
                    f"Cross-space lease access rejected: lease belongs to "
                    f"{lease.space_id}, request from {request.space_id}"
                ),
                recoverable=False,
            )

        now = self.resource_manager.clock.now()
        if not lease.is_valid(now):
            return False, ExecutionError(
                error_class="terminal.permission_denied",
                message=f"Execution rejected: lease {request.lease_id} is {lease.state.value}",
                recoverable=False,
            )

        return True, None

    def _publish_called_pulse(self, request: ExecutionRequest) -> None:
        """Publish worker.tool.called Pulse."""
        if not self.bus:
            return
        pulse = Pulse(
            id=f"pulse-{uuid.uuid4().hex[:12]}",
            space_id=request.space_id,
            type="worker.tool.called",
            severity=Severity.INFO,
            source=f"worker.{self.worker_id}",
            timestamp=datetime.now(timezone.utc),
            payload={
                "tool_id": self.worker_id,
                "capability": request.capability,
                "attempt": 1,
            },
            correlation_id=request.correlation_id,
        )
        try:
            self.bus.publish(pulse)
        except Exception as exc:
            logger.warning("Failed to publish worker.tool.called pulse: %s", exc)

    def _publish_succeeded_pulse(
        self, request: ExecutionRequest, result: ExecutionResult
    ) -> None:
        """Publish worker.tool.succeeded Pulse."""
        if not self.bus:
            return
        result_ref = (
            result.artifacts[0].artifact_id if result.artifacts else "inline-result"
        )
        pulse = Pulse(
            id=f"pulse-{uuid.uuid4().hex[:12]}",
            space_id=request.space_id,
            type="worker.tool.succeeded",
            severity=Severity.INFO,
            source=f"worker.{self.worker_id}",
            timestamp=datetime.now(timezone.utc),
            payload={
                "tool_id": self.worker_id,
                "result_ref": result_ref,
                "attempt": 1,
            },
            correlation_id=request.correlation_id,
            taint=result.taint,
        )
        try:
            self.bus.publish(pulse)
        except Exception as exc:
            logger.warning("Failed to publish worker.tool.succeeded pulse: %s", exc)

    def _publish_failed_pulse(
        self, request: ExecutionRequest, error: ExecutionError
    ) -> None:
        """Publish worker.tool.failed Pulse strictly conforming to payload schema."""
        if not self.bus:
            return
        mapped_class = map_error_to_failure_taxonomy(error.error_class)
        pulse = Pulse(
            id=f"pulse-{uuid.uuid4().hex[:12]}",
            space_id=request.space_id,
            type="worker.tool.failed",
            severity=Severity.ERROR if not error.retryable else Severity.WARNING,
            source=f"worker.{self.worker_id}",
            timestamp=datetime.now(timezone.utc),
            payload={
                "error_class": mapped_class,
                "message": sanitize_text(error.message),
                "retryable": error.retryable,
                "attempt": error.attempt,
            },
            correlation_id=request.correlation_id,
        )
        try:
            self.bus.publish(pulse)
        except Exception as exc:
            logger.warning("Failed to publish worker.tool.failed pulse: %s", exc)

    def _cleanup(self, request: ExecutionRequest) -> None:
        """Hook called after execution to clean up resources, files, and leases."""
        pass

    @abstractmethod
    def _execute_sandboxed(self, request: ExecutionRequest) -> ExecutionResult:
        """Specialized capability execution logic wrapped in sandbox."""
        raise NotImplementedError
