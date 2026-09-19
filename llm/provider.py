"""LLM Provider abstraction and contract models.

Enforces provider-neutral interface between Agent reasoning and model backends
(docs/Architecture §12, §15).
Deterministic core NEVER imports from this layer (AGENTS.md §4, dep_guard.py).

spec §12 (LLM call recording, contract v0), ROADMAP Phase 5, AGENT-001/002 — Phase 5
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Protocol


@dataclass(frozen=True)
class LLMMetadata:
    """Metadata contract capturing invocation parameters and model info."""

    model: str
    provider: str
    temperature: float = 0.0
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LLMUsage:
    """Token accounting contract."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


@dataclass
class LLMError(Exception):
    """Error contract mapped deterministically to RYU failure taxonomy."""

    error_class: str = "internal_error"
    message: str = ""
    retryable: bool = False
    details: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        super().__init__(self.message)

    def __str__(self) -> str:
        return f"{self.error_class}: {self.message}"


@dataclass
class LLMRequest:
    """Request contract for model invocation."""

    request_id: str
    correlation_id: str
    space_id: str
    agent_id: str
    model: str
    provider: str
    messages: list[dict[str, Any]]
    parameters: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class LLMResponse:
    """Structured response contract from model invocation."""

    request_id: str
    content: str
    structured_output: dict[str, Any] | None = None
    usage: LLMUsage = field(default_factory=LLMUsage)
    status: str = "ok"  # "ok" | "failed"
    error: LLMError | None = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class LLMProvider(Protocol):
    """Provider-neutral Protocol for LLM backends."""

    def complete(self, request: LLMRequest) -> LLMResponse: ...


class MockLLMProvider:
    """Deterministic Mock LLM Provider for testing, recording, and replay verification.

    Supports:
    - Canned/scripted response queues.
    - Template generation based on prompt keywords.
    - Systematic failure injection (timeouts, rate limits, malformed JSON).
    - Call counting for verifying zero live calls during replay.
    """

    def __init__(
        self,
        default_model: str = "mock-gpt",
        provider_name: str = "mock_provider",
    ) -> None:
        self.default_model = default_model
        self.provider_name = provider_name
        self._lock = threading.Lock()
        self._canned_responses: list[LLMResponse | Callable[[LLMRequest], LLMResponse]] = []
        self.calls: list[LLMRequest] = []
        self.call_count: int = 0

    def enqueue_response(self, response: LLMResponse | Callable[[LLMRequest], LLMResponse]) -> None:
        """Enqueue a canned response or dynamic generator."""
        with self._lock:
            self._canned_responses.append(response)

    def complete(self, request: LLMRequest) -> LLMResponse:
        """Execute mock completion."""
        with self._lock:
            self.calls.append(request)
            self.call_count += 1

            if self._canned_responses:
                item = self._canned_responses.pop(0)
                if callable(item):
                    return item(request)
                return item

            # Default canned structured response
            return LLMResponse(
                request_id=request.request_id,
                content="Deterministic mock response",
                structured_output={
                    "intent": "analyze_goal",
                    "reasoning": "Mock deterministic analysis",
                    "requested_action": "read_spec",
                    "parameters": {"fragment": "task-1"},
                    "confidence": 0.95,
                    "required_capabilities": ["fs.read"],
                },
                usage=LLMUsage(prompt_tokens=50, completion_tokens=25, total_tokens=75),
                status="ok",
            )
