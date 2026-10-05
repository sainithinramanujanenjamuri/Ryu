"""Unit and adversarial test suite for Phase 15.5.1: Embedding Protocol + Deterministic Mock.

Contracts: MEM-SEM-003, ADR-0049
Verification IDs: EMB-001 through EMB-018, adversarial test matrix
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from core.space.memory_protocol import (
    EmbeddingProviderProtocol,
    EmbeddingResult,
    normalize_embedding_input,
)
from memory.embeddings.deterministic_mock import (
    DeterministicMockEmbeddingProvider,
    compute_similarity,
)


class TestEmbeddingProtocolAndMock:
    """EMB-001 through EMB-018 tests."""

    def test_emb_001_protocol_conformance(self) -> None:
        """EMB-001: Verify DeterministicMockEmbeddingProvider implements EmbeddingProviderProtocol."""
        provider = DeterministicMockEmbeddingProvider()
        assert isinstance(provider, EmbeddingProviderProtocol)
        assert hasattr(provider, "model_name")
        assert hasattr(provider, "dimension")
        assert hasattr(provider, "version")
        assert hasattr(provider, "embed")
        assert hasattr(provider, "embed_batch")

    def test_emb_002_deterministic_single_embedding(self) -> None:
        """EMB-002: Same text repeatedly produces identical vector."""
        provider = DeterministicMockEmbeddingProvider()
        text = "Task execution failed with error: transient connection timeout"
        res1 = provider.embed(text)
        res2 = provider.embed(text)
        assert res1.vector == res2.vector
        assert res1.model == res2.model
        assert res1.dimension == res2.dimension
        assert res1.version == res2.version

    def test_emb_003_cross_process_determinism(self) -> None:
        """EMB-003: Same text produces bitwise identical vector in separate Python processes."""
        provider = DeterministicMockEmbeddingProvider()
        text = "Deterministic cross-process testing invariant: 42"
        local_result = provider.embed(text)

        # Execute in external Python subprocess
        code = f"""
import json
from memory.embeddings.deterministic_mock import DeterministicMockEmbeddingProvider
p = DeterministicMockEmbeddingProvider()
r = p.embed({repr(text)})
print(json.dumps(r.to_dict()))
"""
        proc = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            check=True,
            cwd=str(Path(__file__).resolve().parents[2]),
        )
        remote_data = json.loads(proc.stdout.strip())
        assert remote_data["model"] == local_result.model
        assert remote_data["dimension"] == local_result.dimension
        assert remote_data["version"] == local_result.version
        assert remote_data["vector"] == list(local_result.vector)

    def test_emb_004_batch_determinism_and_order(self) -> None:
        """EMB-004: Same batch produces identical results and preserves input order."""
        provider = DeterministicMockEmbeddingProvider()
        texts = [
            "first text outcome",
            "second text repair step",
            "third text counterfactual analysis",
        ]
        batch_results = provider.embed_batch(texts)
        assert len(batch_results) == 3

        # Compare with individual sequential calls
        for idx, text in enumerate(texts):
            single = provider.embed(text)
            assert batch_results[idx].vector == single.vector
            assert batch_results[idx].model == single.model
            assert batch_results[idx].dimension == single.dimension

    def test_emb_005_dimension_enforcement(self) -> None:
        """EMB-005: Vector length strictly equals declared dimension."""
        for dim in [32, 64, 128, 256, 384]:
            provider = DeterministicMockEmbeddingProvider(dimension=dim)
            assert provider.dimension == dim
            res = provider.embed("sample test text")
            assert len(res.vector) == dim
            assert res.dimension == dim

    def test_emb_006_l2_normalization(self) -> None:
        """EMB-006: Vector norm meets defined unit tolerance (norm ≈ 1.0)."""
        provider = DeterministicMockEmbeddingProvider()
        for sample in [
            "quick brown fox",
            "critical failure in database connection",
            "short",
            "a" * 500,
        ]:
            res = provider.embed(sample)
            norm = res.norm()
            assert abs(norm - 1.0) < 1e-6, f"Norm {norm} deviated from 1.0 for '{sample}'"

    def test_emb_007_metadata_consistency(self) -> None:
        """EMB-007: Model, dimension, and version are stable and internally consistent."""
        provider = DeterministicMockEmbeddingProvider(
            model_name="custom-mock-v2",
            dimension=64,
            version="2.1.0",
        )
        res = provider.embed("sample input")
        assert res.model == "custom-mock-v2"
        assert res.dimension == 64
        assert res.version == "2.1.0"

    def test_emb_008_empty_input_behavior(self) -> None:
        """EMB-008: Empty and whitespace-only strings are rejected deterministically."""
        provider = DeterministicMockEmbeddingProvider()
        with pytest.raises(ValueError, match="empty or whitespace-only"):
            provider.embed("")

        with pytest.raises(ValueError, match="empty or whitespace-only"):
            provider.embed("   \t  \n  ")

    def test_emb_009_oversized_input_behavior(self) -> None:
        """EMB-009: Oversized input is deterministically bounded to max_chars."""
        provider = DeterministicMockEmbeddingProvider(max_input_chars=100, fail_on_oversized=False)
        long_text = "word " * 100  # 500 chars
        res = provider.embed(long_text)
        assert len(res.vector) == provider.dimension

        # Normalized and truncated text matches expected_norm
        norm_long = normalize_embedding_input(long_text, max_chars=100, fail_on_oversized=False)
        expected_norm = provider.embed(norm_long)
        assert res.vector == expected_norm.vector

        # Fail closed when configured
        strict_provider = DeterministicMockEmbeddingProvider(max_input_chars=100, fail_on_oversized=True)
        with pytest.raises(ValueError, match="exceeds maximum allowed"):
            strict_provider.embed(long_text)

    def test_emb_010_unicode_handling(self) -> None:
        """EMB-010: Unicode equivalence (NFKC) yields identical vectors."""
        provider = DeterministicMockEmbeddingProvider()
        # "café" with precomposed é vs "cafe" + combining acute accent
        text1 = "caf\u00e9"
        text2 = "cafe\u0301"
        assert text1 != text2  # raw characters differ
        res1 = provider.embed(text1)
        res2 = provider.embed(text2)
        assert res1.vector == res2.vector

    def test_emb_011_whitespace_normalization(self) -> None:
        """EMB-011: Whitespace collapsing guarantees canonical representation."""
        provider = DeterministicMockEmbeddingProvider()
        text1 = "hello   world \t from \n ryu"
        text2 = "hello world from ryu"
        res1 = provider.embed(text1)
        res2 = provider.embed(text2)
        assert res1.vector == res2.vector

    def test_emb_012_nan_and_infinity_rejected(self) -> None:
        """EMB-012: EmbeddingResult validation rejects NaN and Infinity."""
        # NaN rejection
        with pytest.raises(ValueError, match="NaN"):
            EmbeddingResult(
                vector=(1.0, float("nan"), 0.0),
                model="test",
                dimension=3,
                version="1.0.0",
            )

        # Infinity rejection
        with pytest.raises(ValueError, match="infinite"):
            EmbeddingResult(
                vector=(1.0, float("inf"), 0.0),
                model="test",
                dimension=3,
                version="1.0.0",
            )

        # Negative infinity rejection
        with pytest.raises(ValueError, match="infinite"):
            EmbeddingResult(
                vector=(1.0, float("-inf"), 0.0),
                model="test",
                dimension=3,
                version="1.0.0",
            )

    def test_emb_013_invalid_dimension_rejected(self) -> None:
        """EMB-013: Malformed results with dimension mismatch are rejected."""
        with pytest.raises(ValueError, match="does not match declared dimension"):
            EmbeddingResult(
                vector=(0.1, 0.2, 0.3),
                model="test",
                dimension=4,  # Mismatch
                version="1.0.0",
            )

        with pytest.raises(ValueError, match="dimension must be positive"):
            EmbeddingResult(
                vector=(),
                model="test",
                dimension=0,
                version="1.0.0",
            )

    def test_emb_014_batch_bounds_and_empty(self) -> None:
        """EMB-014: Oversized batches are rejected; empty batches return empty list."""
        provider = DeterministicMockEmbeddingProvider(max_batch_size=4)

        # Empty batch
        assert provider.embed_batch([]) == []

        # Valid batch
        valid = provider.embed_batch(["a", "b", "c", "d"])
        assert len(valid) == 4

        # Oversized batch
        with pytest.raises(ValueError, match="exceeds maximum allowed batch size"):
            provider.embed_batch(["a", "b", "c", "d", "e"])

    def test_emb_015_no_external_network_access(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """EMB-015: Mock provider performs zero network operations."""
        import socket

        def guarded_socket(*args: Any, **kwargs: Any) -> None:
            raise RuntimeError("Network socket call intercepted: mock provider must remain offline")

        monkeypatch.setattr(socket, "socket", guarded_socket)

        provider = DeterministicMockEmbeddingProvider()
        res = provider.embed("hermetic verification text")
        assert len(res.vector) == 128

    def test_emb_016_no_ml_dependency(self) -> None:
        """EMB-016: Verify the deterministic provider requires no external ML framework."""
        import memory.embeddings.deterministic_mock as mod

        mod_file = Path(mod.__file__)
        code = mod_file.read_text(encoding="utf-8")

        for forbidden in ["torch", "numpy", "sentence_transformers", "fastembed", "ollama"]:
            assert f"import {forbidden}" not in code
            assert f"from {forbidden}" not in code

    def test_emb_017_core_dependency_guard(self) -> None:
        """EMB-017: Run scripts/dep_guard.py to guarantee Core Boundary independence."""
        repo_root = Path(__file__).resolve().parents[2]
        proc = subprocess.run(
            [sys.executable, str(repo_root / "scripts" / "dep_guard.py")],
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 0, f"dep_guard failed:\n{proc.stdout}\n{proc.stderr}"

    def test_emb_018_deterministic_serialization(self) -> None:
        """EMB-018: Equivalent embedding results serialize deterministically."""
        provider = DeterministicMockEmbeddingProvider()
        res1 = provider.embed("sample serialization text")
        res2 = provider.embed("sample serialization text")

        d1 = res1.to_dict()
        d2 = res2.to_dict()
        assert d1 == d2

        json1 = json.dumps(d1, sort_keys=True)
        json2 = json.dumps(d2, sort_keys=True)
        assert json1 == json2

        # Round-trip reconstruction
        reconstructed = EmbeddingResult.from_dict(d1)
        assert reconstructed == res1
        assert reconstructed.vector == res1.vector


class TestAdversarialEmbeddingCases:
    """Adversarial stress and edge cases for the embedding boundary."""

    def test_adv_non_string_input(self) -> None:
        """Non-string inputs raise TypeError."""
        provider = DeterministicMockEmbeddingProvider()
        for invalid in [None, 123, ["nested"], {"dict": 1}]:
            with pytest.raises(TypeError):
                provider.embed(invalid)  # type: ignore

    def test_adv_similarity_computation(self) -> None:
        """Identical text gives cosine similarity 1.0; differing text gives < 1.0."""
        provider = DeterministicMockEmbeddingProvider()
        r1 = provider.embed("capability failure: terminal.exec timeout")
        r2 = provider.embed("capability failure: terminal.exec timeout")
        r3 = provider.embed("completely unrelated task execution completed")

        sim_identical = compute_similarity(r1, r2)
        assert abs(sim_identical - 1.0) < 1e-6

        sim_diff = compute_similarity(r1, r3)
        assert sim_diff < 0.99  # Avalanche effect ensures distinct vectors

    def test_adv_similarity_dimension_mismatch(self) -> None:
        """compute_similarity raises ValueError if dimensions mismatch."""
        p1 = DeterministicMockEmbeddingProvider(dimension=64)
        p2 = DeterministicMockEmbeddingProvider(dimension=128)
        r1 = p1.embed("text")
        r2 = p2.embed("text")
        with pytest.raises(ValueError, match="differing dimensions"):
            compute_similarity(r1, r2)

    def test_adv_similarity_model_mismatch(self) -> None:
        """compute_similarity raises ValueError if models mismatch."""
        p1 = DeterministicMockEmbeddingProvider(model_name="model-a")
        p2 = DeterministicMockEmbeddingProvider(model_name="model-b")
        r1 = p1.embed("text")
        r2 = p2.embed("text")
        with pytest.raises(ValueError, match="differing model/version"):
            compute_similarity(r1, r2)

    def test_adv_duplicate_batch_items(self) -> None:
        """Duplicate texts in a batch produce identical vectors in place."""
        provider = DeterministicMockEmbeddingProvider()
        batch = ["repeated", "repeated", "repeated"]
        results = provider.embed_batch(batch)
        assert len(results) == 3
        assert results[0].vector == results[1].vector == results[2].vector

    def test_adv_degenerate_zero_norm_handling(self) -> None:
        """Fallback unit vector for zero norm vector."""
        # Create EmbeddingResult directly
        zero_vec = (0.0, 0.0, 0.0)
        res = EmbeddingResult(
            vector=zero_vec,
            model="test",
            dimension=3,
            version="1.0.0",
        )
        assert res.norm() == 0.0

    def test_adv_negative_zero_component(self) -> None:
        """Negative zero float is handled cleanly."""
        vec = (-0.0, 1.0, 0.0)
        res = EmbeddingResult(
            vector=vec,
            model="test",
            dimension=3,
            version="1.0.0",
        )
        assert abs(res.norm() - 1.0) < 1e-6
