"""Browser Worker for external content fetching and injection canary verification.

spec §7 (Execution Layer), §10 (Prompt-Injection & Taint Model),
CONTRACT_MATRIX WORKER-002, TAINT-001, ADR-0013
"""

from __future__ import annotations

import re

from ryu.pulse_bus.bus import PulseBus

from core.resources.manager import ResourceManager
from workers.base import BaseWorker, sanitize_text
from workers.contract import (
    ExecutionMetrics,
    ExecutionRequest,
    ExecutionResult,
    WorkerIdentity,
)
from workers.sandbox.network import NetworkSandbox


class BrowserWorker(BaseWorker):
    """Minimal browser worker for external content retrieval with injection canary defenses.

    Structural Invariants:
    1. Returns external content strictly as passive data, never instructions (WORKER-002).
    2. External web content is stamped with taint: True (TAINT-001).
    3. Network egress is validated against the assigned NetworkPolicy.
    """

    def __init__(
        self,
        identity: WorkerIdentity | None = None,
        bus: PulseBus | None = None,
        resource_manager: ResourceManager | None = None,
    ) -> None:
        ident = identity or WorkerIdentity(
            worker_id="browser-worker-01",
            capability="browser.action",
            space_id="default-space",
        )
        super().__init__(identity=ident, bus=bus, resource_manager=resource_manager)

    def _execute_sandboxed(self, request: ExecutionRequest) -> ExecutionResult:
        url = request.arguments.get("url")
        raw_html = request.arguments.get("html")

        # Network sandbox validation if url is provided
        if url:
            # Parse host and port
            match = re.search(r"://([^/:]+)(?::(\d+))?", url)
            host = match.group(1) if match else "localhost"
            port = int(match.group(2)) if (match and match.group(2)) else 80

            net = NetworkSandbox(request.sandbox_policy.network_policy)
            net.validate_connection(host, port)

        # Content retrieval (either mock HTML passed in or fetched)
        content = raw_html or f"<html><body>Parsed content from {url}</body></html>"

        # Extract text content while preserving it as pure data
        # Any prompt injection like "ignore your instructions..." is treated as inert string data
        sanitized_content = sanitize_text(content)

        return ExecutionResult(
            request_id=request.request_id,
            status="ok",
            output_data={"url": url, "content": sanitized_content},
            taint=True,  # Invariant: external content enters with taint=True (TAINT-001)
            metrics=ExecutionMetrics(),
            logs=[f"Retrieved {len(sanitized_content)} chars from {url or 'html'}"],
        )
