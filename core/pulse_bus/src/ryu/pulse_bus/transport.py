import json
from datetime import datetime, timezone
from typing import Protocol

import redis
from redis.exceptions import ConnectionError

from ryu.pulse_bus.config import RedisConfig
from ryu.pulse_bus.pulse import Pulse


class PulseTransport(Protocol):
    def publish(self, pulse: Pulse, stream: str) -> str: ...

    def consume(
        self, stream: str, group: str, consumer: str, count: int
    ) -> list[tuple[str, Pulse]]: ...

    def acknowledge(self, stream: str, group: str, entry_id: str) -> None: ...

    def get_pending(self, stream: str, group: str) -> dict: ...

    def reclaim_stale(
        self, stream: str, group: str, consumer: str, min_idle_ms: int
    ) -> list[tuple[str, Pulse]]: ...

    def ensure_group(self, stream: str, group: str) -> None: ...


class NoopTransport:
    def __init__(self, raise_on_publish: bool = False):
        self._published: list[Pulse] = []
        self._raise_on_publish = raise_on_publish

    def publish(self, pulse: Pulse, stream: str) -> str:
        if self._raise_on_publish:
            raise ConnectionError("Simulated Redis failure")
        self._published.append(pulse)
        return "noop-id"

    def consume(
        self, stream: str, group: str, consumer: str, count: int
    ) -> list[tuple[str, Pulse]]:
        return []

    def acknowledge(self, stream: str, group: str, entry_id: str) -> None:
        pass

    def get_pending(self, stream: str, group: str) -> dict:
        return {"pending": 0}

    def reclaim_stale(
        self, stream: str, group: str, consumer: str, min_idle_ms: int
    ) -> list[tuple[str, Pulse]]:
        return []

    def ensure_group(self, stream: str, group: str) -> None:
        pass

class RedisStreamTransport:
    def __init__(self, config: RedisConfig):
        self.config = config
        self.redis = redis.Redis(host=config.host, port=config.port, decode_responses=True)

    def publish(self, pulse: Pulse, stream: str) -> str:
        try:
            fields = {
                "id": pulse.id,
                "type": pulse.type,
                "severity": pulse.severity,
                "correlation_id": pulse.correlation_id,
                "taint": str(pulse.taint),
                "space_id": pulse.space_id,
                "payload": json.dumps(pulse.payload)
            }
            ret = self.redis.xadd(stream, fields)
            return str(ret)
        except redis.ConnectionError as e:
            raise ConnectionError(f"Redis publish failed: {e}")

    def ensure_group(self, stream: str, group: str) -> None:
        try:
            self.redis.xgroup_create(stream, group, mkstream=True)
        except redis.ResponseError as e:
            if "BUSYGROUP" not in str(e):
                raise

    def consume(
        self, stream: str, group: str, consumer: str, count: int
    ) -> list[tuple[str, Pulse]]:
        res = self.redis.xreadgroup(group, consumer, {stream: ">"}, count=count)
        # res format: [[stream, [(entry_id, fields), ...]]]
        out = []
        if res:
            for entry_id, fields in res[0][1]:
                p = Pulse(
                    id=fields["id"],
                    space_id=fields["space_id"],
                    type=fields["type"],
                    severity=fields["severity"],
                    source="redis",
                    # timestamp not stored in stream; use epoch as placeholder
                    timestamp=datetime.fromtimestamp(0, tz=timezone.utc),
                    payload=json.loads(fields["payload"]),
                    taint=fields["taint"] == "True",
                    correlation_id=fields["correlation_id"],
                )
                out.append((entry_id, p))
        return out

    def acknowledge(self, stream: str, group: str, entry_id: str) -> None:
        self.redis.xack(stream, group, entry_id)

    def get_pending(self, stream: str, group: str) -> dict:
        res = self.redis.xpending(stream, group)
        return dict(res) if isinstance(res, dict) else {"pending": 0}

    def reclaim_stale(
        self, stream: str, group: str, consumer: str, min_idle_ms: int
    ) -> list[tuple[str, Pulse]]:
        # xautoclaim stream group consumer min_idle_time start_id count
        res = self.redis.xautoclaim(stream, group, consumer, min_idle_ms, "0-0")
        out = []
        if res and len(res) >= 2:
            messages = res[1]
            for entry_id, fields in messages:
                p = Pulse(
                    id=fields["id"],
                    space_id=fields["space_id"],
                    type=fields["type"],
                    severity=fields["severity"],
                    source="redis",
                    timestamp=datetime.fromtimestamp(0, tz=timezone.utc),
                    payload=json.loads(fields["payload"]),
                    taint=fields["taint"] == "True",
                    correlation_id=fields["correlation_id"]
                )
                out.append((entry_id, p))
        return out

