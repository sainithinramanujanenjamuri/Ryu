"""Unit tests for EvaluationModule and FrozenTraceCorpus.

spec §7 (Adaptation Layer), MEM-004 — Phase 10
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from core.space.memory_protocol import ExperienceRecord
from memory.evaluation import (
    EvaluationModule,
    FrozenTrace,
    FrozenTraceCorpus,
)


def _sample_corpus() -> FrozenTraceCorpus:
    t1 = FrozenTrace(
        trace_id="trace-001",
        capability="net.http",
        situation_description="Download document from server",
        expected_outcome="200 OK with document body",
        actual_outcome="404 Not Found",
        succeeded=False,
    )
    t2 = FrozenTrace(
        trace_id="trace-002",
        capability="fs.read",
        situation_description="Read local configuration",
        expected_outcome="Configuration parsed",
        actual_outcome="Success",
        succeeded=True,
    )
    return FrozenTraceCorpus(
        corpus_id="corpus-unit-test",
        version="1.0.0",
        traces=(t1, t2),
    )


def test_frozen_trace_corpus_from_dict() -> None:
    data = {
        "corpus_id": "c-1",
        "version": "2.0",
        "traces": [
            {
                "trace_id": "tr-1",
                "capability": "general.compute",
                "situation_description": "Compute squares",
                "expected_outcome": "List of squares",
                "actual_outcome": "Computed",
                "succeeded": True,
                "recorded_at": "2026-09-23T10:00:00+00:00",
            }
        ],
    }
    corpus = FrozenTraceCorpus.from_dict(data)
    assert corpus.corpus_id == "c-1"
    assert len(corpus.traces) == 1
    assert corpus.traces[0].capability == "general.compute"


def test_evaluation_module_empty_corpus_raises_value_error() -> None:
    empty_corpus = FrozenTraceCorpus(corpus_id="empty", version="1", traces=())
    rec = ExperienceRecord(
        experience_id="exp-1",
        space_id="space-1",
        situation={},
        action={"capability": "net.http"},
        outcome="404",
        counterfactual="Use fallback",
        applicable_context={},
        stored_at=datetime.now(timezone.utc),
    )
    with pytest.raises(ValueError, match="at least one trace"):
        EvaluationModule.evaluate(rec, empty_corpus)


def test_evaluation_module_scores_deterministically() -> None:
    corpus = _sample_corpus()
    rec = ExperienceRecord(
        experience_id="exp-2",
        space_id="space-1",
        situation={"task": "download"},
        action={"capability": "net.http"},
        outcome="500 Server Error",
        counterfactual="Try mirror url",
        applicable_context={},
        stored_at=datetime.now(timezone.utc),
    )

    res1 = EvaluationModule.evaluate(rec, corpus)
    res2 = EvaluationModule.evaluate(rec, corpus)

    # 1 out of 2 traces matches net.http
    assert res1.matched_traces == 1
    assert res1.corpus_size == 2
    assert res1.score == 0.5
    assert res1.score == res2.score
    assert len(res1.delta_description) > 0


def test_evaluation_module_unmatched_capability_scores_zero() -> None:
    corpus = _sample_corpus()
    rec = ExperienceRecord(
        experience_id="exp-3",
        space_id="space-1",
        situation={},
        action={"capability": "db.sql_query"},
        outcome="timeout",
        counterfactual="Add query index",
        applicable_context={},
        stored_at=datetime.now(timezone.utc),
    )
    res = EvaluationModule.evaluate(rec, corpus)
    assert res.matched_traces == 0
    assert res.score == 0.0

