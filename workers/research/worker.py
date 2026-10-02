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
    ModelAssertionSubordinationError,
    ProvenanceIntegrityError,
    ProvenanceInvalidError,
    ProvenanceRecord,
    ResearchConflict,
    ResearchContent,
    ResearchError,
    ResearchResult,
    ResearchSpaceIsolationViolation,
    SourceAuthorizationPolicyProtocol,
    SourceIdentity,
    SourceNotAuthorizedError,
    SynthesisLimitExceededError,
    SynthesisStatus,
    TransformationStage,
    canonical_json,
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
from workers.research.synthesis import (
    ResearchSynthesizer,
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
        synthesizer: ResearchSynthesizer | None = None,
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
        self.synthesizer = synthesizer or ResearchSynthesizer()

    def _is_capability_supported(self, requested_capability: str) -> bool:
        if requested_capability in ("research", "research.retrieve", "research.synthesize"):
            return True
        if requested_capability.startswith("research."):
            return True
        return super()._is_capability_supported(requested_capability)

    def _execute_sandboxed(self, request: ExecutionRequest) -> ExecutionResult:
        """Dispatch sandboxed research operation (retrieval or multi-source synthesis)."""
        args = request.arguments or {}
        action = args.get("action")
        if (
            action == "synthesize"
            or request.capability == "research.synthesize"
            or "research_results" in args
            or "sources" in args
        ):
            return self._execute_synthesis(request)
        return self._execute_retrieval(request)

    def _execute_retrieval(self, request: ExecutionRequest) -> ExecutionResult:
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

        res = ExecutionResult(
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
        object.__setattr__(res, "_research_result", research_res)
        return res

    def _execute_synthesis(self, request: ExecutionRequest) -> ExecutionResult:
        """Execute sandboxed multi-source research synthesis, conflict analysis, and evidence binding."""
        args = request.arguments or {}
        results: list[ResearchResult] = []

        # 1. Gather research results: either provided directly or retrieved from multiple sources
        raw_results = args.get("research_results")
        if raw_results is not None:
            if not isinstance(raw_results, (list, tuple)):
                err = ExecutionError(
                    error_class="terminal.invalid_params",
                    message="'research_results' must be a list of ResearchResult instances",
                    recoverable=False,
                )
                return ExecutionResult(
                    request_id=request.request_id,
                    status="failed",
                    error=err,
                    metrics=ExecutionMetrics(),
                )
            results = list(raw_results)
        elif "sources" in args:
            sources_spec = args.get("sources", [])
            if not isinstance(sources_spec, (list, tuple)):
                err = ExecutionError(
                    error_class="terminal.invalid_params",
                    message="'sources' must be a list of source descriptors",
                    recoverable=False,
                )
                return ExecutionResult(
                    request_id=request.request_id,
                    status="failed",
                    error=err,
                    metrics=ExecutionMetrics(),
                )
            source_policy: SourceAuthorizationPolicyProtocol = args.get(
                "source_policy"
            ) or DefaultDenySourcePolicy()

            # Retrieve each source
            for idx, src_item in enumerate(sources_spec):
                sub_args = dict(src_item) if isinstance(src_item, dict) else {"locator": str(src_item)}
                sub_req = ExecutionRequest(
                    request_id=f"{request.request_id}-sub-{idx}",
                    correlation_id=request.correlation_id,
                    space_id=request.space_id,
                    worker_id=self.worker_id,
                    task_id=f"{request.task_id}-sub-{idx}",
                    plan_version=request.plan_version,
                    capability="research.retrieve",
                    arguments={**sub_args, "source_policy": source_policy},
                )
                sub_res = self._execute_retrieval(sub_req)
                if sub_res.status != "ok":
                    return ExecutionResult(
                        request_id=request.request_id,
                        status=sub_res.status,
                        error=sub_res.error,
                        metrics=ExecutionMetrics(),
                        logs=sub_res.logs,
                    )
                if hasattr(sub_res, "_research_result") and getattr(sub_res, "_research_result"):
                    results.append(getattr(sub_res, "_research_result"))
        else:
            err = ExecutionError(
                error_class="terminal.invalid_params",
                message="Synthesis request requires 'research_results' or 'sources'",
                recoverable=False,
            )
            return ExecutionResult(
                request_id=request.request_id,
                status="failed",
                error=err,
                metrics=ExecutionMetrics(),
            )

        query = str(args.get("query", ""))
        context = args.get("context")

        # 2. Run synthesis engine
        try:
            synthesis = self.synthesizer.synthesize(
                results=results,
                space_id=request.space_id,
                task_id=request.task_id,
                plan_version=request.plan_version,
                query=query,
                context=context,
            )
        except SynthesisLimitExceededError as exc:
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
        except ModelAssertionSubordinationError as exc:
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
        except ResearchSpaceIsolationViolation as exc:
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
        except (ProvenanceIntegrityError, ProvenanceInvalidError) as exc:
            err = ExecutionError(
                error_class="terminal.security_violation",
                message=sanitize_text(str(exc)),
                recoverable=False,
            )
            return ExecutionResult(
                request_id=request.request_id,
                status="failed",
                error=err,
                metrics=ExecutionMetrics(),
            )
        except Exception as exc:
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

        # 3. Create SYNTHESIZED provenance record (PROVENANCE-001)
        report_data = {
            "synthesis_id": synthesis.synthesis_id,
            "space_id": synthesis.space_id,
            "task_id": synthesis.task_id,
            "plan_version": synthesis.plan_version,
            "status": synthesis.status.value,
            "summary": synthesis.summary,
            "claims": [
                {
                    "claim_id": c.claim_id,
                    "statement": c.statement,
                    "source_ids": list(c.source_ids),
                    "provenance_ids": list(c.provenance_ids),
                    "confidence": c.confidence,
                    "taint": c.taint,
                }
                for c in synthesis.claims
            ],
            "conflicts": [
                {
                    "conflict_id": conf.conflict_id,
                    "topic": conf.topic,
                    "source_a_prov": conf.source_a_provenance_id,
                    "source_b_prov": conf.source_b_provenance_id,
                    "statement_a": conf.statement_a,
                    "statement_b": conf.statement_b,
                }
                for conf in synthesis.conflicts
            ],
            "uncertainties": list(synthesis.uncertainties),
            "is_partial": synthesis.is_partial,
            "source_ids": list(synthesis.source_ids),
            "provenance_records": list(synthesis.provenance_records),
            "taint": synthesis.taint,
        }
        report_json = canonical_json(report_data)
        synth_report_hash = compute_sha256(report_json)

        primary_parent_prov_id = synthesis.provenance_records[0] if synthesis.provenance_records else f"prov-{request.task_id}-root"
        primary_source = results[0].content.source_identity if results else SourceIdentity(
            source_id=f"src-{request.task_id}-agg",
            source_type="doc_store",
            locator=f"space://{request.space_id}/research/synthesis",
            space_id=request.space_id,
        )

        synth_prov = ProvenanceRecord(
            provenance_id=f"prov-{request.task_id}-synth",
            source_identity=primary_source,
            space_id=request.space_id,
            task_id=request.task_id,
            plan_version=request.plan_version,
            producer=self.worker_id,
            content_hash=synth_report_hash,
            transformation_stage=TransformationStage.SYNTHESIZED,
            parent_provenance_id=primary_parent_prov_id,
            evidence_relationship=EvidenceRelationship.SYNTHESIZED_FROM,
        )

        # 4. Artifact generation (if base working dir is set)
        artifacts: list[Artifact] = []
        if self.base_working_dir:
            artifact_dir = self.base_working_dir / "artifacts" / "research"
            artifact_dir.mkdir(parents=True, exist_ok=True)

            report_file = artifact_dir / f"{request.task_id}_synthesis_report.json"
            report_file.write_text(report_json, encoding="utf-8")
            artifacts.append(
                Artifact(
                    artifact_id=f"art-{request.task_id}-report",
                    name=f"{request.task_id}_synthesis_report.json",
                    path=str(report_file),
                    mime_type="application/json",
                    size_bytes=len(report_json.encode("utf-8")),
                    sha256=synth_report_hash,
                    metadata={"synthesis_id": synthesis.synthesis_id, "provenance_id": synth_prov.provenance_id},
                )
            )

            summary_file = artifact_dir / f"{request.task_id}_synthesis_summary.md"
            summary_file.write_text(synthesis.summary, encoding="utf-8")
            artifacts.append(
                Artifact(
                    artifact_id=f"art-{request.task_id}-summary",
                    name=f"{request.task_id}_synthesis_summary.md",
                    path=str(summary_file),
                    mime_type="text/markdown",
                    size_bytes=len(synthesis.summary.encode("utf-8")),
                    sha256=compute_sha256(synthesis.summary),
                    metadata={"synthesis_id": synthesis.synthesis_id, "provenance_id": synth_prov.provenance_id},
                )
            )

        # 5. Emit research.conflict_detected if contradiction found
        if synthesis.status == SynthesisStatus.CONTRADICTION and self.bus is not None:
            for conf in synthesis.conflicts:
                self.bus.publish(
                    Pulse(
                        id=f"pulse-conflict-{request.space_id}-{conf.conflict_id}",
                        space_id=request.space_id,
                        type="research.conflict_detected",
                        severity=Severity.WARNING,
                        source="research_worker",
                        timestamp=datetime.now(timezone.utc),
                        payload={
                            "source_a": conf.source_a_provenance_id,
                            "source_b": conf.source_b_provenance_id,
                            "topic": conf.topic,
                            "conflict_summary": f"Conflict between {conf.statement_a[:50]} and {conf.statement_b[:50]}",
                            "task_id": request.task_id,
                            "plan_version": request.plan_version,
                        },
                        taint=True,
                        correlation_id=request.correlation_id,
                    )
                )

        # 6. Return Ok ExecutionResult
        output_data = {
            "synthesis_id": synthesis.synthesis_id,
            "status": synthesis.status.value,
            "claims_count": len(synthesis.claims),
            "conflicts_count": len(synthesis.conflicts),
            "summary": synthesis.summary,
            "uncertainties": list(synthesis.uncertainties),
            "is_partial": synthesis.is_partial,
            "source_ids": list(synthesis.source_ids),
            "provenance_records": list(synthesis.provenance_records),
            "synthesis_provenance_id": synth_prov.provenance_id,
            "taint": True,
        }

        res = ExecutionResult(
            request_id=request.request_id,
            status="ok",
            artifacts=artifacts,
            output_data=output_data,
            taint=True,  # Mandatory taint invariant
            metrics=ExecutionMetrics(),
            logs=[
                f"Synthesized {len(synthesis.claims)} claims from {len(synthesis.source_ids)} sources; status={synthesis.status.value}",
            ],
        )
        object.__setattr__(res, "_synthesis", synthesis)
        object.__setattr__(res, "_provenance", synth_prov)
        return res

