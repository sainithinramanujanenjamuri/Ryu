"""Node Device Worker for hardware-accelerated capability execution.

Space-Centric Cognitive Architecture (SCCA) — Phase 7
spec §7, §11, CONTRACT_MATRIX NODE-001..NODE-004, WORKER-001..WORKER-003
ADR-0017, ADR-0018, ADR-0019

INVARIANT:
Workers execute through Node Runtime only with an authoritative,
space-scoped Device Grant backed by a valid Resource Manager Lease.
"""

from __future__ import annotations

import logging

from ryu.pulse_bus.bus import PulseBus

from core.resources.manager import ResourceManager
from node.contract import (
    DeviceBindingError,
    DeviceGrant,
    DeviceNotFoundError,
    DeviceUnavailableError,
    GrantExpiredError,
    GrantInvalidError,
    GrantRevokedError,
    LeaseInvalidError,
    NodeError,
)
from node.runtime import NodeRuntime
from workers.base import BaseWorker, sanitize_text
from workers.contract import (
    ExecutionError,
    ExecutionRequest,
    ExecutionResult,
    WorkerIdentity,
)

logger = logging.getLogger(__name__)


class NodeWorker(BaseWorker):
    """Executes capability tasks bound to node devices through NodeRuntime."""

    def __init__(
        self,
        node_runtime: NodeRuntime,
        identity: WorkerIdentity | None = None,
        bus: PulseBus | None = None,
        resource_manager: ResourceManager | None = None,
    ) -> None:
        ident = identity or WorkerIdentity(
            worker_id="node-worker-01",
            capability="node.compute",
            space_id="default-space",
        )
        super().__init__(identity=ident, bus=bus, resource_manager=resource_manager)
        self.node_runtime = node_runtime

    def _execute_sandboxed(self, request: ExecutionRequest) -> ExecutionResult:
        """Bind device via NodeRuntime, perform execution, and release."""
        grant_data = request.arguments.get("grant")
        device_id = request.arguments.get("device_id")

        if not grant_data or not isinstance(grant_data, (dict, DeviceGrant)):
            err = ExecutionError(
                error_class="terminal.invalid_params",
                message="Missing or invalid 'grant' parameter for node execution",
                recoverable=False,
            )
            return ExecutionResult(
                request_id=request.request_id,
                status="failed",
                error=err,
            )

        if not device_id or not isinstance(device_id, str):
            err = ExecutionError(
                error_class="terminal.invalid_params",
                message="Missing or invalid 'device_id' parameter for node execution",
                recoverable=False,
            )
            return ExecutionResult(
                request_id=request.request_id,
                status="failed",
                error=err,
            )

        grant = (
            grant_data
            if isinstance(grant_data, DeviceGrant)
            else DeviceGrant.from_dict(grant_data)
        )

        binding = None
        try:
            # 1. Bind device via NodeRuntime
            binding = self.node_runtime.bind_device(
                worker_id=self.worker_id,
                space_id=self.space_id,
                grant=grant,
                device_id=device_id,
            )

            # 2. Simulate hardware operation
            operation = request.arguments.get("operation", "compute")

            # Simulated hardware computation
            output_data = {
                "binding_id": binding.binding_id,
                "node_id": self.node_runtime.node_id,
                "device_id": device_id,
                "operation": operation,
                "status": "success",
                "result": f"Executed {operation} on device {device_id}",
            }

            return ExecutionResult(
                request_id=request.request_id,
                status="ok",
                output_data=output_data,
                logs=[
                    f"Successfully executed on device {device_id} "
                    f"under binding {binding.binding_id}"
                ],
            )

        except (GrantRevokedError, LeaseInvalidError) as exc:
            return ExecutionResult(
                request_id=request.request_id,
                status="denied",
                error=ExecutionError(
                    error_class="terminal.permission_denied",
                    message=sanitize_text(str(exc)),
                    recoverable=False,
                ),
            )
        except GrantExpiredError as exc:
            return ExecutionResult(
                request_id=request.request_id,
                status="denied",
                error=ExecutionError(
                    error_class="transient.resource_busy",
                    message=sanitize_text(str(exc)),
                    recoverable=True,
                ),
            )
        except (GrantInvalidError, DeviceNotFoundError, NodeError) as exc:
            return ExecutionResult(
                request_id=request.request_id,
                status="denied",
                error=ExecutionError(
                    error_class="terminal.invalid_params",
                    message=sanitize_text(str(exc)),
                    recoverable=False,
                ),
            )
        except (DeviceBindingError, DeviceUnavailableError) as exc:
            return ExecutionResult(
                request_id=request.request_id,
                status="failed",
                error=ExecutionError(
                    error_class="transient.resource_busy",
                    message=sanitize_text(str(exc)),
                    recoverable=True,
                ),
            )
        except Exception as exc:
            return ExecutionResult(
                request_id=request.request_id,
                status="failed",
                error=ExecutionError(
                    error_class="terminal.execution_failed",
                    message=sanitize_text(f"Device execution failed: {exc}"),
                    recoverable=False,
                ),
            )
        finally:
            # 3. Always release device binding cleanly
            if binding:
                try:
                    self.node_runtime.release_device(binding.binding_id)
                except Exception as rel_exc:
                    logger.warning(
                        f"Failed to release device binding {binding.binding_id}: {rel_exc}"
                    )
