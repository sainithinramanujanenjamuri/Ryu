import json
from datetime import timezone
from typing import Protocol

import psycopg2
from psycopg2.extras import Json

from ryu.pulse_bus.config import PostgresConfig
from ryu.pulse_bus.pulse import Pulse


class PulseStore(Protocol):
    def append(self, pulse: Pulse) -> int: ...
    def read(self, from_position: int) -> list[Pulse]: ...
    def read_by_correlation(self, correlation_id: str) -> list[Pulse]: ...
    def read_by_space(self, space_id: str) -> list[Pulse]: ...
    def read_by_parent(self, parent_pulse_id: str) -> list[Pulse]: ...
    def get_by_id(self, pulse_id: str) -> Pulse | None: ...
    def exists(self, pulse_id: str) -> bool: ...
    def mark_published(self, pulse_id: str) -> None: ...
    def get_unpublished(self, limit: int) -> list[Pulse]: ...

class InMemoryPulseStore:
    def __init__(self) -> None:
        self._log: list[Pulse] = []
        self._published: set[str] = set()

    def append(self, pulse: Pulse) -> int:
        if self.exists(pulse.id):
            return next(i + 1 for i, p in enumerate(self._log) if p.id == pulse.id)
        self._log.append(pulse)
        return len(self._log)

    def read(self, from_position: int) -> list[Pulse]:
        return self._log[from_position:]

    def read_by_correlation(self, correlation_id: str) -> list[Pulse]:
        return [p for p in self._log if p.correlation_id == correlation_id]

    def read_by_space(self, space_id: str) -> list[Pulse]:
        return [p for p in self._log if p.space_id == space_id]

    def read_by_parent(self, parent_pulse_id: str) -> list[Pulse]:
        return [p for p in self._log if p.parent_pulse_id == parent_pulse_id]

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

class PostgresPulseStore:
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
            pulse.id, pulse.space_id, pulse.type, pulse.severity, pulse.source,
            pulse.timestamp, Json(pulse.payload), pulse.taint,
            pulse.correlation_id, pulse.parent_pulse_id,
        )
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(query, params)
                res = cur.fetchone()
                if res:
                    return int(res[0])
                # It was a duplicate. Fetch its position.
                cur.execute("SELECT position FROM pulses WHERE id = %s", (pulse.id,))
                row = cur.fetchone()
                assert row is not None
                return int(row[0])

    def read(self, from_position: int) -> list[Pulse]:
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                sql = "SELECT * FROM pulses WHERE position > %s ORDER BY position ASC"
                cur.execute(sql, (from_position,))
                return [self._row_to_pulse(row) for row in cur.fetchall()]

    def read_by_correlation(self, correlation_id: str) -> list[Pulse]:
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                sql = (
                    "SELECT * FROM pulses WHERE correlation_id = %s"
                    " ORDER BY position ASC"
                )
                cur.execute(sql, (correlation_id,))
                return [self._row_to_pulse(row) for row in cur.fetchall()]

    def read_by_space(self, space_id: str) -> list[Pulse]:
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                sql = "SELECT * FROM pulses WHERE space_id = %s ORDER BY position ASC"
                cur.execute(sql, (space_id,))
                return [self._row_to_pulse(row) for row in cur.fetchall()]

    def read_by_parent(self, parent_pulse_id: str) -> list[Pulse]:
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                sql = (
                    "SELECT * FROM pulses WHERE parent_pulse_id = %s"
                    " ORDER BY position ASC"
                )
                cur.execute(sql, (parent_pulse_id,))
                return [self._row_to_pulse(row) for row in cur.fetchall()]

    def get_by_id(self, pulse_id: str) -> Pulse | None:
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM pulses WHERE id = %s", (pulse_id,))
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
                    "SELECT * FROM pulses WHERE redis_published = FALSE"
                    " ORDER BY position ASC LIMIT %s"
                )
                cur.execute(sql, (limit,))
                return [self._row_to_pulse(row) for row in cur.fetchall()]
