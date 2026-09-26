import os
from dataclasses import dataclass


@dataclass
class PostgresConfig:
    host: str = "localhost"
    port: int = 5432
    db: str = "ryu_dev"
    user: str = "ryu"
    password: str = "ryu_dev_password"

    @classmethod
    def from_env(cls) -> "PostgresConfig":
        return cls(
            host=os.environ.get("RYU_PG_HOST", "localhost"),
            port=int(os.environ.get("RYU_PG_PORT", 5432)),
            db=os.environ.get("RYU_PG_DB", "ryu_dev"),
            user=os.environ.get("RYU_PG_USER", "ryu"),
            password=os.environ.get("RYU_PG_PASSWORD", "ryu_dev_password"),
        )

@dataclass
class RedisConfig:
    host: str = "localhost"
    port: int = 6379
    stream_prefix: str = "ryu:pulses"
    consumer_group: str = "ryu-consumer"

    @classmethod
    def from_env(cls) -> "RedisConfig":
        return cls(
            host=os.environ.get("RYU_REDIS_HOST", "localhost"),
            port=int(os.environ.get("RYU_REDIS_PORT", 6379)),
            stream_prefix=os.environ.get("RYU_REDIS_STREAM_PREFIX", "ryu:pulses"),
            consumer_group=os.environ.get("RYU_REDIS_CONSUMER_GROUP", "ryu-consumer"),
        )

@dataclass
class DurableBusConfig:
    pg: PostgresConfig
    redis: RedisConfig
    integration_tests: bool = False

    @classmethod
    def from_env(cls) -> "DurableBusConfig":
        return cls(
            pg=PostgresConfig.from_env(),
            redis=RedisConfig.from_env(),
            integration_tests=os.environ.get("RYU_INTEGRATION_TESTS", "0") == "1",
        )


