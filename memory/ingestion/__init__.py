"""Memory experience embedding ingestion and outbox subsystem.

Phase 15.6.5 — F05-AUDIT-01, MEM-INGEST-001, ADR-0050.
"""

from __future__ import annotations

from memory.ingestion.pipeline import (
    EmbeddingIngestionPipeline,
    IngestionBatchResult,
    format_experience_for_embedding,
)

__all__ = [
    "EmbeddingIngestionPipeline",
    "IngestionBatchResult",
    "format_experience_for_embedding",
]

