"""Adaptation Layer: Read-only query layer producing contextual experience hints.

Extracts lessons from past experiences (using bounded semantic retrieval with graceful degradation)
to inform the Planner's and ConvergenceEngine's capability choices without bypassing Plan CAS,
Admission Control, or Kernel authority (ADR-0036, ADR-0043, ADR-0049, MEM-SEM-004, MEM-SEM-005).

spec §7 (Adaptation Layer), ROADMAP Phase 10 & 15.5, MEM-004, MEM-SEM-004, MEM-SEM-005 — Phase 15.5.4
"""

from __future__ import annotations

import concurrent.futures
import threading
from typing import Any

from core.space.memory_protocol import (
    AdaptationLayerProtocol,
    EmbeddingProviderProtocol,
    ExperienceHint,
    ExperienceQuery,
    ExperienceRecord,
    ScoredExperienceRecord,
    SemanticExperienceQuery,
    SpaceIsolationViolation,
    SpaceMemoryProtocol,
)

__all__ = ["AdaptationLayer", "ExperienceHint"]


class AdaptationLayer(AdaptationLayerProtocol):
    """Read-only query layer bridging SpaceMemory to Orchestrator/Planner hints.

    Constitutional Invariants (ADR-0036, ADR-0043, ADR-0049, MEM-SEM-004, MEM-SEM-005):
    - READ-ONLY: Never mutates plans, commits plans, or writes to memory.
    - NO PULSES: Emits zero Pulses directly; telemetry belongs to the Orchestrator/Reflector.
    - NO ADMISSION/RESOURCES: Cannot grant permissions or acquire resources.
    - NO KERNEL BYPASS: Planner turns hints into ProposedPlans; SpaceKernel commits via CAS.
    - BOUNDED HINTS: Produces at most K <= 5 advisory hints (MEM-SEM-005).
    - DETERMINISTIC: Deterministic deduplication and rank-preserved ordering.
    - PROVENANCE-AWARE: All hints link to valid source experience IDs and provenance refs.
    - GRACEFUL DEGRADATION: Semantic retrieval failures degrade safely to metadata fallback (MEM-SEM-004).
    - TIMEOUT BOUNDED: Semantic retrieval path bounded by 500ms timeout.
    - SPACE ISOLATION: Never returns hints from foreign Spaces (Law 1, Law 4).
    """

    def __init__(
        self,
        memory_store: SpaceMemoryProtocol,
        embedding_provider: EmbeddingProviderProtocol | None = None,
        timeout_seconds: float = 0.5,
        executor: concurrent.futures.ThreadPoolExecutor | None = None,
        max_workers: int = 2,
    ) -> None:
        self.memory_store = memory_store
        self.embedding_provider = embedding_provider
        self.timeout_seconds = timeout_seconds
        self.max_workers = max(1, min(max_workers, 8))
        self._external_executor = executor is not None
        self._executor: concurrent.futures.ThreadPoolExecutor | None = executor
        self._lock = threading.Lock()
        self._closed = False
        self._last_retrieval_status: dict[str, Any] = {}

    def _get_executor(self) -> concurrent.futures.ThreadPoolExecutor:
        """Return the managed thread pool executor, lazily initializing if needed (F05-AUDIT-02, MEM-SEM-004)."""
        with self._lock:
            if self._closed:
                raise RuntimeError("AdaptationLayer executor has been closed")
            if self._executor is None:
                self._executor = concurrent.futures.ThreadPoolExecutor(
                    max_workers=self.max_workers,
                    thread_name_prefix="adaptation-retrieval",
                )
            return self._executor

    def close(self, wait: bool = False, cancel_futures: bool = True) -> None:
        """Release managed executor resources without blocking on hanging worker threads (F05-AUDIT-02, MEM-SEM-004)."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            if self._executor is not None and not self._external_executor:
                try:
                    self._executor.shutdown(wait=wait, cancel_futures=cancel_futures)
                except TypeError:
                    self._executor.shutdown(wait=wait)
                self._executor = None

    def __enter__(self) -> AdaptationLayer:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close(wait=False, cancel_futures=True)

    def __del__(self) -> None:
        try:
            self.close(wait=False, cancel_futures=True)
        except Exception:
            pass

    def get_last_retrieval_status(self) -> dict[str, Any]:
        """Return observability metadata from the most recent hint generation call (MEM-SEM-004)."""
        return dict(self._last_retrieval_status)

    def generate_hints(
        self,
        space_id: str,
        situation_hint: dict[str, Any],
        limit: int = 5,
    ) -> list[ExperienceHint]:
        """Generate contextual hints from past experiences within the Space.

        First attempts bounded semantic retrieval (MEM-SEM-001, MEM-SEM-002).
        If semantic retrieval fails or is unavailable, degrades gracefully to metadata query (MEM-SEM-004).

        Raises:
            SpaceIsolationViolation: if space_id is empty or malformed.
            MemoryFailure: if the underlying store encounters an unrecoverable failure during fallback.
        """
        if not space_id or not space_id.strip():
            raise SpaceIsolationViolation(
                requesting_space=space_id or "<empty>",
                target_space=space_id or "<empty>",
            )

        # Enforce hard ceiling K <= 5 (MEM-SEM-005)
        clamped_limit = min(max(1, limit), 5)
        target_task = str(situation_hint.get("task_id", ""))

        scored_experiences: list[ScoredExperienceRecord] = []
        semantic_attempted = False
        semantic_error: str | None = None

        should_attempt_semantic = (
            hasattr(self.memory_store, "retrieve_semantic_experiences")
            and (
                self.embedding_provider is not None
                or "query_embedding" in situation_hint
                or ("query_text" in situation_hint and bool(situation_hint["query_text"]))
            )
        )

        if should_attempt_semantic:
            semantic_attempted = True
            query_text = str(situation_hint.get("query_text") or "")
            if not query_text:
                parts = []
                if situation_hint.get("capability"):
                    parts.append(f"capability: {situation_hint['capability']}")
                if situation_hint.get("error_class"):
                    parts.append(f"error: {situation_hint['error_class']}")
                if situation_hint.get("task_id"):
                    parts.append(f"task: {situation_hint['task_id']}")
                if situation_hint.get("fingerprint"):
                    parts.append(f"fingerprint: {situation_hint['fingerprint']}")
                query_text = " ".join(parts) if parts else "task execution failure"

            failure_fp = situation_hint.get("fingerprint") or situation_hint.get("failure_fingerprint")
            q_emb = situation_hint.get("query_embedding")
            min_sim = float(situation_hint.get("min_similarity", 0.0))

            try:
                sem_query = SemanticExperienceQuery(
                    space_id=space_id,
                    query_text=query_text,
                    query_embedding=q_emb,
                    situation_hint=situation_hint,
                    failure_fingerprint=str(failure_fp) if failure_fp else None,
                    top_k=clamped_limit,
                    min_similarity=min_sim,
                )

                if self.timeout_seconds and self.timeout_seconds > 0:
                    executor = self._get_executor()
                    future = executor.submit(
                        self.memory_store.retrieve_semantic_experiences,
                        sem_query,
                        self.embedding_provider,
                    )
                    try:
                        scored_experiences = future.result(timeout=self.timeout_seconds)
                    except (concurrent.futures.TimeoutError, TimeoutError) as t_err:
                        future.cancel()
                        semantic_error = f"timeout_exceeded: {t_err}"
                else:
                    scored_experiences = self.memory_store.retrieve_semantic_experiences(
                        sem_query, embedding_provider=self.embedding_provider
                    )
            except (concurrent.futures.TimeoutError, TimeoutError) as t_err:
                semantic_error = f"timeout_exceeded: {t_err}"
            except Exception as s_err:
                semantic_error = f"{type(s_err).__name__}: {s_err}"

        # If semantic retrieval was attempted and succeeded without error, return its hints (even if empty)
        if semantic_attempted and semantic_error is None:
            hints = self._convert_scored_to_hints(
                scored_experiences=scored_experiences,
                space_id=space_id,
                target_task=target_task,
                clamped_limit=clamped_limit,
            )
            self._last_retrieval_status = {
                "mode": "semantic",
                "semantic_attempted": True,
                "semantic_succeeded": True,
                "retrieved_count": len(scored_experiences),
                "hint_count": len(hints),
                "error": None,
            }
            return hints

        # Fallback to metadata / lexical query (MEM-SEM-004)
        meta_query = ExperienceQuery(
            space_id=space_id,
            situation_hint=situation_hint,
            limit=clamped_limit,
        )

        experiences = self.memory_store.query_similar_experiences(meta_query)
        hints = self._convert_records_to_hints(
            records=experiences,
            space_id=space_id,
            target_task=target_task,
            clamped_limit=clamped_limit,
        )

        self._last_retrieval_status = {
            "mode": "metadata_fallback" if semantic_attempted else "metadata_direct",
            "semantic_attempted": semantic_attempted,
            "semantic_succeeded": False if semantic_attempted else None,
            "retrieved_count": len(experiences),
            "hint_count": len(hints),
            "error": semantic_error,
        }
        return hints

    def _convert_scored_to_hints(
        self,
        scored_experiences: list[ScoredExperienceRecord],
        space_id: str,
        target_task: str,
        clamped_limit: int,
    ) -> list[ExperienceHint]:
        hints: list[ExperienceHint] = []
        seen_exp_ids: set[str] = set()
        seen_dedup_keys: set[tuple[str, str]] = set()

        for scored in scored_experiences:
            exp = scored.record
            if exp.space_id != space_id:
                continue
            if not exp.experience_id or not exp.experience_id.strip():
                continue
            if exp.experience_id in seen_exp_ids:
                continue

            hint = self._build_hint_from_record(
                exp=exp,
                relevance_score=scored.similarity_score,
                exact_fingerprint_match=scored.exact_fingerprint_match,
                target_task=target_task,
            )
            if hint is None:
                continue

            dedup_key = (
                hint.failed_capability or hint.suggested_alternative_capability,
                hint.counterfactual_summary,
            )
            if dedup_key in seen_dedup_keys:
                continue

            seen_dedup_keys.add(dedup_key)
            seen_exp_ids.add(exp.experience_id)

            ranked_hint = ExperienceHint(
                experience_id=hint.experience_id,
                failed_capability=hint.failed_capability,
                suggested_avoidance=hint.suggested_avoidance,
                outcome_summary=hint.outcome_summary,
                counterfactual_summary=hint.counterfactual_summary,
                relevance_score=hint.relevance_score,
                source_space_id=hint.source_space_id,
                suggested_alternative_capability=hint.suggested_alternative_capability,
                target_task_id=hint.target_task_id,
                provenance_ref=hint.provenance_ref,
                rank=len(hints) + 1,
                exact_fingerprint_match=hint.exact_fingerprint_match,
            )
            hints.append(ranked_hint)
            if len(hints) >= clamped_limit:
                break

        return hints

    def _convert_records_to_hints(
        self,
        records: list[ExperienceRecord],
        space_id: str,
        target_task: str,
        clamped_limit: int,
    ) -> list[ExperienceHint]:
        hints: list[ExperienceHint] = []
        seen_exp_ids: set[str] = set()
        seen_dedup_keys: set[tuple[str, str]] = set()

        for exp in records:
            if exp.space_id != space_id:
                continue
            if not exp.experience_id or not exp.experience_id.strip():
                continue
            if exp.experience_id in seen_exp_ids:
                continue

            hint = self._build_hint_from_record(
                exp=exp,
                relevance_score=1.0,
                exact_fingerprint_match=False,
                target_task=target_task,
            )
            if hint is None:
                continue

            dedup_key = (
                hint.failed_capability or hint.suggested_alternative_capability,
                hint.counterfactual_summary,
            )
            if dedup_key in seen_dedup_keys:
                continue

            seen_dedup_keys.add(dedup_key)
            seen_exp_ids.add(exp.experience_id)

            ranked_hint = ExperienceHint(
                experience_id=hint.experience_id,
                failed_capability=hint.failed_capability,
                suggested_avoidance=hint.suggested_avoidance,
                outcome_summary=hint.outcome_summary,
                counterfactual_summary=hint.counterfactual_summary,
                relevance_score=hint.relevance_score,
                source_space_id=hint.source_space_id,
                suggested_alternative_capability=hint.suggested_alternative_capability,
                target_task_id=hint.target_task_id,
                provenance_ref=hint.provenance_ref,
                rank=len(hints) + 1,
                exact_fingerprint_match=hint.exact_fingerprint_match,
            )
            hints.append(ranked_hint)
            if len(hints) >= clamped_limit:
                break

        return hints

    def _build_hint_from_record(
        self,
        exp: ExperienceRecord,
        relevance_score: float,
        exact_fingerprint_match: bool,
        target_task: str,
    ) -> ExperienceHint | None:
        if not exp.outcome or not str(exp.outcome).strip():
            return None

        outcome_lower = str(exp.outcome).lower()
        is_negative = (
            not (
                outcome_lower.startswith("success")
                or outcome_lower.startswith("ok")
                or outcome_lower.startswith("completed")
            )
            or any(
                term in outcome_lower
                for term in (
                    "fail",
                    "error",
                    "rejected",
                    "timeout",
                    "abort",
                    "rate limit",
                    "denied",
                    "exceeded",
                    "429",
                )
            )
        )

        action_dict = exp.action if isinstance(exp.action, dict) else {}
        context_dict = exp.applicable_context if isinstance(exp.applicable_context, dict) else {}

        if is_negative and exp.counterfactual and str(exp.counterfactual).strip():
            failed_cap = str(action_dict.get("capability", ""))
            avoid = (failed_cap,) if failed_cap else ()
            alternative = str(
                context_dict.get("suggested_alternative", "")
                or context_dict.get("alternative_capability", "")
            )
            return ExperienceHint(
                experience_id=exp.experience_id,
                failed_capability=failed_cap,
                suggested_avoidance=avoid,
                outcome_summary=str(exp.outcome)[:100],
                counterfactual_summary=str(exp.counterfactual)[:200],
                relevance_score=relevance_score,
                source_space_id=exp.space_id,
                suggested_alternative_capability=alternative,
                target_task_id=target_task,
                provenance_ref=exp.provenance_ref,
                rank=1,
                exact_fingerprint_match=exact_fingerprint_match,
            )
        elif not is_negative and action_dict.get("capability"):
            success_cap = str(action_dict.get("capability", ""))
            return ExperienceHint(
                experience_id=exp.experience_id,
                failed_capability="",
                suggested_avoidance=(),
                outcome_summary=str(exp.outcome)[:100],
                counterfactual_summary=(
                    str(exp.counterfactual)[:200]
                    if exp.counterfactual and str(exp.counterfactual).strip()
                    else "Validated working strategy"
                ),
                relevance_score=relevance_score,
                source_space_id=exp.space_id,
                suggested_alternative_capability=success_cap,
                target_task_id=target_task,
                provenance_ref=exp.provenance_ref,
                rank=1,
                exact_fingerprint_match=exact_fingerprint_match,
            )

        return None
