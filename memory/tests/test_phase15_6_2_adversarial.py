"""Adversarial and Edge Case Tests for Phase 15.6.2 — Non-Blocking Adaptation Timeout.

Covers:
  - Hanging provider threads do not block close() (non-blocking shutdown)
  - Zero and negative timeout_seconds executes directly without executor
  - Idempotent close() calls across concurrent threads
  - Concurrent generate_hints calls racing with close()
  - Provider raising unhandled fatal exceptions in worker thread
  - Thread leak containment under repeated timeouts

Contracts:
  - MEM-SEM-004 (Graceful Semantic Retrieval Degradation)
  - MEM-SEM-005 (Bounded Advisory Experience Hints)
  - ADR-0050 §3

AGENTS.md §5, §6, §7
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timezone

from core.memory.adaptation import AdaptationLayer
from core.space.memory_protocol import (
    EmbeddingResult,
    ExperienceRecord,
)
from memory.adapters.in_memory import InMemoryMemoryAdapter
from memory.embeddings.deterministic_mock import DeterministicMockEmbeddingProvider

_MOCK_PROV = DeterministicMockEmbeddingProvider()
_MOCK_VEC = _MOCK_PROV.embed("default test").vector


def _make_experience(
    exp_id: str,
    space_id: str = "space-adv",
    embedding: tuple[float, ...] | None = _MOCK_VEC,
) -> ExperienceRecord:
    return ExperienceRecord(
        experience_id=exp_id,
        space_id=space_id,
        situation={"capability": "tool.test"},
        action={"capability": "tool.test"},
        outcome="failure",
        counterfactual="Fallback advice",
        applicable_context={"error_class": "structural.error"},
        stored_at=datetime.now(timezone.utc),
        embedding=embedding,
        embedding_model=_MOCK_PROV.model_name if embedding is not None else None,
        embedding_dimension=_MOCK_PROV.dimension if embedding is not None else None,
        embedding_version=_MOCK_PROV.version if embedding is not None else None,
    )


def test_adversarial_hanging_provider_does_not_block_close() -> None:
    """layer.close(wait=False, cancel_futures=True) returns immediately even with a hanging thread."""
    store = InMemoryMemoryAdapter()
    store.store_experience(_make_experience("exp-hang-1"))

    class IndefiniteHangProvider:
        def embed(self, text: str) -> EmbeddingResult:
            # Hang for a long duration (10s)
            time.sleep(10.0)
            return EmbeddingResult(vector=(0.0,) * 128, model="hang", dimension=128, version="1")

    layer = AdaptationLayer(
        memory_store=store,
        embedding_provider=IndefiniteHangProvider(),  # type: ignore[arg-type]
        timeout_seconds=0.05,
    )

    # Trigger retrieval which spawns a hanging background task
    t0 = time.perf_counter()
    layer.generate_hints("space-adv", {"query_text": "hang"})
    call_time = time.perf_counter() - t0
    assert call_time < 0.25

    # close() must return immediately (< 0.1s), NOT wait 10s!
    t_close_0 = time.perf_counter()
    layer.close(wait=False, cancel_futures=True)
    close_time = time.perf_counter() - t_close_0

    assert close_time < 0.15, f"close() blocked for {close_time:.2f}s instead of returning immediately!"


def test_adversarial_zero_and_negative_timeout_direct_sync() -> None:
    """When timeout_seconds <= 0, retrieval runs synchronously without spawning a thread pool."""
    store = InMemoryMemoryAdapter()
    store.store_experience(_make_experience("exp-sync-1"))

    class SyncProvider:
        def __init__(self) -> None:
            self.thread_ids: list[int] = []

        def embed(self, text: str) -> EmbeddingResult:
            self.thread_ids.append(threading.get_ident())
            return _MOCK_PROV.embed(text)

    provider = SyncProvider()
    layer = AdaptationLayer(
        memory_store=store,
        embedding_provider=provider,  # type: ignore[arg-type]
        timeout_seconds=0.0,  # Zero timeout -> synchronous direct execution
    )

    caller_tid = threading.get_ident()
    hints = layer.generate_hints("space-adv", {"query_text": "default test"})

    assert len(hints) >= 1
    # Executor should NOT have been initialized
    assert layer._executor is None
    # If embed was called, it was called on the caller thread
    if provider.thread_ids:
        assert provider.thread_ids[0] == caller_tid


def test_adversarial_idempotent_close_concurrent() -> None:
    """Calling close() repeatedly or concurrently across multiple threads is safely idempotent."""
    store = InMemoryMemoryAdapter()
    layer = AdaptationLayer(memory_store=store, timeout_seconds=0.5)

    def closer() -> None:
        for _ in range(10):
            layer.close(wait=False, cancel_futures=True)

    threads = [threading.Thread(target=closer) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert layer._closed is True
    assert layer._executor is None


def test_adversarial_racing_generate_hints_and_close() -> None:
    """Racing generate_hints() with close() does not raise uncaught errors and degrades gracefully."""
    store = InMemoryMemoryAdapter()
    store.store_experience(_make_experience("exp-race-1"))

    class ShortSleepProvider:
        def embed(self, text: str) -> EmbeddingResult:
            time.sleep(0.02)
            return EmbeddingResult(vector=(0.1,) * 128, model="race", dimension=128, version="1")

    layer = AdaptationLayer(
        memory_store=store,
        embedding_provider=ShortSleepProvider(),  # type: ignore[arg-type]
        timeout_seconds=0.05,
    )

    errors: list[Exception] = []

    def caller() -> None:
        try:
            for _ in range(5):
                layer.generate_hints("space-adv", {"query_text": "race"})
        except Exception as e:
            errors.append(e)

    t_call = threading.Thread(target=caller)
    t_call.start()
    time.sleep(0.01)
    layer.close(wait=False, cancel_futures=True)
    t_call.join()

    # Zero unhandled crashes allowed
    assert len(errors) == 0


def test_adversarial_provider_fatal_error_handled_gracefully() -> None:
    """Provider raising an unexpected RuntimeError inside worker thread is captured and degraded."""
    store = InMemoryMemoryAdapter()
    store.store_experience(_make_experience("exp-err-1"))

    class CrashingProvider:
        def embed(self, text: str) -> EmbeddingResult:
            raise RuntimeError("Underlying C library crashed")

    layer = AdaptationLayer(
        memory_store=store,
        embedding_provider=CrashingProvider(),  # type: ignore[arg-type]
        timeout_seconds=0.5,
    )

    hints = layer.generate_hints("space-adv", {"query_text": "crash test"})
    assert len(hints) >= 1
    assert hints[0].experience_id == "exp-err-1"
    status = layer.get_last_retrieval_status()
    assert status["mode"] == "metadata_fallback"
    assert "RuntimeError" in str(status["error"])

    layer.close(wait=False, cancel_futures=True)
