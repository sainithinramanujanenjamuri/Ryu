"""InMemoryMemoryAdapter: Thread-safe, hermetic in-memory Space Memory.

Authoritative storage implementation for fast, hermetic unit testing (ADR-0033).
Enforces Space isolation (Law 1, Law 4, MEM-001) and cryptographic promotion capability tokens (ADR-0035).

spec §4 (Space Memory), MEM-001..006, ADR-0033..0035 — Phase 10
"""

from __future__ import annotations

import threading
from typing import Any, Callable

from core.space.memory_protocol import (
    ExperienceQuery,
    ExperienceRecord,
    KnowledgeEntry,
    PromotionAuthorization,
    SpaceIsolationViolation,
    SpaceMemoryProtocol,
    verify_promotion_authorization,
)


class InMemoryMemoryAdapter(SpaceMemoryProtocol):
    """Hermetic in-memory implementation of SpaceMemoryProtocol.

    Invariants:
    - Thread-safe via RLock.
    - Space isolation: Records stored in Space A cannot be retrieved by queries for Space B.
    - Global knowledge writes require valid, unconsumed PromotionAuthorization.
    """

    def __init__(
        self,
        signing_key: bytes | None = None,
        key_resolver: Callable[[str], bytes] | None = None,
    ) -> None:
        self._signing_key = signing_key
        self._key_resolver = key_resolver
        self._lock = threading.RLock()
        # [space_id][experience_id] -> ExperienceRecord
        self._experiences: dict[str, dict[str, ExperienceRecord]] = {}
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

    def store_experience(self, record: ExperienceRecord) -> str:
        """Store an experience record under its owning space_id."""
        with self._lock:
            space_map = self._experiences.setdefault(record.space_id, {})
            space_map[record.experience_id] = record
            return record.experience_id

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

    def list_experiences(self, space_id: str) -> list[ExperienceRecord]:
        """List all experiences belonging strictly to space_id."""
        if not space_id:
            raise SpaceIsolationViolation(
                requesting_space="<empty>", target_space="<empty>"
            )
        with self._lock:
            return list(self._experiences.get(space_id, {}).values())

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

