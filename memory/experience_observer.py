"""Execution Experience Observer: Concrete bridge from execution outcomes to Space memory.

Phase 13 — Closed-Loop Experiential Adaptation (ADR-0043, ADAPT-001)

Connects verified execution outcomes from the DeterministicDispatcher to the
Reflector and SpaceMemoryProtocol without core-to-memory coupling (AGENTS.md §7).

Responsibilities:
- Implements ExperienceObserverProtocol defined in core.space.memory_protocol.
- Secret sanitization: scrubs credentials, tokens, and sensitive strings before reflection.
- Structured counterfactual generation for both successful and failed tasks (MEM-002).
- Delegates to Reflector.reflect() for persistence-first storage and pulse emission.
- Preserves execution provenance (task_id, space_id, plan_version, failure_fingerprint).
"""

from __future__ import annotations

import logging
import re
from typing import Any

from core.space.memory_protocol import (
    ExperienceObserverProtocol,
    TaskExecutionOutcome,
)
from memory.reflector import Reflector

logger = logging.getLogger(__name__)

# Patterns for secret sanitization
_SECRET_PATTERNS = [
    re.compile(r"(?i)(password|secret|token|api[_-]?key|auth|bearer)\s*[:=]\s*['\"]?([^'\"\s,]+)"),
    re.compile(r"(?i)bearer\s+[a-zA-Z0-9_\-\.]{10,}"),
]


def _sanitize_dict(data: dict[str, Any]) -> dict[str, Any]:
    """Recursively scrub known sensitive patterns from dictionary values."""
    sanitized: dict[str, Any] = {}
    for k, v in data.items():
        k_lower = str(k).lower()
        if any(term in k_lower for term in ("secret", "token", "password", "api_key", "bearer")):
            sanitized[k] = "[REDACTED]"
        elif isinstance(v, dict):
            sanitized[k] = _sanitize_dict(v)
        elif isinstance(v, str):
            val = v
            val = re.sub(
                r"(?i)(password|secret|token|api[_-]?key|auth|bearer)\s*[:=]\s*['\"]?([^'\"\s,]+)",
                r"\1: [REDACTED]",
                val,
            )
            val = re.sub(
                r"(?i)bearer\s+[a-zA-Z0-9_\-\.]{10,}",
                "Bearer [REDACTED]",
                val,
            )
            sanitized[k] = val
        else:
            sanitized[k] = v
    return sanitized


class ExecutionExperienceObserver(ExperienceObserverProtocol):
    """Bridges DeterministicDispatcher to Reflector to capture execution experiences.

    Lives in memory/ to maintain strict core independence:
    - Core defines ExperienceObserverProtocol.
    - Memory implements it here.
    - Composition root / runner passes an instance to DeterministicDispatcher.
    """

    def __init__(
        self,
        reflector: Reflector,
        sanitize_secrets: bool = True,
    ) -> None:
        self.reflector = reflector
        self.sanitize_secrets = sanitize_secrets

    def observe_task_outcome(self, outcome: TaskExecutionOutcome) -> str | None:
        """Receive a verified task execution outcome and persist as a structured experience.

        Constructs actionable situation, action, outcome, and counterfactual representations.
        Returns the resulting experience_id, or None if skipped/failed.
        """
        try:
            # Step 1: Sanitize situation parameters if enabled
            raw_situation = {
                "task_id": outcome.task_id,
                "capability": outcome.capability,
                "plan_version": outcome.plan_version,
                "params": outcome.params,
                "dependencies": list(outcome.dependencies),
            }
            situation = _sanitize_dict(raw_situation) if self.sanitize_secrets else raw_situation

            # Step 2: Construct action record
            raw_action = {
                "capability": outcome.capability,
                "duration_seconds": round(outcome.duration_seconds, 4),
                "exit_code": outcome.exit_code,
            }
            action = _sanitize_dict(raw_action) if self.sanitize_secrets else raw_action

            # Step 3: Determine outcome description and mandatory counterfactual
            if outcome.status == "completed":
                outcome_desc = (
                    f"Task '{outcome.task_id}' completed successfully "
                    f"(capability: '{outcome.capability}', exit_code: {outcome.exit_code or 0})"
                )
                counterfactual = (
                    f"Maintain capability '{outcome.capability}' and current parameters "
                    f"under equivalent constraints."
                )
                applicable_context = {
                    "task_id": outcome.task_id,
                    "status": "completed",
                    "plan_version": outcome.plan_version,
                    "artifact_refs": list(outcome.artifact_refs),
                    "suggested_alternative": outcome.capability,
                }
            else:
                fail_reason = outcome.error_message or outcome.error_class or "Execution failure"
                outcome_desc = f"Task '{outcome.task_id}' failed: {fail_reason}"
                counterfactual = (
                    f"Verify capability prerequisites, constraints, or alternative capabilities "
                    f"when encountering '{outcome.error_class or 'task_failure'}': {fail_reason[:150]}"
                )
                applicable_context = {
                    "task_id": outcome.task_id,
                    "status": "failed",
                    "plan_version": outcome.plan_version,
                    "error_class": outcome.error_class,
                    "failure_fingerprint": outcome.failure_fingerprint,
                }

            # Step 4: Delegate to Reflector for validation, persistence, and pulse emission
            record = self.reflector.reflect(
                situation=situation,
                action=action,
                outcome=outcome_desc,
                counterfactual=counterfactual,
                applicable_context=applicable_context,
                space_id=outcome.space_id,
                correlation_id=f"corr-{outcome.space_id}-{outcome.task_id}",
            )

            return record.experience_id

        except Exception as e:
            logger.warning(
                "ExecutionExperienceObserver failed to record experience for task '%s': %s",
                outcome.task_id,
                e,
            )
            # Memory failure must not crash execution, but must not be completely silent
            return None
