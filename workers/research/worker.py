"""Autonomous Research Worker for capability-controlled research execution.

spec §7 (Execution Layer), §10 (Prompt-Injection & Taint Model), ADR-0044,
CONTRACT_MATRIX RESEARCH-001..005, PROVENANCE-001..003 — Phase 14.2

Enforces:
1. SCCA Law 1 (Space Isolation): All research data, provenance, and artifacts are strictly space-bound.
2. SCCA Law 2 (Capabilities Requested, Never Owned): Requires explicit capability authorization.
3. SCCA Law 4 (Knowledge Belongs to Space First): Output stored and scoped within Space.
4. SCCA Law 6 (Deterministic Containment): Failures map to failure taxonomy; unhandled exceptions escalate.
5. TAINT-001 & RESEARCH-005: External research content enters with taint: True unconditionally.
6. WORKER-002: External content is strictly passive data, never executable instructions.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ryu.pulse_bus.bus import PulseBus
from ryu.pulse_bus.pulse import Pulse, Severity

from core.resources.manager import ResourceManager
from core.space.research_protocol import (
    DefaultDenySourcePolicy,
    EvidenceRelationship,
    ProvenanceInvalidError,
    ProvenanceRecord,
    ResearchConflict,
    ResearchContent,
    ResearchError,
    ResearchResult,
    SourceAuthorizationPolicyProtocol,
    SourceIdentity,
    SourceNotAuthorizedError,
    TransformationStage,
    compute_sha256,
    verify_provenance_chain,
)
from workers.base import BaseWorker, sanitize_text
from workers.contract import (
    Artifact,
    ExecutionError,
    ExecutionMetrics,
    ExecutionRequest,
    ExecutionResult,
    WorkerIdentity,
)
from workers.research.retrieval import (
    BoundedSourceRetriever,
    RetrievalConfig,
    RetrievedDocument,
)
from workers.research.security import (
    ContentTooLargeError,
    ContentTypeRejectedError,
    CredentialBearingURLError,
    NetworkSecurityError,
    RedirectLimitExceeded,
    RedirectSecurityViolation,
    SSRFSecurityViolation,
    UnsupportedSchemeError,
)

logger = logging.getLogger(__name__)


class ResearchWorker(BaseWorker):
    """Capability worker executing bounded research tasks under SCCA governance.

    Capabilities supported:
    - research.retrieve
    - research.*
    - research
    """

    def __init__(
        self,
        identity: WorkerIdentity | None = None,
        bus: PulseBus | None = None,
        resource_manager: ResourceManager | None = None,
        base_working_dir: Path | str | None = None,
        retriever_config: RetrievalConfig | None = None,
        retriever: BoundedSourceRetriever | None = None,
    ) -> None:
        ident = identity or WorkerIdentity(
            worker_id="research-worker-01",
            capability="research.retrieve",
            space_id="default-space",
        )
        super().__init__(identity=ident, bus=bus, resource_manager=resource_manager)
        self.base_working_dir = Path(base_working_dir) if base_working_dir else None
        self.retriever_config = retriever_config or RetrievalConfig()
        self.retriever = retriever or BoundedSourceRetriever(self.retriever_config)

    def _is_capability_supported(self, requested_capability: str) -> bool:
        if requested_capability in ("research", "research.retrieve"):
            return True
        if requested_capability.startswith("research."):
            return True
        return super()._is_capability_supported(requested_capability)

    def _execute_sandboxed(self, request: ExecutionRequest) -> ExecutionResult:
        """Execute sandboxed research retrieval, fact extraction, and provenance binding."""
        args = request.arguments or {}

        # 1. Extract target locator and parameters
        raw_locator = args.get("locator") or args.get("url") or args.get("source_locator")
        if not raw_locator:
            err = ExecutionError(
                error_class="terminal.invalid_params",
                message="Research request requires 'locator' or 'url' argument",
                recoverable=False,
            )
            return ExecutionResult(
                request_id=request.request_id,
                status="failed",
                error=err,
                metrics=ExecutionMetrics(),
            )

        source_type = args.get("source_type", "http_endpoint")
        source_id = args.get("source_id") or f"src-{compute_sha256(raw_locator)[:12]}"
        source_policy: SourceAuthorizationPolicyProtocol = args.get(
            "source_policy"
        ) or DefaultDenySourcePolicy()
        extraction_criteria: dict[str, Any] = args.get("extraction_criteria") or {}
        raw_content_override: str | None = args.get("raw_content")

        # 2. Construct SourceIdentity and validate against credential leakage
        try:
            source = SourceIdentity(
                source_id=source_id,
                source_type=source_type,
                locator=raw_locator,
                space_id=request.space_id,
            )
        except ValueError as exc:
            err = ExecutionError(
                error_class="terminal.security_violation",
                message=sanitize_text(f"Invalid research source: {exc}"),
                recoverable=False,
            )
            return ExecutionResult(
                request_id=request.request_id,
                status="denied",
                error=err,
                metrics=ExecutionMetrics(),
            )

        # 3. Source Policy Evaluation (RESEARCH-001)
        auth_decision = source_policy.evaluate_source(source, request.space_id)
        if not auth_decision.is_allowed:
            err = ExecutionError(
                error_class="terminal.permission_denied",
                message=f"Research source '{source.canonical_locator}' denied by policy: {auth_decision.reason}",
                recoverable=False,
                details={"policy_id": auth_decision.policy_id, "locator": source.canonical_locator},
            )
            return ExecutionResult(
                request_id=request.request_id,
                status="denied",
                error=err,
                metrics=ExecutionMetrics(),
                logs=[f"Source authorization denied: {auth_decision.reason}"],
            )

        # 4. Content Retrieval under Network Bounds
        try:
            if raw_content_override is not None:
                # Direct content ingestion mode (offline/unit test fixture)
                raw_bytes = raw_content_override.encode("utf-8")
                content_hash = compute_sha256(raw_bytes)
                doc = RetrievedDocument(
                    canonical_url=source.canonical_locator,
                    raw_content=raw_content_override,
                    content_bytes=raw_bytes,
                    content_hash=content_hash,
                    media_type="text/plain",
                    http_status=200,
                    extracted_text=raw_content_override,
                )
            else:
                doc = self.retriever.retrieve(
                    source.canonical_locator,
                    space_id=request.space_id,
                    source_policy=source_policy,
                )
        except SSRFSecurityViolation as exc:
            err = ExecutionError(
                error_class="terminal.security_violation",
                message=sanitize_text(str(exc)),
                recoverable=False,
            )
            return ExecutionResult(
                request_id=request.request_id,
                status="denied",
                error=err,
                metrics=ExecutionMetrics(),
            )
        except (CredentialBearingURLError, RedirectSecurityViolation) as exc:
            err = ExecutionError(
                error_class="terminal.security_violation",
                message=sanitize_text(str(exc)),
                recoverable=False,
            )
            return ExecutionResult(
                request_id=request.request_id,
                status="denied",
                error=err,
                metrics=ExecutionMetrics(),
            )
        except UnsupportedSchemeError as exc:
            err = ExecutionError(
                error_class="terminal.invalid_params",
                message=sanitize_text(str(exc)),
                recoverable=False,
            )
            return ExecutionResult(
                request_id=request.request_id,
                status="failed",
                error=err,
                metrics=ExecutionMetrics(),
            )
        except SourceNotAuthorizedError as exc:
            err = ExecutionError(
                error_class="terminal.permission_denied",
                message=sanitize_text(str(exc)),
                recoverable=False,
            )
            return ExecutionResult(
                request_id=request.request_id,
                status="denied",
                error=err,
                metrics=ExecutionMetrics(),
            )
        except (ContentTooLargeError, RedirectLimitExceeded) as exc:
            err = ExecutionError(
                error_class="terminal.resource_limit",
                message=sanitize_text(str(exc)),
                recoverable=False,
            )
            return ExecutionResult(
                request_id=request.request_id,
                status="failed",
                error=err,
                metrics=ExecutionMetrics(),
            )
        except ContentTypeRejectedError as exc:
            err = ExecutionError(
                error_class="terminal.invalid_content",
                message=sanitize_text(str(exc)),
                recoverable=False,
            )
            return ExecutionResult(
                request_id=request.request_id,
                status="failed",
                error=err,
                metrics=ExecutionMetrics(),
            )
        except (TimeoutError, NetworkSecurityError, ResearchError, Exception) as exc:
            err = ExecutionError(
                error_class="transient.timeout" if isinstance(exc, TimeoutError) else "transient.network",
                message=sanitize_text(str(exc)),
                recoverable=True,
            )
            return ExecutionResult(
                request_id=request.request_id,
                status="timeout" if isinstance(exc, TimeoutError) else "failed",
                error=err,
                metrics=ExecutionMetrics(),
            )

        # 5. Create Untrusted Research Content & RAW Provenance Record
        raw_content_obj = ResearchContent(
            content_id=f"cnt-{request.task_id}-raw",
            source_identity=source,
            raw_content=doc.raw_content,
            content_hash=doc.content_hash,
            media_type=doc.media_type,
            taint=True,  # SCCA Invariant: external content is always tainted (TAINT-001, RESEARCH-005)
        )

        raw_prov = ProvenanceRecord(
            provenance_id=f"prov-{request.task_id}-raw",
            source_identity=source,
            space_id=request.space_id,
            task_id=request.task_id,
            plan_version=request.plan_version,
            producer=self.worker_id,
            content_hash=doc.content_hash,
            transformation_stage=TransformationStage.RAW,
        )

        # 6. Extract Structured Facts & EXTRACTED Provenance Record
        extracted_text = doc.extracted_text or doc.raw_content
        if "filter_keyword" in extraction_criteria:
            kw = str(extraction_criteria["filter_keyword"]).lower()
            lines = [line for line in extracted_text.splitlines() if kw in line.lower()]
            extracted_text = "\n".join(lines) if lines else extracted_text

        ext_bytes = extracted_text.encode("utf-8")
        ext_hash = compute_sha256(ext_bytes)

        ext_content_obj = ResearchContent(
            content_id=f"cnt-{request.task_id}-ext",
            source_identity=source,
            raw_content=extracted_text,
            content_hash=ext_hash,
            media_type="text/plain",
            taint=True,
        )

        ext_prov = ProvenanceRecord(
            provenance_id=f"prov-{request.task_id}-ext",
            source_identity=source,
            space_id=request.space_id,
            task_id=request.task_id,
            plan_version=request.plan_version,
            producer=self.worker_id,
            content_hash=ext_hash,
            transformation_stage=TransformationStage.EXTRACTED,
            parent_provenance_id=raw_prov.provenance_id,
            evidence_relationship=EvidenceRelationship.EXTRACTED_FROM,
        )

        # 7. Validate Provenance Chain Integrity (PROVENANCE-001..003)
        valid_chain, chain_err = verify_provenance_chain(
            [raw_prov, ext_prov],
            expected_space_id=request.space_id,
        )
        if not valid_chain:
            raise ProvenanceInvalidError(f"Provenance chain integrity failure: {chain_err}")

        # 8. Detect Contradictions / Conflicting Research (RESEARCH-004)
        conflict_obj: ResearchConflict | None = None
        conflict_detected = False
        secondary_claim = args.get("conflicting_statement")
        secondary_prov_id = args.get("conflicting_provenance_id")
        if secondary_claim and secondary_prov_id:
            conflict_detected = True
            conflict_obj = ResearchConflict(
                conflict_id=f"conf-{request.task_id}",
                space_id=request.space_id,
                task_id=request.task_id,
                plan_version=request.plan_version,
                topic=str(args.get("conflict_topic", "Technical fact conflict")),
                source_a_provenance_id=raw_prov.provenance_id,
                source_b_provenance_id=secondary_prov_id,
                statement_a=extracted_text[:100],
                statement_b=secondary_claim[:100],
            )
            # Publish conflict pulse if bus is available
            if self.bus is not None:
                self.bus.publish(
                    Pulse(
                        id=f"pulse-conflict-{request.space_id}-{request.task_id}",
                        space_id=request.space_id,
                        type="research.conflict_detected",
                        severity=Severity.WARNING,
                        source="research_worker",
                        timestamp=datetime.now(timezone.utc),
                        payload={
                            "source_a": raw_prov.source_identity.canonical_locator,
                            "source_b": str(args.get("conflicting_source_locator", "secondary_source")),
                            "topic": conflict_obj.topic,
                            "conflict_summary": f"Conflict between {conflict_obj.statement_a} and {conflict_obj.statement_b}",
                            "task_id": request.task_id,
                            "plan_version": request.plan_version,
                        },
                        taint=True,
                        correlation_id=request.correlation_id,
                    )
                )

        # 9. Artifact Persistence (if working directory is available)
        artifacts: list[Artifact] = []
        if self.base_working_dir:
            artifact_dir = self.base_working_dir / "artifacts" / "research"
            artifact_dir.mkdir(parents=True, exist_ok=True)

            raw_file = artifact_dir / f"{request.task_id}_raw.txt"
            raw_file.write_text(doc.raw_content, encoding="utf-8")
            raw_art = Artifact(
                artifact_id=f"art-{request.task_id}-raw",
                name=f"{request.task_id}_raw.txt",
                path=str(raw_file),
                mime_type=doc.media_type,
                size_bytes=len(doc.content_bytes),
                sha256=doc.content_hash,
                metadata={"provenance_id": raw_prov.provenance_id},
            )
            artifacts.append(raw_art)

            ext_file = artifact_dir / f"{request.task_id}_extracted.txt"
            ext_file.write_text(extracted_text, encoding="utf-8")
            ext_art = Artifact(
                artifact_id=f"art-{request.task_id}-ext",
                name=f"{request.task_id}_extracted.txt",
                path=str(ext_file),
                mime_type="text/plain",
                size_bytes=len(ext_bytes),
                sha256=ext_hash,
                metadata={"provenance_id": ext_prov.provenance_id},
            )
            artifacts.append(ext_art)

        # 10. Emit research.retrieved Pulse (ADR-0044, RESEARCH-001)
        if self.bus is not None:
            self.bus.publish(
                Pulse(
                    id=f"pulse-retrieved-{request.space_id}-{request.task_id}",
                    space_id=request.space_id,
                    type="research.retrieved",
                    severity=Severity.INFO,
                    source="research_worker",
                    timestamp=datetime.now(timezone.utc),
                    payload={
                        "source_location": source.canonical_locator,
                        "source_type": source.source_type,
                        "content_hash": doc.content_hash,
                        "provenance_id": raw_prov.provenance_id,
                        "task_id": request.task_id,
                        "plan_version": request.plan_version,
                    },
                    taint=True,
                    correlation_id=request.correlation_id,
                )
            )

        # 11. Assemble ResearchResult Contract
        research_res = ResearchResult(
            result_id=f"res-{request.task_id}",
            content=ext_content_obj,
            provenance=ext_prov,
            status="conflicting" if conflict_detected else "verified",
            conflict=conflict_obj,
            space_id=request.space_id,
            taint=True,
        )

        output_data = {
            "result_id": research_res.result_id,
            "source_id": source.source_id,
            "locator": source.canonical_locator,
            "raw_content_id": raw_content_obj.content_id,
            "content_hash": doc.content_hash,
            "extracted_hash": ext_hash,
            "extracted_text": extracted_text,
            "raw_provenance_id": raw_prov.provenance_id,
            "extracted_provenance_id": ext_prov.provenance_id,
            "status": research_res.status,
            "redirect_chain": doc.redirect_chain,
            "taint": True,
        }

        return ExecutionResult(
            request_id=request.request_id,
            status="ok",
            artifacts=artifacts,
            output_data=output_data,
            taint=True,  # Mandatory taint invariant
            metrics=ExecutionMetrics(),
            logs=[
                f"Retrieved {len(doc.content_bytes)} bytes from {source.canonical_locator}",
                f"Extracted {len(ext_bytes)} bytes; raw_prov={raw_prov.provenance_id}; ext_prov={ext_prov.provenance_id}",
            ],
        )
