"""Memory Adapters package: concrete implementations and extension boundaries.

spec §4 (Space Memory), §15 (Storage Layer), ADR-0033 — Phase 10
"""

from __future__ import annotations

from memory.adapters.in_memory import InMemoryMemoryAdapter
from memory.adapters.neo4j_stub import Neo4jAdapterStub
from memory.adapters.postgres import PostgreSQLMemoryAdapter
from memory.adapters.qdrant_stub import QdrantAdapterStub

__all__ = [
    "InMemoryMemoryAdapter",
    "PostgreSQLMemoryAdapter",
    "QdrantAdapterStub",
    "Neo4jAdapterStub",
]

