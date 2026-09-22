"""Skill execution coordinator and capability request delegator.

Enforces:
- Skill enablement and valid lifecycle state
- Schema validation of input and output contracts
- Delegation to AdmissionController (Law 2, ADR-0028)
- Taint marking on outputs (Law 6, ADR-0032)

spec §4, §7, §16, ADR-0028, ADR-0032 — Phase 9
"""

from __future__ import annotations

import time
from typing import Any, Callable, Protocol

from core.capabilities.admission import AdmissionController, CapabilityRequest
from skills.contract import (
    SkillError,
    SkillRequest,
    SkillResponse,
    validate_schema_payload,
)
from skills.model import SkillLifecycleState
from skills.registry import SkillRegistry


class CapabilityHandler(Protocol):
    """Protocol for executing admitted capability workloads."""

    def __call__(
        self,
        capability: str,
        parameters: dict[str, Any],
        context: Any,
    ) -> Any: ...


class SkillExecutor:
    """
    Coordinates invocation of registered Skills through Space Admission Control.

    Skills possess zero execution authority; all operations route via CapabilityRequest.
    """

    def __init__(
        self,
        registry: SkillRegistry,
        admission_controller: AdmissionController | None = None,
    ) -> None:
        self.registry = registry
        self.admission_controller = admission_controller

    def execute(
        self,
        request: SkillRequest,
        handler: CapabilityHandler | Callable[..., Any],
    ) -> SkillResponse:
        """
        Execute a Skill request through admission evaluation and sandbox execution.

        Returns structured SkillResponse with taint: True.
        """
        start_time = time.time()
        ctx = request.context

        # 1. Resolve registered skill
        try:
            skill = self.registry.get_skill(ctx.skill_id, ctx.skill_version)
        except Exception as e:
            return SkillResponse(
                request_id=request.request_id,
                status="failed",
                error=SkillError(
                    error_class="terminal.not_found",
                    message=f"Skill resolution failed: {e}",
                ),
                duration_seconds=time.time() - start_time,
            )

        # 2. Verify lifecycle state
        if skill.lifecycle_state != SkillLifecycleState.ENABLED:
            return SkillResponse(
                request_id=request.request_id,
                status="denied",
                error=SkillError(
                    error_class="terminal.permission_denied",
                    message=f"Skill '{skill.versioned_id}' is {skill.lifecycle_state.value} and cannot be executed.",
                ),
                duration_seconds=time.time() - start_time,
            )

        # 3. Validate input parameters against schema
        try:
            validate_schema_payload(request.parameters, skill.input_schema, label="Skill Input")
        except SkillError as exc:
            return SkillResponse(
                request_id=request.request_id,
                status="failed",
                error=exc,
                duration_seconds=time.time() - start_time,
            )

        # 4. Route through Admission Control (Law 2, ADR-0028)
        if self.admission_controller is not None:
            primary_capability = skill.capabilities[0] if skill.capabilities else f"skill.{skill.skill_id}"
            cap_request = CapabilityRequest(
                requester_id=ctx.worker_id or ctx.agent_id,
                space_id=ctx.space_id,
                capability=primary_capability,
                params=request.parameters,
                timeout=request.timeout_seconds,
            )
            if hasattr(self.admission_controller, "check_admission"):
                admission_response = self.admission_controller.check_admission(cap_request)
            elif hasattr(self.admission_controller, "evaluate"):
                admission_response = getattr(self.admission_controller, "evaluate")(cap_request)
            else:
                raise AttributeError("Admission controller has neither check_admission nor evaluate method")
            if admission_response.status != "ok":
                return SkillResponse(
                    request_id=request.request_id,
                    status="denied",
                    error=SkillError(
                        error_class="terminal.permission_denied",
                        message=f"Admission denied for capability '{primary_capability}': {admission_response.error}",
                        details={"status": admission_response.status},
                    ),
                    duration_seconds=time.time() - start_time,
                )

        # 5. Execute capability handler
        try:
            raw_output = handler(
                capability=skill.capabilities[0] if skill.capabilities else f"skill.{skill.skill_id}",
                parameters=request.parameters,
                context=ctx,
            )
        except SkillError as exc:
            return SkillResponse(
                request_id=request.request_id,
                status="failed",
                error=exc,
                duration_seconds=time.time() - start_time,
            )
        except Exception as exc:
            return SkillResponse(
                request_id=request.request_id,
                status="failed",
                error=SkillError(
                    error_class="terminal.tool_failure",
                    message=f"Unexpected error executing Skill '{skill.versioned_id}': {exc}",
                ),
                duration_seconds=time.time() - start_time,
            )

        # 6. Validate output against output schema
        try:
            validate_schema_payload(raw_output, skill.output_schema, label="Skill Output")
        except SkillError as exc:
            return SkillResponse(
                request_id=request.request_id,
                status="failed",
                error=exc,
                duration_seconds=time.time() - start_time,
            )

        # 7. Return successful SkillResponse with taint: True (ADR-0032)
        return SkillResponse(
            request_id=request.request_id,
            status="ok",
            output_data=raw_output,
            taint=True,
            duration_seconds=time.time() - start_time,
        )

