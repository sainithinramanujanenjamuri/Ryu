"""Skill invocation contracts, execution context, and schema validation.

spec §7 (Skills Layer), §16 (Component Contracts), ROADMAP Phase 9, ADR-0028 — Phase 9
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import jsonschema  # type: ignore[import-untyped]


def map_skill_error_to_failure_taxonomy(error_class: str) -> str:
    """Map error code into a standard failure-taxonomy class."""
    if error_class.startswith("transient.") or error_class.startswith("terminal."):
        return error_class
    mapping = {
        "TIMEOUT": "transient.timeout",
        "RATE_LIMIT": "transient.rate_limit",
        "NETWORK_ERROR": "transient.network",
        "SCHEMA_VIOLATION": "terminal.invalid_params",
        "INVALID_PARAMS": "terminal.invalid_params",
        "PERMISSION_DENIED": "terminal.permission_denied",
        "NOT_FOUND": "terminal.not_found",
        "BUDGET_EXCEEDED": "terminal.budget_exceeded",
    }
    return mapping.get(error_class.upper(), "terminal.invalid_params")


@dataclass(frozen=True)
class SkillExecutionContext:
    """Immutable context provided to a Skill execution."""

    space_id: str
    plan_id: str
    plan_version: int
    task_id: str
    correlation_id: str
    parent_pulse_id: str
    agent_id: str
    worker_id: str
    skill_id: str
    skill_version: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        return {
            "space_id": self.space_id,
            "plan_id": self.plan_id,
            "plan_version": self.plan_version,
            "task_id": self.task_id,
            "correlation_id": self.correlation_id,
            "parent_pulse_id": self.parent_pulse_id,
            "agent_id": self.agent_id,
            "worker_id": self.worker_id,
            "skill_id": self.skill_id,
            "skill_version": self.skill_version,
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass
class SkillError(Exception):
    """Structured error contract mapped to failure-taxonomy.json."""

    error_class: str
    message: str
    retryable: bool = False
    details: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        super().__init__(self.message)
        self.error_class = map_skill_error_to_failure_taxonomy(self.error_class)

    def to_dict(self) -> dict[str, Any]:
        return {
            "error_class": self.error_class,
            "message": self.message,
            "retryable": self.retryable,
            "details": self.details,
        }


@dataclass(frozen=True)
class SkillRequest:
    """Request submitted to invoke an authorized Skill."""

    request_id: str
    context: SkillExecutionContext
    parameters: dict[str, Any] = field(default_factory=dict)
    timeout_seconds: float = 30.0


@dataclass(frozen=True)
class SkillResponse:
    """Structured response returned by a Skill."""

    request_id: str
    status: str                         # "ok" | "failed" | "denied"
    output_data: Any = None             # Strictly passive data
    taint: bool = True                  # Default to True for external tool outputs
    duration_seconds: float = 0.0
    error: SkillError | None = None

    @property
    def is_success(self) -> bool:
        return self.status == "ok" and self.error is None

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "status": self.status,
            "output_data": self.output_data,
            "taint": self.taint,
            "duration_seconds": self.duration_seconds,
            "error": self.error.to_dict() if self.error else None,
        }


def validate_schema_payload(data: Any, schema: dict[str, Any], label: str = "payload") -> None:
    """Validate data against a JSON Schema Draft 2020-12 specification."""
    if not schema:
        return
    try:
        validator = jsonschema.Draft202012Validator(schema)
        errors = list(validator.iter_errors(data))
        if errors:
            first_err = errors[0]
            err_path = ".".join(str(p) for p in first_err.absolute_path) or "root"
            raise SkillError(
                error_class="terminal.schema_violation",
                message=f"Validation failed for {label} at '{err_path}': {first_err.message}",
                retryable=False,
                details={"errors": [e.message for e in errors]},
            )
    except jsonschema.exceptions.SchemaError as exc:
        raise SkillError(
            error_class="terminal.schema_violation",
            message=f"Invalid JSON Schema for {label}: {exc.message}",
            retryable=False,
        )
