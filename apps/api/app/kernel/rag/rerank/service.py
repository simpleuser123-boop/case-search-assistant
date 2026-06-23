"""Explainable fact-similarity rerank for Day 1 step 5.4."""
from __future__ import annotations

import math
import re
from collections.abc import Iterable
from typing import Any

from app.core.config import Settings, settings
from app.kernel.rag.query_processing.models import QueryPlan
from app.kernel.rag.rerank.models import RankedCaseCandidate, RerankWeights
from app.kernel.rag.retrieval.models import CaseCandidate

DEFAULT_RERANK_WEIGHTS = RerankWeights()
WEIGHT_SUM_TOLERANCE = 1e-6
FACT_SIGNAL_EPSILON = 1e-6
CASE_CAUSE_LOW_VECTOR_FLOOR = 0.70
CASE_CAUSE_ONLY_CAP = 0.25
NO_FACT_GUARD_STRONG_VECTOR_FLOOR = 0.75
NO_FACT_GUARD_MULTI_SOURCE_VECTOR_FLOOR = 0.64
NO_FACT_GUARD_VECTOR_BUCKET_SIZE = 0.10
NO_FACT_GUARD_MULTI_SOURCE_BUCKET_BONUS = 0.01
NO_FACT_GUARD_WEAK_TIE_BREAK_WEIGHT = 0.002

KEY_PARAGRAPH_TYPES = {
    "court_found",
    "court_opinion",
    "本院查明",
    "经审理查明",
    "本院认为",
}
FORBIDDEN_RUNTIME_TEXT_METADATA_KEYS = {
    "case_id",
    "caseid",
    "chunk_id",
    "source_chunk_id",
    "query_id",
    "eval_query_id",
    "qrel",
    "qrels",
    "label",
    "labels",
    "relevance",
    "relevance_label",
}


class FactSimilarityReranker:
    """Rerank merged case candidates without calling an LLM per candidate."""

    def __init__(
        self,
        *,
        config: Settings = settings,
        weights: RerankWeights | None = None,
        enabled: bool | None = None,
    ) -> None:
        self.config = config
        self._configured_weights = weights
        self._enabled = enabled

    def rerank(self, query_plan: QueryPlan, candidates: Iterable[CaseCandidate]) -> list[RankedCaseCandidate]:
        candidate_list = list(candidates)
        weights, weight_error = self._weights()
        enabled = self._enabled if self._enabled is not None else bool(self.config.ENABLE_WEIGHTED_RERANK)
        ranked = [
            self._score_candidate(
                query_plan=query_plan,
                candidate=candidate,
                weights=weights,
                enabled=enabled,
                weight_error=weight_error,
                input_rank=index,
            )
            for index, candidate in enumerate(candidate_list)
        ]
        return sorted(
            ranked,
            key=lambda item: (
                item.final_score,
                _base_retrieval_score(item.candidate),
                -int(item.score_breakdown["input_rank"]),
            ),
            reverse=True,
        )

    def _score_candidate(
        self,
        *,
        query_plan: QueryPlan,
        candidate: CaseCandidate,
        weights: RerankWeights,
        enabled: bool,
        weight_error: str | None,
        input_rank: int,
    ) -> RankedCaseCandidate:
        vector_similarity, similarity_source = _vector_similarity(candidate)
        legal_element_overlap = _legal_element_overlap(query_plan.legal_elements, candidate)
        legal_element_hit_count = _legal_element_hit_count(query_plan.legal_elements, candidate)
        case_cause_match = _case_cause_match(query_plan.case_cause_hint, candidate)
        key_paragraph_match = _key_paragraph_match(candidate)
        authority_signal = _authority_signal(candidate.metadata)
        effective_signals, fusion_guards = _effective_feature_signals(
            vector_similarity=vector_similarity,
            legal_element_overlap=legal_element_overlap,
            case_cause_match=case_cause_match,
            key_paragraph_match=key_paragraph_match,
            authority_signal=authority_signal,
        )

        raw_weighted_score = (
            weights.vector_similarity * vector_similarity
            + weights.legal_element_overlap * legal_element_overlap
            + weights.case_cause_match * case_cause_match
            + weights.key_paragraph_match * key_paragraph_match
            + weights.authority_signal * authority_signal
        )
        base_score = _base_retrieval_score(candidate)
        weighted_score = (
            weights.vector_similarity * vector_similarity
            + weights.legal_element_overlap * effective_signals["legal_element_overlap"]
            + weights.case_cause_match * effective_signals["case_cause_match"]
            + weights.key_paragraph_match * effective_signals["key_paragraph_match"]
            + weights.authority_signal * effective_signals["authority_signal"]
        )
        m1_2_fusion_guards = list(fusion_guards)
        m1_2_guarded_score = (
            base_score if _should_use_base_retrieval_guard(m1_2_fusion_guards) else weighted_score
        )
        m1_2_final_score_source = (
            "base_retrieval_guard"
            if _should_use_base_retrieval_guard(m1_2_fusion_guards)
            else "weighted_score"
        )
        final_score_source = "weighted_score"
        if enabled and _should_use_base_retrieval_guard(fusion_guards):
            relaxation_reasons = _no_fact_guard_relaxation_reasons(
                candidate=candidate,
                vector_similarity=vector_similarity,
                similarity_source=similarity_source,
            )
            if relaxation_reasons:
                source_consensus = _has_source_consensus(candidate)
                final_score = _guarded_vector_bucket_score(
                    vector_similarity=vector_similarity,
                    raw_weighted_score=raw_weighted_score,
                    source_consensus=source_consensus,
                )
                final_score_source = "guarded_vector_bucket"
                fusion_guards.extend(relaxation_reasons)
                fusion_guards.append("weak_signal_tiebreak_limited_to_vector_bucket")
            else:
                final_score = base_score
                final_score_source = "base_retrieval_guard"
        else:
            final_score = weighted_score if enabled else base_score
            if not enabled:
                final_score_source = "base_retrieval_disabled"

        score_breakdown: dict[str, Any] = {
            "vector_similarity": _round_score(vector_similarity),
            "legal_element_overlap": _round_score(legal_element_overlap),
            "legal_element_hit_count": legal_element_hit_count,
            "case_cause_match": _round_score(case_cause_match),
            "key_paragraph_match": _round_score(key_paragraph_match),
            "authority_signal": _round_score(authority_signal),
            "effective_legal_element_overlap": _round_score(effective_signals["legal_element_overlap"]),
            "effective_case_cause_match": _round_score(effective_signals["case_cause_match"]),
            "effective_key_paragraph_match": _round_score(effective_signals["key_paragraph_match"]),
            "effective_authority_signal": _round_score(effective_signals["authority_signal"]),
            "raw_weighted_score": _round_score(raw_weighted_score),
            "weighted_score": _round_score(weighted_score),
            "base_retrieval_score": _round_score(base_score),
            "m1_2_guarded_score": _round_score(m1_2_guarded_score),
            "m1_2_final_score_source": m1_2_final_score_source,
            "m1_2_fusion_guards": m1_2_fusion_guards,
            "top_chunk_score": _round_score(candidate.top_chunk_score),
            "vector_score": _round_optional(candidate.vector_score),
            "fallback_score": _round_optional(candidate.fallback_score),
            "soft_filter_score": _round_score(candidate.soft_filter_score),
            "soft_filter_breakdown": candidate.soft_filter_breakdown,
            "retrieval_source": candidate.retrieval_source,
            "similarity_source": similarity_source,
            "weights": weights.as_dict(),
            "weighted_rerank_enabled": enabled,
            "weight_config_valid": weight_error is None,
            "score_mode": "weighted_rerank" if enabled else "base_retrieval",
            "input_rank": input_rank,
            "final_score": _round_score(final_score),
            "final_score_source": final_score_source,
            "fusion_guards": fusion_guards,
        }
        if weight_error:
            score_breakdown["weight_config_error"] = weight_error

        return RankedCaseCandidate(
            candidate=candidate,
            final_score=_round_score(final_score),
            score_breakdown=score_breakdown,
        )

    def _weights(self) -> tuple[RerankWeights, str | None]:
        weights = self._configured_weights or RerankWeights(
            vector_similarity=float(self.config.RERANK_WEIGHT_VECTOR_SIMILARITY),
            legal_element_overlap=float(self.config.RERANK_WEIGHT_LEGAL_ELEMENT_OVERLAP),
            case_cause_match=float(self.config.RERANK_WEIGHT_CASE_CAUSE_MATCH),
            key_paragraph_match=float(self.config.RERANK_WEIGHT_KEY_PARAGRAPH_MATCH),
            authority_signal=float(self.config.RERANK_WEIGHT_AUTHORITY_SIGNAL),
        )
        error = _validate_weights(weights)
        if error:
            return DEFAULT_RERANK_WEIGHTS, error
        return weights, None


def _validate_weights(weights: RerankWeights) -> str | None:
    values = weights.as_dict()
    for name, value in values.items():
        if not math.isfinite(value):
            return f"invalid_weight:{name}:not_finite"
        if value < 0:
            return f"invalid_weight:{name}:negative"
    total = sum(values.values())
    if abs(total - 1.0) > WEIGHT_SUM_TOLERANCE:
        return f"invalid_weight_sum:{total:.6f}"
    return None


def _effective_feature_signals(
    *,
    vector_similarity: float,
    legal_element_overlap: float,
    case_cause_match: float,
    key_paragraph_match: float,
    authority_signal: float,
) -> tuple[dict[str, float], list[str]]:
    effective_case_cause_match = case_cause_match
    fusion_guards: list[str] = []

    has_legal_support = legal_element_overlap > FACT_SIGNAL_EPSILON
    if (
        case_cause_match > FACT_SIGNAL_EPSILON
        and not has_legal_support
        and vector_similarity < CASE_CAUSE_LOW_VECTOR_FLOOR
    ):
        effective_case_cause_match = min(case_cause_match, CASE_CAUSE_ONLY_CAP)
        fusion_guards.append("case_cause_low_fact_similarity_cap")

    has_fact_support = has_legal_support or effective_case_cause_match > FACT_SIGNAL_EPSILON
    effective_key_paragraph_match = key_paragraph_match
    effective_authority_signal = authority_signal
    if not has_fact_support:
        fusion_guards.append("no_fact_support_base_retrieval")
        if key_paragraph_match > FACT_SIGNAL_EPSILON:
            fusion_guards.append("key_paragraph_without_fact_support")
        if authority_signal > FACT_SIGNAL_EPSILON:
            fusion_guards.append("authority_without_fact_support")
        effective_key_paragraph_match = 0.0
        effective_authority_signal = 0.0

    return (
        {
            "legal_element_overlap": legal_element_overlap,
            "case_cause_match": effective_case_cause_match,
            "key_paragraph_match": effective_key_paragraph_match,
            "authority_signal": effective_authority_signal,
        },
        fusion_guards,
    )


def _should_use_base_retrieval_guard(fusion_guards: list[str]) -> bool:
    return "no_fact_support_base_retrieval" in fusion_guards


def _no_fact_guard_relaxation_reasons(
    *,
    candidate: CaseCandidate,
    vector_similarity: float,
    similarity_source: str,
) -> list[str]:
    if similarity_source != "vector":
        return []

    reasons: list[str] = []
    if vector_similarity >= NO_FACT_GUARD_STRONG_VECTOR_FLOOR:
        reasons.append("no_fact_guard_relaxed_strong_vector")
    if (
        _has_source_consensus(candidate)
        and vector_similarity >= NO_FACT_GUARD_MULTI_SOURCE_VECTOR_FLOOR
    ):
        reasons.append("no_fact_guard_relaxed_multi_source")
    return reasons


def _has_source_consensus(candidate: CaseCandidate) -> bool:
    sources = {str(source) for source in candidate.retrieval_source if str(source)}
    if len(sources) >= 2:
        return True
    return bool(
        candidate.matched_by_vector
        and (candidate.matched_by_rewrite or candidate.matched_by_bm25)
    )


def _guarded_vector_bucket_score(
    *,
    vector_similarity: float,
    raw_weighted_score: float,
    source_consensus: bool,
) -> float:
    source_bonus = NO_FACT_GUARD_MULTI_SOURCE_BUCKET_BONUS if source_consensus else 0.0
    bucket_ceiling = 1.0 - source_bonus - NO_FACT_GUARD_WEAK_TIE_BREAK_WEIGHT
    bucket = min(_nearest_score_bucket(vector_similarity), bucket_ceiling)
    weak_tie_break = _clamp01(raw_weighted_score) * NO_FACT_GUARD_WEAK_TIE_BREAK_WEIGHT
    return _clamp01(bucket + source_bonus + weak_tie_break)


def _nearest_score_bucket(value: float) -> float:
    clamped = _clamp01(value)
    return _clamp01(
        math.floor((clamped / NO_FACT_GUARD_VECTOR_BUCKET_SIZE) + 0.5)
        * NO_FACT_GUARD_VECTOR_BUCKET_SIZE
    )


def _vector_similarity(candidate: CaseCandidate) -> tuple[float, str]:
    if candidate.vector_score is not None:
        return _clamp01(candidate.vector_score), "vector"
    if candidate.fallback_score is not None:
        return _clamp01(candidate.fallback_score), "fallback"
    return _base_retrieval_score(candidate), "base_retrieval"


def _base_retrieval_score(candidate: CaseCandidate) -> float:
    if candidate.retrieval_score is not None:
        return _clamp01(candidate.retrieval_score)
    if candidate.vector_score is not None:
        return _clamp01(candidate.vector_score)
    if candidate.fallback_score is not None:
        return _clamp01(candidate.fallback_score)
    return 0.0


def _legal_element_overlap(legal_elements: list[str], candidate: CaseCandidate) -> float:
    normalized_elements = [_compact_text(element) for element in legal_elements if _compact_text(element)]
    if not normalized_elements:
        return 0.0
    target_text = _compact_text(_candidate_fact_text(candidate))
    if not target_text:
        return 0.0

    scores = [_single_element_overlap(element, target_text) for element in normalized_elements]
    return _clamp01(sum(scores) / len(normalized_elements))


def _legal_element_hit_count(legal_elements: list[str], candidate: CaseCandidate) -> int:
    normalized_elements = [_compact_text(element) for element in legal_elements if _compact_text(element)]
    if not normalized_elements:
        return 0
    target_text = _compact_text(_candidate_fact_text(candidate))
    if not target_text:
        return 0
    return sum(
        1
        for element in normalized_elements
        if _single_element_overlap(element, target_text) > FACT_SIGNAL_EPSILON
    )


def _single_element_overlap(element: str, target_text: str) -> float:
    if element in target_text:
        return 1.0
    tokens = _tokens(element)
    if not tokens:
        return 0.0
    target_tokens = set(_tokens(target_text))
    if not target_tokens:
        return 0.0
    return len([token for token in tokens if token in target_tokens]) / len(tokens)


def _case_cause_match(case_cause_hint: str, candidate: CaseCandidate) -> float:
    hint = _compact_text(case_cause_hint)
    if not hint:
        return 0.0
    candidate_cause = _compact_text(
        " ".join(
            _metadata_text(candidate.metadata.get(key))
            for key in ("case_cause", "crime_type", "title")
        )
    )
    if not candidate_cause:
        return 0.0
    if hint in candidate_cause or candidate_cause in hint:
        return 1.0
    hint_tokens = set(_tokens(hint))
    cause_tokens = set(_tokens(candidate_cause))
    if not hint_tokens or not cause_tokens:
        return 0.0
    return _clamp01(len(hint_tokens & cause_tokens) / len(hint_tokens))


def _key_paragraph_match(candidate: CaseCandidate) -> float:
    metadata = candidate.metadata
    paragraph_type = _compact_text(
        _metadata_text(
            metadata.get("paragraph_type")
            or metadata.get("chunk_type")
            or metadata.get("chunk_type_cn")
            or metadata.get("section")
        )
    )
    if paragraph_type in {_compact_text(value) for value in KEY_PARAGRAPH_TYPES}:
        return 1.0
    text = (candidate.matched_text or "").strip()
    if text.startswith(("本院查明", "经审理查明", "本院认为")):
        return 1.0
    return 0.0


def _authority_signal(metadata: dict[str, Any]) -> float:
    court_score = _court_level_score(metadata)
    trial_score = _trial_level_score(metadata)
    date_score = _judgment_date_score(metadata)
    return _clamp01(0.5 * court_score + 0.25 * trial_score + 0.25 * date_score)


def _court_level_score(metadata: dict[str, Any]) -> float:
    court_level = _compact_text(
        _metadata_text(metadata.get("court_level"))
        or _metadata_text(metadata.get("court"))
    )
    if not court_level:
        return 0.0
    if "最高" in court_level:
        return 1.0
    if "高级" in court_level or "高院" in court_level:
        return 0.8
    if "中级" in court_level or "中院" in court_level:
        return 0.55
    if "基层" in court_level or "人民法院" in court_level or "法院" in court_level:
        return 0.3
    return 0.0


def _trial_level_score(metadata: dict[str, Any]) -> float:
    trial_level = _compact_text(_metadata_text(metadata.get("trial_level")))
    if not trial_level:
        return 0.0
    if "再审" in trial_level:
        return 0.7
    if "二审" in trial_level or "终审" in trial_level:
        return 0.6
    if "一审" in trial_level:
        return 0.35
    return 0.0


def _judgment_date_score(metadata: dict[str, Any]) -> float:
    year = _judgment_year(metadata)
    if year is None:
        return 0.0
    if year >= 2020:
        return 1.0
    if year >= 2015:
        return 0.75
    if year >= 2010:
        return 0.5
    if year >= 2000:
        return 0.25
    return 0.0


def _judgment_year(metadata: dict[str, Any]) -> int | None:
    raw = metadata.get("judgment_year") or metadata.get("year") or metadata.get("judgment_date")
    if raw is None:
        return None
    if isinstance(raw, int):
        return raw if 1900 <= raw <= 2099 else None
    match = re.search(r"(?:19|20)\d{2}", str(raw))
    if not match:
        return None
    year = int(match.group(0))
    return year if 1900 <= year <= 2099 else None


def _candidate_fact_text(candidate: CaseCandidate) -> str:
    metadata_text = " ".join(
        _metadata_text_without_forbidden(key, value)
        for key, value in candidate.metadata.items()
        if value is not None and _is_runtime_text_metadata_key_allowed(key)
    )
    return " ".join([candidate.matched_text or "", metadata_text])


def _metadata_text_without_forbidden(key: object, value: object) -> str:
    if not _is_runtime_text_metadata_key_allowed(key):
        return ""
    if value is None:
        return ""
    if isinstance(value, dict):
        return " ".join(
            _metadata_text_without_forbidden(item_key, item_value)
            for item_key, item_value in value.items()
        )
    if isinstance(value, (list, tuple, set)):
        return " ".join(
            _metadata_text_without_forbidden("", item)
            for item in value
        )
    return str(value)


def _is_runtime_text_metadata_key_allowed(key: object) -> bool:
    normalized = _compact_text(str(key)).lower()
    return normalized not in FORBIDDEN_RUNTIME_TEXT_METADATA_KEYS


def _metadata_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, dict):
        return " ".join(_metadata_text(item) for item in value.values())
    if isinstance(value, (list, tuple, set)):
        return " ".join(_metadata_text(item) for item in value)
    return str(value)


def _tokens(text: str) -> list[str]:
    text = text or ""
    tokens = [match.group(0).lower() for match in re.finditer(r"[a-zA-Z0-9]+", text)]
    cjk = re.findall(r"[\u4e00-\u9fff]", text)
    tokens.extend("".join(cjk[index:index + 2]) for index in range(max(0, len(cjk) - 1)))
    if len(cjk) == 1:
        tokens.append(cjk[0])
    return tokens


def _compact_text(value: str) -> str:
    return re.sub(r"[\s,，、。；;：:（）()《》<>\"'“”‘’\[\]【】]+", "", value or "").strip()


def _clamp01(value: float | int | None) -> float:
    if value is None:
        return 0.0
    numeric = float(value)
    if not math.isfinite(numeric):
        return 0.0
    return max(0.0, min(1.0, numeric))


def _round_score(value: float | int | None) -> float:
    return round(_clamp01(value), 6)


def _round_optional(value: float | int | None) -> float | None:
    return round(float(value), 6) if value is not None and math.isfinite(float(value)) else None
