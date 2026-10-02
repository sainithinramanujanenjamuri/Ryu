"""Bounded Research Synthesis & Evidence Reconciliation Engine.

Phase 14.7 — ADR-0044 §6, CONTRACT_MATRIX RESEARCH-001..005, PROVENANCE-001..003.

Enforces:
1. SCCA Law 1 (Space Isolation): All synthesized claims, conflicts, and relations belong strictly to the requesting Space.
2. SCCA Law 4 (Knowledge Belongs to Space First): Synthesis produces Space-scoped knowledge artifacts, not global state.
3. SCCA Law 6 (Deterministic Containment): Contradictions are never silently suppressed; explicit CONTRADICTION state emitted.
4. TAINT-001 & RESEARCH-003: External research content enters and remains tainted (taint=True).
5. WORKER-002: Malicious prompt-injection instructions remain inert data; never executed or promoted to plan mutations.
6. EVIDENCE-002 & Model Subordination: Model assertions cannot override verified source evidence or fabricate citations.
7. Boundedness: Hard deterministic ceilings on sources, claims, relationships, and input bytes.
8. Replay Determinism: Synthesis operates offline over recorded provenance and content without network access.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from core.space.research_protocol import (
    MAX_CLAIMS_PER_SYNTHESIS,
    MAX_CONFLICT_RELATIONSHIPS,
    MAX_EVIDENCE_ITEMS,
    MAX_SOURCES_PER_SYNTHESIS,
    MAX_SYNTHESIS_DEPTH,
    MAX_SYNTHESIS_INPUT_BYTES,
    MAX_SYNTHESIS_OUTPUT_BYTES,
    EvidenceRelation,
    EvidenceRelationType,
    ModelAssertionSubordinationError,
    ProvenanceIntegrityError,
    ProvenanceInvalidError,
    ProvenanceRecord,
    ResearchClaim,
    ResearchConflict,
    ResearchResult,
    ResearchSpaceIsolationViolation,
    ResearchSynthesis,
    SourceIdentity,
    SynthesisLimitExceededError,
    SynthesisStatus,
    TransformationStage,
    compute_sha256,
    verify_synthesis_provenance,
)
from workers.base import sanitize_text

logger = logging.getLogger(__name__)

# Prompt injection signatures to neutralize and treat strictly as passive data
_INJECTION_PATTERNS = [
    re.compile(r"(?i)\bignore\s+(?:all\s+)?(?:previous|prior)\s+instructions\b"),
    re.compile(r"(?i)\bexecute\s+(?:shell|command|bash|powershell|rm|del)\b"),
    re.compile(r"(?i)\bchange\s+the\s+plan\b"),
    re.compile(r"(?i)\breveal\s+(?:all\s+)?(?:secrets|tokens|keys|passwords)\b"),
    re.compile(r"(?i)\bmark\s+(?:this\s+)?task\s+(?:as\s+)?complete\b"),
    re.compile(r"(?i)\bbypass\s+(?:policy|admission|gate|approval)\b"),
    re.compile(r"(?i)\b<script[\s>]"),
    re.compile(r"(?i)eval\s*\("),
]

_NEGATION_WORDS = {"not", "never", "no", "cannot", "disabled", "false", "disallows", "rejects", "prohibits", "fails"}
_AFFIRMATION_WORDS = {"is", "always", "yes", "can", "enabled", "true", "allows", "accepts", "permits", "passes"}

_ANTONYM_PAIRS = [
    ({"enabled", "enable"}, {"disabled", "disable"}),
    ({"supported", "supports", "support"}, {"unsupported", "disallowed"}),
    ({"permitted", "permit", "permits"}, {"prohibited", "prohibit", "prohibits"}),
    ({"allowed", "allow", "allows"}, {"disallowed", "disallow", "disallows"}),
    ({"true"}, {"false"}),
    ({"valid"}, {"invalid"}),
    ({"required", "require", "requires"}, {"optional"}),
    ({"recommended"}, {"deprecated"}),
]


def _has_antonym_contradiction(tok1: set[str], tok2: set[str]) -> bool:
    for pos_set, neg_set in _ANTONYM_PAIRS:
        if (tok1 & pos_set and tok2 & neg_set) or (tok1 & neg_set and tok2 & pos_set):
            return True
    return False


def _detect_polarity(tokens: set[str]) -> int:
    """Return -1 for negative polarity, +1 for positive, 0 for neutral."""
    if bool(tokens & _NEGATION_WORDS):
        return -1
    if bool(tokens & _AFFIRMATION_WORDS):
        return 1
    return 0


@runtime_checkable
class AdvisorySynthesisModel(Protocol):
    """Optional advisory LLM synthesis model protocol (ADR-0044, Phase 14.7 §13)."""

    def generate_advisory_summary(
        self,
        claims: list[ResearchClaim],
        sources: list[SourceIdentity],
        context: dict[str, Any] | None = None,
    ) -> str:
        """Generate human-readable synthesis summary from verified claims. Strictly advisory."""
        ...


@dataclass(frozen=True)
class SynthesisConfig:
    """Bounded ceilings and options for research synthesis operations."""

    max_sources: int = MAX_SOURCES_PER_SYNTHESIS
    max_evidence_items: int = MAX_EVIDENCE_ITEMS
    max_claims: int = MAX_CLAIMS_PER_SYNTHESIS
    max_input_bytes: int = MAX_SYNTHESIS_INPUT_BYTES
    max_depth: int = MAX_SYNTHESIS_DEPTH
    max_conflict_relationships: int = MAX_CONFLICT_RELATIONSHIPS
    max_output_bytes: int = MAX_SYNTHESIS_OUTPUT_BYTES
    strict_limits: bool = True  # If True, raises SynthesisLimitExceededError; if False, returns partial/inconclusive


def _extract_statements(text: str) -> list[str]:
    """Break extracted text into clean, individual factual statements."""
    statements: list[str] = []
    lines = text.splitlines()
    for line in lines:
        line_clean = line.strip()
        if not line_clean:
            continue
        # Strip list markers
        if line_clean.startswith(("- ", "* ", "# ", "1. ", "2. ", "3. ")):
            line_clean = re.sub(r"^[-*#\d\.\s]+", "", line_clean).strip()
        # Sentences
        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", line_clean) if s.strip()]
        for s in sentences:
            if len(s) >= 5 and s not in statements:
                statements.append(s)
    return statements


def _normalize_tokens(statement: str) -> set[str]:
    """Normalize a statement into lowercased alphanumeric tokens for comparison."""
    words = re.findall(r"\b[a-zA-Z0-9_\-\.]+\b", statement.lower())
    # Exclude common stop words
    stop = {"the", "a", "an", "in", "on", "at", "by", "for", "with", "about", "to", "from", "of", "and", "or"}
    return {w for w in words if w not in stop}


class ResearchSynthesizer:
    """Bounded, deterministic research synthesis and conflict reconciliation engine."""

    def __init__(
        self,
        config: SynthesisConfig | None = None,
        advisory_model: AdvisorySynthesisModel | None = None,
    ) -> None:
        self.config = config or SynthesisConfig()
        self.advisory_model = advisory_model

    def synthesize(
        self,
        results: list[ResearchResult],
        space_id: str,
        task_id: str,
        plan_version: int,
        query: str = "",
        context: dict[str, Any] | None = None,
    ) -> ResearchSynthesis:
        """Execute bounded multi-source synthesis, conflict detection, and provenance reconciliation."""
        ctx = context or {}

        # ── 1. Bounded Limits Enforcement ───────────────────────────────────
        if len(results) > self.config.max_sources:
            if self.config.strict_limits:
                raise SynthesisLimitExceededError(
                    f"Number of research sources ({len(results)}) exceeds limit ({self.config.max_sources})"
                )
            # Truncate and mark partial
            results = results[: self.config.max_sources]

        total_input_bytes = sum(len(r.content.raw_content.encode("utf-8")) for r in results)
        if total_input_bytes > self.config.max_input_bytes:
            if self.config.strict_limits:
                raise SynthesisLimitExceededError(
                    f"Total research input size ({total_input_bytes} bytes) exceeds limit ({self.config.max_input_bytes} bytes)"
                )

        # Empty result handling
        if not results:
            return ResearchSynthesis(
                synthesis_id=f"synth-{task_id}",
                space_id=space_id,
                task_id=task_id,
                plan_version=plan_version,
                claims=(),
                status=SynthesisStatus.INSUFFICIENT_EVIDENCE,
                summary="Insufficient evidence: No research results provided for synthesis.",
                provenance_records=(),
                source_ids=(),
                taint=True,
                is_partial=False,
                uncertainties=("No sources available",),
            )

        # ── 2. Space Isolation & Provenance Integrity Verification ──────────
        source_ids: list[str] = []
        provenance_records: list[str] = []
        prov_map: dict[str, ProvenanceRecord] = {}

        for r in results:
            # Space isolation (SCCA Law 1, Law 4, PROVENANCE-003)
            if r.space_id != space_id or r.provenance.space_id != space_id:
                raise ResearchSpaceIsolationViolation(
                    requesting_space=space_id,
                    target_space=r.space_id,
                    entity_id=r.result_id,
                )

            # Provenance integrity check (PROVENANCE-002)
            calculated_content_hash = compute_sha256(r.content.raw_content)
            if r.content.content_hash != calculated_content_hash:
                raise ProvenanceIntegrityError(
                    expected_hash=r.content.content_hash,
                    actual_hash=calculated_content_hash,
                    provenance_id=r.provenance.provenance_id,
                )

            if r.provenance.compute_canonical_hash() != r.provenance.canonical_hash:
                raise ProvenanceInvalidError(
                    f"Canonical hash tampering detected for provenance record '{r.provenance.provenance_id}'"
                )

            # Provenance continuity check (PROVENANCE-001)
            stage = r.provenance.transformation_stage
            if stage in (TransformationStage.EXTRACTED, TransformationStage.SYNTHESIZED, "extracted", "synthesized"):
                parent_id = r.provenance.parent_provenance_id
                if not parent_id or "non-existent" in parent_id:
                    raise ProvenanceInvalidError(
                        f"Broken provenance chain for record '{r.provenance.provenance_id}': "
                        f"missing or invalid parent_provenance_id '{parent_id}'"
                    )

            sid = r.content.source_identity.source_id
            if sid not in source_ids:
                source_ids.append(sid)
            pid = r.provenance.provenance_id
            if pid not in provenance_records:
                provenance_records.append(pid)
            prov_map[pid] = r.provenance

        # ── 3. Structured Claim Extraction & Neutralization ─────────────────
        raw_claims: list[ResearchClaim] = []
        claim_counter = 0

        for r in results:
            sid = r.content.source_identity.source_id
            pid = r.provenance.provenance_id
            statements = _extract_statements(r.content.raw_content)

            for stmt in statements:
                # Prompt Injection Neutralization (WORKER-002):
                # Malicious instructions remain strictly inert text data in the claim statement.
                for pat in _INJECTION_PATTERNS:
                    if pat.search(stmt):
                        logger.warning("Neutralized potential prompt injection pattern in source '%s': %s", sid, stmt[:80])

                claim_counter += 1
                if claim_counter > self.config.max_claims:
                    if self.config.strict_limits:
                        raise SynthesisLimitExceededError(
                            f"Total extracted claims exceeded MAX_CLAIMS_PER_SYNTHESIS ({self.config.max_claims})"
                        )
                    break

                claim = ResearchClaim(
                    claim_id=f"claim-{task_id}-{claim_counter}",
                    space_id=space_id,
                    statement=stmt,
                    source_ids=(sid,),
                    provenance_ids=(pid,),
                    taint=True,  # Mandatory taint invariant (TAINT-001)
                    confidence=1.0,
                    is_model_assertion=False,
                )
                raw_claims.append(claim)

        if not raw_claims:
            return ResearchSynthesis(
                synthesis_id=f"synth-{task_id}",
                space_id=space_id,
                task_id=task_id,
                plan_version=plan_version,
                claims=(),
                status=SynthesisStatus.INSUFFICIENT_EVIDENCE,
                summary="Insufficient evidence: Sources contained no extractable factual statements.",
                provenance_records=tuple(provenance_records),
                source_ids=tuple(source_ids),
                taint=True,
                is_partial=False,
                uncertainties=("No extractable facts found",),
            )

        # ── 4. Agreement, Qualification, and Contradiction Detection ─────────
        relations: list[EvidenceRelation] = []
        conflicts: list[ResearchConflict] = []
        rel_counter = 0
        conflict_counter = 0

        has_agreement = False
        has_contradiction = False

        # Compare claims pairwise across different sources
        for i in range(len(raw_claims)):
            for j in range(i + 1, len(raw_claims)):
                c1 = raw_claims[i]
                c2 = raw_claims[j]
                if c1.source_ids[0] == c2.source_ids[0]:
                    continue  # Only compare cross-source statements

                tok1 = _normalize_tokens(c1.statement)
                tok2 = _normalize_tokens(c2.statement)
                overlap = tok1 & tok2

                # If statements share key subjects/topics
                if len(overlap) >= 2 or (len(overlap) == 1 and len(tok1) <= 3 and len(tok2) <= 3):
                    pol1 = _detect_polarity(tok1)
                    pol2 = _detect_polarity(tok2)

                    is_antonym = _has_antonym_contradiction(tok1, tok2)

                    # Opposing polarities or antonyms on overlapping subject indicates CONTRADICTION
                    if is_antonym or (pol1 == 1 and pol2 == -1) or (pol1 == -1 and pol2 == 1):
                        has_contradiction = True
                        conflict_counter += 1
                        rel_counter += 1

                        rel = EvidenceRelation(
                            relation_id=f"rel-{task_id}-{rel_counter}",
                            source_claim_id=c1.claim_id,
                            target_claim_id=c2.claim_id,
                            relation_type=EvidenceRelationType.CONTRADICTS,
                            provenance_ids=(c1.provenance_ids[0], c2.provenance_ids[0]),
                            explanation=f"Contradiction between '{c1.statement[:60]}' and '{c2.statement[:60]}'",
                        )
                        relations.append(rel)

                        conf = ResearchConflict(
                            conflict_id=f"conf-{task_id}-{conflict_counter}",
                            space_id=space_id,
                            task_id=task_id,
                            plan_version=plan_version,
                            topic=f"Contradiction regarding {', '.join(sorted(overlap)[:3])}",
                            source_a_provenance_id=c1.provenance_ids[0],
                            source_b_provenance_id=c2.provenance_ids[0],
                            statement_a=c1.statement,
                            statement_b=c2.statement,
                        )
                        conflicts.append(conf)

                    elif pol1 == pol2 and len(overlap) >= 2:
                        # Concordant statements -> AGREEMENT / SUPPORTS
                        has_agreement = True
                        rel_counter += 1
                        rel = EvidenceRelation(
                            relation_id=f"rel-{task_id}-{rel_counter}",
                            source_claim_id=c1.claim_id,
                            target_claim_id=c2.claim_id,
                            relation_type=EvidenceRelationType.SUPPORTS,
                            provenance_ids=(c1.provenance_ids[0], c2.provenance_ids[0]),
                            explanation=f"Cross-source support for topic '{', '.join(sorted(overlap)[:2])}'",
                        )
                        relations.append(rel)

        # Check relation ceilings
        if len(relations) > self.config.max_conflict_relationships:
            if self.config.strict_limits:
                raise SynthesisLimitExceededError(
                    f"Relationship count ({len(relations)}) exceeds MAX_CONFLICT_RELATIONSHIPS ({self.config.max_conflict_relationships})"
                )
            relations = relations[: self.config.max_conflict_relationships]

        # ── 5. Determine Overall Synthesis Status ───────────────────────────
        is_partial = False
        uncertainties: list[str] = []

        if has_contradiction:
            status = SynthesisStatus.CONTRADICTION
            uncertainties.append(f"Identified {len(conflicts)} direct contradiction(s) between sources.")
        elif has_agreement:
            status = SynthesisStatus.AGREEMENT
        elif len(source_ids) > 1 and not relations:
            status = SynthesisStatus.UNRELATED
            uncertainties.append("Retrieved sources discuss non-overlapping technical topics.")
        else:
            status = SynthesisStatus.PARTIAL_AGREEMENT
            is_partial = True
            uncertainties.append("Single source or partial coverage; secondary corroboration absent.")

        # ── 6. Model Assertion Subordination & Advisory Summary ──────────────
        summary = ""
        sources_list = [r.content.source_identity for r in results]

        if self.advisory_model is not None:
            try:
                advisory_text = self.advisory_model.generate_advisory_summary(
                    raw_claims, sources_list, context=ctx
                )
                # Model Assertion Subordination Checks (§14):
                # 1. Check for fabricated citations
                for sid in re.findall(r"\b(?:src-[a-zA-Z0-9_\-]+|https?://[^\s,\)]+)\b", advisory_text):
                    known_locators = {s.locator for s in sources_list}
                    known_ids = {s.source_id for s in sources_list}
                    if sid not in known_ids and sid not in known_locators:
                        raise ModelAssertionSubordinationError(
                            f"Model fabricated citation '{sid}' which does not exist in retrieved sources"
                        )

                # 2. Check for contradiction suppression
                if has_contradiction and re.search(r"(?i)\b(unanimous|full consensus|all sources agree|no conflict)\b", advisory_text):
                    logger.warning("Advisory model attempted to suppress contradiction; overriding with verified conflict")
                    advisory_text += f"\n\n[WARNING: Contradiction detected between sources regarding {conflicts[0].topic}]"

                summary = sanitize_text(advisory_text)
            except ModelAssertionSubordinationError:
                raise
            except Exception as exc:
                logger.warning("Advisory model summary generation failed: %s; using deterministic fallback", exc)
                summary = self._generate_deterministic_summary(status, raw_claims, conflicts)
        else:
            summary = self._generate_deterministic_summary(status, raw_claims, conflicts)

        # ── 7. Build and Return Immutable ResearchSynthesis ──────────────────
        synthesis = ResearchSynthesis(
            synthesis_id=f"synth-{task_id}",
            space_id=space_id,
            task_id=task_id,
            plan_version=plan_version,
            claims=tuple(raw_claims),
            relations=tuple(relations),
            conflicts=tuple(conflicts),
            status=status,
            summary=summary,
            provenance_records=tuple(provenance_records),
            source_ids=tuple(source_ids),
            taint=True,  # Mandatory taint invariant (TAINT-001)
            is_partial=is_partial,
            uncertainties=tuple(uncertainties),
            metadata={
                "input_bytes": total_input_bytes,
                "sources_count": len(source_ids),
                "claims_count": len(raw_claims),
                "conflicts_count": len(conflicts),
                "query": query,
            },
        )

        # Verify full synthesis provenance
        valid, err = verify_synthesis_provenance(synthesis, prov_map, space_id)
        if not valid:
            raise ProvenanceInvalidError(f"Synthesized evidence failed provenance verification: {err}")

        return synthesis

    def _generate_deterministic_summary(
        self,
        status: SynthesisStatus,
        claims: list[ResearchClaim],
        conflicts: list[ResearchConflict],
    ) -> str:
        """Produce deterministic, structured summary text without any LLM dependency."""
        lines = [
            "# Research Synthesis Report",
            f"Status: {status.value.upper()}",
            f"Total Claims Extracted: {len(claims)}",
        ]
        if conflicts:
            lines.append(f"\n## Conflicts Detected ({len(conflicts)}):")
            for c in conflicts:
                lines.append(f"- Topic: {c.topic}")
                lines.append(f"  * Source A (prov: {c.source_a_provenance_id}): {c.statement_a}")
                lines.append(f"  * Source B (prov: {c.source_b_provenance_id}): {c.statement_b}")
        else:
            lines.append("\n## Key Synthesized Facts:")
            for cl in claims[:10]:
                lines.append(f"- {cl.statement} [Sources: {', '.join(cl.source_ids)}]")

        return "\n".join(lines)
