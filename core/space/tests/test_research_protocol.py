"""Unit, contract, and adversarial security tests for Research Protocol & Provenance Foundation.

Verifies:
- SCCA Law 1 (Everything happens in a space) & Law 2 (Capabilities requested, never owned)
- Contract IDs: RESEARCH-001, RESEARCH-002, RESEARCH-003, RESEARCH-004, RESEARCH-005
- Contract IDs: PROVENANCE-001, PROVENANCE-002, PROVENANCE-003
- Adversarial Security Vectors: RES-SEC-01..05, PROV-SEC-01..05
- AGENTS.md §7: Deterministic Core Independence (zero external or higher-layer imports)
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from typing import Any

import pytest

from core.space.research_protocol import (
    DefaultDenySourcePolicy,
    EvidenceRelationship,
    ProvenanceIntegrityError,
    ProvenanceInvalidError,
    ProvenanceRecord,
    ResearchConflict,
    ResearchContent,
    ResearchResult,
    ResearchSourceProtocol,
    ResearchSpaceIsolationViolation,
    SourceAuthorizationDecision,
    SourceAuthorizationPolicyProtocol,
    SourceIdentity,
    TransformationStage,
    canonical_json,
    canonicalize_locator,
    compute_sha256,
    verify_provenance_chain,
)

# ── Fixtures & Helpers ────────────────────────────────────────────────────────

def create_source_identity(
    source_id: str = "src-python-docs-01",
    source_type: str = "web_doc",
    locator: str = "https://docs.python.org/3/library/json.html",
    space_id: str = "space-core-14",
    metadata: dict[str, Any] | None = None,
) -> SourceIdentity:
    return SourceIdentity(
        source_id=source_id,
        source_type=source_type,
        locator=locator,
        space_id=space_id,
        metadata=metadata or {},
    )


class MockAllowlistSourcePolicy:
    """Mock allowlist policy for testing authorization semantics."""

    def __init__(self, allowed_prefixes: list[str]) -> None:
        self.allowed_prefixes = allowed_prefixes

    def evaluate_source(self, source: SourceIdentity, space_id: str) -> SourceAuthorizationDecision:
        for prefix in self.allowed_prefixes:
            if source.canonical_locator.startswith(prefix):
                return SourceAuthorizationDecision(
                    is_allowed=True,
                    source_identity=source,
                    reason=f"Matched prefix allowlist: {prefix}",
                    policy_id="mock-allowlist-v1",
                )
        return SourceAuthorizationDecision(
            is_allowed=False,
            source_identity=source,
            reason="Not present in allowlist",
            policy_id="mock-allowlist-v1",
        )


class MockResearchSource:
    """Mock research source implementing ResearchSourceProtocol for test verification."""

    def __init__(self, source_identity: SourceIdentity, raw_payload: str) -> None:
        self._identity = source_identity
        self._raw_payload = raw_payload

    def describe_source(self) -> SourceIdentity:
        return self._identity

    def authorize(
        self,
        space_id: str,
        policy: SourceAuthorizationPolicyProtocol,
    ) -> SourceAuthorizationDecision:
        return policy.evaluate_source(self._identity, space_id)

    def retrieve(
        self,
        space_id: str,
        task_id: str,
        plan_version: int,
        producer: str,
    ) -> ResearchResult:
        if space_id != self._identity.space_id:
            raise ResearchSpaceIsolationViolation(space_id, self._identity.space_id)

        content_hash = compute_sha256(self._raw_payload)
        content = ResearchContent(
            content_id="cnt-raw-001",
            source_identity=self._identity,
            raw_content=self._raw_payload,
            content_hash=content_hash,
            taint=True,
        )
        prov = ProvenanceRecord(
            provenance_id="prov-raw-001",
            source_identity=self._identity,
            space_id=space_id,
            task_id=task_id,
            plan_version=plan_version,
            producer=producer,
            content_hash=content_hash,
            transformation_stage=TransformationStage.RAW,
        )
        return ResearchResult(
            result_id="res-raw-001",
            content=content,
            provenance=prov,
            status="verified",
            space_id=space_id,
            taint=True,
        )

    def extract(
        self,
        raw_result: ResearchResult,
        extraction_criteria: dict[str, Any],
        task_id: str,
        plan_version: int,
        producer: str,
    ) -> ResearchResult:
        extracted_text = f"FACTS extracted according to {extraction_criteria}: {raw_result.content.raw_content[:20]}"
        content_hash = compute_sha256(extracted_text)
        content = ResearchContent(
            content_id="cnt-ext-002",
            source_identity=self._identity,
            raw_content=extracted_text,
            content_hash=content_hash,
            taint=True,
        )
        prov = ProvenanceRecord(
            provenance_id="prov-ext-002",
            source_identity=self._identity,
            space_id=raw_result.space_id,
            task_id=task_id,
            plan_version=plan_version,
            producer=producer,
            content_hash=content_hash,
            transformation_stage=TransformationStage.EXTRACTED,
            parent_provenance_id=raw_result.provenance.provenance_id,
            evidence_relationship=EvidenceRelationship.EXTRACTED_FROM,
        )
        return ResearchResult(
            result_id="res-ext-002",
            content=content,
            provenance=prov,
            status="verified",
            space_id=raw_result.space_id,
            taint=True,
        )


# ── Canonical Hashing & Serialization Tests ──────────────────────────────────

def test_canonical_json_sorting_and_determinism() -> None:
    dict_a = {"b": 2, "a": 1, "nested": {"z": 9, "m": 5}}
    dict_b = {"nested": {"m": 5, "z": 9}, "a": 1, "b": 2}
    assert canonical_json(dict_a) == canonical_json(dict_b)
    assert canonical_json(dict_a) == '{"a":1,"b":2,"nested":{"m":5,"z":9}}'


def test_compute_sha256_stability() -> None:
    text = "Ryu Autonomous Research Protocol"
    hash1 = compute_sha256(text)
    hash2 = compute_sha256(text.encode("utf-8"))
    assert hash1 == hash2
    assert len(hash1) == 64
    assert hash1 == "ea443deb907010fe35ef6490dedb71d96d0e48365c63f0746f4d5618273a7592"


def test_canonicalize_locator_normalizations() -> None:
    # Web URL: lowercase scheme/host, strip trailing slash, preserve path
    web = canonicalize_locator("web_doc", "HTTPS://Docs.Python.ORG/3/library/json.html/")
    assert web == "https://docs.python.org/3/library/json.html"

    # Local file / git repo: forward slashes, normalize path
    file_loc = canonicalize_locator("local_file", "docs\\spec\\..\\architecture\\core.md")
    assert "docs/architecture/core.md" in file_loc
    assert "\\" not in file_loc


# ── Source Identity & Security Checks (RESEARCH-001, RES-SEC-02) ─────────────

def test_source_identity_valid() -> None:
    src = create_source_identity()
    assert src.source_id == "src-python-docs-01"
    assert src.space_id == "space-core-14"
    assert src.canonical_locator == "https://docs.python.org/3/library/json.html"


def test_source_identity_immutable() -> None:
    src = create_source_identity()
    with pytest.raises(FrozenInstanceError):
        src.locator = "https://evil.com"  # type: ignore


@pytest.mark.parametrize(
    "bad_locator",
    [
        "https://api.example.com/v1?token=abcdef123456",
        "https://user:password123@git.example.com/repo.git",
        "https://api.service.org/data?bearer=supersecrettoken",
        "https://cloud.io?api_key=sk-1234567890abcdef",
    ],
)
def test_source_identity_rejects_credentials_in_locator(bad_locator: str) -> None:
    """RES-SEC-02: Embedded credentials in source locator must be rejected."""
    with pytest.raises(ValueError, match="credential-like"):
        SourceIdentity(
            source_id="src-bad-01",
            source_type="web_doc",
            locator=bad_locator,
            space_id="space-core-14",
        )


def test_source_identity_rejects_credential_labels_in_metadata() -> None:
    """RES-SEC-02: Secret labels in metadata keys or token values must be rejected."""
    with pytest.raises(ValueError, match="credential label"):
        SourceIdentity(
            source_id="src-bad-meta",
            source_type="api",
            locator="https://api.example.com/public",
            space_id="space-core-14",
            metadata={"api_key": "any-value"},
        )

    with pytest.raises(ValueError, match="secret token"):
        SourceIdentity(
            source_id="src-bad-token",
            source_type="api",
            locator="https://api.example.com/public",
            space_id="space-core-14",
            metadata={"header_comment": "token: abcdef12345678"},
        )


# ── Source Authorization & Policy (RESEARCH-001, RES-SEC-01) ─────────────────

def test_default_deny_source_policy() -> None:
    """RESEARCH-001: Default-deny policy must reject all sources."""
    policy = DefaultDenySourcePolicy()
    src = create_source_identity()
    decision = policy.evaluate_source(src, "space-core-14")
    assert decision.is_allowed is False
    assert "Default-deny" in decision.reason
    assert decision.policy_id == "default-deny-v1"


def test_allowlist_source_policy_permitted() -> None:
    policy = MockAllowlistSourcePolicy(allowed_prefixes=["https://docs.python.org/"])
    src = create_source_identity(locator="https://docs.python.org/3/library/ast.html")
    decision = policy.evaluate_source(src, "space-core-14")
    assert decision.is_allowed is True
    assert "Matched prefix" in decision.reason


def test_allowlist_source_policy_denied() -> None:
    """RES-SEC-01: Unapproved source retrieval must be denied."""
    policy = MockAllowlistSourcePolicy(allowed_prefixes=["https://docs.python.org/"])
    src = create_source_identity(locator="https://unauthorized-domain.com/notes")
    decision = policy.evaluate_source(src, "space-core-14")
    assert decision.is_allowed is False
    assert decision.is_allowed is False


# ── Content & Integrity (RESEARCH-002, RESEARCH-003, RES-SEC-03) ─────────────

def test_research_content_valid() -> None:
    src = create_source_identity()
    text = "def hello(): return 'world'"
    chash = compute_sha256(text)
    content = ResearchContent(
        content_id="cnt-01",
        source_identity=src,
        raw_content=text,
        content_hash=chash,
        taint=True,
    )
    assert content.content_hash == chash
    assert content.taint is True


def test_research_content_integrity_violation() -> None:
    """RESEARCH-003 & RES-SEC-01: Tampered content hash must be rejected."""
    src = create_source_identity()
    text = "Real content"
    tampered_hash = compute_sha256("Different forged content")
    with pytest.raises(ProvenanceIntegrityError) as exc_info:
        ResearchContent(
            content_id="cnt-tampered",
            source_identity=src,
            raw_content=text,
            content_hash=tampered_hash,
            taint=True,
        )
    assert exc_info.value.expected_hash == tampered_hash
    assert exc_info.value.actual_hash == compute_sha256(text)


def test_research_content_taint_default() -> None:
    """RESEARCH-005 & RES-SEC-03: External research content must be tainted by default."""
    src = create_source_identity()
    text = "external research fact"
    content = ResearchContent(
        content_id="cnt-default-taint",
        source_identity=src,
        raw_content=text,
        content_hash=compute_sha256(text),
    )
    assert content.taint is True


# ── Provenance Record & Immutability (PROVENANCE-001..003, PROV-SEC-01..05) ──

def test_provenance_record_raw_auto_canonical_hash() -> None:
    src = create_source_identity()
    text = "RAW source content bytes"
    chash = compute_sha256(text)
    prov = ProvenanceRecord(
        provenance_id="prov-raw-01",
        source_identity=src,
        space_id="space-core-14",
        task_id="task-research-1",
        plan_version=1,
        producer="research_worker",
        content_hash=chash,
        transformation_stage=TransformationStage.RAW,
    )
    assert prov.canonical_hash is not None
    assert len(prov.canonical_hash) == 64
    assert prov.canonical_hash == prov.compute_canonical_hash()


def test_provenance_record_immutability() -> None:
    """PROV-SEC-04: Mutation of frozen provenance record must be rejected."""
    src = create_source_identity()
    prov = ProvenanceRecord(
        provenance_id="prov-imm-01",
        source_identity=src,
        space_id="space-core-14",
        task_id="task-1",
        plan_version=1,
        producer="worker-1",
        content_hash=compute_sha256("data"),
        transformation_stage=TransformationStage.RAW,
    )
    with pytest.raises(FrozenInstanceError):
        prov.task_id = "task-tampered"  # type: ignore


def test_provenance_record_extracted_requires_parent() -> None:
    """PROVENANCE-001 & PROV-SEC-03: EXTRACTED stage must have parent_provenance_id."""
    src = create_source_identity()
    with pytest.raises(ProvenanceInvalidError, match="parent_provenance_id"):
        ProvenanceRecord(
            provenance_id="prov-extracted-orphan",
            source_identity=src,
            space_id="space-core-14",
            task_id="task-2",
            plan_version=1,
            producer="extractor-1",
            content_hash=compute_sha256("facts"),
            transformation_stage=TransformationStage.EXTRACTED,
            parent_provenance_id=None,
        )


def test_provenance_record_tampered_canonical_hash_rejected() -> None:
    """PROVENANCE-002 & PROV-SEC-01: Mismatched canonical hash must be rejected."""
    src = create_source_identity()
    with pytest.raises(ProvenanceInvalidError, match="Canonical hash mismatch"):
        ProvenanceRecord(
            provenance_id="prov-tampered-hash",
            source_identity=src,
            space_id="space-core-14",
            task_id="task-1",
            plan_version=1,
            producer="worker-1",
            content_hash=compute_sha256("data"),
            transformation_stage=TransformationStage.RAW,
            canonical_hash="0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
        )


# ── Research Conflict Representation (RESEARCH-004, RES-SEC-05) ─────────────

def test_research_conflict_valid() -> None:
    conflict = ResearchConflict(
        conflict_id="conf-01",
        space_id="space-core-14",
        task_id="task-eval-1",
        plan_version=1,
        topic="API deprecation date",
        source_a_provenance_id="prov-src-a",
        source_b_provenance_id="prov-src-b",
        statement_a="Deprecated in v3.11",
        statement_b="Deprecated in v3.13",
    )
    assert conflict.conflict_id == "conf-01"
    assert conflict.source_a_provenance_id != conflict.source_b_provenance_id


def test_research_conflict_rejects_self_conflict() -> None:
    with pytest.raises(ValueError, match="cannot conflict with itself"):
        ResearchConflict(
            conflict_id="conf-self",
            space_id="space-core-14",
            task_id="task-1",
            plan_version=1,
            topic="Same source",
            source_a_provenance_id="prov-same",
            source_b_provenance_id="prov-same",
            statement_a="A",
            statement_b="B",
        )


# ── Research Result Contract & Space Isolation (RESEARCH-001..005, RES-SEC-04) ──

def test_research_result_valid() -> None:
    src = create_source_identity()
    text = "Valid research payload"
    chash = compute_sha256(text)
    content = ResearchContent(
        content_id="cnt-res-01",
        source_identity=src,
        raw_content=text,
        content_hash=chash,
        taint=True,
    )
    prov = ProvenanceRecord(
        provenance_id="prov-res-01",
        source_identity=src,
        space_id="space-core-14",
        task_id="task-1",
        plan_version=1,
        producer="worker-1",
        content_hash=chash,
        transformation_stage=TransformationStage.RAW,
    )
    result = ResearchResult(
        result_id="res-01",
        content=content,
        provenance=prov,
        status="verified",
        space_id="space-core-14",
        taint=True,
    )
    assert result.result_id == "res-01"
    assert result.space_id == "space-core-14"
    assert result.status == "verified"


def test_research_result_cross_space_isolation_rejected() -> None:
    """RES-SEC-04 & PROVENANCE-003: ResearchResult cannot mix spaces."""
    src = create_source_identity(space_id="space-A")
    text = "Cross-space data"
    chash = compute_sha256(text)
    content = ResearchContent(
        content_id="cnt-cross-01",
        source_identity=src,
        raw_content=text,
        content_hash=chash,
        taint=True,
    )
    prov = ProvenanceRecord(
        provenance_id="prov-cross-01",
        source_identity=src,
        space_id="space-A",
        task_id="task-1",
        plan_version=1,
        producer="worker-1",
        content_hash=chash,
        transformation_stage=TransformationStage.RAW,
    )
    # Attempt to assign result to space-B
    with pytest.raises(ResearchSpaceIsolationViolation):
        ResearchResult(
            result_id="res-cross-fail",
            content=content,
            provenance=prov,
            status="verified",
            space_id="space-B",
        )


def test_research_result_content_provenance_hash_mismatch() -> None:
    src = create_source_identity()
    text_content = "Content payload"
    text_prov = "Different provenance payload"
    content = ResearchContent(
        content_id="cnt-mismatch",
        source_identity=src,
        raw_content=text_content,
        content_hash=compute_sha256(text_content),
        taint=True,
    )
    prov = ProvenanceRecord(
        provenance_id="prov-mismatch",
        source_identity=src,
        space_id="space-core-14",
        task_id="task-1",
        plan_version=1,
        producer="worker-1",
        content_hash=compute_sha256(text_prov),
        transformation_stage=TransformationStage.RAW,
    )
    with pytest.raises(ProvenanceIntegrityError):
        ResearchResult(
            result_id="res-mismatch-fail",
            content=content,
            provenance=prov,
            space_id="space-core-14",
        )


def test_research_result_conflicting_status_requires_conflict_entity() -> None:
    """RES-SEC-05: Conflicting results must attach a formal ResearchConflict."""
    src = create_source_identity()
    text = "Contradictory claim"
    chash = compute_sha256(text)
    content = ResearchContent(
        content_id="cnt-conf-01",
        source_identity=src,
        raw_content=text,
        content_hash=chash,
        taint=True,
    )
    prov = ProvenanceRecord(
        provenance_id="prov-conf-01",
        source_identity=src,
        space_id="space-core-14",
        task_id="task-1",
        plan_version=1,
        producer="worker-1",
        content_hash=chash,
        transformation_stage=TransformationStage.RAW,
    )
    with pytest.raises(ValueError, match="requires an attached ResearchConflict"):
        ResearchResult(
            result_id="res-conf-fail",
            content=content,
            provenance=prov,
            status="conflicting",
            conflict=None,
            space_id="space-core-14",
        )


# ── Provenance Chain Verification (PROVENANCE-001..003, PROV-SEC-02, 03, 05) ─

def test_verify_provenance_chain_success() -> None:
    src = create_source_identity(space_id="space-chain-1")
    prov_raw = ProvenanceRecord(
        provenance_id="prov-chain-01",
        source_identity=src,
        space_id="space-chain-1",
        task_id="task-1",
        plan_version=1,
        producer="worker-retrieve",
        content_hash=compute_sha256("raw bytes"),
        transformation_stage=TransformationStage.RAW,
    )
    prov_extracted = ProvenanceRecord(
        provenance_id="prov-chain-02",
        source_identity=src,
        space_id="space-chain-1",
        task_id="task-2",
        plan_version=1,
        producer="worker-extract",
        content_hash=compute_sha256("extracted facts"),
        transformation_stage=TransformationStage.EXTRACTED,
        parent_provenance_id="prov-chain-01",
        evidence_relationship=EvidenceRelationship.EXTRACTED_FROM,
    )
    prov_synthesized = ProvenanceRecord(
        provenance_id="prov-chain-03",
        source_identity=src,
        space_id="space-chain-1",
        task_id="task-3",
        plan_version=1,
        producer="worker-synthesize",
        content_hash=compute_sha256("synthesized solution"),
        transformation_stage=TransformationStage.SYNTHESIZED,
        parent_provenance_id="prov-chain-02",
        evidence_relationship=EvidenceRelationship.SYNTHESIZED_FROM,
    )

    valid, err = verify_provenance_chain([prov_raw, prov_extracted, prov_synthesized], "space-chain-1")
    assert valid is True
    assert err is None


def test_verify_provenance_chain_broken_link_rejected() -> None:
    """PROV-SEC-03: Broken parent link in provenance chain must be rejected."""
    src = create_source_identity(space_id="space-chain-1")
    prov_raw = ProvenanceRecord(
        provenance_id="prov-chain-01",
        source_identity=src,
        space_id="space-chain-1",
        task_id="task-1",
        plan_version=1,
        producer="worker-retrieve",
        content_hash=compute_sha256("raw bytes"),
        transformation_stage=TransformationStage.RAW,
    )
    prov_extracted = ProvenanceRecord(
        provenance_id="prov-chain-02",
        source_identity=src,
        space_id="space-chain-1",
        task_id="task-2",
        plan_version=1,
        producer="worker-extract",
        content_hash=compute_sha256("extracted facts"),
        transformation_stage=TransformationStage.EXTRACTED,
        parent_provenance_id="prov-chain-UNKNOWN",  # Does not match predecessor
        evidence_relationship=EvidenceRelationship.EXTRACTED_FROM,
    )

    valid, err = verify_provenance_chain([prov_raw, prov_extracted], "space-chain-1")
    assert valid is False
    assert "Broken transformation link" in (err or "")


def test_verify_provenance_chain_cycle_or_duplicate_rejected() -> None:
    """PROV-SEC-02: Duplicate ID or cycle in chain must be rejected."""
    src = create_source_identity(space_id="space-chain-1")
    prov_raw = ProvenanceRecord(
        provenance_id="prov-cycle-01",
        source_identity=src,
        space_id="space-chain-1",
        task_id="task-1",
        plan_version=1,
        producer="worker-retrieve",
        content_hash=compute_sha256("raw bytes"),
        transformation_stage=TransformationStage.RAW,
    )

    valid, err = verify_provenance_chain([prov_raw, prov_raw], "space-chain-1")
    assert valid is False
    assert "cycle detected" in (err or "")


def test_verify_provenance_chain_cross_space_rejected() -> None:
    """PROV-SEC-05: Cross-space injection in chain must be rejected."""
    src_a = create_source_identity(space_id="space-A")
    src_b = create_source_identity(space_id="space-B")
    prov_raw = ProvenanceRecord(
        provenance_id="prov-space-01",
        source_identity=src_a,
        space_id="space-A",
        task_id="task-1",
        plan_version=1,
        producer="worker-1",
        content_hash=compute_sha256("data"),
        transformation_stage=TransformationStage.RAW,
    )
    prov_cross = ProvenanceRecord(
        provenance_id="prov-space-02",
        source_identity=src_b,
        space_id="space-B",
        task_id="task-2",
        plan_version=1,
        producer="worker-2",
        content_hash=compute_sha256("data-cross"),
        transformation_stage=TransformationStage.EXTRACTED,
        parent_provenance_id="prov-space-01",
        evidence_relationship=EvidenceRelationship.EXTRACTED_FROM,
    )

    valid, err = verify_provenance_chain([prov_raw, prov_cross], "space-A")
    assert valid is False
    assert "Cross-space violation" in (err or "")


# ── Full Protocol Lifecycle Verification ─────────────────────────────────────

def test_research_source_protocol_full_flow() -> None:
    """Verifies end-to-end workflow: describe -> authorize -> retrieve -> extract -> verify chain."""
    src_identity = create_source_identity(
        locator="https://docs.python.org/3/library/json.html",
        space_id="space-flow-01",
    )
    source = MockResearchSource(
        source_identity=src_identity,
        raw_payload="JSON (JavaScript Object Notation) is a lightweight data-interchange format.",
    )

    # 1. Verify source implements ResearchSourceProtocol
    assert isinstance(source, ResearchSourceProtocol)

    # 2. Describe
    desc = source.describe_source()
    assert desc.source_id == src_identity.source_id

    # 3. Authorize with Allowlist policy
    policy = MockAllowlistSourcePolicy(allowed_prefixes=["https://docs.python.org/"])
    auth_decision = source.authorize("space-flow-01", policy)
    assert auth_decision.is_allowed is True

    # 4. Retrieve RAW content
    raw_res = source.retrieve("space-flow-01", "task-ret-01", 1, "worker-fetch")
    assert raw_res.status == "verified"
    assert raw_res.provenance.transformation_stage == TransformationStage.RAW
    assert raw_res.taint is True

    # 5. Extract structured facts
    ext_res = source.extract(
        raw_res,
        {"topic": "overview"},
        "task-ext-02",
        1,
        "worker-parser",
    )
    assert ext_res.status == "verified"
    assert ext_res.provenance.transformation_stage == TransformationStage.EXTRACTED
    assert ext_res.provenance.parent_provenance_id == raw_res.provenance.provenance_id

    # 6. Verify full provenance chain
    valid, err = verify_provenance_chain(
        [raw_res.provenance, ext_res.provenance],
        "space-flow-01",
    )
    assert valid is True
    assert err is None
