"""PostgreSQLMemoryAdapter: Authoritative durable Space Memory implementation.

Persists ExperienceRecords and Promoted Knowledge across process restarts (ADR-0033).
Enforces Space isolation (SPACE-001, Law 1, Law 4), three-layer counterfactual constraint (MEM-002),
and cryptographic promotion authorization (MEM-005, MEM-006, ADR-0035).

spec §4 (Space Memory), §15 (Storage Layer), MEM-001..006, ADR-0033..0035 — Phase 10
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Callable

try:
    import psycopg2
    from psycopg2.extras import Json
except ImportError:  # pragma: no cover
    psycopg2 = None  # type: ignore[assignment]
    Json = None  # type: ignore[assignment, misc]

from ryu.pulse_bus.config import PostgresConfig

from core.space.memory_protocol import (
    ExperienceQuery,
    ExperienceRecord,
    KnowledgeEntry,
    MemoryFailure,
    PromotionAuthorization,
    SpaceIsolationViolation,
    SpaceMemoryProtocol,
    verify_promotion_authorization,
)


class PostgreSQLMemoryAdapter(SpaceMemoryProtocol):
    """Authoritative durable storage adapter backed by PostgreSQL."""

    def __init__(
        self,
        config: PostgresConfig,
        signing_key: bytes | None = None,
        key_resolver: Callable[[str], bytes] | None = None,
    ) -> None:
        if psycopg2 is None:
            raise MemoryFailure(
                operation="init",
                reason="psycopg2 is not installed. Install optional dependencies 'durable' or 'integration'.",
            )
        self.config = config
        self._signing_key = signing_key
        self._key_resolver = key_resolver

    def _get_signing_key(self, space_id: str) -> bytes:
        if self._key_resolver is not None:
            return self._key_resolver(space_id)
        if self._signing_key is not None:
            return self._signing_key
        import hashlib

        return hashlib.sha256(f"kernel-signing-key-{space_id}".encode("utf-8")).digest()

    def _get_conn(self) -> Any:
        try:
            return psycopg2.connect(
                host=self.config.host,
                port=self.config.port,
                dbname=self.config.db,
                user=self.config.user,
                password=self.config.password,
            )
        except Exception as e:
            raise MemoryFailure(operation="connect", reason=str(e)) from e

    def store_experience(self, record: ExperienceRecord) -> str:
        """Durable append of an ExperienceRecord to space_experiences table."""
        sql = """
            INSERT INTO space_experiences (
                experience_id, space_id, situation, action, outcome,
                counterfactual, applicable_context, stored_at
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s
            )
            ON CONFLICT (experience_id, space_id) DO NOTHING;
        """
        params = (
            record.experience_id,
            record.space_id,
            Json(record.situation),
            Json(record.action),
            record.outcome,
            record.counterfactual,
            Json(record.applicable_context),
            record.stored_at,
        )
        try:
            with self._get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, params)
            return record.experience_id
        except Exception as e:
            raise MemoryFailure(operation="store_experience", reason=str(e)) from e

    def get_experience(
        self, space_id: str, experience_id: str
    ) -> ExperienceRecord | None:
        """Retrieve an experience record scoped strictly to space_id."""
        if not space_id:
            raise SpaceIsolationViolation(
                requesting_space="<empty>", target_space="<empty>"
            )

        sql = """
            SELECT experience_id, space_id, situation, action, outcome,
                   counterfactual, applicable_context, stored_at
            FROM space_experiences
            WHERE space_id = %s AND experience_id = %s;
        """
        try:
            with self._get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, (space_id, experience_id))
                    row = cur.fetchone()
                    if row is None:
                        return None
                    return self._row_to_experience(row)
        except Exception as e:
            raise MemoryFailure(operation="get_experience", reason=str(e)) from e

    def list_experiences(self, space_id: str) -> list[ExperienceRecord]:
        """List all experiences belonging strictly to space_id."""
        if not space_id:
            raise SpaceIsolationViolation(
                requesting_space="<empty>", target_space="<empty>"
            )

        sql = """
            SELECT experience_id, space_id, situation, action, outcome,
                   counterfactual, applicable_context, stored_at
            FROM space_experiences
            WHERE space_id = %s
            ORDER BY stored_at DESC;
        """
        try:
            with self._get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, (space_id,))
                    rows = cur.fetchall()
                    return [self._row_to_experience(r) for r in rows]
        except Exception as e:
            raise MemoryFailure(operation="list_experiences", reason=str(e)) from e

    def query_similar_experiences(
        self, query: ExperienceQuery
    ) -> list[ExperienceRecord]:
        """Query experiences within query.space_id."""
        sql = """
            SELECT experience_id, space_id, situation, action, outcome,
                   counterfactual, applicable_context, stored_at
            FROM space_experiences
            WHERE space_id = %s
            ORDER BY stored_at DESC
            LIMIT %s;
        """
        try:
            with self._get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, (query.space_id, query.limit))
                    rows = cur.fetchall()
                    records = [self._row_to_experience(r) for r in rows]

            # In-memory keyword relevance scoring on recent space records
            hint_cap = query.situation_hint.get("capability", "").strip().lower()
            hint_terms = [
                str(v).lower()
                for v in query.situation_hint.values()
                if isinstance(v, (str, int))
            ]

            scored: list[tuple[int, ExperienceRecord]] = []
            for rec in records:
                score = 0
                rec_cap = rec.action.get("capability", "").strip().lower()
                if hint_cap and rec_cap == hint_cap:
                    score += 10
                rec_text = (
                    f"{rec.outcome} {rec.counterfactual} {rec.situation}".lower()
                )
                for term in hint_terms:
                    if term in rec_text:
                        score += 1
                scored.append((score, rec))

            scored.sort(key=lambda item: (item[0], item[1].stored_at), reverse=True)
            return [item[1] for item in scored[: query.limit]]
        except Exception as e:
            raise MemoryFailure(
                operation="query_similar_experiences", reason=str(e)
            ) from e

    def store_knowledge(
        self, entry: KnowledgeEntry, auth: PromotionAuthorization
    ) -> None:
        """Persist promoted global knowledge with cryptographic token and single-use audit."""
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

        signing_key = self._get_signing_key(auth.source_space_id)
        if not verify_promotion_authorization(auth, signing_key):
            raise PermissionError(
                "PromotionAuthorization signature verification failed: forged or tampered token (ADR-0035)"
            )

        check_sql = "SELECT 1 FROM promotion_audit WHERE promotion_id = %s AND event_type = 'approved';"
        audit_sql = """
            INSERT INTO promotion_audit (
                event_type, promotion_id, knowledge_id, source_space_id, approver_id, pulse_id
            ) VALUES (
                'approved', %s, %s, %s, %s, %s
            );
        """
        knowledge_sql = """
            INSERT INTO global_knowledge (
                knowledge_id, source_space_id, content, promoted_by,
                promotion_pulse_id, global_version, promoted_at
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s
            )
            ON CONFLICT (knowledge_id) DO UPDATE SET
                content = EXCLUDED.content,
                global_version = global_knowledge.global_version + 1,
                promoted_at = EXCLUDED.promoted_at;
        """
        try:
            with self._get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(check_sql, (auth.promotion_id,))
                    if cur.fetchone() is not None:
                        raise PermissionError(
                            f"Replayed promotion authorization: promotion_id '{auth.promotion_id}' "
                            f"has already been consumed (MEM-005)"
                        )

                    cur.execute(
                        audit_sql,
                        (
                            auth.promotion_id,
                            entry.knowledge_id,
                            entry.source_space_id,
                            entry.promoted_by,
                            entry.promotion_pulse_id,
                        ),
                    )
                    cur.execute(
                        knowledge_sql,
                        (
                            entry.knowledge_id,
                            entry.source_space_id,
                            Json(entry.content),
                            entry.promoted_by,
                            entry.promotion_pulse_id,
                            entry.global_version,
                            entry.promoted_at,
                        ),
                    )
        except PermissionError:
            raise
        except Exception as e:
            raise MemoryFailure(operation="store_knowledge", reason=str(e)) from e

    def get_global_knowledge(self, knowledge_id: str) -> KnowledgeEntry | None:
        """Retrieve a promoted global knowledge entry."""
        sql = """
            SELECT knowledge_id, source_space_id, content, promoted_by,
                   promotion_pulse_id, global_version, promoted_at
            FROM global_knowledge
            WHERE knowledge_id = %s;
        """
        try:
            with self._get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, (knowledge_id,))
                    row = cur.fetchone()
                    if row is None:
                        return None
                    return self._row_to_knowledge(row)
        except Exception as e:
            raise MemoryFailure(operation="get_global_knowledge", reason=str(e)) from e

    def _row_to_experience(self, row: tuple) -> ExperienceRecord:  # type: ignore[type-arg]
        stored_at = row[7]
        if stored_at and stored_at.tzinfo is None:
            stored_at = stored_at.replace(tzinfo=timezone.utc)
        return ExperienceRecord(
            experience_id=row[0],
            space_id=row[1],
            situation=row[2] if isinstance(row[2], dict) else json.loads(row[2]),
            action=row[3] if isinstance(row[3], dict) else json.loads(row[3]),
            outcome=row[4],
            counterfactual=row[5],
            applicable_context=row[6]
            if isinstance(row[6], dict)
            else json.loads(row[6]),
            stored_at=stored_at,
        )

    def _row_to_knowledge(self, row: tuple) -> KnowledgeEntry:  # type: ignore[type-arg]
        promoted_at = row[6]
        if promoted_at and promoted_at.tzinfo is None:
            promoted_at = promoted_at.replace(tzinfo=timezone.utc)
        return KnowledgeEntry(
            knowledge_id=row[0],
            source_space_id=row[1],
            content=row[2] if isinstance(row[2], dict) else json.loads(row[2]),
            promoted_by=row[3],
            promotion_pulse_id=row[4],
            global_version=row[5],
            promoted_at=promoted_at,
        )

