"""Runtime confidence layering for search candidates.

This module is intentionally runtime-only. It does not accept qrels, labels,
relevance judgments, query ids, case ids, or historical evaluation artifacts.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.kernel.rag.rerank.models import RankedCaseCandidate

ConfidenceLevel = Literal["high", "medium", "low"]

MAIN_RESULT_TARGET = 5
LOW_CONFIDENCE_CANDIDATE_LIMIT = 5
LOW_CONFIDENCE_SCORE_CEILING = 0.65
MEDIUM_CONFIDENCE_SCORE_FLOOR = 0.65
HIGH_CONFIDENCE_SCORE_FLOOR = 0.78
HIGH_CONFIDENCE_LEGAL_HIT_FLOOR = 2

LOW_SCORE_BAND = "LOW_SCORE_BAND"
RELAXED_RECALL_SOURCE = "RELAXED_RECALL_SOURCE"
LOW_LEGAL_ELEMENT_HIT_COUNT = "LOW_LEGAL_ELEMENT_HIT_COUNT"
DEGRADED_SEARCH_PATH = "DEGRADED_SEARCH_PATH"
MAIN_RESULT_COUNT_BELOW_TARGET = "MAIN_RESULT_COUNT_BELOW_TARGET"

DEGRADED_REASON_PREFIXES = ("CHROMA_", "EMBEDDING_")
DEGRADED_REASON_CODES = {
    "BM25_FALLBACK_USED",
    "BM25_FALLBACK_FAILED",
}


@dataclass(frozen=True)
class ConfidenceProfile:
    confidence_level: ConfidenceLevel
    confidence_reasons: tuple[str, ...]
    score_band: str


@dataclass(frozen=True)
class LayeredRankedCandidate:
    ranked: RankedCaseCandidate
    confidence: ConfidenceProfile
    original_rank: int


@dataclass(frozen=True)
class ConfidenceSplit:
    results: list[LayeredRankedCandidate]
    low_confidence_candidates: list[LayeredRankedCandidate]


def split_low_confidence_candidates(
    ranked_candidates: list[RankedCaseCandidate],
    *,
    limit: int,
    degraded_reasons: list[str],
) -> ConfidenceSplit:
    """Split already-ranked candidates without changing their relative order."""

    primary: list[LayeredRankedCandidate] = []
    low_pool: list[LayeredRankedCandidate] = []
    for original_rank, ranked in enumerate(ranked_candidates, start=1):
        confidence = build_confidence_profile(
            ranked,
            degraded_reasons=degraded_reasons,
        )
        layered = LayeredRankedCandidate(
            ranked=ranked,
            confidence=confidence,
            original_rank=original_rank,
        )
        if confidence.confidence_level == "low":
            low_pool.append(layered)
        else:
            primary.append(layered)

    safe_limit = max(0, limit)
    visible_primary = primary[:safe_limit]
    if len(visible_primary) >= MAIN_RESULT_TARGET:
        return ConfidenceSplit(results=visible_primary, low_confidence_candidates=[])

    low_candidates = [
        _with_context_reason(layered, MAIN_RESULT_COUNT_BELOW_TARGET)
        for layered in low_pool[:LOW_CONFIDENCE_CANDIDATE_LIMIT]
    ]
    return ConfidenceSplit(
        results=visible_primary,
        low_confidence_candidates=low_candidates,
    )


def build_confidence_profile(
    ranked: RankedCaseCandidate,
    *,
    degraded_reasons: list[str],
) -> ConfidenceProfile:
    score = _score(ranked)
    legal_hit_count = _legal_element_hit_count(ranked)
    retrieval_sources = _retrieval_sources(ranked)
    reasons: list[str] = []

    if score < LOW_CONFIDENCE_SCORE_CEILING:
        reasons.append(LOW_SCORE_BAND)
    if _has_relaxed_recall_source(retrieval_sources):
        reasons.append(RELAXED_RECALL_SOURCE)
    if legal_hit_count <= 0 and score < MEDIUM_CONFIDENCE_SCORE_FLOOR:
        reasons.append(LOW_LEGAL_ELEMENT_HIT_COUNT)
    if _is_degraded(degraded_reasons) and _has_fallback_source(retrieval_sources) and score < MEDIUM_CONFIDENCE_SCORE_FLOOR:
        reasons.append(DEGRADED_SEARCH_PATH)

    if reasons:
        return ConfidenceProfile(
            confidence_level="low",
            confidence_reasons=tuple(_unique(reasons)),
            score_band=_score_band(score),
        )
    if score >= HIGH_CONFIDENCE_SCORE_FLOOR and legal_hit_count >= HIGH_CONFIDENCE_LEGAL_HIT_FLOOR:
        return ConfidenceProfile(
            confidence_level="high",
            confidence_reasons=(),
            score_band=_score_band(score),
        )
    return ConfidenceProfile(
        confidence_level="medium",
        confidence_reasons=(),
        score_band=_score_band(score),
    )


def _with_context_reason(
    layered: LayeredRankedCandidate,
    reason: str,
) -> LayeredRankedCandidate:
    reasons = list(layered.confidence.confidence_reasons)
    if reason not in reasons:
        reasons.append(reason)
    return LayeredRankedCandidate(
        ranked=layered.ranked,
        confidence=ConfidenceProfile(
            confidence_level=layered.confidence.confidence_level,
            confidence_reasons=tuple(reasons),
            score_band=layered.confidence.score_band,
        ),
        original_rank=layered.original_rank,
    )


def _score(ranked: RankedCaseCandidate) -> float:
    score = ranked.final_score
    if score is None:
        return 0.0
    return max(0.0, min(1.0, float(score)))


def _legal_element_hit_count(ranked: RankedCaseCandidate) -> int:
    value = ranked.score_breakdown.get("legal_element_hit_count", 0)
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return max(0, value)
    if isinstance(value, float):
        return max(0, int(value))
    return 0


def _retrieval_sources(ranked: RankedCaseCandidate) -> list[str]:
    value = ranked.score_breakdown.get("retrieval_source", ranked.candidate.retrieval_source)
    if isinstance(value, list):
        return [str(item) for item in value if str(item)]
    return [str(value)] if str(value) else []


def _has_relaxed_recall_source(sources: list[str]) -> bool:
    return any(source.endswith("relaxed_recall") for source in sources)


def _has_fallback_source(sources: list[str]) -> bool:
    return any(source.startswith("bm25_fallback") for source in sources)


def _is_degraded(reasons: list[str]) -> bool:
    return any(
        reason in DEGRADED_REASON_CODES or reason.startswith(DEGRADED_REASON_PREFIXES)
        for reason in reasons
    )


def _score_band(score: float) -> str:
    if score < LOW_CONFIDENCE_SCORE_CEILING:
        return "0.00-0.65"
    if score < HIGH_CONFIDENCE_SCORE_FLOOR:
        return "0.65-0.78"
    return "0.78-1.00"


def _unique(values: list[str]) -> list[str]:
    unique: list[str] = []
    for value in values:
        if value and value not in unique:
            unique.append(value)
    return unique
