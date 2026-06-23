"""Source-anchored risk hints for review-only display.

Risk hints are derived after ranking and low-confidence splitting. They do not
participate in retrieval, source selection, rerank, or result ordering.
"""
from __future__ import annotations

import re

from app.schemas import RiskHint, RiskType, SearchResultItem, SourceAnchor

MAX_RISK_HINTS = 5
MAX_ANCHORS_PER_HINT = 2

FORBIDDEN_RUNTIME_RISK_INPUT_KEYS = {
    "qrels",
    "label",
    "relevance",
    "query_id",
    "eval_query_id",
    "case_id",
    "candidate_case_id",
}

SAFE_REASON_CODE = re.compile(r"^[A-Z][A-Z0-9_]{1,63}$")

RISK_TYPES: tuple[RiskType, ...] = (
    "fact_difference",
    "key_element_missing",
    "low_confidence_candidate",
    "adverse_tendency_source",
    "degraded_or_uncertain",
)

REVIEW_NOTES: dict[RiskType, str] = {
    "fact_difference": "该来源片段与当前案情可能存在事实差异，建议律师结合原文复核。",
    "key_element_missing": "该候选命中的关键要素较少，供复核是否缺少必要事实。",
    "low_confidence_candidate": "该候选为低置信度结果，仅作为补充复核线索。",
    "adverse_tendency_source": "该来源片段可能包含不利倾向线索，建议结合原文复核。",
    "degraded_or_uncertain": "本次检索存在降级或不确定状态，建议结合来源片段复核。",
}

CONFIDENCE_REASON_TO_RISK: dict[str, tuple[RiskType, str]] = {
    "LOW_LEGAL_ELEMENT_HIT_COUNT": ("key_element_missing", "KEY_ELEMENT_MISSING_REVIEW"),
    "RELAXED_RECALL_SOURCE": ("fact_difference", "FACT_DIFFERENCE_REVIEW"),
    "DEGRADED_SEARCH_PATH": ("degraded_or_uncertain", "DEGRADED_SEARCH_REVIEW"),
    "LOW_SCORE_BAND": ("low_confidence_candidate", "LOW_CONFIDENCE_CANDIDATE_REVIEW"),
    "MAIN_RESULT_COUNT_BELOW_TARGET": (
        "low_confidence_candidate",
        "LOW_CONFIDENCE_CANDIDATE_REVIEW",
    ),
}


def build_risk_hints(
    *,
    results: list[SearchResultItem],
    low_confidence_candidates: list[SearchResultItem],
    degraded_reasons: list[str],
) -> list[RiskHint]:
    """Build review-only hints from already visible, source-anchored items."""

    hints: list[RiskHint] = []
    seen: set[tuple[str, str, str]] = set()
    visible_items = [*results, *low_confidence_candidates]

    for item in visible_items:
        metadata_hint = _metadata_risk_hint(item)
        if metadata_hint is not None:
            _append_unique(hints, metadata_hint, seen)
            if len(hints) >= MAX_RISK_HINTS:
                return hints

    for item in low_confidence_candidates:
        hint = _confidence_risk_hint(item)
        if hint is not None:
            _append_unique(hints, hint, seen)
            if len(hints) >= MAX_RISK_HINTS:
                return hints

    degraded_hint = _degraded_risk_hint(visible_items, degraded_reasons)
    if degraded_hint is not None:
        _append_unique(hints, degraded_hint, seen)

    return hints[:MAX_RISK_HINTS]


def _metadata_risk_hint(item: SearchResultItem) -> RiskHint | None:
    anchors = _valid_source_anchors(item.source_anchors)
    if not anchors:
        return None

    sanitized_metadata = {
        key: value
        for key, value in item.metadata.items()
        if key.lower() not in FORBIDDEN_RUNTIME_RISK_INPUT_KEYS
    }
    risk_type = _safe_risk_type(sanitized_metadata.get("risk_type"))
    if risk_type is None:
        return None

    reason_code = _safe_reason_code(sanitized_metadata.get("risk_reason_code"))
    if reason_code is None:
        reason_code = "SOURCE_RISK_SIGNAL_REVIEW"
    return _make_hint(
        risk_type=risk_type,
        anchors=anchors,
        confidence_level=_safe_confidence_level(sanitized_metadata.get("risk_confidence_level")),
        confidence_reasons=[reason_code],
        reason_code=reason_code,
    )


def _confidence_risk_hint(item: SearchResultItem) -> RiskHint | None:
    anchors = _valid_source_anchors(item.source_anchors)
    if not anchors:
        return None

    safe_reasons = _safe_reason_codes(item.confidence_reasons)
    for reason in safe_reasons:
        mapping = CONFIDENCE_REASON_TO_RISK.get(reason)
        if mapping is None:
            continue
        risk_type, reason_code = mapping
        return _make_hint(
            risk_type=risk_type,
            anchors=anchors,
            confidence_level=item.confidence_level or "low",
            confidence_reasons=safe_reasons,
            reason_code=reason_code,
        )

    if item.confidence_level == "low":
        return _make_hint(
            risk_type="low_confidence_candidate",
            anchors=anchors,
            confidence_level="low",
            confidence_reasons=safe_reasons or ["LOW_CONFIDENCE_CANDIDATE"],
            reason_code="LOW_CONFIDENCE_CANDIDATE_REVIEW",
        )

    return None


def _degraded_risk_hint(
    items: list[SearchResultItem],
    degraded_reasons: list[str],
) -> RiskHint | None:
    safe_reasons = _safe_reason_codes(degraded_reasons)
    if not safe_reasons:
        return None

    for item in items:
        anchors = _valid_source_anchors(item.source_anchors)
        if anchors:
            return _make_hint(
                risk_type="degraded_or_uncertain",
                anchors=anchors,
                confidence_level="low",
                confidence_reasons=safe_reasons,
                reason_code="DEGRADED_OR_UNCERTAIN_REVIEW",
            )

    return None


def _make_hint(
    *,
    risk_type: RiskType,
    anchors: list[SourceAnchor],
    confidence_level: str,
    confidence_reasons: list[str],
    reason_code: str,
) -> RiskHint:
    return RiskHint(
        risk_type=risk_type,
        source_anchors=anchors[:MAX_ANCHORS_PER_HINT],
        confidence_level=_safe_confidence_level(confidence_level),
        confidence_reasons=_safe_reason_codes(confidence_reasons),
        reason_code=reason_code,
        review_note=REVIEW_NOTES[risk_type],
    )


def _append_unique(
    hints: list[RiskHint],
    hint: RiskHint,
    seen: set[tuple[str, str, str]],
) -> None:
    first_anchor = hint.source_anchors[0]
    key = (hint.risk_type, first_anchor.source_chunk_id, hint.reason_code)
    if key in seen:
        return
    seen.add(key)
    hints.append(hint)


def _valid_source_anchors(anchors: list[SourceAnchor]) -> list[SourceAnchor]:
    valid: list[SourceAnchor] = []
    for anchor in anchors:
        if str(anchor.case_id or "").strip() and str(anchor.source_chunk_id or "").strip():
            valid.append(anchor)
    return valid


def _safe_reason_codes(values: list[str]) -> list[str]:
    safe: list[str] = []
    for value in values:
        code = _safe_reason_code(value)
        if code and code not in safe:
            safe.append(code)
    return safe


def _safe_reason_code(value: object) -> str | None:
    code = str(value or "").strip()
    if SAFE_REASON_CODE.fullmatch(code):
        return code
    return None


def _safe_risk_type(value: object) -> RiskType | None:
    risk_type = str(value or "").strip()
    if risk_type in RISK_TYPES:
        return risk_type  # type: ignore[return-value]
    return None


def _safe_confidence_level(value: object) -> str:
    level = str(value or "").strip()
    if level in {"high", "medium", "low"}:
        return level
    return "low"
