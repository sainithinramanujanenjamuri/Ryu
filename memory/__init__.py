"""Memory Package: Space Memory adapters, evaluation harness, reflector, and promotion pipeline.

spec §4 (Space Memory), §7 (Adaptation Layer), MEM-001..006, ADR-0033..0036 — Phase 10
"""

from __future__ import annotations

from memory.adapters.in_memory import InMemoryMemoryAdapter
from memory.adapters.neo4j_stub import Neo4jAdapterStub
from memory.adapters.postgres import PostgreSQLMemoryAdapter
from memory.adapters.qdrant_stub import QdrantAdapterStub
from memory.evaluation import (
    EvaluationCase,
    EvaluationModule,
    EvaluationResult,
    FrozenTrace,
    FrozenTraceCorpus,
)
from memory.promotion import (
    PromotionError,
    PromotionPipeline,
    PromotionRequest,
)
from memory.reflector import Reflector

__all__ = [
    "InMemoryMemoryAdapter",
    "PostgreSQLMemoryAdapter",
    "QdrantAdapterStub",
    "Neo4jAdapterStub",
    "Reflector",
    "EvaluationModule",
    "EvaluationResult",
    "FrozenTrace",
    "EvaluationCase",
    "FrozenTraceCorpus",
    "PromotionPipeline",
    "PromotionRequest",
    "PromotionError",
]
