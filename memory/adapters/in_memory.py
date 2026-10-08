"""InMemoryMemoryAdapter: Thread-safe, hermetic in-memory Space Memory.

Authoritative storage implementation for fast, hermetic unit testing (ADR-0033).
Enforces Space isolation (Law 1, Law 4, MEM-001) and cryptographic promotion capability tokens (ADR-0035).

spec §4 (Space Memory), MEM-001..006, ADR-0033..0035 — Phase 10
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Callable

from core.space.memory_protocol import (
    CompactionResult,
    EmbeddingProviderProtocol,
    EmbeddingResult,
    ExperienceQuery,
    ExperienceRecord,
    KnowledgeEntry,
    MemoryFailure,
    PromotionAuthorization,
    RetentionPolicy,
    ScoredExperienceRecord,
    SemanticExperienceQuery,
    SpaceIsolationViolation,
    SpaceMemoryProtocol,
    select_compaction_victims,
    verify_promotion_authorization,
)
from memory.retrieval.ranker import (
    deterministic_rank_candidates,
    resolve_query_embedding,
)


class InMemoryMemoryAdapter(SpaceMemoryProtocol):
    """Hermetic in-memory implementation of SpaceMemoryProtocol.

    Invariants:
    - Thread-safe via RLock.
    - Space isolation: Records stored in Space A cannot be retrieved by queries for Space B.
    - Global knowledge writes require valid, unconsumed PromotionAuthorization.
    - Bounded multi-prong semantic candidate selection (C <= 50, MEM-SEM-001).
    - Deterministic tie-breaking similarity ranking (K <= 5, MEM-SEM-002).
    """

    def __init__(
        self,
        signing_key: bytes | None = None,
        key_resolver: Callable[[str], bytes] | None = None,
        default_retention_policy: RetentionPolicy | None = None,
        auto_prune: bool = False,
    ) -> None:
        self._signing_key = signing_key
        self._key_resolver = key_resolver
        self._default_retention_policy = default_retention_policy
        self._auto_prune = auto_prune
        self._lock = threading.RLock()
        # [space_id][experience_id] -> ExperienceRecord
        self._experiences: dict[str, dict[str, ExperienceRecord]] = {}
        # Secondary indexes for candidate generation (MEM-SEM-001)
        # [space_id][failure_fingerprint] -> list[experience_id]
        self._fingerprint_idx: dict[str, dict[str, list[str]]] = {}
        # [space_id][capability] -> list[experience_id]
        self._capability_idx: dict[str, dict[str, list[str]]] = {}
        # [space_id][error_class] -> list[experience_id]
        self._error_class_idx: dict[str, dict[str, list[str]]] = {}
        # [space_id] -> list[experience_id] in recency order
        self._recency_idx: dict[str, list[str]] = {}
        # [knowledge_id] -> KnowledgeEntry
        self._global_knowledge: dict[str, KnowledgeEntry] = {}
        # consumed promotion_ids (single-use enforcement)
        self._consumed_promotions: set[str] = set()

    def _get_signing_key(self, space_id: str) -> bytes:
        if self._key_resolver is not None:
            return self._key_resolver(space_id)
        if self._signing_key is not None:
            return self._signing_key
        # Default deterministic key derived from space_id
        import hashlib

        return hashlib.sha256(f"kernel-signing-key-{space_id}".encode("utf-8")).digest()

    def store_experience(
        self, record: ExperienceRecord, embedding: EmbeddingResult | None = None
    ) -> str:
        """Store an experience record under its owning space_id."""
        if not record.space_id or not record.space_id.strip():
            raise SpaceIsolationViolation(
                requesting_space="<empty>", target_space="<empty>"
            )

        if embedding is not None:
            effective_record = record.with_embedding(embedding)
        else:
            if record.embedding is None and record.embedding_status == "completed":
                effective_record = record.with_embedding_status("pending")
            else:
                effective_record = record

        with self._lock:
            space_map = self._experiences.setdefault(effective_record.space_id, {})
            space_map[effective_record.experience_id] = effective_record

            # Update secondary indexes for candidate selection
            sid = effective_record.space_id
            eid = effective_record.experience_id

            if effective_record.failure_fingerprint:
                fp_map = self._fingerprint_idx.setdefault(sid, {})
                fp_list = fp_map.setdefault(effective_record.failure_fingerprint, [])
                if eid not in fp_list:
                    fp_list.append(eid)

            cap = (
                effective_record.action.get("capability")
                or effective_record.situation.get("capability")
            )
            if cap:
                cap_str = str(cap).strip().lower()
                cap_map = self._capability_idx.setdefault(sid, {})
                cap_list = cap_map.setdefault(cap_str, [])
                if eid not in cap_list:
                    cap_list.append(eid)

            err = effective_record.applicable_context.get("error_class")
            if err:
                err_str = str(err).strip().lower()
                err_map = self._error_class_idx.setdefault(sid, {})
                err_list = err_map.setdefault(err_str, [])
                if eid not in err_list:
                    err_list.append(eid)

            rec_list = self._recency_idx.setdefault(sid, [])
            if eid in rec_list:
                rec_list.remove(eid)
            rec_list.append(eid)

            if self._auto_prune and self._default_retention_policy:
                pol = self._default_retention_policy
                space_records = list(space_map.values())
                if len(space_records) > pol.max_experiences or pol.ttl_seconds is not None:
                    victim_ids, _ = select_compaction_victims(space_records, pol)
                    for vid in victim_ids:
                        vrec = space_map.pop(vid, None)
                        if vrec is not None:
                            self._cleanup_secondary_indexes(sid, vid, vrec)

            return effective_record.experience_id

    def _cleanup_secondary_indexes(
        self, space_id: str, experience_id: str, record: ExperienceRecord
    ) -> None:
        """Remove experience from secondary lookup indexes upon eviction."""
        if record.failure_fingerprint and space_id in self._fingerprint_idx:
            fp_list = self._fingerprint_idx[space_id].get(record.failure_fingerprint, [])
            if experience_id in fp_list:
                fp_list.remove(experience_id)

        cap = record.action.get("capability") or record.situation.get("capability")
        if cap and space_id in self._capability_idx:
            cap_str = str(cap).strip().lower()
            cap_list = self._capability_idx[space_id].get(cap_str, [])
            if experience_id in cap_list:
                cap_list.remove(experience_id)

        err = record.applicable_context.get("error_class")
        if err and space_id in self._error_class_idx:
            err_str = str(err).strip().lower()
            err_list = self._error_class_idx[space_id].get(err_str, [])
            if experience_id in err_list:
                err_list.remove(experience_id)

        if space_id in self._recency_idx:
            rec_list = self._recency_idx[space_id]
            if experience_id in rec_list:
                rec_list.remove(experience_id)

    def get_experience(
        self, space_id: str, experience_id: str
    ) -> ExperienceRecord | None:
        """Retrieve an experience record strictly within space_id."""
        if not space_id:
            raise SpaceIsolationViolation(
                requesting_space="<empty>", target_space="<empty>"
            )
        with self._lock:
            return self._experiences.get(space_id, {}).get(experience_id)

    def list_experiences(
        self, space_id: str, limit: int = 50, before_stored_at: datetime | None = None
    ) -> list[ExperienceRecord]:
        """List all experiences belonging strictly to space_id with bounded pagination (MEM-SEM-001)."""
        if not space_id:
            raise SpaceIsolationViolation(
                requesting_space="<empty>", target_space="<empty>"
            )
        effective_limit = min(max(1, limit), 100)
        with self._lock:
            records = list(self._experiences.get(space_id, {}).values())

        if before_stored_at is not None:
            records = [
                r
                for r in records
                if (
                    r.stored_at
                    if isinstance(r.stored_at, datetime)
                    else datetime.fromtimestamp(float(r.stored_at), tz=timezone.utc)
                )
                < before_stored_at
            ]

        def _sort_key(r: ExperienceRecord) -> tuple[float, str]:
            if isinstance(r.stored_at, (int, float)):
                ts = float(r.stored_at)
            elif isinstance(r.stored_at, datetime):
                ts = (
                    r.stored_at.timestamp()
                    if r.stored_at.tzinfo is not None
                    else r.stored_at.replace(tzinfo=timezone.utc).timestamp()
                )
            else:
                ts = 0.0
            return (-ts, r.experience_id)

        records.sort(key=_sort_key)
        return records[:effective_limit]

    def count_experiences(self, space_id: str) -> int:
        """Count experiences stored strictly in space_id (MEM-RETAIN-001)."""
        if not space_id or not space_id.strip():
            raise SpaceIsolationViolation(
                requesting_space=space_id or "<empty>", target_space=space_id or "<empty>"
            )
        with self._lock:
            return len(self._experiences.get(space_id, {}))

    def prune_experiences(
        self, space_id: str, policy: RetentionPolicy | None = None
    ) -> CompactionResult:
        """Prune experiences in space_id according to retention policy (MEM-RETAIN-001)."""
        if not space_id or not space_id.strip():
            raise SpaceIsolationViolation(
                requesting_space=space_id or "<empty>", target_space=space_id or "<empty>"
            )
        pol = policy or self._default_retention_policy or RetentionPolicy()
        with self._lock:
            space_map = self._experiences.get(space_id, {})
            records = list(space_map.values())
            initial_count = len(records)
            if initial_count == 0:
                return CompactionResult(
                    space_id=space_id,
                    initial_count=0,
                    final_count=0,
                    pruned_count=0,
                    pruned_experience_ids=(),
                    reasons={},
                )

            victim_ids, reasons = select_compaction_victims(records, pol)
            for vid in victim_ids:
                vrec = space_map.pop(vid, None)
                if vrec is not None:
                    self._cleanup_secondary_indexes(space_id, vid, vrec)

            final_count = len(space_map)
            return CompactionResult(
                space_id=space_id,
                initial_count=initial_count,
                final_count=final_count,
                pruned_count=len(victim_ids),
                pruned_experience_ids=tuple(victim_ids),
                reasons=reasons,
            )

    def _get_semantic_candidates(
        self, query: SemanticExperienceQuery
    ) -> list[ExperienceRecord]:
        """Bounded multi-prong candidate selection without scanning full space history (C <= 50, MEM-SEM-001).

        Prongs:
        1. Exact failure fingerprint matches (up to 10)
        2. Capability and error-class structured matches (up to 25)
        3. Recent space execution recency window (up to 20)
        Deduplicated in priority order and clamped to C <= 50.
        """
        if not query.space_id or not query.space_id.strip():
            raise SpaceIsolationViolation(
                requesting_space=query.space_id or "<empty>",
                target_space=query.space_id or "<empty>",
            )

        with self._lock:
            space_records = self._experiences.get(query.space_id, {})
            if not space_records:
                return []

            candidate_ids: list[str] = []

            # Prong A: Exact failure fingerprint matches (slot priority 1, max 10)
            target_fp = query.failure_fingerprint or query.situation_hint.get("failure_fingerprint")
            if target_fp:
                fp_matches = self._fingerprint_idx.get(query.space_id, {}).get(str(target_fp), [])
                for eid in reversed(fp_matches[-10:]):
                    if eid not in candidate_ids:
                        candidate_ids.append(eid)

            # Prong B: Capability & error-class matches (slot priority 2, max 25)
            cap = query.situation_hint.get("capability")
            err = query.situation_hint.get("error_class")
            prong_b_ids: list[str] = []
            if cap:
                cap_matches = self._capability_idx.get(query.space_id, {}).get(str(cap).strip().lower(), [])
                for eid in reversed(cap_matches):
                    if eid not in candidate_ids and eid not in prong_b_ids:
                        prong_b_ids.append(eid)
                        if len(prong_b_ids) >= 25:
                            break
            if err and len(prong_b_ids) < 25:
                err_matches = self._error_class_idx.get(query.space_id, {}).get(str(err).strip().lower(), [])
                for eid in reversed(err_matches):
                    if eid not in candidate_ids and eid not in prong_b_ids:
                        prong_b_ids.append(eid)
                        if len(prong_b_ids) >= 25:
                            break
            candidate_ids.extend(prong_b_ids[:25])

            # Prong C: Recent space recency window (slot priority 3, max 20)
            rec_list = self._recency_idx.get(query.space_id, [])
            for eid in reversed(rec_list[-20:]):
                if eid not in candidate_ids:
                    candidate_ids.append(eid)

            # Clamp pre-ranking candidate set to C <= 50
            clamped_ids = candidate_ids[:50]
            return [space_records[eid] for eid in clamped_ids if eid in space_records]

    def retrieve_semantic_experiences(
        self,
        query: SemanticExperienceQuery,
        embedding_provider: EmbeddingProviderProtocol | None = None,
    ) -> list[ScoredExperienceRecord]:
        """Retrieve and deterministically rank experiences using bounded multi-prong retrieval (MEM-SEM-001, MEM-SEM-002)."""
        query_emb = resolve_query_embedding(query, embedding_provider)
        candidates = self._get_semantic_candidates(query)
        target_fp = query.failure_fingerprint or query.situation_hint.get("failure_fingerprint")
        return deterministic_rank_candidates(
            candidates=candidates,
            query_emb=query_emb,
            top_k=query.top_k,
            min_similarity=query.min_similarity,
            target_fingerprint=str(target_fp) if target_fp else None,
        )

    def query_similar_experiences(
        self, query: ExperienceQuery
    ) -> list[ExperienceRecord]:
        """Query experiences within query.space_id matching situation hints."""
        with self._lock:
            space_records = list(
                self._experiences.get(query.space_id, {}).values()
            )

        if not space_records:
            return []

        # Deterministic keyword / capability matching
        hint_cap = query.situation_hint.get("capability", "").strip().lower()
        hint_terms = [
            str(v).lower()
            for v in query.situation_hint.values()
            if isinstance(v, (str, int))
        ]

        scored: list[tuple[int, ExperienceRecord]] = []
        for rec in space_records:
            score = 0
            rec_cap = rec.action.get("capability", "").strip().lower()
            if hint_cap and rec_cap == hint_cap:
                score += 10
            # Context term matching
            rec_text = (
                f"{rec.outcome} {rec.counterfactual} {rec.situation}".lower()
            )
            for term in hint_terms:
                if term in rec_text:
                    score += 1
            scored.append((score, rec))

        # Sort by relevance descending, tie-break by stored_at
        scored.sort(key=lambda item: (item[0], item[1].stored_at), reverse=True)
        return [item[1] for item in scored[: query.limit]]

    def get_pending_embeddings(
        self, space_id: str, limit: int = 16
    ) -> list[ExperienceRecord]:
        """Retrieve records pending embedding generation in space_id (MEM-INGEST-001)."""
        if not space_id or not space_id.strip():
            raise SpaceIsolationViolation(
                requesting_space=space_id or "<empty>", target_space=space_id or "<empty>"
            )

        clamped_limit = max(1, min(limit, 50))
        with self._lock:
            space_map = self._experiences.get(space_id, {})
            pending = [
                rec
                for rec in space_map.values()
                if rec.embedding_status in ("pending", "processing")
                and rec.embedding_attempts < 3
                and rec.embedding is None
            ]
            pending.sort(key=lambda r: (r.stored_at, r.experience_id))
            return pending[:clamped_limit]

    def update_experience_embedding(
        self, space_id: str, experience_id: str, embedding: EmbeddingResult
    ) -> None:
        """Atomically update an experience record with its generated embedding (MEM-INGEST-001)."""
        if not space_id or not space_id.strip():
            raise SpaceIsolationViolation(
                requesting_space=space_id or "<empty>", target_space=space_id or "<empty>"
            )

        with self._lock:
            space_map = self._experiences.get(space_id)
            if not space_map or experience_id not in space_map:
                raise MemoryFailure(
                    operation="update_experience_embedding",
                    reason=f"Experience '{experience_id}' not found in space '{space_id}'",
                )
            rec = space_map[experience_id]
            updated = rec.with_embedding(embedding)
            space_map[experience_id] = updated

    def mark_embedding_failed(
        self,
        space_id: str,
        experience_id: str,
        error: str,
        attempts: int,
        terminal: bool = False,
    ) -> None:
        """Record embedding generation failure or increment attempt count (MEM-INGEST-001)."""
        if not space_id or not space_id.strip():
            raise SpaceIsolationViolation(
                requesting_space=space_id or "<empty>", target_space=space_id or "<empty>"
            )

        with self._lock:
            space_map = self._experiences.get(space_id)
            if not space_map or experience_id not in space_map:
                raise MemoryFailure(
                    operation="mark_embedding_failed",
                    reason=f"Experience '{experience_id}' not found in space '{space_id}'",
                )
            rec = space_map[experience_id]
            status = "failed" if terminal else "pending"
            updated = rec.with_embedding_status(status, attempts=attempts, error=error)
            space_map[experience_id] = updated

    def store_knowledge(
        self, entry: KnowledgeEntry, auth: PromotionAuthorization
    ) -> None:
        """Persist promoted global knowledge. Enforces capability token integrity and single-use."""
        if not isinstance(auth, PromotionAuthorization):
            raise PermissionError(
                "Direct global knowledge writes rejected: PromotionAuthorization capability token required (MEM-005)"
            )

        if auth.knowledge_id != entry.knowledge_id:
            raise PermissionError(
                f"PromotionAuthorization mismatch: token knowledge_id '{auth.knowledge_id}' "
                f"does not match entry knowledge_id '{entry.knowledge_id}'"
            )

        if auth.source_space_id != entry.source_space_id:
            raise PermissionError(
                f"PromotionAuthorization mismatch: token source_space_id '{auth.source_space_id}' "
                f"does not match entry source_space_id '{entry.source_space_id}'"
            )

        # Cryptographic verification
        signing_key = self._get_signing_key(auth.source_space_id)
        if not verify_promotion_authorization(auth, signing_key):
            raise PermissionError(
                "PromotionAuthorization signature verification failed: forged or tampered token (ADR-0035)"
            )

        with self._lock:
            if auth.promotion_id in self._consumed_promotions:
                raise PermissionError(
                    f"Replayed promotion authorization: promotion_id '{auth.promotion_id}' has already been consumed (MEM-005)"
                )

            # Atomic commit & consumption
            self._consumed_promotions.add(auth.promotion_id)
            self._global_knowledge[entry.knowledge_id] = entry

    def get_global_knowledge(self, knowledge_id: str) -> KnowledgeEntry | None:
        """Retrieve a promoted global knowledge entry."""
        with self._lock:
            return self._global_knowledge.get(knowledge_id)

