"""Space Memory Protocol: Core abstractions, dataclasses, and cryptographic capability tokens.

Defines the boundary between the deterministic Space Kernel / Orchestrator and
concrete storage implementations (docs/Architecture §4, §16).
Enforces Space isolation (SPACE-001, ARC-004), Experience contract validation (MEM-002),
and promotion authorization (MEM-005, MEM-006).

spec §4 (Space Memory), §16 (Component Contracts), MEM-001..006, ADR-0033..0036 — Phase 10
"""

from __future__ import annotations

import hashlib
import hmac
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Protocol


class MemoryScope(str, Enum):
    """Memory scope enum mapping to schema contract memory.updated.json."""

    TASK = "task"
    AGENT = "agent"
    SPACE = "space"


class MemoryError(Exception):
    """Base exception for all memory operations."""


class MemoryFailure(MemoryError):
    """Non-silent storage failure containing operation context (SCCA Law 6)."""

    def __init__(self, operation: str, reason: str) -> None:
        super().__init__(f"Memory failure during '{operation}': {reason}")
        self.operation = operation
        self.reason = reason


class SpaceIsolationViolation(MemoryError):
    """Attempted cross-space memory traversal without promotion authority (Law 1, Law 4)."""

    def __init__(self, requesting_space: str, target_space: str) -> None:
        super().__init__(
            f"Space isolation violation: requesting space '{requesting_space}' "
            f"cannot access memory of space '{target_space}'."
        )
        self.requesting_space = requesting_space
        self.target_space = target_space


@dataclass(frozen=True)
class ExperienceRecord:
    """Structured experience captured upon task outcome per §16 Component Contracts.

    The counterfactual field is mandatory to ensure learning is actionable (MEM-002).
    """

    experience_id: str
    space_id: str
    situation: dict[str, Any]
    action: dict[str, Any]
    outcome: str
    counterfactual: str
    applicable_context: dict[str, Any]
    stored_at: datetime

    def __post_init__(self) -> None:
        if not self.experience_id or not self.experience_id.strip():
            raise ValueError("experience_id must not be empty")
        if not self.space_id or not self.space_id.strip():
            raise ValueError("space_id must not be empty (Law 1)")
        if not self.counterfactual or not self.counterfactual.strip():
            raise ValueError(
                "counterfactual must not be empty: an ExperienceRecord without a counterfactual "
                "cannot be evaluated for behavioral adaptation (MEM-002, ADR-0034)"
            )


@dataclass(frozen=True)
class KnowledgeEntry:
    """Promoted global knowledge entity approved through the Human Gate (Law 4, Law 5)."""

    knowledge_id: str
    source_space_id: str
    content: dict[str, Any]
    promoted_by: str
    promotion_pulse_id: str
    global_version: int
    promoted_at: datetime

    def __post_init__(self) -> None:
        if not self.knowledge_id or not self.knowledge_id.strip():
            raise ValueError("knowledge_id must not be empty")
        if not self.source_space_id or not self.source_space_id.strip():
            raise ValueError("source_space_id must not be empty")
        if not self.promoted_by or not self.promoted_by.strip():
            raise ValueError("promoted_by must not be empty (MEM-006)")


@dataclass(frozen=True)
class PromotionAuthorization:
    """Cryptographically bound, single-use capability token for global knowledge storage.

    Issued strictly by PromotionPipeline upon authenticating human approval through
    SpaceKernel.approval_mgr (ADR-0035).
    """

    promotion_id: str
    knowledge_id: str
    source_space_id: str
    approver_id: str
    approval_request_id: str
    signature: str
    issued_at: float = field(default_factory=time.time)

    def __post_init__(self) -> None:
        if not self.promotion_id:
            raise ValueError("promotion_id must not be empty")
        if not self.knowledge_id:
            raise ValueError("knowledge_id must not be empty")
        if not self.source_space_id:
            raise ValueError("source_space_id must not be empty")
        if not self.approver_id:
            raise ValueError("approver_id must not be empty")
        if not self.signature:
            raise ValueError("signature must not be empty")


@dataclass(frozen=True)
class ExperienceQuery:
    """Read-only query for retrieving past experiences within a single Space."""

    space_id: str
    situation_hint: dict[str, Any]
    limit: int = 10

    def __post_init__(self) -> None:
        if not self.space_id or not self.space_id.strip():
            raise ValueError("space_id must not be empty (Law 1, Law 4)")


def compute_promotion_signature(
    signing_key: bytes,
    promotion_id: str,
    knowledge_id: str,
    source_space_id: str,
    approver_id: str,
    approval_request_id: str,
    issued_at: float,
) -> str:
    """Compute HMAC-SHA256 signature for a PromotionAuthorization capability token."""
    payload = (
        f"ryu-promotion-grant-v1\n"
        f"{promotion_id}\n"
        f"{knowledge_id}\n"
        f"{source_space_id}\n"
        f"{approver_id}\n"
        f"{approval_request_id}\n"
        f"{issued_at:.6f}"
    )
    return hmac.new(signing_key, payload.encode("utf-8"), hashlib.sha256).hexdigest()


def verify_promotion_authorization(
    auth: PromotionAuthorization, signing_key: bytes
) -> bool:
    """Verify constant-time integrity and authenticity of a PromotionAuthorization token."""
    if not auth.signature or auth.issued_at <= 0.0:
        return False
    expected = compute_promotion_signature(
        signing_key=signing_key,
        promotion_id=auth.promotion_id,
        knowledge_id=auth.knowledge_id,
        source_space_id=auth.source_space_id,
        approver_id=auth.approver_id,
        approval_request_id=auth.approval_request_id,
        issued_at=auth.issued_at,
    )
    return hmac.compare_digest(expected, auth.signature)


class SpaceMemoryProtocol(Protocol):
    """Protocol for Space-scoped memory operations (ADR-0033)."""

    def store_experience(self, record: ExperienceRecord) -> str:
        """Store an experience record in Space-local memory. Returns experience_id."""
        ...

    def get_experience(
        self, space_id: str, experience_id: str
    ) -> ExperienceRecord | None:
        """Retrieve an experience record strictly within the specified space."""
        ...

    def list_experiences(self, space_id: str) -> list[ExperienceRecord]:
        """List all experiences belonging strictly to the specified space."""
        ...

    def query_similar_experiences(
        self, query: ExperienceQuery
    ) -> list[ExperienceRecord]:
        """Query experiences within the query's space_id matching situation hints."""
        ...

    def store_knowledge(
        self, entry: KnowledgeEntry, auth: PromotionAuthorization
    ) -> None:
        """Persist promoted global knowledge. Requires valid, unconsumed PromotionAuthorization."""
        ...

    def get_global_knowledge(self, knowledge_id: str) -> KnowledgeEntry | None:
        """Retrieve a promoted global knowledge entry."""
        ...

