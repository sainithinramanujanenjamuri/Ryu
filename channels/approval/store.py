"""Postgres-backed durable store for Approvals, Approver Credentials, and Nonces.

Satisfies:
- ApprovalStore (core.space.approver)
- CredentialStore (channels.approval.auth)
- NonceStore (channels.approval.auth)

spec §4, §16, ROADMAP Phase 8, ADR-0022, ADR-0023 — Phase 8
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import psycopg2
from psycopg2.extensions import connection

from channels.approval.auth import ApproverCredentialRecord
from core.space.approver import (
    ApprovalLifecycleState,
    ApprovalRequest,
    AttentionQueueState,
    TimeoutClass,
)
from ryu.pulse_bus.config import PostgresConfig

logger = logging.getLogger(__name__)


class PostgresApprovalStore:
    """PostgreSQL storage backend for approvals, credentials, and nonces."""

    def __init__(self, config: PostgresConfig) -> None:
        self.config = config

    def _get_conn(self) -> connection:
        return psycopg2.connect(
            host=self.config.host,
            port=self.config.port,
            dbname=self.config.db,
            user=self.config.user,
            password=self.config.password,
        )

    # -------------------------------------------------------------------------
    # ApprovalStore Protocol Implementation
    # -------------------------------------------------------------------------

    def _row_to_request(self, row: tuple[Any, ...]) -> ApprovalRequest:
        req = ApprovalRequest(
            request_id=row[0],
            space_id=row[1],
            goal_id=row[2],
            plan_id=row[3],
            plan_version=row[4],
            correlation_id=row[5],
            parent_pulse_id=row[6],
            requester_id=row[7],
            capability=row[8],
            capability_request_hash=row[9],
            risk_tier=row[10],
            taint=row[11],
            summary=row[12],
            timeout_class=row[13],  # type: ignore[arg-type]
            timeout_seconds=row[14],
            created_at=row[15],
            expires_at=row[16],
            status=row[17],  # type: ignore[arg-type]
            queue_state=row[18],  # type: ignore[arg-type]
            approver_id=row[19] or "",
            resolved_at=row[20],
            consumed_at=row[21],
            resolution_reason=row[22],
            decision_signature=row[23],
            decision_key_version=row[24],
        )
        return req

    def save(self, req: ApprovalRequest) -> None:
        query = """
            INSERT INTO approvals (
                approval_id, space_id, goal_id, plan_id, plan_version, correlation_id,
                parent_pulse_id, requester_id, capability, capability_request_hash,
                risk_tier, taint, summary, timeout_class, timeout_seconds,
                created_at, expires_at, status, queue_state, approver_id,
                resolved_at, consumed_at, resolution_reason, decision_signature, decision_key_version
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
            )
            ON CONFLICT (approval_id) DO UPDATE SET
                status = EXCLUDED.status,
                queue_state = EXCLUDED.queue_state,
                approver_id = EXCLUDED.approver_id,
                resolved_at = EXCLUDED.resolved_at,
                consumed_at = EXCLUDED.consumed_at,
                resolution_reason = EXCLUDED.resolution_reason,
                decision_signature = EXCLUDED.decision_signature,
                decision_key_version = EXCLUDED.decision_key_version;
        """
        params = (
            req.approval_id,
            req.space_id,
            req.goal_id,
            req.plan_id,
            req.plan_version,
            req.correlation_id,
            req.parent_pulse_id,
            req.requester_id,
            req.capability,
            req.capability_request_hash,
            req.risk_tier,
            req.taint,
            req.summary,
            req.timeout_class,
            req.timeout_seconds,
            req.created_at,
            req.expires_at,
            req.status,
            req.queue_state,
            req.approver_id or None,
            req.resolved_at,
            req.consumed_at,
            req.resolution_reason,
            req.decision_signature,
            req.decision_key_version,
        )
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(query, params)

    def get(self, approval_id: str) -> ApprovalRequest | None:
        query = """
            SELECT
                approval_id, space_id, goal_id, plan_id, plan_version, correlation_id,
                parent_pulse_id, requester_id, capability, capability_request_hash,
                risk_tier, taint, summary, timeout_class, timeout_seconds,
                created_at, expires_at, status, queue_state, approver_id,
                resolved_at, consumed_at, resolution_reason, decision_signature, decision_key_version,
                created_db_at
            FROM approvals
            WHERE approval_id = %s;
        """
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(query, (approval_id,))
                row = cur.fetchone()
                if row is None:
                    return None
                return self._row_to_request(row)

    def list_by_space(
        self,
        space_id: str,
        status: ApprovalLifecycleState | None = None,
        queue_state: AttentionQueueState | None = None,
    ) -> list[ApprovalRequest]:
        query = """
            SELECT
                approval_id, space_id, goal_id, plan_id, plan_version, correlation_id,
                parent_pulse_id, requester_id, capability, capability_request_hash,
                risk_tier, taint, summary, timeout_class, timeout_seconds,
                created_at, expires_at, status, queue_state, approver_id,
                resolved_at, consumed_at, resolution_reason, decision_signature, decision_key_version,
                created_db_at
            FROM approvals
            WHERE space_id = %s
        """
        params: list[Any] = [space_id]
        if status is not None:
            query += " AND status = %s"
            params.append(status)
        if queue_state is not None:
            query += " AND queue_state = %s"
            params.append(queue_state)
        query += " ORDER BY created_at ASC;"

        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(query, tuple(params))
                rows = cur.fetchall()
                return [self._row_to_request(r) for r in rows]

    def transition_cas(
        self,
        approval_id: str,
        expected_status: ApprovalLifecycleState,
        new_status: ApprovalLifecycleState,
        approver_id: str | None = None,
        resolved_at: float | None = None,
        reason: str | None = None,
        signature: str | None = None,
        key_version: int = 1,
    ) -> bool:
        query = """
            UPDATE approvals
            SET status = %s,
                approver_id = COALESCE(%s, approver_id),
                resolved_at = COALESCE(%s, resolved_at),
                consumed_at = CASE WHEN %s = 'consumed' THEN COALESCE(%s, EXTRACT(EPOCH FROM NOW())) ELSE consumed_at END,
                resolution_reason = COALESCE(%s, resolution_reason),
                decision_signature = COALESCE(%s, decision_signature),
                decision_key_version = %s,
                queue_state = CASE WHEN %s IN ('approved', 'denied', 'expired', 'consumed') THEN 'resolved' ELSE queue_state END
            WHERE approval_id = %s AND status = %s;
        """
        params = (
            new_status,
            approver_id,
            resolved_at,
            new_status,
            resolved_at,
            reason,
            signature,
            key_version,
            new_status,
            approval_id,
            expected_status,
        )
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(query, params)
                return cur.rowcount == 1

    def update_queue_state(
        self, approval_id: str, new_queue_state: AttentionQueueState
    ) -> bool:
        query = "UPDATE approvals SET queue_state = %s WHERE approval_id = %s;"
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(query, (new_queue_state, approval_id))
                return cur.rowcount == 1

    # -------------------------------------------------------------------------
    # CredentialStore Protocol Implementation
    # -------------------------------------------------------------------------

    def get_credential(self, approver_id: str) -> ApproverCredentialRecord | None:
        query = """
            SELECT
                approver_id, token_id, secret_ref, created_at, expires_at,
                is_revoked, revoked_at, revocation_reason, version
            FROM approver_credentials
            WHERE approver_id = %s;
        """
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(query, (approver_id,))
                row = cur.fetchone()
                if row is None:
                    return None
                created_at = row[3] if row[3].tzinfo else row[3].replace(tzinfo=timezone.utc)
                expires_at = row[4] if row[4].tzinfo else row[4].replace(tzinfo=timezone.utc)
                revoked_at = None
                if row[6]:
                    revoked_at = row[6] if row[6].tzinfo else row[6].replace(tzinfo=timezone.utc)
                return ApproverCredentialRecord(
                    approver_id=row[0],
                    token_id=row[1],
                    secret_ref=row[2],
                    created_at=created_at,
                    expires_at=expires_at,
                    is_revoked=row[5],
                    revoked_at=revoked_at,
                    revocation_reason=row[7],
                    version=row[8],
                )

    def register_credential(self, cred: ApproverCredentialRecord) -> None:
        query = """
            INSERT INTO approver_credentials (
                approver_id, token_id, secret_ref, created_at, expires_at,
                is_revoked, revoked_at, revocation_reason, version
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (approver_id) DO UPDATE SET
                token_id = EXCLUDED.token_id,
                secret_ref = EXCLUDED.secret_ref,
                expires_at = EXCLUDED.expires_at,
                is_revoked = EXCLUDED.is_revoked,
                revoked_at = EXCLUDED.revoked_at,
                revocation_reason = EXCLUDED.revocation_reason,
                version = EXCLUDED.version;
        """
        params = (
            cred.approver_id,
            cred.token_id,
            cred.secret_ref,
            cred.created_at,
            cred.expires_at,
            cred.is_revoked,
            cred.revoked_at,
            cred.revocation_reason,
            cred.version,
        )
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(query, params)

    def revoke_credential(self, approver_id: str, reason: str = "") -> bool:
        query = """
            UPDATE approver_credentials
            SET is_revoked = TRUE,
                revoked_at = NOW(),
                revocation_reason = %s
            WHERE approver_id = %s;
        """
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(query, (reason, approver_id))
                return cur.rowcount == 1

    # -------------------------------------------------------------------------
    # NonceStore Protocol Implementation
    # -------------------------------------------------------------------------

    def consume_nonce(self, nonce: str, approver_id: str, timestamp: float) -> bool:
        query = """
            INSERT INTO approver_auth_nonces (nonce, approver_id, timestamp)
            VALUES (%s, %s, %s)
            ON CONFLICT (nonce) DO NOTHING;
        """
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(query, (nonce.lower(), approver_id, timestamp))
                return cur.rowcount == 1

