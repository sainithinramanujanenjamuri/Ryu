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
import math
import time
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Protocol, runtime_checkable


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
    Embedding metadata (Phase 15.5) supports durable semantic memory representation.
    """

    experience_id: str
    space_id: str
    situation: dict[str, Any]
    action: dict[str, Any]
    outcome: str
    counterfactual: str
    applicable_context: dict[str, Any]
    stored_at: datetime
    # Phase 15.5 Semantic embedding & retrieval metadata (optional for backward compatibility)
    embedding: tuple[float, ...] | None = None
    embedding_model: str | None = None
    embedding_dimension: int | None = None
    embedding_version: str | None = None
    failure_fingerprint: str | None = None
    provenance_ref: str | None = None

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

        # Fallback failure_fingerprint from applicable_context
        if self.failure_fingerprint is None and "failure_fingerprint" in self.applicable_context:
            fp = self.applicable_context.get("failure_fingerprint")
            if fp is not None:
                object.__setattr__(self, "failure_fingerprint", str(fp))

        # Fallback provenance_ref from applicable_context
        if self.provenance_ref is None and "provenance_ref" in self.applicable_context:
            pref = self.applicable_context.get("provenance_ref")
            if pref is not None:
                object.__setattr__(self, "provenance_ref", str(pref))

        # Phase 15.5: Validate embedding metadata if present
        if self.embedding is not None:
            if not isinstance(self.embedding, tuple):
                object.__setattr__(self, "embedding", tuple(float(x) for x in self.embedding))

            if self.embedding_dimension is None or self.embedding_dimension <= 0:
                raise ValueError(
                    f"embedding_dimension must be a positive integer when embedding is present, "
                    f"got {self.embedding_dimension}"
                )

            if len(self.embedding) != self.embedding_dimension:
                raise ValueError(
                    f"embedding length {len(self.embedding)} does not match declared "
                    f"embedding_dimension {self.embedding_dimension}"
                )

            for i, val in enumerate(self.embedding):
                if not isinstance(val, (int, float)):
                    raise TypeError(f"embedding component at index {i} must be a float, got {type(val).__name__}")
                if math.isnan(val):
                    raise ValueError(f"embedding component at index {i} must not be NaN")
                if math.isinf(val):
                    raise ValueError(f"embedding component at index {i} must not be infinite")

            if not self.embedding_model or not str(self.embedding_model).strip():
                raise ValueError("embedding_model must not be empty when embedding is present")

            if not self.embedding_version or not str(self.embedding_version).strip():
                raise ValueError("embedding_version must not be empty when embedding is present")
        elif self.embedding_dimension is not None:
            raise ValueError("embedding_dimension specified without an embedding vector")

    def to_embedding_result(self) -> EmbeddingResult | None:
        """Construct an EmbeddingResult from this record's embedding fields if present."""
        if (
            self.embedding is None
            or self.embedding_model is None
            or self.embedding_dimension is None
            or self.embedding_version is None
        ):
            return None
        return EmbeddingResult(
            vector=self.embedding,
            model=self.embedding_model,
            dimension=self.embedding_dimension,
            version=self.embedding_version,
        )

    def with_embedding(self, embedding_result: EmbeddingResult) -> ExperienceRecord:
        """Return a copy of this record with the specified embedding metadata attached."""
        return ExperienceRecord(
            experience_id=self.experience_id,
            space_id=self.space_id,
            situation=self.situation,
            action=self.action,
            outcome=self.outcome,
            counterfactual=self.counterfactual,
            applicable_context=self.applicable_context,
            stored_at=self.stored_at,
            embedding=embedding_result.vector,
            embedding_model=embedding_result.model,
            embedding_dimension=embedding_result.dimension,
            embedding_version=embedding_result.version,
            failure_fingerprint=self.failure_fingerprint,
            provenance_ref=self.provenance_ref,
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

    def store_experience(
        self, record: ExperienceRecord, embedding: EmbeddingResult | None = None
    ) -> str:
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


# ── Phase 13: Closed-Loop Experiential Adaptation Protocols (ADR-0043) ────────

@dataclass(frozen=True)
class TaskExecutionOutcome:
    """Core-neutral, verified execution outcome reported by DeterministicDispatcher (ADAPT-001).

    The Dispatcher reports this outcome to an ExperienceObserverProtocol.
    The Dispatcher does NOT construct memory-domain ExperienceRecords;
    the memory subsystem owns reflection, sanitization, and persistence.
    """

    task_id: str
    space_id: str
    plan_version: int
    capability: str
    params: dict[str, Any]
    status: str  # 'completed' | 'failed'
    exit_code: int | None = None
    duration_seconds: float = 0.0
    error_class: str | None = None
    error_message: str | None = None
    failure_fingerprint: str | None = None
    result_ref: str | None = None
    artifact_refs: tuple[str, ...] = field(default_factory=tuple)
    dependencies: tuple[str, ...] = field(default_factory=tuple)
    taint: bool = False
    completed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True)
class ExperienceHint:
    """Contextual, immutable advisory hint derived from past execution experience (ADR-0036, ADR-0043, ADAPT-002).

    Advisory only; cannot directly mutate plans, grant capabilities, or execute workers.
    """

    experience_id: str
    failed_capability: str
    suggested_avoidance: list[str]
    outcome_summary: str
    counterfactual_summary: str
    relevance_score: float = 1.0
    source_space_id: str = ""
    suggested_alternative_capability: str = ""
    target_task_id: str = ""


@runtime_checkable
class ExperienceObserverProtocol(Protocol):
    """Protocol for observing verified task execution outcomes without coupling core to memory (ADAPT-001)."""

    def observe_task_outcome(self, outcome: TaskExecutionOutcome) -> str | None:
        """Observe verified task execution outcome, reflect, persist experience, and return experience_id or None."""
        ...


@runtime_checkable
class AdaptationLayerProtocol(Protocol):
    """Protocol for querying advisory experience hints without coupling core to memory (ADAPT-002, ADAPT-003)."""

    def generate_hints(
        self,
        space_id: str,
        situation_hint: dict[str, Any],
        limit: int = 5,
    ) -> list[ExperienceHint]:
        """Generate advisory experience hints strictly within the specified space."""
        ...


# ── Phase 15.5: Semantic Memory & Experience Retrieval Protocols (ADR-0049, MEM-SEM-003) ──

DEFAULT_MAX_INPUT_CHARS: int = 2048
DEFAULT_MAX_BATCH_SIZE: int = 16


def normalize_embedding_input(
    text: str,
    max_chars: int = DEFAULT_MAX_INPUT_CHARS,
    fail_on_oversized: bool = False,
) -> str:
    """Normalize input text deterministically for embedding generation.

    Enforces Unicode NFKC normalization, whitespace collapsing, and bounded character length.

    Raises:
        TypeError: If input is not a string.
        ValueError: If input is empty, whitespace-only, or oversized when fail_on_oversized=True.
    """
    if not isinstance(text, str):
        raise TypeError(f"Embedding input must be a string, got {type(text).__name__}")

    # Step 1: Unicode normalization (NFKC)
    normalized = unicodedata.normalize("NFKC", text)

    # Step 2: Whitespace normalization (collapse consecutive whitespace, strip ends)
    normalized = " ".join(normalized.split())

    # Step 3: Empty check
    if not normalized:
        raise ValueError("Embedding input text must not be empty or whitespace-only")

    # Step 4: Max length bounding
    if len(normalized) > max_chars:
        if fail_on_oversized:
            raise ValueError(
                f"Embedding input length {len(normalized)} exceeds maximum allowed {max_chars} characters"
            )
        normalized = normalized[:max_chars].rstrip()

    return normalized


@dataclass(frozen=True)
class EmbeddingResult:
    """Immutable, validated embedding representation (MEM-SEM-003, ADR-0049).

    Captures vector components and model provenance metadata with strict numerical validation.
    """

    vector: tuple[float, ...]
    model: str
    dimension: int
    version: str

    def __post_init__(self) -> None:
        if not self.model or not self.model.strip():
            raise ValueError("model must not be empty")
        if not self.version or not str(self.version).strip():
            raise ValueError("version must not be empty")
        if self.dimension <= 0:
            raise ValueError(f"dimension must be positive, got {self.dimension}")

        # Enforce tuple type for immutability
        if not isinstance(self.vector, tuple):
            object.__setattr__(self, "vector", tuple(self.vector))

        if len(self.vector) != self.dimension:
            raise ValueError(
                f"vector length {len(self.vector)} does not match declared dimension {self.dimension}"
            )

        # Numerical validation: finite numbers only (no NaN, no Inf)
        for i, val in enumerate(self.vector):
            if not isinstance(val, (int, float)):
                raise TypeError(f"vector component at index {i} must be a float, got {type(val).__name__}")
            if math.isnan(val):
                raise ValueError(f"vector component at index {i} must not be NaN")
            if math.isinf(val):
                raise ValueError(f"vector component at index {i} must not be infinite")

    def norm(self) -> float:
        """Compute the L2 Euclidean norm of the vector."""
        return math.sqrt(sum(x * x for x in self.vector))

    def to_dict(self) -> dict[str, Any]:
        """Deterministic dictionary serialization."""
        return {
            "model": self.model,
            "dimension": self.dimension,
            "version": self.version,
            "vector": list(self.vector),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EmbeddingResult:
        """Reconstruct EmbeddingResult from serialized dictionary."""
        return cls(
            vector=tuple(float(x) for x in data["vector"]),
            model=str(data["model"]),
            dimension=int(data["dimension"]),
            version=str(data["version"]),
        )


@runtime_checkable
class EmbeddingProviderProtocol(Protocol):
    """Protocol for provider-independent embedding generation (MEM-SEM-003, ADR-0049).

    Core owns this protocol. Implementations reside strictly outside core (AGENTS.md §7).
    Uses standard-library types only; zero dependencies on external ML frameworks.
    """

    @property
    def model_name(self) -> str:
        """Model or provider identifier."""
        ...

    @property
    def dimension(self) -> int:
        """Declared vector dimension."""
        ...

    @property
    def version(self) -> str:
        """Version string of the embedding provider / algorithm."""
        ...

    def embed(self, text: str) -> EmbeddingResult:
        """Generate an embedding for a single text input."""
        ...

    def embed_batch(self, texts: list[str]) -> list[EmbeddingResult]:
        """Generate embeddings for a batch of text inputs preserving order."""
        ...


