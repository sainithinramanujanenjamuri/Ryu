"""Durable store interfaces and implementations for Resource and Lease states.

spec §9 (Resource Manager), REC-005 (Lease recovery) — Phase 3
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from typing import Any, Protocol

from core.resources.identity import Resource, ResourceIdentity
from core.resources.lease import Lease, LeaseState


class ResourceStore(Protocol):
    """Protocol for durable or in-memory resource and lease state persistence."""

    def save_lease(self, lease: Lease) -> None:
        """Save or update lease state."""
        ...

    def get_lease(self, lease_token: str) -> Lease | None:
        """Retrieve lease by token."""
        ...

    def get_by_idempotency(
        self, space_id: str, requester_id: str, idempotency_key: str
    ) -> Lease | None:
        """Retrieve active or existing lease by idempotency key."""
        ...

    def update_lease_state(
        self,
        lease_token: str,
        state: LeaseState,
        expiry: datetime | None = None,
        renewed_count: int | None = None,
    ) -> None:
        """Update lease lifecycle state, expiration, or renewal count."""
        ...

    def list_active_leases(self, space_id: str | None = None) -> list[Lease]:
        """List active leases."""
        ...

    def list_all_leases(self, space_id: str | None = None) -> list[Lease]:
        """List all leases regardless of state."""
        ...

    def save_resource(self, resource: Resource) -> None:
        """Register or update resource descriptor."""
        ...

    def get_resource(self, handle: str) -> Resource | None:
        """Retrieve resource by canonical handle."""
        ...

    def list_resources(self, space_id: str | None = None) -> list[Resource]:
        """List registered resources."""
        ...


class InMemoryResourceStore:
    """Thread-safe in-memory store for unit tests and local execution."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._leases: dict[str, Lease] = {}
        self._idempotency_map: dict[tuple[str, str, str], str] = {}
        self._resources: dict[str, Resource] = {}

    def save_lease(self, lease: Lease) -> None:
        with self._lock:
            self._leases[lease.lease_token] = lease
            if lease.idempotency_key:
                key = (lease.space_id, lease.requester_id, lease.idempotency_key)
                self._idempotency_map[key] = lease.lease_token

    def get_lease(self, lease_token: str) -> Lease | None:
        with self._lock:
            return self._leases.get(lease_token)

    def get_by_idempotency(
        self, space_id: str, requester_id: str, idempotency_key: str
    ) -> Lease | None:
        with self._lock:
            token = self._idempotency_map.get((space_id, requester_id, idempotency_key))
            if token:
                return self._leases.get(token)
            return None

    def update_lease_state(
        self,
        lease_token: str,
        state: LeaseState,
        expiry: datetime | None = None,
        renewed_count: int | None = None,
    ) -> None:
        with self._lock:
            lease = self._leases.get(lease_token)
            if lease:
                lease.state = state
                if expiry is not None:
                    lease.expiry = expiry
                if renewed_count is not None:
                    lease.renewed_count = renewed_count

    def list_active_leases(self, space_id: str | None = None) -> list[Lease]:
        with self._lock:
            res = [item for item in self._leases.values() if item.state == LeaseState.ACTIVE]
            if space_id:
                return [item for item in res if item.space_id == space_id]
            return res

    def list_all_leases(self, space_id: str | None = None) -> list[Lease]:
        with self._lock:
            if space_id:
                return [item for item in self._leases.values() if item.space_id == space_id]
            return list(self._leases.values())

    def save_resource(self, resource: Resource) -> None:
        with self._lock:
            self._resources[resource.handle] = resource

    def get_resource(self, handle: str) -> Resource | None:
        with self._lock:
            return self._resources.get(handle)

    def list_resources(self, space_id: str | None = None) -> list[Resource]:
        with self._lock:
            if space_id:
                return [r for r in self._resources.values() if r.space_id == space_id]
            return list(self._resources.values())


class PostgresResourceStore:
    """Production PostgreSQL-backed durable store for resources and leases."""

    def __init__(self, config: Any) -> None:
        self.config = config
        self._ensure_schema()

    def _get_connection(self) -> Any:
        import psycopg2
        return psycopg2.connect(
            host=self.config.host,
            port=self.config.port,
            dbname=self.config.db,
            user=self.config.user,
            password=self.config.password,
        )

    def _ensure_schema(self) -> None:
        """Create resource_leases and registered_resources tables if they do not exist."""
        sql = """
        CREATE TABLE IF NOT EXISTS registered_resources (
            handle VARCHAR(255) PRIMARY KEY,
            space_id VARCHAR(255) NOT NULL,
            resource_type VARCHAR(255) NOT NULL,
            provider_id VARCHAR(255) NOT NULL,
            instance_id VARCHAR(255) NOT NULL,
            total_capacity INT NOT NULL DEFAULT 1,
            allocated_capacity INT NOT NULL DEFAULT 0,
            metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        CREATE INDEX IF NOT EXISTS idx_resources_space ON registered_resources (space_id);

        CREATE TABLE IF NOT EXISTS resource_leases (
            lease_token VARCHAR(255) PRIMARY KEY,
            space_id VARCHAR(255) NOT NULL,
            resource_type VARCHAR(255) NOT NULL,
            provider_id VARCHAR(255) NOT NULL,
            instance_id VARCHAR(255) NOT NULL,
            requester_id VARCHAR(255) NOT NULL,
            idempotency_key VARCHAR(255),
            units INT NOT NULL DEFAULT 1,
            state VARCHAR(50) NOT NULL,
            acquired_at TIMESTAMPTZ NOT NULL,
            expiry TIMESTAMPTZ NOT NULL,
            renewed_count INT NOT NULL DEFAULT 0,
            metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        CREATE INDEX IF NOT EXISTS idx_leases_space ON resource_leases (space_id);
        CREATE INDEX IF NOT EXISTS idx_leases_res ON resource_leases (
            resource_type, provider_id, instance_id
        );
        CREATE INDEX IF NOT EXISTS idx_leases_state ON resource_leases (state);
        CREATE INDEX IF NOT EXISTS idx_leases_idempotency ON resource_leases (
            space_id, requester_id, idempotency_key
        );
        """
        conn = self._get_connection()
        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute(sql)
        finally:
            conn.close()

    def save_lease(self, lease: Lease) -> None:
        sql = """
        INSERT INTO resource_leases (
            lease_token, space_id, resource_type, provider_id, instance_id,
            requester_id, idempotency_key, units, state, acquired_at, expiry,
            renewed_count, metadata
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (lease_token) DO UPDATE SET
            state = EXCLUDED.state,
            expiry = EXCLUDED.expiry,
            renewed_count = EXCLUDED.renewed_count,
            units = EXCLUDED.units,
            metadata = EXCLUDED.metadata;
        """
        conn = self._get_connection()
        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        sql,
                        (
                            lease.lease_token,
                            lease.space_id,
                            lease.resource_id.resource_type,
                            lease.resource_id.provider_id,
                            lease.resource_id.instance_id,
                            lease.requester_id,
                            lease.idempotency_key,
                            lease.units,
                            lease.state.value,
                            lease.acquired_at,
                            lease.expiry,
                            lease.renewed_count,
                            json.dumps(lease.metadata),
                        ),
                    )
        finally:
            conn.close()

    def get_lease(self, lease_token: str) -> Lease | None:
        sql = "SELECT * FROM resource_leases WHERE lease_token = %s;"
        conn = self._get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(sql, (lease_token,))
                row = cur.fetchone()
                if not row:
                    return None
                return self._row_to_lease(row, cur.description)
        finally:
            conn.close()

    def get_by_idempotency(
        self, space_id: str, requester_id: str, idempotency_key: str
    ) -> Lease | None:
        sql = """
        SELECT * FROM resource_leases
        WHERE space_id = %s AND requester_id = %s AND idempotency_key = %s
        ORDER BY created_at DESC LIMIT 1;
        """
        conn = self._get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(sql, (space_id, requester_id, idempotency_key))
                row = cur.fetchone()
                if not row:
                    return None
                return self._row_to_lease(row, cur.description)
        finally:
            conn.close()

    def update_lease_state(
        self,
        lease_token: str,
        state: LeaseState,
        expiry: datetime | None = None,
        renewed_count: int | None = None,
    ) -> None:
        parts = ["state = %s"]
        params: list[Any] = [state.value]
        if expiry is not None:
            parts.append("expiry = %s")
            params.append(expiry)
        if renewed_count is not None:
            parts.append("renewed_count = %s")
            params.append(renewed_count)

        params.append(lease_token)
        sql = f"UPDATE resource_leases SET {', '.join(parts)} WHERE lease_token = %s;"
        conn = self._get_connection()
        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute(sql, tuple(params))
        finally:
            conn.close()

    def list_active_leases(self, space_id: str | None = None) -> list[Lease]:
        if space_id:
            sql = "SELECT * FROM resource_leases WHERE state = 'active' AND space_id = %s;"
            params: tuple[Any, ...] = (space_id,)
        else:
            sql = "SELECT * FROM resource_leases WHERE state = 'active';"
            params = ()

        conn = self._get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                rows = cur.fetchall()
                desc = cur.description
                return [self._row_to_lease(r, desc) for r in rows]
        finally:
            conn.close()

    def list_all_leases(self, space_id: str | None = None) -> list[Lease]:
        if space_id:
            sql = "SELECT * FROM resource_leases WHERE space_id = %s;"
            params: tuple[Any, ...] = (space_id,)
        else:
            sql = "SELECT * FROM resource_leases;"
            params = ()

        conn = self._get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                rows = cur.fetchall()
                desc = cur.description
                return [self._row_to_lease(r, desc) for r in rows]
        finally:
            conn.close()

    def save_resource(self, resource: Resource) -> None:
        sql = """
        INSERT INTO registered_resources (
            handle, space_id, resource_type, provider_id, instance_id,
            total_capacity, allocated_capacity, metadata, updated_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW())
        ON CONFLICT (handle) DO UPDATE SET
            total_capacity = EXCLUDED.total_capacity,
            allocated_capacity = EXCLUDED.allocated_capacity,
            metadata = EXCLUDED.metadata,
            updated_at = NOW();
        """
        conn = self._get_connection()
        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        sql,
                        (
                            resource.handle,
                            resource.space_id,
                            resource.identity.resource_type,
                            resource.identity.provider_id,
                            resource.identity.instance_id,
                            resource.total_capacity,
                            resource.allocated_capacity,
                            json.dumps(resource.metadata),
                        ),
                    )
        finally:
            conn.close()

    def get_resource(self, handle: str) -> Resource | None:
        sql = "SELECT * FROM registered_resources WHERE handle = %s;"
        conn = self._get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(sql, (handle,))
                row = cur.fetchone()
                if not row:
                    return None
                return self._row_to_resource(row, cur.description)
        finally:
            conn.close()

    def list_resources(self, space_id: str | None = None) -> list[Resource]:
        if space_id:
            sql = "SELECT * FROM registered_resources WHERE space_id = %s;"
            params: tuple[Any, ...] = (space_id,)
        else:
            sql = "SELECT * FROM registered_resources;"
            params = ()

        conn = self._get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                rows = cur.fetchall()
                desc = cur.description
                return [self._row_to_resource(r, desc) for r in rows]
        finally:
            conn.close()

    def _row_to_lease(self, row: tuple[Any, ...], desc: Any) -> Lease:
        col_names = [d[0] for d in desc]
        d = dict(zip(col_names, row, strict=False))

        acquired_at = d["acquired_at"]
        if acquired_at.tzinfo is None:
            acquired_at = acquired_at.replace(tzinfo=timezone.utc)

        expiry = d["expiry"]
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)

        meta = d["metadata"]
        if isinstance(meta, str):
            meta = json.loads(meta)

        return Lease(
            lease_token=d["lease_token"],
            resource_id=ResourceIdentity(
                resource_type=d["resource_type"],
                provider_id=d["provider_id"],
                instance_id=d["instance_id"],
            ),
            space_id=d["space_id"],
            requester_id=d["requester_id"],
            units=d["units"],
            acquired_at=acquired_at,
            expiry=expiry,
            idempotency_key=d["idempotency_key"],
            state=LeaseState(d["state"]),
            renewed_count=d["renewed_count"],
            metadata=meta or {},
        )

    def _row_to_resource(self, row: tuple[Any, ...], desc: Any) -> Resource:
        col_names = [d[0] for d in desc]
        d = dict(zip(col_names, row, strict=False))

        meta = d["metadata"]
        if isinstance(meta, str):
            meta = json.loads(meta)

        return Resource(
            identity=ResourceIdentity(
                resource_type=d["resource_type"],
                provider_id=d["provider_id"],
                instance_id=d["instance_id"],
            ),
            space_id=d["space_id"],
            total_capacity=d["total_capacity"],
            allocated_capacity=d["allocated_capacity"],
            metadata=meta or {},
        )
