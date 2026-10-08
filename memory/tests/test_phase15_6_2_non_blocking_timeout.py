"""Unit and Integration Tests for Phase 15.6.2 — Non-Blocking Adaptation Timeout & Managed Executor.

Covers:
  - Strict 500ms caller SLA: calling thread does NOT block on hanging worker threads (F05-AUDIT-02)
  - Managed executor pool reuse across sequential calls (zero thread churn)
  - Custom external executor injection and ownership preservation
  - Context manager lifecycle (__enter__, __exit__)
  - Explicit close() non-blocking resource release (wait=False, cancel_futures=True)
  - Graceful degradation when executor is closed
  - Bounded worker thread limits (max_workers clamping)
  - Concurrent multi-space timeout resilience

Contracts:
  - MEM-SEM-004 (Graceful Semantic Retrieval Degradation)
  - MEM-SEM-005 (Bounded Advisory Experience Hints)
  - ADR-0050 §3

AGENTS.md §5, §7
"""

from __future__ import annotations

import concurrent.futures
import threading
import time
from datetime import datetime, timezone
from typing import Any

from core.memory.adaptation import AdaptationLayer
from core.space.memory_protocol import (
    EmbeddingResult,
    ExperienceRecord,
    SemanticExperienceQuery,
)
from memory.adapters.in_memory import InMemoryMemoryAdapter
from memory.embeddings.deterministic_mock import DeterministicMockEmbeddingProvider

_MOCK_PROV = DeterministicMockEmbeddingProvider()
_MOCK_VEC = _MOCK_PROV.embed("default test embedding").vector


def _make_experience(
    exp_id: str,
    space_id: str = "space-timeout-test",
    capability: str = "tool.exec",
    counterfactual: str = "Use alternative tool",
    outcome: str = "failure",
    embedding: tuple[float, ...] | None = _MOCK_VEC,
) -> ExperienceRecord:
    return ExperienceRecord(
        experience_id=exp_id,
        space_id=space_id,
        situation={"task_id": "task-test", "capability": capability},
        action={"capability": capability},
        outcome=outcome,
        counterfactual=counterfactual,
        applicable_context={"error_class": "transient.timeout"},
        stored_at=datetime.now(timezone.utc),
        embedding=embedding,
        embedding_model=_MOCK_PROV.model_name if embedding is not None else None,
        embedding_dimension=_MOCK_PROV.dimension if embedding is not None else None,
        embedding_version=_MOCK_PROV.version if embedding is not None else None,
    )


class SlowEmbeddingProvider:
    """Mock embedding provider that simulates a hanging / slow network call."""

    def __init__(self, delay_seconds: float = 2.0) -> None:
        self.delay_seconds = delay_seconds
        self.call_count = 0

    def embed(self, text: str) -> EmbeddingResult:
        self.call_count += 1
        time.sleep(self.delay_seconds)
        return EmbeddingResult(
            vector=(0.05,) * 128,
            model="slow-mock",
            dimension=128,
            version="1.0",
        )


class SlowMemoryStore(InMemoryMemoryAdapter):
    """Mock memory adapter whose retrieve_semantic_experiences call hangs."""

    def __init__(self, delay_seconds: float = 2.0) -> None:
        super().__init__()
        self.delay_seconds = delay_seconds

    def retrieve_semantic_experiences(
        self,
        query: SemanticExperienceQuery,
        embedding_provider: Any = None,
    ) -> list[Any]:
        time.sleep(self.delay_seconds)
        return super().retrieve_semantic_experiences(query, embedding_provider)


def test_non_blocking_timeout_caller_sla() -> None:
    """F05-AUDIT-02 & MEM-SEM-004: Caller thread returns within timeout_seconds and does NOT block for 2.0s."""
    store = InMemoryMemoryAdapter()
    store.store_experience(_make_experience("exp-fallback-1", counterfactual="Fallback advice"))

    # Provider takes 2.0 seconds; timeout is set to 0.05 seconds
    slow_provider = SlowEmbeddingProvider(delay_seconds=2.0)
    layer = AdaptationLayer(
        memory_store=store,
        embedding_provider=slow_provider,  # type: ignore[arg-type]
        timeout_seconds=0.05,
    )

    t0 = time.perf_counter()
    hints = layer.generate_hints(
        space_id="space-timeout-test",
        situation_hint={"task_id": "task-test", "capability": "tool.exec", "query_text": "run command"},
    )
    elapsed = time.perf_counter() - t0

    # Must return well before 2.0s (strictly within ~0.25s allowing test scheduling margin)
    assert elapsed < 0.35, f"Calling thread blocked for {elapsed:.2f}s instead of returning immediately!"

    # Verify graceful degradation to metadata fallback
    assert len(hints) >= 1
    assert hints[0].experience_id == "exp-fallback-1"
    status = layer.get_last_retrieval_status()
    assert status["mode"] == "metadata_fallback"
    assert status["semantic_attempted"] is True
    assert status["semantic_succeeded"] is False
    assert "timeout_exceeded" in str(status["error"])

    layer.close(wait=False, cancel_futures=True)


def test_managed_executor_pool_reuse_no_thread_churn() -> None:
    """Managed executor pool reuses worker threads across calls without thread creation churn."""
    store = InMemoryMemoryAdapter()
    store.store_experience(_make_experience("exp-reuse-1"))

    layer = AdaptationLayer(
        memory_store=store,
        embedding_provider=_MOCK_PROV,
        timeout_seconds=0.5,
        max_workers=2,
    )

    # First call: initializes pool
    hints1 = layer.generate_hints("space-timeout-test", {"query_text": "default test embedding"})
    assert len(hints1) >= 1
    exec_id_1 = id(layer._executor)

    # Second call: reuses exact same pool
    hints2 = layer.generate_hints("space-timeout-test", {"query_text": "default test embedding"})
    assert len(hints2) >= 1
    exec_id_2 = id(layer._executor)

    assert exec_id_1 == exec_id_2

    layer.close(wait=False, cancel_futures=True)


def test_custom_external_executor_injection() -> None:
    """Injecting an external executor preserves caller ownership and is not shut down by close()."""
    store = InMemoryMemoryAdapter()
    store.store_experience(_make_experience("exp-ext-1"))

    external_pool = concurrent.futures.ThreadPoolExecutor(max_workers=2, thread_name_prefix="external-test")
    layer = AdaptationLayer(
        memory_store=store,
        embedding_provider=_MOCK_PROV,
        executor=external_pool,
        timeout_seconds=0.5,
    )

    hints = layer.generate_hints("space-timeout-test", {"query_text": "default test embedding"})
    assert len(hints) >= 1

    # Closing layer must NOT shut down the external pool
    layer.close()
    assert layer._closed is True
    # External pool should still be usable
    future = external_pool.submit(lambda: 42)
    assert future.result() == 42

    external_pool.shutdown(wait=True)


def test_context_manager_lifecycle() -> None:
    """AdaptationLayer functions as a context manager and closes cleanly on exit."""
    store = InMemoryMemoryAdapter()
    store.store_experience(_make_experience("exp-ctx-1"))

    with AdaptationLayer(
        memory_store=store,
        embedding_provider=_MOCK_PROV,
        timeout_seconds=0.5,
    ) as layer:
        assert layer._closed is False
        hints = layer.generate_hints("space-timeout-test", {"query_text": "default test embedding"})
        assert len(hints) >= 1

    # After with-block exit, layer is closed
    assert layer._closed is True
    assert layer._executor is None


def test_closed_layer_graceful_degradation() -> None:
    """Calling generate_hints on a closed layer degrades safely to metadata fallback without crashing."""
    store = InMemoryMemoryAdapter()
    store.store_experience(_make_experience("exp-closed-1", counterfactual="Closed fallback"))

    layer = AdaptationLayer(memory_store=store, timeout_seconds=0.5)
    layer.close()
    assert layer._closed is True

    # Must not raise RuntimeError to caller; degrades gracefully
    hints = layer.generate_hints("space-timeout-test", {"task_id": "t1", "query_text": "test"})
    assert len(hints) >= 1
    assert hints[0].experience_id == "exp-closed-1"
    status = layer.get_last_retrieval_status()
    assert status["mode"] in ("metadata_fallback", "metadata_direct")


def test_concurrent_multi_space_non_blocking_timeout() -> None:
    """Multiple concurrent space callers with hanging providers return in parallel under strict SLA."""
    store = InMemoryMemoryAdapter()
    store.store_experience(_make_experience("exp-s1", space_id="space-1", counterfactual="Advice 1"))
    store.store_experience(_make_experience("exp-s2", space_id="space-2", counterfactual="Advice 2"))

    slow_provider = SlowEmbeddingProvider(delay_seconds=2.0)
    layer = AdaptationLayer(
        memory_store=store,
        embedding_provider=slow_provider,  # type: ignore[arg-type]
        timeout_seconds=0.08,
        max_workers=4,
    )

    results: dict[str, list[Any]] = {}
    elapsed_times: dict[str, float] = {}

    def worker(space_id: str) -> None:
        t0 = time.perf_counter()
        h = layer.generate_hints(space_id, {"task_id": "t", "query_text": f"query {space_id}"})
        elapsed_times[space_id] = time.perf_counter() - t0
        results[space_id] = h

    t1 = threading.Thread(target=worker, args=("space-1",))
    t2 = threading.Thread(target=worker, args=("space-2",))

    t1.start()
    t2.start()
    t1.join()
    t2.join()

    # Both must complete well under 2.0s
    assert elapsed_times["space-1"] < 0.4
    assert elapsed_times["space-2"] < 0.4

    # Both must receive Space-isolated fallback hints
    assert results["space-1"][0].experience_id == "exp-s1"
    assert results["space-2"][0].experience_id == "exp-s2"

    layer.close(wait=False, cancel_futures=True)


def test_bounded_worker_threads_clamping() -> None:
    """max_workers is safely clamped to 1 <= max_workers <= 8."""
    store = InMemoryMemoryAdapter()
    layer_low = AdaptationLayer(memory_store=store, max_workers=0)
    assert layer_low.max_workers == 1

    layer_high = AdaptationLayer(memory_store=store, max_workers=100)
    assert layer_high.max_workers == 8

    layer_low.close()
    layer_high.close()
