"""Memory Embeddings Package: Provider abstractions and hermetic mocks.

Phase 15.5.1 — Embedding Protocol + Deterministic Mock (MEM-SEM-003, ADR-0049)
"""

from __future__ import annotations

from memory.embeddings.deterministic_mock import (
    DeterministicMockEmbeddingProvider,
    compute_similarity,
)

__all__ = [
    "DeterministicMockEmbeddingProvider",
    "compute_similarity",
]
