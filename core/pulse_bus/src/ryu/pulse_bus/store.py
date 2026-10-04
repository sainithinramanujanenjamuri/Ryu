from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import timezone
from typing import Protocol

import psycopg2
from psycopg2.extras import Json

from ryu.pulse_bus.config import (
    DEFAULT_PULSE_PAGE_SIZE,
    PostgresConfig,
    get_max_pulse_page_size,
)
from ryu.pulse_bus.pulse import Pulse


@dataclass(frozen=True)
class StoredPulse:
    """A pulse associated with its authoritative database sequence position."""

    position: int
    pulse: Pulse


@dataclass(frozen=True)
class PulsePage:
    """A bounded page of pulses retrieved from a PulseStore."""

    space_id: str
    entries: tuple[StoredPulse, ...]
    next_after_position: int | None
    next_before_position: int | None
    has_more: bool


class PulseRetrievalBoundExceeded(RuntimeError):
    """Raised when an unpaged legacy pulse retrieval exceeds the safe limit ceiling."""


def validate_page_bounds(
    *,
    space_id: str | None = None,
    limit: int | None = None,
    after_position: int | None = None,
    before_position: int | None = None,
    pulse_types: frozenset[str] | set[str] | None = None,
    type_prefix: str | None = None,
) -> None:
    """Pre-execution validation of limits, cursors, and filters for pulse retrieval."""
    if space_id is not None:
        if not isinstance(space_id, str) or not space_id.strip():
            raise ValueError(f"Invalid space_id: {space_id!r}")

    if limit is not None:
        if type(limit) is not int:  # explicitly reject bool (subclass of int)
            raise TypeError(f"limit must be an integer, got {type(limit).__name__}")
        max_allowed = get_max_pulse_page_size()
        if limit < 1 or limit > max_allowed:
            raise ValueError(
                f"limit {limit} is out of bounds [1, {max_allowed}]"
            )

    if after_position is not None:
        if type(after_position) is not int:
            raise TypeError(
                f"after_position must be an integer, got {type(after_position).__name__}"
            )
        if after_position < 0:
            raise ValueError(f"after_position must be non-negative, got {after_position}")

    if before_position is not None:
        if type(before_position) is not int:
            raise TypeError(
                f"before_position must be an integer, got {type(before_position).__name__}"
            )
        if before_position < 0:
            raise ValueError(f"before_position must be non-negative, got {before_position}")

    if pulse_types is not None:
        if not isinstance(pulse_types, (set, frozenset)):
            raise TypeError("pulse_types must be a set or frozenset of strings")
        for pt in pulse_types:
            if not isinstance(pt, str) or not pt:
                raise ValueError("pulse_types cannot contain empty or non-string items")

    if type_prefix is not None:
        if not isinstance(type_prefix, str) or not type_prefix:
            raise ValueError("type_prefix must be a non-empty string")


class PulseStore(Protocol):
    """Authoritative protocol for durable and in-memory pulse stores."""

    def append(self, pulse: Pulse) -> int: ...
    def read(self, from_position: int) -> list[Pulse]: ...
    def read_by_correlation(self, correlation_id: str) -> list[Pulse]: ...
    def read_by_space(self, space_id: str) -> list[Pulse]: ...
    def read_by_parent(self, parent_pulse_id: str) -> list[Pulse]: ...
    def get_by_id(self, pulse_id: str) -> Pulse | None: ...
    def exists(self, pulse_id: str) -> bool: ...
    def mark_published(self, pulse_id: str) -> None: ...
    def get_unpublished(self, limit: int) -> list[Pulse]: ...

    # Bounded retrieval primitives (Phase 15.3 — PULSE-013..017)
    def read_space_page(
        self,
        space_id: str,
        *,
        after_position: int = 0,
        limit: int = DEFAULT_PULSE_PAGE_SIZE,
        pulse_types: frozenset[str] | set[str] | None = None,
    ) -> PulsePage: ...

    def read_space_tail(
        self,
        space_id: str,
        *,
        limit: int = DEFAULT_PULSE_PAGE_SIZE,
        before_position: int | None = None,
        pulse_types: frozenset[str] | set[str] | None = None,
        type_prefix: str | None = None,
    ) -> PulsePage: ...

    def iter_space(
        self,
        space_id: str,
        *,
        after_position: int = 0,
        page_size: int = DEFAULT_PULSE_PAGE_SIZE,
    ) -> Iterator[StoredPulse]: ...

    def has_pulse_of_type(
        self,
        space_id: str,
        correlation_id: str,
        pulse_type: str,
    ) -> bool: ...

    def read_page(
        self,
        *,
        after_position: int = 0,
        limit: int = DEFAULT_PULSE_PAGE_SIZE,
    ) -> PulsePage: ...


class InMemoryPulseStore:
    """In-memory pulse store implementation for local testing and ephemeral bus."""

    def __init__(self) -> None:
        self._log: list[Pulse] = []
        self._published: set[str] = set()

    def append(self, pulse: Pulse) -> int:
        if self.exists(pulse.id):
            return next(i + 1 for i, p in enumerate(self._log) if p.id == pulse.id)
        self._log.append(pulse)
        return len(self._log)

    def read(self, from_position: int) -> list[Pulse]:
        cap = get_max_pulse_page_size()
        matching = [p for i, p in enumerate(self._log, start=1) if i > from_position]
        if len(matching) > cap:
            raise PulseRetrievalBoundExceeded(
                f"Global read returned {len(matching)} pulses, exceeding safe cap of {cap}"
            )
        return matching

    def read_by_correlation(self, correlation_id: str) -> list[Pulse]:
        cap = get_max_pulse_page_size()
        matching = [p for p in self._log if p.correlation_id == correlation_id]
        if len(matching) > cap:
            raise PulseRetrievalBoundExceeded(
                f"read_by_correlation returned {len(matching)} pulses, exceeding safe cap of {cap}"
            )
        return matching

    def read_by_space(self, space_id: str) -> list[Pulse]:
        cap = get_max_pulse_page_size()
        matching = [p for p in self._log if p.space_id == space_id]
        if len(matching) > cap:
            raise PulseRetrievalBoundExceeded(
                f"read_by_space for {space_id!r} returned {len(matching)} pulses, exceeding safe cap of {cap}"
            )
        return matching

    def read_by_parent(self, parent_pulse_id: str) -> list[Pulse]:
        cap = get_max_pulse_page_size()
        matching = [p for p in self._log if p.parent_pulse_id == parent_pulse_id]
        if len(matching) > cap:
            raise PulseRetrievalBoundExceeded(
                f"read_by_parent returned {len(matching)} pulses, exceeding safe cap of {cap}"
            )
        return matching

    def get_by_id(self, pulse_id: str) -> Pulse | None:
        for p in self._log:
            if p.id == pulse_id:
                return p
        return None

    def exists(self, pulse_id: str) -> bool:
        return self.get_by_id(pulse_id) is not None

    def mark_published(self, pulse_id: str) -> None:
        self._published.add(pulse_id)

    def get_unpublished(self, limit: int) -> list[Pulse]:
        return [p for p in self._log if p.id not in self._published][:limit]

    def read_space_page(
        self,
        space_id: str,
        *,
        after_position: int = 0,
        limit: int = DEFAULT_PULSE_PAGE_SIZE,
        pulse_types: frozenset[str] | set[str] | None = None,
    ) -> PulsePage:
        validate_page_bounds(
            space_id=space_id,
            limit=limit,
            after_position=after_position,
            pulse_types=pulse_types,
        )

        matching: list[StoredPulse] = []
        for pos, p in enumerate(self._log, start=1):
            if p.space_id != space_id or pos <= after_position:
                continue
            if pulse_types is not None and p.type not in pulse_types:
                continue
            matching.append(StoredPulse(position=pos, pulse=p))
            if len(matching) > limit:
                break

        has_more = len(matching) > limit
        entries = tuple(matching[:limit])
        next_after = entries[-1].position if entries and has_more else None
        return PulsePage(
            space_id=space_id,
            entries=entries,
            next_after_position=next_after,
            next_before_position=None,
            has_more=has_more,
        )

    def read_space_tail(
        self,
        space_id: str,
        *,
        limit: int = DEFAULT_PULSE_PAGE_SIZE,
        before_position: int | None = None,
        pulse_types: frozenset[str] | set[str] | None = None,
        type_prefix: str | None = None,
    ) -> PulsePage:
        validate_page_bounds(
            space_id=space_id,
            limit=limit,
            before_position=before_position,
            pulse_types=pulse_types,
            type_prefix=type_prefix,
        )

        matching: list[StoredPulse] = []
        for pos in range(len(self._log), 0, -1):
            p = self._log[pos - 1]
            if p.space_id != space_id:
                continue
            if before_position is not None and pos >= before_position:
                continue
            if pulse_types is not None and p.type not in pulse_types:
                continue
            if type_prefix is not None and not p.type.startswith(type_prefix):
                continue
            matching.append(StoredPulse(position=pos, pulse=p))
            if len(matching) > limit:
                break

        has_more = len(matching) > limit
        tail_slice = matching[:limit]
        next_before = tail_slice[-1].position if tail_slice and has_more else None

        # Return in ascending position order for natural chronological presentation
        tail_slice.reverse()
        return PulsePage(
            space_id=space_id,
            entries=tuple(tail_slice),
            next_after_position=None,
            next_before_position=next_before,
            has_more=has_more,
        )

    def iter_space(
        self,
        space_id: str,
        *,
        after_position: int = 0,
        page_size: int = DEFAULT_PULSE_PAGE_SIZE,
    ) -> Iterator[StoredPulse]:
        validate_page_bounds(
            space_id=space_id,
            limit=page_size,
            after_position=after_position,
        )
        snapshot = [
            StoredPulse(position=pos, pulse=p)
            for pos, p in enumerate(self._log, start=1)
            if p.space_id == space_id and pos > after_position
        ]
        yield from snapshot

    def has_pulse_of_type(
        self,
        space_id: str,
        correlation_id: str,
        pulse_type: str,
    ) -> bool:
        validate_page_bounds(space_id=space_id)
        return any(
            p.space_id == space_id
            and p.correlation_id == correlation_id
            and p.type == pulse_type
            for p in self._log
        )

    def read_page(
        self,
        *,
        after_position: int = 0,
        limit: int = DEFAULT_PULSE_PAGE_SIZE,
    ) -> PulsePage:
        validate_page_bounds(limit=limit, after_position=after_position)
        matching: list[StoredPulse] = []
        for pos, p in enumerate(self._log, start=1):
            if pos <= after_position:
                continue
            matching.append(StoredPulse(position=pos, pulse=p))
            if len(matching) > limit:
                break

        has_more = len(matching) > limit
        entries = tuple(matching[:limit])
        next_after = entries[-1].position if entries and has_more else None
        return PulsePage(
            space_id="*",
            entries=entries,
            next_after_position=next_after,
            next_before_position=None,
            has_more=has_more,
        )


class PostgresPulseStore:
    """PostgreSQL durable pulse store backed by ACID persistence and keyset pagination."""

    def __init__(self, config: PostgresConfig) -> None:
        self.config = config

    def _get_conn(self) -> psycopg2.extensions.connection:
        return psycopg2.connect(
            host=self.config.host,
            port=self.config.port,
            dbname=self.config.db,
            user=self.config.user,
            password=self.config.password,
        )

    def _row_to_pulse(self, row: tuple) -> Pulse:  # type: ignore[type-arg]
        return Pulse(
            id=row[1],
            space_id=row[2],
            type=row[3],
            severity=row[4],
            source=row[5],
            timestamp=row[6] if row[6].tzinfo else row[6].replace(tzinfo=timezone.utc),
            payload=row[7] if isinstance(row[7], dict) else json.loads(row[7]),
            taint=row[8],
            correlation_id=row[9],
            parent_pulse_id=row[10],
        )

    def _row_to_stored_pulse(self, row: tuple) -> StoredPulse:  # type: ignore[type-arg]
        return StoredPulse(
            position=int(row[0]),
            pulse=self._row_to_pulse(row),
        )

    def append(self, pulse: Pulse) -> int:
        query = """
            INSERT INTO pulses (
                id, space_id, type, severity, source, timestamp,
                payload, taint, correlation_id, parent_pulse_id
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
            )
            ON CONFLICT (id) DO NOTHING
            RETURNING position;
        """
        params = (
            pulse.id,
            pulse.space_id,
            pulse.type,
            pulse.severity,
            pulse.source,
            pulse.timestamp,
            Json(pulse.payload),
            pulse.taint,
            pulse.correlation_id,
            pulse.parent_pulse_id,
        )
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(query, params)
                res = cur.fetchone()
                if res:
                    return int(res[0])
                # Duplicate: fetch existing position
                cur.execute("SELECT position FROM pulses WHERE id = %s", (pulse.id,))
                row = cur.fetchone()
                assert row is not None
                return int(row[0])

    def read(self, from_position: int) -> list[Pulse]:
        cap = get_max_pulse_page_size()
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                sql = (
                    "SELECT position, id, space_id, type, severity, source, timestamp, "
                    "payload, taint, correlation_id, parent_pulse_id "
                    "FROM pulses WHERE position > %s ORDER BY position ASC LIMIT %s"
                )
                cur.execute(sql, (from_position, cap + 1))
                rows = cur.fetchall()
                if len(rows) > cap:
                    raise PulseRetrievalBoundExceeded(
                        f"Global read returned more than {cap} pulses. Use read_page() instead."
                    )
                return [self._row_to_pulse(row) for row in rows]

    def read_by_correlation(self, correlation_id: str) -> list[Pulse]:
        cap = get_max_pulse_page_size()
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                sql = (
                    "SELECT position, id, space_id, type, severity, source, timestamp, "
                    "payload, taint, correlation_id, parent_pulse_id "
                    "FROM pulses WHERE correlation_id = %s ORDER BY position ASC LIMIT %s"
                )
                cur.execute(sql, (correlation_id, cap + 1))
                rows = cur.fetchall()
                if len(rows) > cap:
                    raise PulseRetrievalBoundExceeded(
                        f"read_by_correlation returned more than {cap} pulses."
                    )
                return [self._row_to_pulse(row) for row in rows]

    def read_by_space(self, space_id: str) -> list[Pulse]:
        cap = get_max_pulse_page_size()
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                sql = (
                    "SELECT position, id, space_id, type, severity, source, timestamp, "
                    "payload, taint, correlation_id, parent_pulse_id "
                    "FROM pulses WHERE space_id = %s ORDER BY position ASC LIMIT %s"
                )
                cur.execute(sql, (space_id, cap + 1))
                rows = cur.fetchall()
                if len(rows) > cap:
                    raise PulseRetrievalBoundExceeded(
                        f"read_by_space for {space_id!r} returned more than {cap} pulses. "
                        "Use read_space_page() or read_space_tail() instead."
                    )
                return [self._row_to_pulse(row) for row in rows]

    def read_by_parent(self, parent_pulse_id: str) -> list[Pulse]:
        cap = get_max_pulse_page_size()
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                sql = (
                    "SELECT position, id, space_id, type, severity, source, timestamp, "
                    "payload, taint, correlation_id, parent_pulse_id "
                    "FROM pulses WHERE parent_pulse_id = %s ORDER BY position ASC LIMIT %s"
                )
                cur.execute(sql, (parent_pulse_id, cap + 1))
                rows = cur.fetchall()
                if len(rows) > cap:
                    raise PulseRetrievalBoundExceeded(
                        f"read_by_parent returned more than {cap} pulses."
                    )
                return [self._row_to_pulse(row) for row in rows]

    def get_by_id(self, pulse_id: str) -> Pulse | None:
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                sql = (
                    "SELECT position, id, space_id, type, severity, source, timestamp, "
                    "payload, taint, correlation_id, parent_pulse_id "
                    "FROM pulses WHERE id = %s"
                )
                cur.execute(sql, (pulse_id,))
                row = cur.fetchone()
                if row:
                    return self._row_to_pulse(row)
                return None

    def exists(self, pulse_id: str) -> bool:
        return self.get_by_id(pulse_id) is not None

    def mark_published(self, pulse_id: str) -> None:
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                sql = "UPDATE pulses SET redis_published = TRUE WHERE id = %s"
                cur.execute(sql, (pulse_id,))

    def get_unpublished(self, limit: int) -> list[Pulse]:
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                sql = (
                    "SELECT position, id, space_id, type, severity, source, timestamp, "
                    "payload, taint, correlation_id, parent_pulse_id "
                    "FROM pulses WHERE redis_published = FALSE ORDER BY position ASC LIMIT %s"
                )
                cur.execute(sql, (limit,))
                return [self._row_to_pulse(row) for row in cur.fetchall()]

    def read_space_page(
        self,
        space_id: str,
        *,
        after_position: int = 0,
        limit: int = DEFAULT_PULSE_PAGE_SIZE,
        pulse_types: frozenset[str] | set[str] | None = None,
    ) -> PulsePage:
        validate_page_bounds(
            space_id=space_id,
            limit=limit,
            after_position=after_position,
            pulse_types=pulse_types,
        )

        clauses = ["space_id = %s", "position > %s"]
        params: list[object] = [space_id, after_position]
        if pulse_types is not None:
            clauses.append("type = ANY(%s)")
            params.append(list(pulse_types))
        params.append(limit + 1)

        sql = (
            "SELECT position, id, space_id, type, severity, source, timestamp, "
            "payload, taint, correlation_id, parent_pulse_id "
            f"FROM pulses WHERE {' AND '.join(clauses)} ORDER BY position ASC LIMIT %s"
        )
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                rows = cur.fetchall()

        has_more = len(rows) > limit
        page_rows = rows[:limit]
        entries = tuple(self._row_to_stored_pulse(r) for r in page_rows)
        next_after = entries[-1].position if entries and has_more else None
        return PulsePage(
            space_id=space_id,
            entries=entries,
            next_after_position=next_after,
            next_before_position=None,
            has_more=has_more,
        )

    def read_space_tail(
        self,
        space_id: str,
        *,
        limit: int = DEFAULT_PULSE_PAGE_SIZE,
        before_position: int | None = None,
        pulse_types: frozenset[str] | set[str] | None = None,
        type_prefix: str | None = None,
    ) -> PulsePage:
        validate_page_bounds(
            space_id=space_id,
            limit=limit,
            before_position=before_position,
            pulse_types=pulse_types,
            type_prefix=type_prefix,
        )

        clauses = ["space_id = %s"]
        params: list[object] = [space_id]
        if before_position is not None:
            clauses.append("position < %s")
            params.append(before_position)
        if pulse_types is not None:
            clauses.append("type = ANY(%s)")
            params.append(list(pulse_types))
        if type_prefix is not None:
            escaped = (
                type_prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            )
            clauses.append("type LIKE %s ESCAPE '\\'")
            params.append(f"{escaped}%")
        params.append(limit + 1)

        sql = (
            "SELECT position, id, space_id, type, severity, source, timestamp, "
            "payload, taint, correlation_id, parent_pulse_id "
            f"FROM pulses WHERE {' AND '.join(clauses)} ORDER BY position DESC LIMIT %s"
        )
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                rows = cur.fetchall()

        has_more = len(rows) > limit
        tail_rows = rows[:limit]
        next_before = int(tail_rows[-1][0]) if tail_rows and has_more else None

        # Reverse to ascending for natural chronological presentation
        tail_rows.reverse()
        entries = tuple(self._row_to_stored_pulse(r) for r in tail_rows)
        return PulsePage(
            space_id=space_id,
            entries=entries,
            next_after_position=None,
            next_before_position=next_before,
            has_more=has_more,
        )

    def iter_space(
        self,
        space_id: str,
        *,
        after_position: int = 0,
        page_size: int = DEFAULT_PULSE_PAGE_SIZE,
    ) -> Iterator[StoredPulse]:
        validate_page_bounds(
            space_id=space_id,
            limit=page_size,
            after_position=after_position,
        )

        conn = self._get_conn()
        try:
            conn.set_session(isolation_level="REPEATABLE READ", readonly=True)
            cursor_name = f"ryu_iter_{uuid.uuid4().hex}"
            with conn.cursor(name=cursor_name) as cur:
                cur.itersize = page_size
                sql = (
                    "SELECT position, id, space_id, type, severity, source, timestamp, "
                    "payload, taint, correlation_id, parent_pulse_id "
                    "FROM pulses WHERE space_id = %s AND position > %s ORDER BY position ASC"
                )
                cur.execute(sql, (space_id, after_position))
                for row in cur:
                    yield self._row_to_stored_pulse(row)
        finally:
            conn.close()

    def has_pulse_of_type(
        self,
        space_id: str,
        correlation_id: str,
        pulse_type: str,
    ) -> bool:
        validate_page_bounds(space_id=space_id)
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                sql = (
                    "SELECT 1 FROM pulses "
                    "WHERE space_id = %s AND correlation_id = %s AND type = %s LIMIT 1"
                )
                cur.execute(sql, (space_id, correlation_id, pulse_type))
                return cur.fetchone() is not None

    def read_page(
        self,
        *,
        after_position: int = 0,
        limit: int = DEFAULT_PULSE_PAGE_SIZE,
    ) -> PulsePage:
        validate_page_bounds(limit=limit, after_position=after_position)
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                sql = (
                    "SELECT position, id, space_id, type, severity, source, timestamp, "
                    "payload, taint, correlation_id, parent_pulse_id "
                    "FROM pulses WHERE position > %s ORDER BY position ASC LIMIT %s"
                )
                cur.execute(sql, (after_position, limit + 1))
                rows = cur.fetchall()

        has_more = len(rows) > limit
        page_rows = rows[:limit]
        entries = tuple(self._row_to_stored_pulse(r) for r in page_rows)
        next_after = entries[-1].position if entries and has_more else None
        return PulsePage(
            space_id="*",
            entries=entries,
            next_after_position=next_after,
            next_before_position=None,
            has_more=has_more,
        )
