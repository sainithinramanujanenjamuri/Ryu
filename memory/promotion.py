"""Knowledge Promotion Pipeline: Rigorous cross-space promotion governance.

Enforces SCCA Law 4 ("Knowledge belongs to the Space first"), SCCA Law 5 (Human Gate),
and cryptographic capability token generation (ADR-0035).

Protects against:
- Direct global knowledge writes without promotion authorization
- Unknown, unauthenticated, or unauthorized approvers (ApprovalManager integration)
- Cross-space promotion attempts (immutable requesting Space binding)
- Replay and promotion ID substitution attacks
- Post-evaluation experience tampering (blake2b verification)
- Out-of-band forged approved pulses (audit rejection)

spec §4 (Space Memory), §7 (Adaptation Layer), MEM-005, MEM-006, ADR-0035 — Phase 10
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Protocol

from ryu.pulse_bus.pulse import Pulse, Severity

from core.space.kernel import SpaceKernel
from core.space.memory_protocol import (
    ExperienceRecord,
    KnowledgeEntry,
    PromotionAuthorization,
    SpaceMemoryProtocol,
    compute_promotion_signature,
)
from memory.evaluation import EvaluationModule, FrozenTraceCorpus

logger = logging.getLogger(__name__)


class PulsePublisher(Protocol):
    def publish(self, pulse: Pulse) -> Pulse: ...


class PromotionError(Exception):
    """Domain exception raised when a promotion invariant or security boundary is violated."""

    def __init__(self, reason: str, knowledge_id: str, promotion_id: str) -> None:
        super().__init__(
            f"Promotion error [{reason}] for knowledge_id='{knowledge_id}', promotion_id='{promotion_id}'"
        )
        self.reason = reason
        self.knowledge_id = knowledge_id
        self.promotion_id = promotion_id


@dataclass(frozen=True)
class PromotionRequest:
    """Active, in-flight promotion candidate in PENDING state."""

    knowledge_id: str
    source_space_id: str
    experience_id: str
    evidence_refs: tuple[str, ...]
    promotion_id: str
    experience_hash: str
    approval_request_id: str
    created_at: datetime


class PromotionPipeline:
    """Manages the lifecycle of cross-Space knowledge promotions."""

    def __init__(
        self,
        memory_store: SpaceMemoryProtocol,
        bus: PulsePublisher,
        kernel: SpaceKernel,
        requesting_space_id: str,
        evaluation_threshold: float = 0.5,
    ) -> None:
        if not requesting_space_id or not requesting_space_id.strip():
            raise ValueError("requesting_space_id must not be empty (Law 1)")

        kernel.verify_space_identity(requesting_space_id)
        self.memory_store = memory_store
        self.bus = bus
        self.kernel = kernel
        self._requesting_space_id = requesting_space_id
        self.evaluation_threshold = evaluation_threshold

        self._lock = threading.Lock()
        # [promotion_id] -> PromotionRequest
        self._pending: dict[str, PromotionRequest] = {}
        # consumed / terminal promotion_ids
        self._used_ids: set[str] = set()

    @property
    def requesting_space_id(self) -> str:
        """authoritative immutable Space identity bound to this pipeline."""
        return self._requesting_space_id

    def request(
        self,
        experience: ExperienceRecord,
        corpus: FrozenTraceCorpus,
        evidence_refs: list[str] | None = None,
    ) -> PromotionRequest | None:
        """Initiate a promotion request for an experience belonging to this Space.

        Runs EvaluationModule benchmark against FrozenTraceCorpus. If score >= threshold,
        registers a formal ApprovalRequest with SpaceKernel.approval_mgr and publishes
        a knowledge.promotion.requested Pulse.
        """
        # Cross-space promotion request boundary
        if experience.space_id != self._requesting_space_id:
            raise PromotionError(
                reason="Cross-space promotion request rejected: experience does not belong to pipeline space",
                knowledge_id="",
                promotion_id="",
            )

        # Behavioral evaluation
        eval_result = EvaluationModule.evaluate(experience, corpus)
        if eval_result.score < self.evaluation_threshold:
            logger.info(
                "Promotion rejected by EvaluationModule: score %f < threshold %f",
                eval_result.score,
                self.evaluation_threshold,
            )
            return None

        promotion_id = uuid.uuid4().hex
        knowledge_id = f"know-{uuid.uuid4().hex[:8]}"

        # Blake2b hash of experience record to detect post-eval tampering
        exp_dict = {
            "experience_id": experience.experience_id,
            "space_id": experience.space_id,
            "situation": experience.situation,
            "action": experience.action,
            "outcome": experience.outcome,
            "counterfactual": experience.counterfactual,
            "applicable_context": experience.applicable_context,
            "stored_at": experience.stored_at.isoformat(),
        }
        exp_hash = hashlib.blake2b(
            json.dumps(exp_dict, sort_keys=True).encode("utf-8")
        ).hexdigest()

        # Formal ApprovalRequest in SpaceKernel.approval_mgr
        appr_req = self.kernel.approval_mgr.request_approval(
            request_id=f"promo-appr-{promotion_id}",
            space_id=self._requesting_space_id,
            capability="knowledge.promotion",
            summary=f"Promote knowledge '{knowledge_id}' from experience '{experience.experience_id}'",
        )

        refs = tuple(evidence_refs or [f"corpus:{corpus.corpus_id}:{corpus.version}"])
        req = PromotionRequest(
            knowledge_id=knowledge_id,
            source_space_id=self._requesting_space_id,
            experience_id=experience.experience_id,
            evidence_refs=refs,
            promotion_id=promotion_id,
            experience_hash=exp_hash,
            approval_request_id=appr_req.request_id,
            created_at=datetime.now(timezone.utc),
        )

        with self._lock:
            self._pending[promotion_id] = req

        # Publish knowledge.promotion.requested Pulse
        self.bus.publish(
            Pulse(
                id=f"pulse-promo-req-{promotion_id}",
                space_id=self._requesting_space_id,
                type="knowledge.promotion.requested",
                severity=Severity.INFO,
                source="promotion_pipeline",
                payload={
                    "knowledge_id": knowledge_id,
                    "source_space_id": self._requesting_space_id,
                    "evidence_refs": list(refs),
                },
                taint=False,
                correlation_id=f"corr-promo-{promotion_id}",
                parent_pulse_id=None,
                timestamp=datetime.now(timezone.utc),
            )
        )
        return req

    def approve(
        self,
        promotion_id: str,
        knowledge_id: str,
        approver_id: str,
    ) -> KnowledgeEntry:
        """Approve an in-flight promotion candidate via Human Gate authentication.

        Authenticates approver_id via SpaceKernel.approval_mgr, verifies experience
        integrity, consumes the approval atomically, issues a cryptographic PromotionAuthorization,
        persists the KnowledgeEntry, and emits knowledge.promotion.approved Pulse.
        """
        if not approver_id or not approver_id.strip():
            raise PromotionError(
                reason="Empty approver_id rejected: identification is required",
                knowledge_id=knowledge_id,
                promotion_id=promotion_id,
            )

        with self._lock:
            if promotion_id in self._used_ids:
                raise PromotionError(
                    reason="Replayed promotion approval rejected",
                    knowledge_id=knowledge_id,
                    promotion_id=promotion_id,
                )

            req = self._pending.get(promotion_id)
            if req is None:
                raise PromotionError(
                    reason="Unknown promotion_id rejected",
                    knowledge_id=knowledge_id,
                    promotion_id=promotion_id,
                )

            # Cross-space promotion approval boundary
            if req.source_space_id != self._requesting_space_id:
                raise PromotionError(
                    reason="Cross-space promotion approval rejected: pipeline space does not match request source_space_id",
                    knowledge_id=knowledge_id,
                    promotion_id=promotion_id,
                )

            if req.knowledge_id != knowledge_id:
                raise PromotionError(
                    reason=f"Promotion ID / Knowledge ID mismatch: expected '{req.knowledge_id}' but got '{knowledge_id}'",
                    knowledge_id=knowledge_id,
                    promotion_id=promotion_id,
                )

            # Re-verify experience hash to detect post-evaluation tampering
            current_exp = self.memory_store.get_experience(
                self._requesting_space_id, req.experience_id
            )
            if current_exp is None:
                raise PromotionError(
                    reason="Experience record no longer exists in space memory",
                    knowledge_id=knowledge_id,
                    promotion_id=promotion_id,
                )

            curr_dict = {
                "experience_id": current_exp.experience_id,
                "space_id": current_exp.space_id,
                "situation": current_exp.situation,
                "action": current_exp.action,
                "outcome": current_exp.outcome,
                "counterfactual": current_exp.counterfactual,
                "applicable_context": current_exp.applicable_context,
                "stored_at": current_exp.stored_at.isoformat(),
            }
            curr_hash = hashlib.blake2b(
                json.dumps(curr_dict, sort_keys=True).encode("utf-8")
            ).hexdigest()
            if curr_hash != req.experience_hash:
                raise PromotionError(
                    reason="Experience record tampered post-evaluation: hash mismatch detected",
                    knowledge_id=knowledge_id,
                    promotion_id=promotion_id,
                )

            # Authenticate approver with ApprovalManager
            expected_approver = self.kernel.approval_mgr.get_approver_id(
                self._requesting_space_id
            )
            if approver_id != expected_approver:
                raise PromotionError(
                    reason=f"Unauthorized approver '{approver_id}' rejected for space '{self._requesting_space_id}'",
                    knowledge_id=knowledge_id,
                    promotion_id=promotion_id,
                )

            # Resolve ApprovalRequest via CAS
            resolved = self.kernel.approval_mgr.resolve(
                request_id=req.approval_request_id,
                approved=True,
                approver_id=approver_id,
                reason="Promotion approved by authorized human operator",
            )
            if not resolved:
                raise PromotionError(
                    reason="ApprovalManager resolution failed",
                    knowledge_id=knowledge_id,
                    promotion_id=promotion_id,
                )

            # Consume the approval (single-use CAS)
            consumed = self.kernel.approval_mgr.consume_approval(
                approval_id=req.approval_request_id,
                current_plan_version=1,
                capability_request_hash="",
            )
            if not consumed:
                raise PromotionError(
                    reason="ApprovalManager consumption failed",
                    knowledge_id=knowledge_id,
                    promotion_id=promotion_id,
                )

            # Generate cryptographic capability token
            kernel_key = self.kernel.approval_mgr.get_decision_signing_key(
                self._requesting_space_id
            )
            now_ts = time.time()
            sig = compute_promotion_signature(
                signing_key=kernel_key,
                promotion_id=promotion_id,
                knowledge_id=knowledge_id,
                source_space_id=self._requesting_space_id,
                approver_id=approver_id,
                approval_request_id=req.approval_request_id,
                issued_at=now_ts,
            )
            auth = PromotionAuthorization(
                promotion_id=promotion_id,
                knowledge_id=knowledge_id,
                source_space_id=self._requesting_space_id,
                approver_id=approver_id,
                approval_request_id=req.approval_request_id,
                signature=sig,
                issued_at=now_ts,
            )

            # Atomic promotion state transition
            del self._pending[promotion_id]
            self._used_ids.add(promotion_id)

            now_dt = datetime.now(timezone.utc)
            entry = KnowledgeEntry(
                knowledge_id=knowledge_id,
                source_space_id=self._requesting_space_id,
                content={
                    "situation": current_exp.situation,
                    "recommended_action": current_exp.counterfactual,
                    "applicable_context": current_exp.applicable_context,
                },
                promoted_by=approver_id,
                promotion_pulse_id=f"pulse-promo-appr-{promotion_id}",
                global_version=1,
                promoted_at=now_dt,
            )

            # Persist knowledge with capability token
            self.memory_store.store_knowledge(entry, auth)

            # Publish knowledge.promotion.approved Pulse
            self.bus.publish(
                Pulse(
                    id=entry.promotion_pulse_id,
                    space_id=self._requesting_space_id,
                    type="knowledge.promotion.approved",
                    severity=Severity.INFO,
                    source="promotion_pipeline",
                    payload={
                        "knowledge_id": knowledge_id,
                        "approver_id": approver_id,
                        "global_version": entry.global_version,
                    },
                    taint=False,
                    correlation_id=f"corr-promo-{promotion_id}",
                    parent_pulse_id=f"pulse-promo-req-{promotion_id}",
                    timestamp=now_dt,
                )
            )
            return entry

    def reject(
        self,
        promotion_id: str,
        knowledge_id: str,
        approver_id: str,
        reason: str = "Rejected by approver",
    ) -> None:
        """Reject an in-flight promotion candidate."""
        with self._lock:
            req = self._pending.get(promotion_id)
            if req is None or req.knowledge_id != knowledge_id:
                raise PromotionError(
                    reason="Cannot reject non-existent or mismatched promotion request",
                    knowledge_id=knowledge_id,
                    promotion_id=promotion_id,
                )

            del self._pending[promotion_id]
            self._used_ids.add(promotion_id)

            self.kernel.approval_mgr.resolve(
                request_id=req.approval_request_id,
                approved=False,
                approver_id=approver_id,
                reason=reason,
            )

            self.bus.publish(
                Pulse(
                    id=f"pulse-promo-rej-{promotion_id}",
                    space_id=self._requesting_space_id,
                    type="knowledge.promotion.rejected",
                    severity=Severity.WARNING,
                    source="promotion_pipeline",
                    payload={
                        "knowledge_id": knowledge_id,
                        "approver_id": approver_id,
                        "reason": reason,
                    },
                    taint=False,
                    correlation_id=f"corr-promo-{promotion_id}",
                    parent_pulse_id=f"pulse-promo-req-{promotion_id}",
                    timestamp=datetime.now(timezone.utc),
                )
            )

    def handle_unauthorized_approved_pulse(self, pulse: Pulse) -> None:
        """Audit and reject out-of-band forged knowledge.promotion.approved Pulses."""
        knowledge_id = pulse.payload.get("knowledge_id", "unknown")
        approver_id = pulse.payload.get("approver_id", "unknown")

        logger.warning(
            "FORGERY DETECTED: Unauthorized knowledge.promotion.approved Pulse received: %s",
            pulse.id,
        )

        self.bus.publish(
            Pulse(
                id=f"pulse-forgery-audit-{pulse.id}",
                space_id=pulse.space_id,
                type="knowledge.promotion.rejected",
                severity=Severity.WARNING,
                source="promotion_pipeline_audit",
                payload={
                    "knowledge_id": knowledge_id,
                    "approver_id": approver_id,
                    "reason": "unauthorized_forged_pulse",
                },
                taint=False,
                correlation_id=pulse.correlation_id,
                parent_pulse_id=pulse.id,
                timestamp=datetime.now(timezone.utc),
            )
        )

