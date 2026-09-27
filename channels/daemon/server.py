"""Local Channel Daemon Server.

Provides a loopback HTTP/SSE adapter for developer interaction surfaces
(interactive CLI, Tauri desktop application) without conferring independent authority.

spec §2, §4, ADR-0026, CONTRACT APP-002, APP-006 — Phase 8.5
"""

from __future__ import annotations

import json
import logging
import queue
import re
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import time
import os
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from channels.approval.auth import (
    ApproverDecisionSubmission,
    AuthenticationError,
    ClockSkewError,
    MalformedAuthenticationPayloadError,
    ReplayDetectedError,
)
from channels.approval.client import ApprovalClient
from channels.daemon.artifacts import ArtifactRecord, SpaceArtifactStore
from channels.daemon.auth import DaemonAuthError, DaemonAuthenticator
from channels.daemon.config import DaemonConfig
from channels.daemon.history import DialogueTurn, SpaceHistoryStore
from core.orchestrator.goal_analyzer import Command, GoalAnalyzer
from core.space.approver import (
    ApprovalLifecycleState,
    ApprovalRequest,
    AttentionQueueState,
)

logger = logging.getLogger("ryu.channels.daemon")


class DaemonRequestHandler(BaseHTTPRequestHandler):
    """Handles HTTP and SSE requests on local loopback."""

    server: DaemonHTTPServer

    def log_message(self, format: str, *args: Any) -> None:
        """Suppress default stderr logging; use structured debug logger."""
        logger.debug("Daemon request: " + format, *args)

    def _send_cors_headers(self) -> None:
        origin = self.headers.get("Origin", "")
        # Allow requests from localhost or Tauri
        if origin and any(origin.startswith(prefix) for prefix in ("http://localhost", "http://127.0.0.1", "tauri://")):
            self.send_header("Access-Control-Allow-Origin", origin)
        else:
            self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
        self.send_header("Access-Control-Max-Age", "86400")

    def _send_json_response(self, data: Any, status: int = 200) -> None:
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._send_cors_headers()
        self.end_headers()
        self.wfile.write(body)

    def _send_error_response(self, message: str, status: int = 400) -> None:
        self._send_json_response({"error": message, "status_code": status}, status=status)

    def do_OPTIONS(self) -> None:
        """Handle CORS preflight."""
        self.send_response(HTTPStatus.NO_CONTENT)
        self._send_cors_headers()
        self.end_headers()

    def do_GET(self) -> None:
        """Route GET requests."""
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")
        query = parse_qs(parsed.query)

        # Health endpoint does not require auth
        if path == "/api/v1/health":
            self._send_json_response({
                "status": "healthy",
                "version": "1.0.1",
                "phase": "8.5",
                "authenticated": False,
            })
            return

        # Authenticate all other endpoints
        try:
            auth_header = self.headers.get("Authorization")
            self.server.authenticator.verify_or_raise(auth_header)
        except DaemonAuthError as e:
            self._send_error_response(str(e), status=HTTPStatus.UNAUTHORIZED)
            return

        # /api/v1/spaces
        if path == "/api/v1/spaces":
            spaces = self.server.list_spaces()
            self._send_json_response({"spaces": spaces})
            return

        # /api/v1/nodes
        if path == "/api/v1/nodes":
            nodes = self.server.list_nodes()
            self._send_json_response({"nodes": nodes})
            return

        # /api/v1/config/llm
        if path == "/api/v1/config/llm":
            from channels.synthesizer import get_llm_config
            self._send_json_response(get_llm_config())
            return

        # /api/v1/spaces/{space_id}/history
        m_hist = re.match(r"^/api/v1/spaces/([^/]+)/history$", path)
        if m_hist:
            space_id = m_hist.group(1)
            limit_str = query.get("limit", ["100"])[0]
            try:
                limit = int(limit_str)
            except ValueError:
                limit = 100
            turns = self.server.get_history(space_id, limit=limit)
            self._send_json_response({"space_id": space_id, "turns": turns})
            return

        # /api/v1/spaces/{space_id}/artifacts/{artifact_id}/content
        m_art_c = re.match(r"^/api/v1/spaces/([^/]+)/artifacts/([^/]+)/content$", path)
        if m_art_c:
            space_id, artifact_id = m_art_c.group(1), m_art_c.group(2)
            content_pair = self.server.get_artifact_content(space_id, artifact_id)
            if content_pair is None:
                self._send_error_response(f"Artifact '{artifact_id}' not found in space '{space_id}'", status=HTTPStatus.NOT_FOUND)
                return
            art_meta = self.server.artifact_store.get_artifact(space_id, artifact_id) or {}
            self._send_json_response({
                "space_id": space_id,
                "artifact_id": artifact_id,
                "content": content_pair[0],
                "mime_type": content_pair[1],
                "sha256": art_meta.get("sha256", ""),
            })
            return

        # /api/v1/spaces/{space_id}/artifacts
        m_arts = re.match(r"^/api/v1/spaces/([^/]+)/artifacts$", path)
        if m_arts:
            space_id = m_arts.group(1)
            artifacts = self.server.list_artifacts(space_id)
            self._send_json_response({"space_id": space_id, "artifacts": artifacts})
            return

        # /api/v1/spaces/{space_id}/memory
        m_mem = re.match(r"^/api/v1/spaces/([^/]+)/memory$", path)
        if m_mem:
            space_id = m_mem.group(1)
            mem_data = self.server.get_space_memory(space_id)
            self._send_json_response(mem_data)
            return

        # /api/v1/spaces/{space_id}/attention
        m_att = re.match(r"^/api/v1/spaces/([^/]+)/attention$", path)
        if m_att:
            space_id = m_att.group(1)
            attention = self.server.get_space_attention(space_id)
            self._send_json_response(attention)
            return

        # /api/v1/spaces/{space_id}/approvals
        m_apps = re.match(r"^/api/v1/spaces/([^/]+)/approvals$", path)
        if m_apps:
            space_id = m_apps.group(1)
            status_filter = query.get("status", [None])[0]
            queue_state_filter = query.get("queue_state", [None])[0]
            approvals = self.server.list_approvals(
                space_id=space_id,
                status=status_filter,
                queue_state=queue_state_filter,
            )
            self._send_json_response({"space_id": space_id, "approvals": approvals})
            return

        # /api/v1/spaces/{space_id}/approvals/{request_id}
        m_app = re.match(r"^/api/v1/spaces/([^/]+)/approvals/([^/]+)$", path)
        if m_app:
            space_id, request_id = m_app.group(1), m_app.group(2)
            approval = self.server.get_approval(space_id, request_id)
            if approval is None:
                self._send_error_response(f"Approval request '{request_id}' not found", status=HTTPStatus.NOT_FOUND)
                return
            self._send_json_response(approval)
            return

        # /api/v1/spaces/{space_id}/tasks
        m_tasks = re.match(r"^/api/v1/spaces/([^/]+)/tasks$", path)
        if m_tasks:
            space_id = m_tasks.group(1)
            tasks = self.server.list_tasks(space_id)
            self._send_json_response({"space_id": space_id, "tasks": tasks})
            return

        # /api/v1/spaces/{space_id}/audit
        m_audit = re.match(r"^/api/v1/spaces/([^/]+)/audit$", path)
        if m_audit:
            space_id = m_audit.group(1)
            limit_str = query.get("limit", ["50"])[0]
            pulse_type = query.get("type", [None])[0]
            try:
                limit = int(limit_str)
            except ValueError:
                limit = 50
            audit_records = self.server.get_audit(space_id, limit=limit, pulse_type=pulse_type)
            self._send_json_response({"space_id": space_id, "events": audit_records})
            return

        # /api/v1/spaces/{space_id}/events (Server-Sent Events stream)
        m_events = re.match(r"^/api/v1/spaces/([^/]+)/events$", path)
        if m_events:
            space_id = m_events.group(1)
            self._handle_sse_stream(space_id)
            return

        # /api/v1/spaces/{space_id} (inspect space)
        m_sp = re.match(r"^/api/v1/spaces/([^/]+)$", path)
        if m_sp:
            space_id = m_sp.group(1)
            sp_info = self.server.get_space(space_id)
            if sp_info is None:
                self._send_error_response(f"Space '{space_id}' not found", status=HTTPStatus.NOT_FOUND)
                return
            self._send_json_response(sp_info)
            return

        self._send_error_response("Endpoint not found", status=HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        """Route POST requests."""
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        # Authenticate
        try:
            auth_header = self.headers.get("Authorization")
            self.server.authenticator.verify_or_raise(auth_header)
        except DaemonAuthError as e:
            self._send_error_response(str(e), status=HTTPStatus.UNAUTHORIZED)
            return

        # Read JSON body
        content_len_str = self.headers.get("Content-Length", "0")
        try:
            content_len = int(content_len_str)
            body_bytes = self.rfile.read(content_len)
            payload = json.loads(body_bytes.decode("utf-8")) if body_bytes else {}
        except Exception as e:
            self._send_error_response(f"Malformed JSON body: {e}", status=HTTPStatus.BAD_REQUEST)
            return

        # /api/v1/spaces (create space)
        if path == "/api/v1/spaces":
            space_id = str(payload.get("space_id", "")).strip()
            name = str(payload.get("name", "")).strip()
            owner_id = str(payload.get("owner_id", "human_operator")).strip()
            budget = float(payload.get("budget", 0.0))
            attention_limit = int(payload.get("attention_limit", 3))

            if not space_id:
                self._send_error_response("Missing required 'space_id' field", status=HTTPStatus.BAD_REQUEST)
                return

            try:
                sp_info = self.server.create_space(
                    space_id=space_id,
                    name=name,
                    owner_id=owner_id,
                    budget=budget,
                    attention_limit=attention_limit,
                )
                self._send_json_response({"success": True, "space": sp_info}, status=HTTPStatus.CREATED)
            except KeyError as e:
                self._send_error_response(str(e), status=HTTPStatus.CONFLICT)
            except ValueError as e:
                self._send_error_response(str(e), status=HTTPStatus.BAD_REQUEST)
            except Exception as e:
                self._send_error_response(f"Failed to create space: {e}", status=HTTPStatus.INTERNAL_SERVER_ERROR)
            return

        # /api/v1/spaces/{space_id}/files (sandboxed file ingress)
        m_files = re.match(r"^/api/v1/spaces/([^/]+)/files$", path)
        if m_files:
            space_id = m_files.group(1)
            filename = str(payload.get("filename", "")).strip()
            content = payload.get("content", "")
            mime_type = str(payload.get("mime_type", "text/plain")).strip()

            if not filename:
                self._send_error_response("Missing required 'filename' parameter", status=HTTPStatus.BAD_REQUEST)
                return

            try:
                res = self.server.handle_file_ingress(
                    space_id=space_id,
                    filename=filename,
                    content=content,
                    mime_type=mime_type,
                )
                self._send_json_response(res, status=HTTPStatus.CREATED)
            except OverflowError as e:
                self._send_error_response(str(e), status=HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
            except TypeError as e:
                self._send_error_response(str(e), status=HTTPStatus.UNSUPPORTED_MEDIA_TYPE)
            except (ValueError, PermissionError) as e:
                self._send_error_response(str(e), status=HTTPStatus.BAD_REQUEST)
            except Exception as e:
                self._send_error_response(f"File ingress failed: {e}", status=HTTPStatus.INTERNAL_SERVER_ERROR)
            return

        # /api/v1/spaces/{space_id}/approvals/{request_id}/decision
        m_dec = re.match(r"^/api/v1/spaces/([^/]+)/approvals/([^/]+)/decision$", path)
        if m_dec:
            space_id, request_id = m_dec.group(1), m_dec.group(2)
            self._handle_submit_decision(space_id, request_id, payload)
            return

        # /api/v1/config/llm
        if path == "/api/v1/config/llm":
            from channels.synthesizer import set_llm_config
            updated = set_llm_config(
                enabled=payload.get("enabled"),
                provider=payload.get("provider"),
                base_url=payload.get("base_url"),
                model=payload.get("model"),
                api_key=payload.get("api_key"),
            )
            self._send_json_response({"success": True, "config": updated})
            return

        # /api/v1/spaces/{space_id}/prompt
        m_prompt = re.match(r"^/api/v1/spaces/([^/]+)/prompt$", path)
        if m_prompt:
            space_id = m_prompt.group(1)
            self._handle_prompt(space_id, payload)
            return

        self._send_error_response("Endpoint not found", status=HTTPStatus.NOT_FOUND)

    def _handle_submit_decision(self, space_id: str, request_id: str, payload: dict[str, Any]) -> None:
        """Submit an authenticated approval decision directly to the authoritative ApprovalClient."""
        required_fields = ["approver_id", "decision", "signature", "plan_version", "capability_request_hash"]
        missing = [f for f in required_fields if f not in payload]
        if missing:
            self._send_error_response(f"Missing required decision fields: {missing}", status=HTTPStatus.BAD_REQUEST)
            return

        # Notice: Raw secret key is NEVER present in the payload or stored by the daemon.
        submission = ApproverDecisionSubmission(
            approver_id=str(payload["approver_id"]),
            timestamp=int(payload.get("timestamp", int(time.time()))),
            nonce=str(payload.get("nonce", "")),
            space_id=space_id,
            approval_id=request_id,
            decision=str(payload["decision"]).upper(),
            plan_version=int(payload["plan_version"]),
            capability_request_hash=str(payload["capability_request_hash"]),
            signature=str(payload["signature"]),
        )

        approval_client = self.server.approval_client
        if approval_client is None:
            self._send_error_response("Authoritative ApprovalClient unavailable", status=HTTPStatus.SERVICE_UNAVAILABLE)
            return

        try:
            success = approval_client.submit_decision(submission)
            if not success:
                self._send_error_response(
                    f"Decision transition failed: request '{request_id}' could not be transitioned",
                    status=HTTPStatus.CONFLICT,
                )
                return

            self._send_json_response({
                "success": True,
                "approval_id": request_id,
                "decision": submission.decision,
                "space_id": space_id,
            })
        except ClockSkewError as e:
            self._send_error_response(f"Clock skew error: {e}", status=HTTPStatus.BAD_REQUEST)
        except ReplayDetectedError as e:
            self._send_error_response(f"Replay detected: {e}", status=HTTPStatus.CONFLICT)
        except MalformedAuthenticationPayloadError as e:
            self._send_error_response(f"Malformed auth payload: {e}", status=HTTPStatus.BAD_REQUEST)
        except AuthenticationError as e:
            self._send_error_response(f"Authentication failed: {e}", status=HTTPStatus.UNAUTHORIZED)
        except PermissionError as e:
            self._send_error_response(f"Permission denied: {e}", status=HTTPStatus.FORBIDDEN)
        except Exception as e:
            self._send_error_response(f"Internal error: {e}", status=HTTPStatus.INTERNAL_SERVER_ERROR)

    def _handle_prompt(self, space_id: str, payload: dict[str, Any]) -> None:
        """Handle conversational prompt submitted to a space via GoalAnalyzer (SCCA §4, §18)."""
        prompt = str(payload.get("prompt", "")).strip()
        if not prompt:
            self._send_error_response("Prompt text cannot be empty", status=HTTPStatus.BAD_REQUEST)
            return

        command_id = str(payload.get("command_id") or f"cmd-{int(time.time() * 1000)}")
        params = payload.get("params", {})
        constraints = payload.get("constraints", [])
        live_llm = payload.get("live_llm")

        command = Command(
            command_id=command_id,
            space_id=space_id,
            objective=prompt,
            params=params,
            constraints=constraints,
        )

        goal_analyzer = GoalAnalyzer(bus=self.server.bus)
        goal_spec = goal_analyzer.analyze_goal(command)

        # SCCA §18: Single agent eligibility fast-path
        single_agent = goal_spec.single_agent_eligible
        exec_mode = "direct_single_agent" if single_agent else "multi_agent_team"

        # Synthesize conversational response
        response_text = self._synthesize_prompt_response(prompt, space_id, goal_spec, live_llm=live_llm)

        # Durably record turn in SpaceHistoryStore (WP-1)
        turn_id = f"turn-{command_id}"
        turn = DialogueTurn(
            turn_id=turn_id,
            space_id=space_id,
            user_prompt=prompt,
            assistant_response=response_text,
            timestamp=time.time(),
            goal_id=goal_spec.goal_id,
            status="completed",
            single_agent_eligible=single_agent,
            required_capabilities=goal_spec.required_capabilities,
        )
        try:
            self.server.history_store.append_turn(turn)
        except Exception as e:
            logger.error(f"Failed to record dialogue turn in space '{space_id}': {e}")

        # Automatically extract generated HTML code blocks and register them in space artifact store (WP-4)
        html_blocks = re.findall(r"```html\s*\n([\s\S]*?)```", response_text, re.IGNORECASE)
        for idx, html_code in enumerate(html_blocks):
            if not html_code.strip():
                continue
            art_name = f"preview_{command_id}_{idx}.html"
            try:
                self.server.artifact_store.register_artifact(
                    space_id=space_id,
                    name=art_name,
                    content=html_code,
                    mime_type="text/html",
                    metadata={"source": "assistant_response", "command_id": command_id},
                )
            except Exception as e:
                logger.warning(f"Failed to auto-register HTML artifact: {e}")

        self._send_json_response({
            "command_id": command_id,
            "space_id": space_id,
            "goal_id": goal_spec.goal_id,
            "objective": goal_spec.objective,
            "required_capabilities": goal_spec.required_capabilities,
            "single_agent_eligible": single_agent,
            "execution_mode": exec_mode,
            "response": response_text,
            "status": "completed",
        })

    def _synthesize_prompt_response(
        self,
        prompt: str,
        space_id: str,
        goal_spec: Any,
        live_llm: bool | None = None,
    ) -> str:
        """Synthesize a structured markdown response fulfilling the prompt under SCCA §18."""
        from channels.synthesizer import synthesize_response

        return synthesize_response(prompt, space_id, goal_spec, live_llm=live_llm)

    def _handle_sse_stream(self, space_id: str) -> None:
        """Stream real-time pulses for space via Server-Sent Events."""
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self._send_cors_headers()
        self.end_headers()

        # Create a thread-safe pulse queue for this SSE connection
        pulse_q: queue.Queue[dict[str, Any] | None] = queue.Queue(maxsize=1000)

        def subscriber_cb(pulse: Any) -> None:
            try:
                # Filter by space_id if pulse has space_id attribute
                p_space = getattr(pulse, "space_id", None)
                if p_space is None or p_space == space_id or space_id == "*":
                    data = {
                        "id": getattr(pulse, "id", None),
                        "type": getattr(pulse, "type", None),
                        "severity": str(getattr(pulse, "severity", "")),
                        "space_id": p_space,
                        "timestamp": str(getattr(pulse, "timestamp", "")),
                        "payload": getattr(pulse, "payload", {}),
                        "taint": bool(getattr(pulse, "taint", False)),
                    }
                    pulse_q.put_nowait(data)
            except Exception:
                pass

        sub = None
        bus = self.server.bus
        if bus is not None and hasattr(bus, "subscribe"):
            sub = bus.subscribe(subscriber_cb)

        # Send initial connected message
        try:
            init_msg = f"event: connected\ndata: {json.dumps({'space_id': space_id, 'timestamp': time.time()})}\n\n"
            self.wfile.write(init_msg.encode("utf-8"))
            self.wfile.flush()

            while self.server.is_running:
                try:
                    item = pulse_q.get(timeout=1.0)
                    if item is None:
                        break
                    line = f"data: {json.dumps(item)}\n\n"
                    self.wfile.write(line.encode("utf-8"))
                    self.wfile.flush()
                except queue.Empty:
                    # Keepalive ping
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            if sub is not None and bus is not None and hasattr(bus, "unsubscribe"):
                try:
                    bus.unsubscribe(sub)
                except Exception:
                    pass


class DaemonHTTPServer(ThreadingHTTPServer):
    """Custom ThreadingHTTPServer providing runtime and store bindings."""

    ALLOWED_EXTENSIONS = frozenset({
        ".txt", ".md", ".json", ".py", ".js", ".ts", ".html", ".css",
        ".yaml", ".yml", ".csv", ".rs", ".go", ".c", ".cpp", ".java",
        ".sh", ".toml", ".sql", ".xml"
    })
    MAX_FILE_BYTES = 2 * 1024 * 1024  # 2MB v1.0.1 policy limit

    def __init__(
        self,
        config: DaemonConfig,
        authenticator: DaemonAuthenticator,
        approval_client: ApprovalClient | None = None,
        bus: Any | None = None,
        pulse_store: Any | None = None,
        space_registry: dict[str, Any] | None = None,
        attention_budget: Any | None = None,
        history_store: SpaceHistoryStore | None = None,
        artifact_store: SpaceArtifactStore | None = None,
        node_registry: Any | None = None,
        memory_adapter: Any | None = None,
    ) -> None:
        super().__init__((config.host, config.port), DaemonRequestHandler)
        self.config = config
        self.authenticator = authenticator
        self.approval_client = approval_client
        self.bus = bus
        self.pulse_store = pulse_store
        self.space_registry = space_registry or {}
        self.attention_budget = attention_budget
        self._space_lock = threading.RLock()
        self.history_store = history_store or SpaceHistoryStore(pulse_store=self.pulse_store)
        self.artifact_store = artifact_store or SpaceArtifactStore()
        self.node_registry = node_registry
        self.memory_adapter = memory_adapter
        self._spaces_catalog_dir = Path.home() / ".ryu" / "spaces"
        self._load_registered_spaces()
        self.is_running = True

    def _load_registered_spaces(self) -> None:
        """Load and instantiate persistent spaces on daemon startup."""
        registry_file = self._spaces_catalog_dir / "registry.json"
        if registry_file.is_file():
            try:
                with open(registry_file, "r", encoding="utf-8") as f:
                    spaces_meta = json.load(f)
                from core.space import create_space
                for s_id, s_info in spaces_meta.items():
                    if s_id not in self.space_registry:
                        try:
                            kernel = create_space(
                                space_id=s_id,
                                owner_id=s_info.get("owner_id", "human_operator"),
                                bus=self.bus,
                                budget=float(s_info.get("budget", 0.0)),
                            )
                            self.space_registry[s_id] = kernel
                        except Exception as e:
                            logger.error(f"Failed to restore space '{s_id}': {e}")
            except Exception as e:
                logger.error(f"Failed to read spaces registry: {e}")

        # Ensure default space is registered
        if "default" not in self.space_registry:
            from core.space import create_space
            try:
                kernel = create_space(space_id="default", owner_id="human_operator", bus=self.bus)
                self.space_registry["default"] = kernel
            except Exception as e:
                logger.error(f"Failed to initialize default space: {e}")

    def _save_space_metadata(self, space_id: str, meta: dict[str, Any]) -> None:
        registry_file = self._spaces_catalog_dir / "registry.json"
        registry_file.parent.mkdir(parents=True, exist_ok=True)
        catalog = {}
        if registry_file.is_file():
            try:
                with open(registry_file, "r", encoding="utf-8") as f:
                    catalog = json.load(f)
            except Exception:
                pass
        catalog[space_id] = meta
        with open(registry_file, "w", encoding="utf-8") as f:
            json.dump(catalog, f, indent=2, ensure_ascii=False)

    def create_space(
        self,
        space_id: str,
        name: str = "",
        owner_id: str = "human_operator",
        budget: float = 0.0,
        attention_limit: int = 3,
    ) -> dict[str, Any]:
        """Authoritative Space creation path: instantiates SpaceKernel, emits space.created pulse."""
        with self._space_lock:
            if not space_id or not re.match(r"^[a-zA-Z0-9_-]{1,64}$", space_id):
                raise ValueError("Invalid space_id format: must be 1-64 alphanumeric characters, hyphens or underscores")

            if space_id in self.space_registry:
                raise KeyError(f"Space '{space_id}' already exists")

            from core.space import create_space
            kernel = create_space(
                space_id=space_id,
                owner_id=owner_id,
                bus=self.bus,
                budget=budget,
            )
            self.space_registry[space_id] = kernel

            space_info = {
                "space_id": space_id,
                "name": name or space_id,
                "owner_id": owner_id,
                "status": "active",
                "budget": budget,
                "attention_limit": attention_limit,
                "created_at": time.time(),
            }
            self._save_space_metadata(space_id, space_info)
            return space_info

    def get_space(self, space_id: str) -> dict[str, Any] | None:
        spaces = self.list_spaces()
        for s in spaces:
            if s["space_id"] == space_id:
                return s
        return None

    def list_spaces(self) -> list[dict[str, Any]]:
        result = []
        registry_file = self._spaces_catalog_dir / "registry.json"
        meta_catalog = {}
        if registry_file.is_file():
            try:
                with open(registry_file, "r", encoding="utf-8") as f:
                    meta_catalog = json.load(f)
            except Exception:
                pass

        all_ids = set(self.space_registry.keys()) | set(meta_catalog.keys())
        if not all_ids:
            all_ids = {"default"}

        for s_id in sorted(all_ids):
            meta = meta_catalog.get(s_id, {})
            name = meta.get("name")
            if not name and s_id in self.space_registry:
                name = getattr(self.space_registry[s_id], "name", s_id)
            result.append({
                "space_id": s_id,
                "name": name or s_id,
                "status": "active",
                "owner_id": meta.get("owner_id", "human_operator"),
                "created_at": meta.get("created_at", 0.0),
            })
        return result

    def get_history(self, space_id: str, limit: int = 100) -> list[dict[str, Any]]:
        return self.history_store.get_history(space_id, limit=limit)

    def list_artifacts(self, space_id: str) -> list[dict[str, Any]]:
        return self.artifact_store.list_artifacts(space_id)

    def get_artifact_content(self, space_id: str, artifact_id: str) -> tuple[str, str] | None:
        return self.artifact_store.get_artifact_content(space_id, artifact_id)

    def handle_file_ingress(
        self,
        space_id: str,
        filename: str,
        content: str,
        mime_type: str = "text/plain",
    ) -> dict[str, Any]:
        """Handle sandboxed file ingress with path traversal checks, size enforcement, and taint tagging."""
        if ".." in filename or "/" in filename or "\\" in filename or "\x00" in filename:
            raise ValueError("Path traversal detected or invalid filename characters")

        base_name = os.path.basename(filename).strip()
        if not base_name or base_name.startswith("."):
            raise ValueError("Invalid or hidden filename")

        if base_name in ("passwd", "shadow", "id_rsa", "id_ed25519"):
            raise ValueError("Forbidden sensitive filename")

        ext = os.path.splitext(base_name)[1].lower()
        if ext not in self.ALLOWED_EXTENSIONS:
            raise TypeError(f"Unsupported file extension '{ext}'. Allowed: {sorted(self.ALLOWED_EXTENSIONS)}")

        raw_bytes = content.encode("utf-8")
        if len(raw_bytes) > self.MAX_FILE_BYTES:
            raise OverflowError(f"File size {len(raw_bytes)} bytes exceeds maximum allowed limit of {self.MAX_FILE_BYTES} bytes (2MB)")

        if "\x00" in content:
            raise TypeError("Binary content not supported: file contains null bytes")

        ingress_dir = (self._spaces_catalog_dir / space_id / "ingress").resolve()
        ingress_dir.mkdir(parents=True, exist_ok=True)
        dest_path = (ingress_dir / base_name).resolve()
        if not str(dest_path).startswith(str(ingress_dir)):
            raise PermissionError("Path traversal violation in sandbox write")

        with open(dest_path, "w", encoding="utf-8") as f:
            f.write(content)

        art_record = self.artifact_store.register_artifact(
            space_id=space_id,
            name=base_name,
            content=content,
            mime_type=mime_type or "text/plain",
            metadata={"source": "file_ingress", "original_name": filename},
        )

        if self.bus is not None and hasattr(self.bus, "publish"):
            from datetime import datetime, timezone
            from ryu.pulse_bus.pulse import Pulse, Severity
            try:
                self.bus.publish(
                    Pulse(
                        id=f"pulse-taint-ingress-{art_record.artifact_id}",
                        space_id=space_id,
                        type="security.taint.detected",
                        severity=Severity.WARNING,
                        source="channel_daemon.ingress",
                        correlation_id=f"corr-ingress-{art_record.artifact_id}",
                        payload={
                            "correlation_id": f"corr-ingress-{art_record.artifact_id}",
                            "source_pulse_id": f"ingress-{art_record.artifact_id}",
                            "reason": f"External user file ingress '{base_name}' ({len(raw_bytes)} bytes)",
                        },
                        taint=True,
                        timestamp=datetime.now(timezone.utc),
                    )
                )
            except Exception as e:
                logger.error(f"Failed to publish taint pulse: {e}")

        return {
            "success": True,
            "filename": base_name,
            "bytes_written": len(raw_bytes),
            "sha256": art_record.sha256,
            "artifact_id": art_record.artifact_id,
            "space_id": space_id,
        }

    def list_nodes(self) -> list[dict[str, Any]]:
        if self.node_registry is not None and hasattr(self.node_registry, "list_nodes"):
            nodes = self.node_registry.list_nodes()
            result = []
            for n in nodes:
                devices = self.node_registry.list_devices(n.node_id) if hasattr(self.node_registry, "list_devices") else []
                result.append({
                    "node_id": n.node_id,
                    "platform": str(getattr(n, "platform", "unknown")),
                    "runtime_state": (
                        n.runtime_state.value
                        if hasattr(n.runtime_state, "value")
                        else str(n.runtime_state)
                    ),
                    "trust_tier": (
                        n.trust_tier.value
                        if hasattr(n.trust_tier, "value")
                        else str(n.trust_tier)
                    ),
                    "device_count": len(devices),
                    "devices": [
                        {
                            "device_id": d.device_id,
                            "device_type": (
                                d.device_type.value
                                if hasattr(d.device_type, "value")
                                else str(d.device_type)
                            ),
                            "state": (
                                d.availability_state.value
                                if hasattr(d.availability_state, "value")
                                else str(d.availability_state)
                            ),
                        }
                        for d in devices
                    ],
                })
            return result
        return []

    def get_space_memory(self, space_id: str) -> dict[str, Any]:
        if self.memory_adapter is not None:
            from core.space.memory_protocol import ExperienceQuery
            experiences = []
            if hasattr(self.memory_adapter, "list_experiences"):
                try:
                    exps = self.memory_adapter.list_experiences(space_id)
                    for e in exps:
                        experiences.append({
                            "experience_id": getattr(e, "experience_id", ""),
                            "outcome": getattr(e, "outcome", ""),
                            "counterfactual": getattr(e, "counterfactual", ""),
                            "situation": getattr(e, "situation", {}),
                        })
                except Exception as e:
                    logger.error(f"Error querying memory experiences: {e}")
            elif hasattr(self.memory_adapter, "search_experiences"):
                try:
                    q = ExperienceQuery(space_id=space_id, limit=50)
                    exps = self.memory_adapter.search_experiences(q)
                    for e in exps:
                        experiences.append({
                            "experience_id": getattr(e, "experience_id", ""),
                            "outcome": getattr(e, "outcome", ""),
                            "counterfactual": getattr(e, "counterfactual", ""),
                            "situation": getattr(e, "situation", {}),
                        })
                except Exception as e:
                    logger.error(f"Error querying memory experiences: {e}")

            global_knowledge = []
            if hasattr(self.memory_adapter, "get_global_knowledge"):
                try:
                    knowledges = self.memory_adapter.get_global_knowledge()
                    for k in knowledges:
                        global_knowledge.append({
                            "knowledge_id": getattr(k, "knowledge_id", ""),
                            "topic": getattr(k, "topic", ""),
                            "content": getattr(k, "content", ""),
                        })
                except Exception:
                    pass

            return {
                "space_id": space_id,
                "experiences": experiences,
                "experience_count": len(experiences),
                "global_knowledge": global_knowledge,
            }
        return {
            "space_id": space_id,
            "experiences": [],
            "experience_count": 0,
            "global_knowledge": [],
        }

    def get_space_attention(self, space_id: str) -> dict[str, Any]:
        """Surface dynamic attention budget state without hardcoded N."""
        limit = 3
        active_count = 0
        queued_count = 0
        is_saturated = False
        active_approvals: list[dict[str, Any]] = []
        queued_approvals: list[dict[str, Any]] = []

        mgr = self.approval_client.manager if self.approval_client is not None else None
        budget = self.attention_budget or (getattr(mgr, "attention_budget", None) if mgr else None)
        if budget is not None:
            limit = budget.get_limit(space_id) if hasattr(budget, "get_limit") else getattr(budget, "default_limit", 3)
            is_saturated = budget.is_saturated(space_id) if hasattr(budget, "is_saturated") else False

        if mgr is not None:
            # Retrieve active and queued requests
            all_reqs = mgr.store.list_by_space(space_id)
            for req in all_reqs:
                req_dict = self._request_to_dict(req)
                if req.queue_state == "active" and req.status == "pending":
                    active_approvals.append(req_dict)
                elif req.queue_state == "queued":
                    queued_approvals.append(req_dict)

            queued_count = len(queued_approvals)
            if active_count == 0:
                active_count = len(active_approvals)

        return {
            "space_id": space_id,
            "concurrency_limit": limit,
            "active_count": active_count,
            "queued_count": queued_count,
            "is_saturated": is_saturated or (active_count >= limit),
            "active_approvals": active_approvals,
            "queued_approvals": queued_approvals,
        }

    def list_approvals(
        self,
        space_id: str,
        status: str | None = None,
        queue_state: str | None = None,
    ) -> list[dict[str, Any]]:
        if self.approval_client is None or not hasattr(self.approval_client, "list_approvals"):
            return []
        reqs = self.approval_client.list_approvals(space_id=space_id, status=status, queue_state=queue_state)
        return [self._request_to_dict(r) for r in reqs]

    def get_approval(self, space_id: str, request_id: str) -> dict[str, Any] | None:
        if self.approval_client is None or not hasattr(self.approval_client, "get_request"):
            return None
        req = self.approval_client.get_request(request_id)
        if req is None or (space_id != "*" and req.space_id != space_id):
            return None
        return self._request_to_dict(req)

    def list_tasks(self, space_id: str) -> list[dict[str, Any]]:
        if self.space_registry and space_id in self.space_registry:
            sp = self.space_registry[space_id]
            plan = getattr(sp, "active_plan", None)
            if plan is not None:
                tasks = getattr(plan, "tasks", [])
                return [
                    {
                        "task_id": getattr(t, "task_id", str(idx)),
                        "title": getattr(t, "title", f"Task {idx}"),
                        "status": str(getattr(t, "status", "pending")),
                    }
                    for idx, t in enumerate(tasks)
                ]
        return []

    def get_audit(self, space_id: str, limit: int = 50, pulse_type: str | None = None) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        if self.pulse_store is not None and hasattr(self.pulse_store, "read_by_space"):
            try:
                pulses = self.pulse_store.read_by_space(space_id)
                for p in reversed(pulses):
                    p_type = getattr(p, "type", "")
                    if pulse_type and p_type != pulse_type:
                        continue
                    results.append({
                        "id": getattr(p, "id", None),
                        "type": p_type,
                        "severity": str(getattr(p, "severity", "")),
                        "timestamp": str(getattr(p, "timestamp", "")),
                        "payload": getattr(p, "payload", {}),
                        "taint": bool(getattr(p, "taint", False)),
                    })
                    if len(results) >= limit:
                        break
            except Exception as e:
                logger.error(f"Audit query failed: {e}")
        return results

    def _request_to_dict(self, req: Any) -> dict[str, Any]:
        if isinstance(req, dict):
            return req
        status_val = req.status.value if hasattr(req.status, "value") else str(req.status)
        queue_val = req.queue_state.value if hasattr(req.queue_state, "value") else str(req.queue_state)
        tier_val = req.risk_tier.value if hasattr(req.risk_tier, "value") else str(req.risk_tier)
        timeout_val = req.timeout_class.value if hasattr(req.timeout_class, "value") else str(req.timeout_class)
        cap = getattr(req, "capability", getattr(req, "capability_name", ""))

        return {
            "approval_id": req.request_id,
            "request_id": req.request_id,
            "space_id": req.space_id,
            "status": status_val,
            "queue_state": queue_val,
            "risk_tier": tier_val,
            "capability": cap,
            "capability_name": cap,
            "requester_id": getattr(req, "requester_id", ""),
            "approver_id": getattr(req, "approver_id", ""),
            "plan_version": getattr(req, "plan_version", 1),
            "capability_request_hash": getattr(req, "capability_request_hash", ""),
            "parameters": getattr(req, "parameters", {}),
            "taint": getattr(req, "taint", False),
            "summary": getattr(req, "summary", ""),
            "timeout_class": timeout_val,
            "created_at": getattr(req, "created_at", 0.0),
            "expires_at": getattr(req, "expires_at", 0.0),
        }


class LocalDaemon:
    """Manager for starting, stopping, and inspecting the Local Channel Daemon."""

    def __init__(
        self,
        config: DaemonConfig | None = None,
        approval_client: ApprovalClient | None = None,
        bus: Any | None = None,
        pulse_store: Any | None = None,
        space_registry: dict[str, Any] | None = None,
        attention_budget: Any | None = None,
        history_store: SpaceHistoryStore | None = None,
        artifact_store: SpaceArtifactStore | None = None,
        node_registry: Any | None = None,
        memory_adapter: Any | None = None,
    ) -> None:
        self.config = config or DaemonConfig.from_env()
        self.authenticator = DaemonAuthenticator(self.config.auth_token)
        self.server = DaemonHTTPServer(
            config=self.config,
            authenticator=self.authenticator,
            approval_client=approval_client,
            bus=bus,
            pulse_store=pulse_store,
            space_registry=space_registry,
            attention_budget=attention_budget,
            history_store=history_store,
            artifact_store=artifact_store,
            node_registry=node_registry,
            memory_adapter=memory_adapter,
        )
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        host = self.config.host
        if host == "0.0.0.0":
            host = "127.0.0.1"
        return f"http://{host}:{self.config.port}"

    def start(self, block: bool = False) -> None:
        """Start daemon server."""
        if block:
            self.server.serve_forever()
        else:
            self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)
            self._thread.start()

    def stop(self) -> None:
        """Stop daemon server."""
        self.server.is_running = False
        self.server.shutdown()
        self.server.server_close()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
