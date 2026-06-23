"""Case-level candidate merge for Day 1 step 5.3.

The retrieval adapters return chunk-level hits. This module collapses those
hits to one candidate per case_id while retaining the chunk evidence needed by
later rerank and summary stages.
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from typing import Any

from app.kernel.rag.retrieval.bm25_fallback import BM25_FALLBACK_SOURCE, BM25_RELAXED_RECALL_SOURCE
from app.kernel.rag.retrieval.models import CaseCandidate, VectorCandidate
from app.kernel.rag.retrieval.service import (
    CONTROLLED_BM25_SUPPLEMENT_SOURCE,
    ORIGINAL_VECTOR_SOURCE,
    RECALL_ONLY_VECTOR_SOURCE,
    VARIANT_VECTOR_SOURCE,
)

SOURCE_PRIORITY = {
    ORIGINAL_VECTOR_SOURCE: 0,
    VARIANT_VECTOR_SOURCE: 1,
    RECALL_ONLY_VECTOR_SOURCE: 2,
    BM25_FALLBACK_SOURCE: 3,
    BM25_RELAXED_RECALL_SOURCE: 4,
    CONTROLLED_BM25_SUPPLEMENT_SOURCE: 5,
}


def merge_case_candidates(candidates: Iterable[VectorCandidate]) -> list[CaseCandidate]:
    """Deduplicate chunk-level candidates by case_id."""

    grouped: defaultdict[str, list[VectorCandidate]] = defaultdict(list)
    for candidate in candidates:
        if candidate.case_id:
            grouped[candidate.case_id].append(candidate)

    merged = [_merge_one_case(case_id, items) for case_id, items in grouped.items()]
    return sorted(
        merged,
        key=lambda item: (
            item.retrieval_score,
            -_best_source_priority(item.retrieval_source),
            item.top_chunk_score,
        ),
        reverse=True,
    )


def _merge_one_case(case_id: str, candidates: list[VectorCandidate]) -> CaseCandidate:
    ranked = sorted(
        candidates,
        key=lambda item: (
            _chunk_score(item),
            -_source_priority(item.retrieval_source),
            item.vector_score,
        ),
        reverse=True,
    )
    top = ranked[0]
    source_chunk_ids = _ordered_chunk_ids(ranked)
    retrieval_sources = sorted(
        {item.retrieval_source for item in ranked if item.retrieval_source},
        key=lambda source: (SOURCE_PRIORITY.get(source, 99), source),
    )
    vector_scores = [
        item.vector_score
        for item in ranked
        if item.vector_score is not None and not _is_fallback_source(item.retrieval_source)
    ]
    fallback_scores = [
        item.vector_score
        for item in ranked
        if item.vector_score is not None and _is_fallback_source(item.retrieval_source)
    ]
    recall_stages = _ordered_recall_stages(ranked)
    matched_by_vector = any(item.matched_by_vector for item in ranked)
    matched_by_bm25 = any(item.matched_by_bm25 for item in ranked)
    matched_by_rewrite = any(item.matched_by_rewrite for item in ranked)
    return CaseCandidate(
        case_id=case_id,
        top_chunk_id=top.chunk_id,
        source_chunk_ids=source_chunk_ids,
        hit_chunk_ids=list(source_chunk_ids),
        retrieval_source=retrieval_sources,
        metadata=_merged_metadata(ranked),
        matched_text=top.matched_text,
        source=top.source,
        vector_score=max(vector_scores) if vector_scores else None,
        fallback_score=max(fallback_scores) if fallback_scores else None,
        top_chunk_score=_chunk_score(top),
        retrieval_score=max(_chunk_score(item) for item in ranked),
        soft_filter_score=max((item.soft_filter_score for item in ranked), default=0.0),
        soft_filter_breakdown=_merged_soft_filter_breakdown(ranked),
        distance=top.distance,
        candidate_source=_candidate_source_label(
            matched_by_vector=matched_by_vector,
            matched_by_bm25=matched_by_bm25,
            matched_by_rewrite=matched_by_rewrite,
        ),
        recall_stage=recall_stages,
        matched_by_vector=matched_by_vector,
        matched_by_bm25=matched_by_bm25,
        matched_by_rewrite=matched_by_rewrite,
        filtered_reason=top.filtered_reason,
        dedup_reason=_dedup_reason(ranked),
    )


def _ordered_chunk_ids(candidates: list[VectorCandidate]) -> list[str]:
    best_score_by_chunk: dict[str, float] = {}
    first_seen: dict[str, int] = {}
    for index, candidate in enumerate(candidates):
        if not candidate.chunk_id:
            continue
        first_seen.setdefault(candidate.chunk_id, index)
        best_score_by_chunk[candidate.chunk_id] = max(
            best_score_by_chunk.get(candidate.chunk_id, 0.0),
            _chunk_score(candidate),
        )
    return sorted(
        best_score_by_chunk,
        key=lambda chunk_id: (best_score_by_chunk[chunk_id], -first_seen[chunk_id]),
        reverse=True,
    )


def _merged_metadata(candidates: list[VectorCandidate]) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    for candidate in candidates:
        for key, value in candidate.metadata.items():
            if key not in metadata or _is_empty(metadata[key]):
                metadata[key] = value
    return metadata


def _merged_soft_filter_breakdown(candidates: list[VectorCandidate]) -> dict[str, float]:
    breakdown: dict[str, float] = {}
    for candidate in candidates:
        for key, value in candidate.soft_filter_breakdown.items():
            breakdown[key] = max(breakdown.get(key, 0.0), float(value))
    return breakdown


def _ordered_recall_stages(candidates: list[VectorCandidate]) -> list[str]:
    stages: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        stage = str(candidate.recall_stage or "").strip()
        if stage and stage not in seen:
            stages.append(stage)
            seen.add(stage)
    return stages


def _candidate_source_label(
    *,
    matched_by_vector: bool,
    matched_by_bm25: bool,
    matched_by_rewrite: bool,
) -> str:
    parts: list[str] = []
    if matched_by_vector:
        parts.append("vector")
    if matched_by_bm25:
        parts.append("bm25")
    if matched_by_rewrite:
        parts.append("rewrite")
    return "+".join(parts) if parts else "unknown"


def _dedup_reason(candidates: list[VectorCandidate]) -> str | None:
    if len(candidates) <= 1:
        return None
    top = candidates[0]
    return (
        f"case_id_dedup_kept_top_chunk:{top.chunk_id}"
        f":source={top.retrieval_source}"
    )


def _chunk_score(candidate: VectorCandidate) -> float:
    return float(candidate.retrieval_score or candidate.vector_score or 0.0)


def _source_priority(source: str) -> int:
    return SOURCE_PRIORITY.get(source, 99)


def _best_source_priority(sources: list[str]) -> int:
    if not sources:
        return 99
    return min(_source_priority(source) for source in sources)


def _is_fallback_source(source: str) -> bool:
    return source.startswith(BM25_FALLBACK_SOURCE)


def _is_empty(value: Any) -> bool:
    return value is None or value == "" or value == []
