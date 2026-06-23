from __future__ import annotations

import pytest

from app.retrieval.bm25_fallback import BM25_FALLBACK_SOURCE, BM25_RELAXED_RECALL_SOURCE
from app.retrieval.merge import merge_case_candidates
from app.retrieval.models import VectorCandidate
from app.retrieval.service import ORIGINAL_VECTOR_SOURCE, VARIANT_VECTOR_SOURCE


def _candidate(
    *,
    case_id: str,
    chunk_id: str,
    score: float,
    retrieval_source: str,
    matched_text: str = "命中片段",
    metadata: dict | None = None,
) -> VectorCandidate:
    return VectorCandidate(
        case_id=case_id,
        chunk_id=chunk_id,
        vector_score=score,
        retrieval_score=score,
        retrieval_source=retrieval_source,
        metadata=metadata or {"case_id": case_id, "chunk_id": chunk_id, "title": f"{case_id}标题"},
        matched_text=matched_text,
        source="unit-test",
    )


def test_merge_deduplicates_case_id_and_keeps_highest_chunk():
    merged = merge_case_candidates(
        [
            _candidate(
                case_id="case-1",
                chunk_id="case-1-low",
                score=0.32,
                retrieval_source=ORIGINAL_VECTOR_SOURCE,
                matched_text="低分片段",
            ),
            _candidate(
                case_id="case-1",
                chunk_id="case-1-high",
                score=0.88,
                retrieval_source=VARIANT_VECTOR_SOURCE,
                matched_text="高分代表片段",
            ),
            _candidate(
                case_id="case-2",
                chunk_id="case-2-c1",
                score=0.64,
                retrieval_source=ORIGINAL_VECTOR_SOURCE,
            ),
        ]
    )

    assert [item.case_id for item in merged] == ["case-1", "case-2"]
    case_1 = merged[0]
    assert case_1.top_chunk_id == "case-1-high"
    assert case_1.top_chunk_score == pytest.approx(0.88)
    assert case_1.retrieval_score == pytest.approx(0.88)
    assert case_1.matched_text == "高分代表片段"
    assert case_1.source_chunk_ids == ["case-1-high", "case-1-low"]
    assert case_1.hit_chunk_ids == ["case-1-high", "case-1-low"]


def test_merge_preserves_multiple_sources_and_vector_or_fallback_scores():
    merged = merge_case_candidates(
        [
            _candidate(
                case_id="case-1",
                chunk_id="case-1-vector",
                score=0.71,
                retrieval_source=ORIGINAL_VECTOR_SOURCE,
            ),
            _candidate(
                case_id="case-1",
                chunk_id="case-1-bm25",
                score=0.83,
                retrieval_source=BM25_FALLBACK_SOURCE,
                metadata={"case_id": "case-1", "chunk_id": "case-1-bm25", "court": "测试法院"},
            ),
            _candidate(
                case_id="case-1",
                chunk_id="case-1-relaxed",
                score=0.52,
                retrieval_source=BM25_RELAXED_RECALL_SOURCE,
            ),
        ]
    )

    assert len(merged) == 1
    candidate = merged[0]
    assert candidate.top_chunk_id == "case-1-bm25"
    assert candidate.retrieval_source == [
        ORIGINAL_VECTOR_SOURCE,
        BM25_FALLBACK_SOURCE,
        BM25_RELAXED_RECALL_SOURCE,
    ]
    assert candidate.vector_score == pytest.approx(0.71)
    assert candidate.fallback_score == pytest.approx(0.83)
    assert candidate.metadata["court"] == "测试法院"
    assert set(candidate.source_chunk_ids) == {"case-1-vector", "case-1-bm25", "case-1-relaxed"}


def test_merge_ignores_candidates_without_case_id():
    merged = merge_case_candidates(
        [
            _candidate(case_id="", chunk_id="missing-case", score=0.99, retrieval_source=ORIGINAL_VECTOR_SOURCE),
            _candidate(case_id="case-1", chunk_id="case-1-c1", score=0.5, retrieval_source=ORIGINAL_VECTOR_SOURCE),
        ]
    )

    assert [item.case_id for item in merged] == ["case-1"]
