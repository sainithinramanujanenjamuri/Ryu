"""LLM Call Recording subsystem.

Persists full input and output of every model invocation keyed by correlation_id
and scoped to the owning Space (docs/Architecture §12, §16).
Guarantees secret sanitization prior to persistence (ADR-0011).

spec §12 (LLM call recording, contract v0), ROADMAP Phase 5, AGENT-003 — Phase 5
"""

from __future__ import annotations

import copy
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol

from llm.provider import LLMError, LLMRequest, LLMResponse, LLMUsage
from llm.sanitizer import SecretSanitizer


@dataclass
class LLMRecord:
    """Full snapshot of an LLM call for observability and replay."""

    call_id: str
    correlation_id: str
    space_id: str
    agent_id: str
    provider: str
    model: str
    request: LLMRequest
    response: LLMResponse
    parameters: dict[str, Any] = field(default_factory=dict)
    usage: LLMUsage = field(default_factory=LLMUsage)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    status: str = "ok"
    error: LLMError | None = None


class LLMRecorder(Protocol):
    """Protocol for Space-scoped LLM interaction recording."""

    def record(self, record: LLMRecord) -> None: ...

    def get_by_correlation(self, space_id: str, correlation_id: str) -> list[LLMRecord]: ...

    def get_by_call_id(self, space_id: str, call_id: str) -> LLMRecord | None: ...

    def list_records(self, space_id: str) -> list[LLMRecord]: ...


class InMemoryLLMRecorder:
    """Thread-safe in-memory LLM call recorder with automatic secret sanitization
    and Space isolation.

    Invariants (ADR-0009, ADR-0011, Correction 3):
    - Automatically sanitizes prompt text, messages, completions, structured output, and errors.
    - Space isolation: A Space A caller cannot retrieve or list Space B records (PermissionError).
    """

    def __init__(self, sanitizer: SecretSanitizer | None = None) -> None:
        self.sanitizer = sanitizer or SecretSanitizer()
        self._lock = threading.Lock()
        self._records: list[LLMRecord] = []

    def record(self, record: LLMRecord) -> None:
        """Sanitize and persist an LLM call record."""
        with self._lock:
            # Deepcopy and sanitize request and response components
            sanitized_record = copy.deepcopy(record)
            if self.sanitizer:
                # Sanitize messages in request
                sanitized_record.request.messages = self.sanitizer.sanitize(
                    sanitized_record.request.messages
                )
                sanitized_record.request.parameters = self.sanitizer.sanitize(
                    sanitized_record.request.parameters
                )
                # Sanitize response
                sanitized_record.response.content = self.sanitizer.sanitize(
                    sanitized_record.response.content
                )
                if sanitized_record.response.structured_output is not None:
                    sanitized_record.response.structured_output = self.sanitizer.sanitize(
                        sanitized_record.response.structured_output
                    )
                sanitized_record.parameters = self.sanitizer.sanitize(sanitized_record.parameters)
                if sanitized_record.error is not None:
                    sanitized_record.error = LLMError(
                        error_class=sanitized_record.error.error_class,
                        message=self.sanitizer.sanitize(sanitized_record.error.message),
                        retryable=sanitized_record.error.retryable,
                        details=self.sanitizer.sanitize(sanitized_record.error.details),
                    )

            self._records.append(sanitized_record)

    def get_by_correlation(self, space_id: str, correlation_id: str) -> list[LLMRecord]:
        """Retrieve all records for a correlation_id within the caller's Space."""
        with self._lock:
            # Check if any record with this correlation belongs to another space
            other_space = [
                r
                for r in self._records
                if r.correlation_id == correlation_id and r.space_id != space_id
            ]
            if other_space and not any(
                r.space_id == space_id for r in self._records if r.correlation_id == correlation_id
            ):
                raise PermissionError(
                    f"Cross-space LLM record access rejected: caller in '{space_id}' "
                    f"attempted to access records in '{other_space[0].space_id}'"
                )
            return [
                r
                for r in self._records
                if r.space_id == space_id and r.correlation_id == correlation_id
            ]

    def get_by_call_id(self, space_id: str, call_id: str) -> LLMRecord | None:
        """Retrieve a specific record by call_id with Space boundary enforcement."""
        with self._lock:
            for r in self._records:
                if r.call_id == call_id:
                    if r.space_id != space_id:
                        raise PermissionError(
                            f"Cross-space LLM record access rejected: caller in '{space_id}' "
                            f"attempted to access record in '{r.space_id}'"
                        )
                    return r
            return None

    def list_records(self, space_id: str) -> list[LLMRecord]:
        """List all records for a specific Space."""
        with self._lock:
            return [r for r in self._records if r.space_id == space_id]
