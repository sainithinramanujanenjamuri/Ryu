"""Research Protocol & Provenance Foundation: Core abstractions, dataclasses, and verification.

Defines the boundary between the deterministic Space Kernel / Orchestrator and
concrete research implementations (ADR-0044, spec §16 Component Contracts).
Enforces Space isolation (SPACE-001, ARC-004), Research Allowlist & Provenance (RESEARCH-001..005),
and Provenance Chain Traceability (PROVENANCE-001..003).

Governing Architecture: Space-Centric Cognitive Architecture (SCCA)
Governing Rule: AGENTS.md §7 (Deterministic Core Independence — zero higher-layer imports)
Phase: 14.1
"""

from __future__ import annotations

import hashlib
import json
import posixpath
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Protocol, runtime_checkable

# Regex patterns for detecting embedded credentials or sensitive tokens in locators/metadata
_SENSITIVE_KEY_PATTERN = re.compile(
    r"(?i)\b(password|secret|token|api[_-]?key|bearer|credential|session)\b"
)
_SECRET_URI_PATTERN = re.compile(
    r"(?i)(?:bearer|token|key|password|secret)[\s:=]+['\"]?([A-Za-z0-9_\-\.]{8,})['\"]?"
)
_URI_USERINFO_CREDENTIAL = re.compile(
    r"://[^/\s:]+:[^/@\s]+@"
)


# ── Exceptions ───────────────────────────────────────────────────────────────

class ResearchError(Exception):
    """Base exception for all research protocol operations."""


class SourceNotAuthorizedError(ResearchError):
    """Raised when an unapproved research source is accessed or proposed (RESEARCH-001)."""

    def __init__(self, source_locator: str, space_id: str, reason: str = "") -> None:
        msg = f"Research source '{source_locator}' is not authorized in space '{space_id}'"
        if reason:
            msg += f": {reason}"
        super().__init__(msg)
        self.source_locator = source_locator
        self.space_id = space_id
        self.reason = reason


class SourceNotFoundError(ResearchError):
    """Raised when a research source cannot be located."""


class ContentUnavailableError(ResearchError):
    """Raised when research content cannot be retrieved from an authorized source."""


class ContentInvalidError(ResearchError):
    """Raised when retrieved content is malformed or corrupted."""


class ProvenanceInvalidError(ResearchError):
    """Raised when a provenance record fails structural or cryptographic validation."""


class ProvenanceIntegrityError(ProvenanceInvalidError):
    """Raised when a content hash does not match recorded provenance (PROVENANCE-002)."""

    def __init__(self, expected_hash: str, actual_hash: str, provenance_id: str) -> None:
        super().__init__(
            f"Provenance integrity violation for record '{provenance_id}': "
            f"expected content hash '{expected_hash}', got '{actual_hash}'"
        )
        self.expected_hash = expected_hash
        self.actual_hash = actual_hash
        self.provenance_id = provenance_id


class ResearchSpaceIsolationViolation(ResearchError):
    """Attempted cross-space research access without formal promotion (SCCA Law 1, Law 4, PROVENANCE-003)."""

    def __init__(self, requesting_space: str, target_space: str, entity_id: str = "") -> None:
        super().__init__(
            f"Space isolation violation: requesting space '{requesting_space}' "
            f"cannot access research or provenance of space '{target_space}' (entity: '{entity_id}')."
        )
        self.requesting_space = requesting_space
        self.target_space = target_space
        self.entity_id = entity_id


class ResearchConflictError(ResearchError):
    """Raised when conflicting evidence across sources is detected without a conflict state (RESEARCH-004)."""


# ── Canonical Hashing & Serialization ───────────────────────────────────────

def canonical_json(data: Any) -> str:
    """Deterministic, canonical JSON serialization for hashing and provenance records.

    Rules:
    - Keys are sorted recursively.
    - Datetimes serialized to ISO 8601 UTC with 'Z' suffix.
    - Floating point values formatted consistently.
    - Separators without superfluous whitespace (',', ':').
    """

    def _default(obj: Any) -> Any:
        if isinstance(obj, datetime):
            if obj.tzinfo is None:
                obj = obj.replace(tzinfo=timezone.utc)
            return obj.astimezone(timezone.utc).isoformat()
        if isinstance(obj, Enum):
            return obj.value
        if hasattr(obj, "to_dict") and callable(obj.to_dict):
            return obj.to_dict()
        raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")

    return json.dumps(data, default=_default, sort_keys=True, separators=(",", ":"))


def compute_sha256(data: str | bytes) -> str:
    """Compute deterministic hex SHA-256 digest of string or raw bytes."""
    if isinstance(data, str):
        b = data.encode("utf-8")
    else:
        b = data
    return hashlib.sha256(b).hexdigest()


def canonicalize_locator(source_type: str, raw_locator: str) -> str:
    """Deterministically normalize source locators (URLs, file paths, URIs).

    Strips trailing slashes, normalizes backslashes to forward slashes,
    lowercases schemes and hostnames for HTTP/HTTPS, and strips query credential params.
    """
    clean = raw_locator.strip()
    clean = clean.replace("\\", "/")
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+\-.]*://", clean):
        match = re.match(r"^([a-zA-Z][a-zA-Z0-9+\-.]*://)([^/?#]+)(.*)$", clean)
        if match:
            scheme = match.group(1).lower()
            host = match.group(2).lower()
            rest = match.group(3)
            if rest.endswith("/") and len(rest) > 1:
                rest = rest.rstrip("/")
            elif rest == "/":
                rest = ""
            clean = f"{scheme}{host}{rest}"
    elif source_type in ("local_file", "repository_file", "doc_store"):
        clean = posixpath.normpath(clean)
    return clean


# ── Domain Neutral Data Models ──────────────────────────────────────────────

@dataclass(frozen=True)
class SourceIdentity:
    """Immutable identity and metadata of a research source (RESEARCH-001, RESEARCH-002)."""

    source_id: str
    source_type: str  # "local_file" | "doc_store" | "http_endpoint" | "repository_file" | "api_endpoint"
    locator: str
    space_id: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.source_id or not self.source_id.strip():
            raise ValueError("source_id must not be empty")
        if not self.source_type or not self.source_type.strip():
            raise ValueError("source_type must not be empty")
        if not self.locator or not self.locator.strip():
            raise ValueError("locator must not be empty")
        if not self.space_id or not self.space_id.strip():
            raise ValueError("space_id must not be empty (SCCA Law 1)")

        # Security check: locators and metadata must not embed raw credentials
        if (
            _SENSITIVE_KEY_PATTERN.search(self.locator)
            or _SECRET_URI_PATTERN.search(self.locator)
            or _URI_USERINFO_CREDENTIAL.search(self.locator)
        ):
            raise ValueError(f"Security violation: source locator contains credential-like string: {self.locator}")
        for k, v in self.metadata.items():
            if _SENSITIVE_KEY_PATTERN.search(str(k)):
                raise ValueError(f"Security violation: source metadata key '{k}' contains credential label")
            if isinstance(v, str) and _SECRET_URI_PATTERN.search(v):
                raise ValueError(f"Security violation: source metadata value for '{k}' contains secret token")

    @property
    def canonical_locator(self) -> str:
        return canonicalize_locator(self.source_type, self.locator)


@dataclass(frozen=True)
class SourceAuthorizationDecision:
    """Authorization evaluation decision for a research source (RESEARCH-001)."""

    is_allowed: bool
    source_identity: SourceIdentity
    reason: str
    policy_id: str
    evaluated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        if not self.policy_id:
            raise ValueError("policy_id must not be empty")


@runtime_checkable
class SourceAuthorizationPolicyProtocol(Protocol):
    """Protocol for evaluating whether a research source is authorized within a Space (RESEARCH-001)."""

    def evaluate_source(self, source: SourceIdentity, space_id: str) -> SourceAuthorizationDecision:
        """Evaluate source authorization. Default behavior must be DENY."""
        ...


class DefaultDenySourcePolicy:
    """Default-deny source authorization policy (SCCA Law 2, RESEARCH-001)."""

    def __init__(self, policy_id: str = "default-deny-v1") -> None:
        self.policy_id = policy_id

    def evaluate_source(self, source: SourceIdentity, space_id: str) -> SourceAuthorizationDecision:
        return SourceAuthorizationDecision(
            is_allowed=False,
            source_identity=source,
            reason="Default-deny policy: source is not in explicit allowlist",
            policy_id=self.policy_id,
        )


@dataclass(frozen=True)
class ResearchContent:
    """Neutral representation of retrieved research content (RESEARCH-002, RESEARCH-003).

    Content is UNTRUSTED / TAINTED by default (TAINT-001).
    External content must never be treated as executable code or authoritative instructions.
    """

    content_id: str
    source_identity: SourceIdentity
    raw_content: str
    content_hash: str
    media_type: str = "text/plain"
    taint: bool = True  # Always tainted by default for external inputs
    retrieved_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.content_id:
            raise ValueError("content_id must not be empty")
        if not self.content_hash:
            raise ValueError("content_hash must not be empty")

        # Verify content_hash integrity
        expected_hash = compute_sha256(self.raw_content)
        if self.content_hash.lower() != expected_hash.lower():
            raise ProvenanceIntegrityError(
                expected_hash=self.content_hash,
                actual_hash=expected_hash,
                provenance_id=self.content_id,
            )

        # Space isolation verification
        if not self.source_identity.space_id:
            raise ValueError("source_identity.space_id must not be empty")


class TransformationStage(str, Enum):
    """Lifecycle stage in the research provenance transformation chain (PROVENANCE-001)."""

    RAW = "raw"                   # Original byte stream / raw text as retrieved
    EXTRACTED = "extracted"       # Structured technical facts extracted from raw source
    SYNTHESIZED = "synthesized"   # Human/agent documented synthesis or solution note


class EvidenceRelationship(str, Enum):
    """Directional relationship between research evidence and derived artifacts (EVIDENCE-003)."""

    DERIVED_FROM = "derived_from"
    EXTRACTED_FROM = "extracted_from"
    SYNTHESIZED_FROM = "synthesized_from"
    VALIDATES = "validates"
    SUPERSEDES = "supersedes"
    CONFLICTS_WITH = "conflicts_with"


@dataclass(frozen=True)
class ProvenanceRecord:
    """Cryptographic provenance record tracking origin, task, plan, and transformation lineage.

    Invariants (PROVENANCE-001..003):
    - Immutable after creation.
    - Cryptographically binds source location, content hash, task ID, plan version, and producing worker.
    - Multi-stage transformations (EXTRACTED, SYNTHESIZED) must reference parent_provenance_id.
    - Space isolation: space_id must match throughout transformation chains.
    """

    provenance_id: str
    source_identity: SourceIdentity
    space_id: str
    task_id: str
    plan_version: int
    producer: str
    content_hash: str
    transformation_stage: TransformationStage
    parent_provenance_id: str | None = None
    evidence_relationship: EvidenceRelationship | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    canonical_hash: str = ""

    def __post_init__(self) -> None:
        if not self.provenance_id:
            raise ValueError("provenance_id must not be empty")
        if not self.space_id:
            raise ValueError("space_id must not be empty (SCCA Law 1)")
        if self.space_id != self.source_identity.space_id:
            raise ResearchSpaceIsolationViolation(
                requesting_space=self.space_id,
                target_space=self.source_identity.space_id,
                entity_id=self.provenance_id,
            )
        if not self.task_id:
            raise ValueError("task_id must not be empty")
        if self.plan_version < 1:
            raise ValueError("plan_version must be >= 1")
        if not self.producer:
            raise ValueError("producer must not be empty")
        if not self.content_hash:
            raise ValueError("content_hash must not be empty")

        # Transformation continuity check
        stage = (
            self.transformation_stage.value
            if isinstance(self.transformation_stage, TransformationStage)
            else str(self.transformation_stage)
        )
        if stage in (TransformationStage.EXTRACTED.value, TransformationStage.SYNTHESIZED.value):
            if not self.parent_provenance_id:
                raise ProvenanceInvalidError(
                    f"Transformation stage '{stage}' requires a non-empty parent_provenance_id"
                )

        # Compute canonical hash if not provided
        expected_canonical = self.compute_canonical_hash()
        if not self.canonical_hash:
            object.__setattr__(self, "canonical_hash", expected_canonical)
        elif self.canonical_hash != expected_canonical:
            raise ProvenanceInvalidError(
                f"Canonical hash mismatch for provenance '{self.provenance_id}': "
                f"expected '{expected_canonical}', got '{self.canonical_hash}'"
            )

    def compute_canonical_hash(self) -> str:
        """Compute deterministic SHA-256 digest over canonical fields."""
        payload = {
            "provenance_id": self.provenance_id,
            "source_id": self.source_identity.source_id,
            "locator": self.source_identity.canonical_locator,
            "space_id": self.space_id,
            "task_id": self.task_id,
            "plan_version": self.plan_version,
            "producer": self.producer,
            "content_hash": self.content_hash,
            "transformation_stage": (
                self.transformation_stage.value
                if isinstance(self.transformation_stage, TransformationStage)
                else str(self.transformation_stage)
            ),
            "parent_provenance_id": self.parent_provenance_id or "",
            "evidence_relationship": (
                self.evidence_relationship.value
                if self.evidence_relationship
                else ""
            ),
        }
        return compute_sha256(canonical_json(payload))


@dataclass(frozen=True)
class ResearchConflict:
    """Formal representation of contradictory evidence between research sources (RESEARCH-004)."""

    conflict_id: str
    space_id: str
    task_id: str
    plan_version: int
    topic: str
    source_a_provenance_id: str
    source_b_provenance_id: str
    statement_a: str
    statement_b: str
    detected_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        if not self.conflict_id:
            raise ValueError("conflict_id must not be empty")
        if not self.space_id:
            raise ValueError("space_id must not be empty")
        if not self.source_a_provenance_id or not self.source_b_provenance_id:
            raise ValueError("source_a_provenance_id and source_b_provenance_id must be provided")
        if self.source_a_provenance_id == self.source_b_provenance_id:
            raise ValueError("A source cannot conflict with itself using the same provenance_id")


@dataclass(frozen=True)
class ResearchResult:
    """Output contract produced by a research source or worker (RESEARCH-001..005)."""

    result_id: str
    content: ResearchContent
    provenance: ProvenanceRecord
    status: str = "verified"  # "verified" | "conflicting" | "insufficient"
    conflict: ResearchConflict | None = None
    space_id: str = ""
    taint: bool = True

    def __post_init__(self) -> None:
        if not self.result_id:
            raise ValueError("result_id must not be empty")
        eff_space = self.space_id or self.content.source_identity.space_id
        if not eff_space:
            raise ValueError("space_id must not be empty")
        if self.space_id != eff_space:
            object.__setattr__(self, "space_id", eff_space)

        # Cross-space integrity
        if self.content.source_identity.space_id != eff_space or self.provenance.space_id != eff_space:
            raise ResearchSpaceIsolationViolation(
                requesting_space=eff_space,
                target_space=self.provenance.space_id,
                entity_id=self.result_id,
            )

        # Hash integrity
        if self.content.content_hash != self.provenance.content_hash:
            raise ProvenanceIntegrityError(
                expected_hash=self.provenance.content_hash,
                actual_hash=self.content.content_hash,
                provenance_id=self.provenance.provenance_id,
            )

        # Conflict integrity check
        if self.status == "conflicting" and self.conflict is None:
            raise ValueError("Status 'conflicting' requires an attached ResearchConflict entity")


# ── Provenance Chain Validation Utilities ───────────────────────────────────

def verify_provenance_chain(
    chain: list[ProvenanceRecord],
    expected_space_id: str,
) -> tuple[bool, str | None]:
    """Verify that a sequence of provenance records forms an unbroken, single-space causal chain.

    Checks:
    1. All records belong strictly to expected_space_id.
    2. Root record has transformation_stage == RAW and parent_provenance_id == None.
    3. Each downstream record points to the immediate predecessor's provenance_id.
    4. Canonical hashes of all records are mathematically valid.
    5. No cycles exist in the chain.
    """
    if not chain:
        return False, "Provenance chain is empty"

    seen_ids: set[str] = set()

    for idx, record in enumerate(chain):
        # 1. Space isolation
        if record.space_id != expected_space_id:
            return False, f"Cross-space violation: record '{record.provenance_id}' space '{record.space_id}' != '{expected_space_id}'"

        # 2. Duplicate detection
        if record.provenance_id in seen_ids:
            return False, f"Duplicate provenance_id or cycle detected: '{record.provenance_id}'"
        seen_ids.add(record.provenance_id)

        # 3. Canonical hash verification
        expected_hash = record.compute_canonical_hash()
        if record.canonical_hash != expected_hash:
            return False, f"Tampered canonical hash in record '{record.provenance_id}'"

        # 4. Chain link verification
        if idx == 0:
            if record.transformation_stage != TransformationStage.RAW:
                return False, f"Root provenance record must have stage RAW; got {record.transformation_stage}"
            if record.parent_provenance_id is not None:
                return False, f"Root provenance record must have parent_provenance_id None; got {record.parent_provenance_id}"
        else:
            prev_record = chain[idx - 1]
            if record.parent_provenance_id != prev_record.provenance_id:
                return False, (
                    f"Broken transformation link at step {idx}: record '{record.provenance_id}' "
                    f"references parent '{record.parent_provenance_id}', but predecessor is '{prev_record.provenance_id}'"
                )

    return True, None


# ── ResearchSourceProtocol ──────────────────────────────────────────────────

@runtime_checkable
class ResearchSourceProtocol(Protocol):
    """Abstract interface defining the boundary for research source integration (ADR-0044, §6).

    Enforces:
    - Pre-dispatch source description and authorization.
    - Retrieval of raw, untrusted content with cryptographic hashing.
    - Fact extraction preserving parent provenance.
    - Zero concrete protocol coupling in core/.
    """

    def describe_source(self) -> SourceIdentity:
        """Return the immutable identity of this research source."""
        ...

    def authorize(
        self,
        space_id: str,
        policy: SourceAuthorizationPolicyProtocol,
    ) -> SourceAuthorizationDecision:
        """Evaluate authorization against the specified Space policy."""
        ...

    def retrieve(
        self,
        space_id: str,
        task_id: str,
        plan_version: int,
        producer: str,
    ) -> ResearchResult:
        """Retrieve raw research content with immutable provenance (RESEARCH-001..003)."""
        ...

    def extract(
        self,
        raw_result: ResearchResult,
        extraction_criteria: dict[str, Any],
        task_id: str,
        plan_version: int,
        producer: str,
    ) -> ResearchResult:
        """Extract structured technical facts from raw content, chaining provenance (RESEARCH-002, PROVENANCE-001)."""
        ...
