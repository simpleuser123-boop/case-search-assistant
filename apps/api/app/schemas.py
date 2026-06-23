"""Pydantic schemas for Day 1 API skeleton."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.core.config import settings
from app.core.timing import SearchTimings


class ErrorDetail(BaseModel):
    code: str
    message: str
    query_session_id: str | None = None


class ErrorResponse(BaseModel):
    error: ErrorDetail


class SearchRequest(BaseModel):
    query: str = Field(
        ...,
        json_schema_extra={"maxLength": settings.QUERY_MAX_LENGTH},
    )
    mode: Literal["standard"] = "standard"
    limit: int = Field(default=10, ge=1, le=50)


class SearchExpandRequest(BaseModel):
    query: str = Field(
        ...,
        json_schema_extra={"maxLength": settings.QUERY_MAX_LENGTH},
    )
    mode: Literal["expand"] = "expand"
    limit: int = Field(default=10, ge=1, le=50)


class SourceAnchor(BaseModel):
    case_id: str
    source_chunk_id: str
    chunk_type: str | None = None
    anchor_type: str
    source_url: str | None = None
    source_ref: str | None = None


class DataCoverage(BaseModel):
    data_source: str = "unavailable"
    data_until: str = "unknown"
    index_version: str = "unknown"
    total_candidate_count: int | None = None
    search_mode: Literal["standard", "expanded"] = "standard"
    degraded_reasons: list[str] = Field(default_factory=list)


RiskType = Literal[
    "fact_difference",
    "key_element_missing",
    "low_confidence_candidate",
    "adverse_tendency_source",
    "degraded_or_uncertain",
]


class RiskHint(BaseModel):
    risk_type: RiskType
    source_anchors: list[SourceAnchor] = Field(default_factory=list)
    confidence_level: Literal["high", "medium", "low"] = "low"
    confidence_reasons: list[str] = Field(default_factory=list)
    reason_code: str
    review_note: str | None = None


class HoldingSummaryItem(BaseModel):
    text: str
    source_anchors: list[SourceAnchor] = Field(default_factory=list)
    confidence: Literal["high", "medium", "low"] = "low"


class HoldingSummary(BaseModel):
    summary_items: list[HoldingSummaryItem] = Field(default_factory=list)
    source_anchors: list[SourceAnchor] = Field(default_factory=list)
    confidence: Literal["high", "medium", "low"] = "low"
    generation_status: Literal["generated", "degraded"] = "degraded"
    degrade_reason: str | None = None


ReadingNavigationCategory = Literal[
    "争议焦点",
    "裁判理由中的关键事实",
    "法院认定的关键要素",
    "与用户阅读相关的程序或证据节点",
]


class ReadingNavigationItem(BaseModel):
    label: str
    category: ReadingNavigationCategory
    source_anchors: list[SourceAnchor] = Field(default_factory=list)
    confidence: Literal["high", "medium", "low"] = "low"
    degrade_reason: str | None = None


class ReadingNavigationSection(BaseModel):
    items: list[ReadingNavigationItem] = Field(default_factory=list)
    source_anchors: list[SourceAnchor] = Field(default_factory=list)
    generation_status: Literal["generated", "degraded"] = "degraded"
    degrade_reason: str | None = None


class FactAlignmentRequest(BaseModel):
    """Request-scoped fact-alignment input.

    ``query_signal`` is the user's case description. It is abstracted to
    controlled dimension keys inside the request and never persisted.
    """

    query_signal: str = Field(
        default="",
        json_schema_extra={"maxLength": settings.QUERY_MAX_LENGTH},
    )

    model_config = ConfigDict(extra="forbid")


FactMatchType = Literal[
    "same_dimension",
    "similar_dimension",
    "difference_to_review",
]


class FactAlignmentItem(BaseModel):
    dimension: str
    dimension_key: str
    query_side_signal: Literal[
        "input_signals_dimension",
        "input_does_not_mention_dimension",
    ]
    case_side_facts: list[str] = Field(default_factory=list)
    source_anchors: list[SourceAnchor] = Field(default_factory=list)
    match_type: FactMatchType
    confidence: Literal["high", "medium", "low"] = "low"
    degrade_reason: str | None = None


class FactAlignmentResponse(BaseModel):
    query_session_id: str | None = None
    case_id: str
    items: list[FactAlignmentItem] = Field(default_factory=list)
    generation_status: Literal["generated", "degraded"] = "degraded"
    degrade_reason: str | None = None
    query_signal_present: bool = False
    timings: SearchTimings = Field(default_factory=SearchTimings)


class SearchResultItem(BaseModel):
    case_id: str
    chunk_id: str | None = None
    top_chunk_id: str | None = None
    source_chunk_ids: list[str] = Field(default_factory=list)
    source_anchors: list[SourceAnchor] = Field(default_factory=list)
    hit_chunk_ids: list[str] = Field(default_factory=list)
    retrieval_source: list[str] = Field(default_factory=list)
    candidate_source: str | None = None
    recall_stage: list[str] = Field(default_factory=list)
    matched_by_vector: bool = False
    matched_by_bm25: bool = False
    matched_by_rewrite: bool = False
    filtered_reason: str | None = None
    dedup_reason: str | None = None
    vector_score: float | None = None
    fallback_score: float | None = None
    retrieval_score: float | None = None
    final_score: float | None = None
    score_breakdown: dict[str, Any] = Field(default_factory=dict)
    title: str | None = None
    case_no: str | None = None
    court: str | None = None
    court_level: str | None = None
    trial_level: str | None = None
    case_cause: str | None = None
    judgment_date: str | None = None
    similarity_score: float | None = None
    confidence: str | None = None
    confidence_level: Literal["high", "medium", "low"] | None = None
    confidence_reasons: list[str] = Field(default_factory=list)
    confidence_score_band: str | None = None
    original_rank: int | None = None
    summary: dict[str, Any] | None = None
    highlights: list[dict[str, Any]] = Field(default_factory=list)
    source_url: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    matched_text: str | None = None


class SearchResponse(BaseModel):
    query_session_id: str
    candidates: list[SearchResultItem] = Field(default_factory=list)
    results: list[SearchResultItem] = Field(default_factory=list)
    low_confidence_candidates: list[SearchResultItem] = Field(default_factory=list)
    risk_hints: list[RiskHint] = Field(default_factory=list)
    coverage: DataCoverage = Field(default_factory=DataCoverage)
    degraded: bool = False
    degraded_reasons: list[str] = Field(default_factory=list)
    retrieval_duration_ms: int = 0
    timings: SearchTimings = Field(default_factory=SearchTimings)


class SimilarityHighlight(BaseModel):
    """M3-5 highlight anchor: locates a source chunk for a reading-assist module.

    Carries only metadata (ids/status/reason). Never any body text.
    """

    highlight_id: str
    case_id: str
    source_chunk_id: str
    anchor_type: str = "detail_chunk"
    related_module: Literal["holding_summary", "issue_focus", "key_elements"]
    display_status: Literal["available", "degraded"] = "available"
    degrade_reason: str | None = None


class CaseChunkResponse(BaseModel):
    chunk_id: str
    chunk_type: str
    source_anchors: list[SourceAnchor] = Field(default_factory=list)
    start_offset: int | None = None
    end_offset: int | None = None
    text: str | None = None


class CaseDetailResponse(BaseModel):
    query_session_id: str | None = None
    case_id: str
    case_no: str | None = None
    title: str | None = None
    court: str | None = None
    court_level: str | None = None
    trial_level: str | None = None
    case_cause: str | None = None
    judgment_date: str | None = None
    region: str | None = None
    source_url: str | None = None
    source_name: str | None = None
    holding_summary: HoldingSummary = Field(default_factory=HoldingSummary)
    issue_focus: ReadingNavigationSection = Field(default_factory=ReadingNavigationSection)
    key_elements: ReadingNavigationSection = Field(default_factory=ReadingNavigationSection)
    similarity_highlights: list[SimilarityHighlight] = Field(default_factory=list)
    chunks: list[CaseChunkResponse] = Field(default_factory=list)
    degraded: bool = False
    degraded_reasons: list[str] = Field(default_factory=list)
    timings: SearchTimings = Field(default_factory=SearchTimings)


class AnalyticsEventRequest(BaseModel):
    event_name: str = Field(..., min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_.:-]+$")
    query_session_id: str | None = Field(default=None, max_length=80)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")


class AnalyticsEventResponse(BaseModel):
    query_session_id: str | None = None
    accepted: bool
    degraded: bool = False
    degraded_reasons: list[str] = Field(default_factory=list)
    timings: SearchTimings = Field(default_factory=SearchTimings)


SAFE_HASH_PATTERN = r"^(sha256_[a-f0-9]{24}|fnv1a_[a-f0-9]{8})$"


class FeedbackEventRequest(BaseModel):
    event_type: Literal["result_feedback"]
    session_hash: str = Field(..., pattern=SAFE_HASH_PATTERN)
    query_hash: str = Field(..., pattern=SAFE_HASH_PATTERN)
    case_id_hash: str = Field(..., pattern=SAFE_HASH_PATTERN)
    rank: int = Field(..., ge=1, le=1000)
    feedback_value: Literal["relevant", "not_relevant", "cleared"]
    search_mode: Literal["standard", "expanded"]
    confidence_level: Literal["high", "medium", "low"]

    model_config = ConfigDict(extra="forbid")


class FeedbackEventResponse(BaseModel):
    accepted: bool
    stored: bool
    degraded: bool = False
    degraded_reasons: list[str] = Field(default_factory=list)
    feedback_value: Literal["relevant", "not_relevant", "cleared"] | None = None
    timings: SearchTimings = Field(default_factory=SearchTimings)
