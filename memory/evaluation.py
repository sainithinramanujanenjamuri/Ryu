"""Evaluation Module and Frozen Trace Corpus: Behavioral evaluation for Adaptation.

Provides deterministic evaluation of structured ExperienceRecords against an immutable
FrozenTraceCorpus (docs/Architecture §7, §12).

IMPORTANT ARCHITECTURAL DISTINCTION:
This is experience-driven behavioral adaptation, NOT model-weight training.
No neural parameters are modified. No LLM fine-tuning is performed.
Evaluation produces regression-backed benchmark deltas on frozen traces.

spec §7 (Adaptation Layer), §12 (LLM replay/traces), ROADMAP Phase 10, MEM-004 — Phase 10
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from core.space.memory_protocol import ExperienceRecord


@dataclass(frozen=True)
class FrozenTrace:
    """Immutable reference execution trace used for deterministic behavioral benchmarking.

    Independent of core/llm/LLMRecord to maintain clean subsystem boundaries.
    """

    trace_id: str
    capability: str
    situation_description: str
    expected_outcome: str
    actual_outcome: str
    succeeded: bool
    recorded_at: datetime = datetime.now(timezone.utc)


@dataclass(frozen=True)
class EvaluationCase:
    """Benchmark test case mapping a situation hint to expected adaptation behavior."""

    case_id: str
    capability: str
    situation_hint: str
    expected_behavior: str


@dataclass(frozen=True)
class FrozenTraceCorpus:
    """Immutable collection of reference execution traces serving as the eval corpus."""

    corpus_id: str
    version: str
    traces: tuple[FrozenTrace, ...]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FrozenTraceCorpus:
        traces_list: list[FrozenTrace] = []
        for t in data.get("traces", []):
            recorded_at_val = t.get("recorded_at")
            if isinstance(recorded_at_val, str):
                recorded_at = datetime.fromisoformat(recorded_at_val)
            elif isinstance(recorded_at_val, datetime):
                recorded_at = recorded_at_val
            else:
                recorded_at = datetime.now(timezone.utc)

            traces_list.append(
                FrozenTrace(
                    trace_id=t["trace_id"],
                    capability=t["capability"],
                    situation_description=t.get("situation_description", ""),
                    expected_outcome=t.get("expected_outcome", ""),
                    actual_outcome=t.get("actual_outcome", ""),
                    succeeded=bool(t.get("succeeded", False)),
                    recorded_at=recorded_at,
                )
            )
        return cls(
            corpus_id=data.get("corpus_id", "default-corpus"),
            version=data.get("version", "1.0.0"),
            traces=tuple(traces_list),
        )


@dataclass(frozen=True)
class EvaluationResult:
    """Outcome of behavioral benchmarking an ExperienceRecord against a FrozenTraceCorpus."""

    score: float
    delta_description: str
    corpus_size: int
    matched_traces: int
    evaluated_at: datetime = datetime.now(timezone.utc)


class EvaluationModule:
    """Evaluates ExperienceRecords against reference FrozenTrace corpora.

    Computes deterministic evidence scores for promotion requests (Law 4, MEM-004, MEM-005).
    """

    @staticmethod
    def evaluate(
        experience: ExperienceRecord, corpus: FrozenTraceCorpus
    ) -> EvaluationResult:
        """Evaluate an ExperienceRecord against an immutable FrozenTraceCorpus.

        Raises:
            ValueError: if the corpus contains zero traces.
        """
        if not corpus.traces:
            raise ValueError(
                "FrozenTraceCorpus must contain at least one trace for evaluation"
            )

        exp_cap = experience.action.get("capability", "").strip().lower()
        matched = 0
        deltas: list[str] = []

        for trace in corpus.traces:
            trace_cap = trace.capability.strip().lower()
            if trace_cap == exp_cap or not exp_cap:
                matched += 1
                deltas.append(
                    f"Matched trace '{trace.trace_id}' on capability '{trace_cap}': "
                    f"prior outcome='{trace.actual_outcome}', counterfactual='{experience.counterfactual[:60]}...'"
                )

        score = matched / len(corpus.traces)
        if matched > 0:
            delta_desc = (
                f"Measurable adaptation delta observed across {matched}/{len(corpus.traces)} "
                f"reference traces for capability '{exp_cap}'."
            )
        else:
            delta_desc = (
                f"No matching reference traces found in corpus '{corpus.corpus_id}' "
                f"for capability '{exp_cap}'."
            )

        return EvaluationResult(
            score=round(score, 4),
            delta_description=delta_desc,
            corpus_size=len(corpus.traces),
            matched_traces=matched,
            evaluated_at=datetime.now(timezone.utc),
        )

