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


class LiveHTTPLLMProvider:
    """Universal HTTP provider supporting Ollama and OpenAI-compatible endpoints.

    Supports:
    - Local Ollama (/api/chat or /v1/chat/completions)
    - OpenAI (/v1/chat/completions)
    - Any compatible server (Groq, vLLM, LMStudio, LocalAI, etc.)
    Uses standard library urllib (zero external dependencies).
    """

    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        api_key: str = "",
        model: str = "llama3",
        timeout: float = 120.0,
        provider_name: str = "live_http",
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.provider_name = provider_name

    def complete(self, request: LLMRequest) -> LLMResponse:
        import json
        import urllib.error
        import urllib.request

        model = request.model or self.model or "llama3"
        messages = request.messages

        # Determine target endpoint and payload
        is_ollama_native = "11434" in self.base_url and not self.base_url.endswith("/v1")
        if is_ollama_native:
            endpoint = f"{self.base_url}/api/chat"
            payload = {
                "model": model,
                "messages": messages,
                "stream": False,
            }
        else:
            base = self.base_url
            if not base.endswith("/chat/completions"):
                if base.endswith("/v1"):
                    endpoint = f"{base}/chat/completions"
                else:
                    endpoint = f"{base}/v1/chat/completions"
            else:
                endpoint = base

            payload = {
                "model": model,
                "messages": messages,
                "stream": False,
                "temperature": request.parameters.get("temperature", 0.7),
            }

        headers = {
            "Content-Type": "application/json",
            "User-Agent": "RYU-AI/1.0",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        data_bytes = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(endpoint, data=data_bytes, headers=headers, method="POST")

        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                resp_data = json.loads(resp.read().decode("utf-8"))

            content = ""
            prompt_tokens = 0
            completion_tokens = 0

            if is_ollama_native:
                msg = resp_data.get("message", {})
                content = msg.get("content", "")
                prompt_tokens = resp_data.get("prompt_eval_count", 0)
                completion_tokens = resp_data.get("eval_count", 0)
            else:
                choices = resp_data.get("choices", [])
                if choices:
                    content = choices[0].get("message", {}).get("content", "")
                usage = resp_data.get("usage", {})
                prompt_tokens = usage.get("prompt_tokens", 0)
                completion_tokens = usage.get("completion_tokens", 0)

            return LLMResponse(
                request_id=request.request_id,
                content=content,
                usage=LLMUsage(
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    total_tokens=prompt_tokens + completion_tokens,
                ),
                status="ok",
            )
        except urllib.error.HTTPError as e:
            err_body = ""
            try:
                err_body = e.read().decode("utf-8")
            except Exception:
                pass
            err_msg = f"HTTP {e.code}: {err_body or e.reason}"
            return LLMResponse(
                request_id=request.request_id,
                content="",
                status="failed",
                error=LLMError(error_class="http_error", message=err_msg, retryable=e.code in (429, 502, 503, 504)),
            )
        except urllib.error.URLError as e:
            err_msg = f"Connection failed to {endpoint}: {e.reason}"
            return LLMResponse(
                request_id=request.request_id,
                content="",
                status="failed",
                error=LLMError(error_class="connection_error", message=err_msg, retryable=True),
            )
        except Exception as e:
            return LLMResponse(
                request_id=request.request_id,
                content="",
                status="failed",
                error=LLMError(error_class="internal_error", message=str(e), retryable=False),
            )

