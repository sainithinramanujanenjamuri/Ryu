"""Deterministic Mock Embedding Provider: Hermetic, offline vector generator.

Phase 15.5.1 — Embedding Protocol + Deterministic Mock (MEM-SEM-003, ADR-0049)

This provider generates reproducible, L2-normalized pseudo-random vectors derived
from SHA-256 digests of canonicalized text. It requires ZERO external ML libraries
(no PyTorch, no NumPy, no sentence-transformers, no Ollama) and executes fully offline.

Purpose:
- Protocol conformance testing (EmbeddingProviderProtocol)
- Vector dimensionality and normalization verification
- Cross-process reproducible testing
- Replay test support without live model execution
"""

from __future__ import annotations

import hashlib
import math

from core.space.memory_protocol import (
    DEFAULT_MAX_BATCH_SIZE,
    DEFAULT_MAX_INPUT_CHARS,
    EmbeddingProviderProtocol,
    EmbeddingResult,
    normalize_embedding_input,
)

__all__ = [
    "DeterministicMockEmbeddingProvider",
    "compute_similarity",
]


class DeterministicMockEmbeddingProvider(EmbeddingProviderProtocol):
    """Hermetic deterministic embedding provider for testing and offline environments.

    Invariants:
    - DETERMINISTIC: Identical input text produces bitwise identical vector components across processes.
    - HERMETIC: 100% offline, zero network access, zero third-party ML dependencies.
    - BOUNDED: Enforces input character ceiling (2048 chars) and batch ceiling (16 items).
    - NORMALIZED: Vectors are L2-normalized (norm(vector) ≈ 1.0) using standard-library arithmetic.
    """

    def __init__(
        self,
        model_name: str = "deterministic-mock",
        dimension: int = 128,
        version: str = "1.0.0",
        max_batch_size: int = DEFAULT_MAX_BATCH_SIZE,
        max_input_chars: int = DEFAULT_MAX_INPUT_CHARS,
        fail_on_oversized: bool = False,
    ) -> None:
        if not model_name or not model_name.strip():
            raise ValueError("model_name must not be empty")
        if not version or not str(version).strip():
            raise ValueError("version must not be empty")
        if dimension <= 0:
            raise ValueError(f"dimension must be positive, got {dimension}")
        if max_batch_size <= 0:
            raise ValueError(f"max_batch_size must be positive, got {max_batch_size}")
        if max_input_chars <= 0:
            raise ValueError(f"max_input_chars must be positive, got {max_input_chars}")

        self._model_name = str(model_name).strip()
        self._dimension = int(dimension)
        self._version = str(version).strip()
        self._max_batch_size = int(max_batch_size)
        self._max_input_chars = int(max_input_chars)
        self._fail_on_oversized = bool(fail_on_oversized)

    @property
    def model_name(self) -> str:
        """Model or provider identifier."""
        return self._model_name

    @property
    def dimension(self) -> int:
        """Declared vector dimension."""
        return self._dimension

    @property
    def version(self) -> str:
        """Version string of the embedding provider / algorithm."""
        return self._version

    @property
    def max_batch_size(self) -> int:
        """Maximum allowed batch size."""
        return self._max_batch_size

    @property
    def max_input_chars(self) -> int:
        """Maximum allowed input character length before truncation or error."""
        return self._max_input_chars

    def embed(self, text: str) -> EmbeddingResult:
        """Generate a deterministic, L2-normalized embedding for a single text input."""
        norm_text = normalize_embedding_input(
            text,
            max_chars=self._max_input_chars,
            fail_on_oversized=self._fail_on_oversized,
        )

        # Generate deterministic float components in [-1.0, 1.0] via SHA-256 counter mode
        raw_values: list[float] = []
        counter = 0
        while len(raw_values) < self._dimension:
            # Hash counter + canonical text
            seed_bytes = f"{counter}\x00{norm_text}".encode("utf-8")
            block = hashlib.sha256(seed_bytes).digest()

            # Extract 4-byte big-endian unsigned integers, map to [-1.0, 1.0]
            for j in range(0, 32, 4):
                if len(raw_values) >= self._dimension:
                    break
                int_val = int.from_bytes(block[j : j + 4], byteorder="big")
                # Scale from [0, 4294967295] to [-1.0, 1.0]
                val = (int_val / 4294967295.0) * 2.0 - 1.0
                raw_values.append(val)

            counter += 1

        # Compute L2 Euclidean norm
        norm = math.sqrt(sum(x * x for x in raw_values))
        if norm == 0.0:
            # Deterministic degenerate fallback: unit vector on axis 0
            normalized_vec = tuple(1.0 if idx == 0 else 0.0 for idx in range(self._dimension))
        else:
            normalized_vec = tuple(x / norm for x in raw_values)

        return EmbeddingResult(
            vector=normalized_vec,
            model=self._model_name,
            dimension=self._dimension,
            version=self._version,
        )

    def embed_text(self, text: str) -> EmbeddingResult:
        """Convenience alias for embed()."""
        return self.embed(text)

    def embed_batch(self, texts: list[str]) -> list[EmbeddingResult]:
        """Generate embeddings for a batch of text inputs preserving exact order."""
        if not isinstance(texts, (list, tuple)):
            raise TypeError(f"texts must be a list or tuple, got {type(texts).__name__}")

        if len(texts) > self._max_batch_size:
            raise ValueError(
                f"Batch size {len(texts)} exceeds maximum allowed batch size of {self._max_batch_size}"
            )

        if len(texts) == 0:
            return []

        return [self.embed(t) for t in texts]


def compute_similarity(res1: EmbeddingResult, res2: EmbeddingResult) -> float:
    """Compute cosine similarity between two L2-normalized embedding results via dot product.

    Raises:
        ValueError: If vector dimensions or models/versions do not match.
    """
    if res1.dimension != res2.dimension:
        raise ValueError(
            f"Cannot compute similarity between differing dimensions: {res1.dimension} vs {res2.dimension}"
        )
    if res1.model != res2.model or res1.version != res2.version:
        raise ValueError(
            f"Cannot compute similarity between differing model/version: "
            f"'{res1.model}@{res1.version}' vs '{res2.model}@{res2.version}'"
        )

    # Dot product of L2-normalized vectors is cosine similarity
    dot = sum(a * b for a, b in zip(res1.vector, res2.vector))
    # Clamp to [-1.0, 1.0] to protect against slight floating point overshoots
    return max(-1.0, min(1.0, dot))
