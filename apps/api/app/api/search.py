"""Search API with Day 1 query processing and 5.3 recall candidates.

E3-3：本端点的检索主路径已切换为消费 E3 内部检索服务
（app.kernel.rag.InternalSearchService.execute）。查询处理 / 召回 / 排序 / 摘要展示准备
的编排是单一权威实现（在内核服务内），本模块只保留「内核富执行结果 -> SearchResponse」的
映射 helper（_candidate_to_result / _build_coverage / risk_hints 等），不再复制一套检索主路径。

外部契约不变：SearchRequest(query, mode, limit) 入参、SearchResponse 响应结构、错误码、
降级行为、日志脱敏口径（只写 input_hash / query_session_id，绝不写 query_text）均与 E3-3 前一致。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request, status

from app.api.errors import api_error_response
from app.core.config import settings
from app.core.logging import logger
# E-2a：检索链路对内核 RAG 核心组的引用，统一经共享内核公开面消费
# （app.kernel.rag），不再深引 app.retrieval/.rerank/.query_processing/.summary 内部子模块。
# E3-3：主路径编排经 InternalSearchService 消费；本模块保留的内核符号仅供
# 「富执行结果 -> SearchResponse」映射 helper 使用（CaseCandidate/LayeredRankedCandidate/
# ResultPresentation 的类型标注 + build_risk_hints），不再直接编排检索链路。
from app.kernel.rag import (
    CaseCandidate,
    FactSimilarityReranker,
    InternalSearchExecutionResult,
    InternalSearchRequest,
    InternalSearchService,
    LayeredRankedCandidate,
    QueryProcessingService,
    ResultPresentation,
    SearchProfile,
    SummaryService,
    VectorRetrievalService,
    build_risk_hints,
)
from app.schemas import (
    DataCoverage,
    ErrorResponse,
    SearchExpandRequest,
    SearchRequest,
    SearchResponse,
    SearchResultItem,
    SourceAnchor,
)

router = APIRouter(prefix="/api", tags=["search"])
# 模块级服务实例：保留供测试 monkeypatch（既有回归集逐一替换这些符号）。
# E3-3：检索主路径不再直接调用这些实例，而是注入 InternalSearchService，由其编排；
# 注入在请求时进行（_build_internal_search_service），以便测试对模块级实例的替换生效。
query_processing_service = QueryProcessingService()
retrieval_service = VectorRetrievalService()
rerank_service = FactSimilarityReranker()
summary_service = SummaryService()


def _build_internal_search_service() -> InternalSearchService:
    """用当前模块级服务实例装配内部检索服务。

    经构造函数注入，确保既有回归集对 query_processing_service / retrieval_service /
    rerank_service / summary_service 的 monkeypatch 仍然生效（主路径单一权威实现在内核服务内）。
    """
    return InternalSearchService(
        query_processing_service=query_processing_service,
        retrieval_service=retrieval_service,
        rerank_service=rerank_service,
        summary_service=summary_service,
    )

ROLLBACK_FLAG_BEHAVIORS = {
    "ENABLE_QUERY_REWRITE": "original_query_direct_retrieval",
    "ENABLE_WEIGHTED_RERANK": "base_retrieval_score_order",
    "ENABLE_SUMMARY": "source_chunk_snippet",
    "ENABLE_EXPANDED_SEARCH": "expanded_search_entry_hidden_or_forbidden",
}

DATA_SOURCE_UNAVAILABLE = "DATA_SOURCE_UNAVAILABLE"
DATA_UNTIL_UNKNOWN = "DATA_UNTIL_UNKNOWN"
INDEX_VERSION_UNKNOWN = "INDEX_VERSION_UNKNOWN"


def _query_session_id(request: Request) -> str:
    return str(request.state.query_session_id)


@router.post(
    "/search",
    response_model=SearchResponse,
    responses={
        status.HTTP_400_BAD_REQUEST: {"model": ErrorResponse},
        status.HTTP_413_REQUEST_ENTITY_TOO_LARGE: {"model": ErrorResponse},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ErrorResponse},
        status.HTTP_422_UNPROCESSABLE_ENTITY: {"model": ErrorResponse},
    },
)
def search(payload: SearchRequest, request: Request):
    return _handle_search_request(
        payload=payload,
        request=request,
        endpoint="/api/search",
        include_relaxed_recall=False,
    )


@router.post(
    "/search/expand",
    response_model=SearchResponse,
    responses={
        status.HTTP_400_BAD_REQUEST: {"model": ErrorResponse},
        status.HTTP_403_FORBIDDEN: {"model": ErrorResponse},
        status.HTTP_413_REQUEST_ENTITY_TOO_LARGE: {"model": ErrorResponse},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ErrorResponse},
        status.HTTP_422_UNPROCESSABLE_ENTITY: {"model": ErrorResponse},
    },
)
def search_expand(payload: SearchExpandRequest, request: Request):
    query_session_id = _query_session_id(request)
    if not settings.ENABLE_EXPANDED_SEARCH:
        _log_rollback_event(
            endpoint="/api/search/expand",
            query_session_id=query_session_id,
            input_hash=None,
            active_flags=["ENABLE_EXPANDED_SEARCH"],
        )
        logger.info(
            "search_expand_disabled query_session_id=%s feature_flag=ENABLE_EXPANDED_SEARCH",
            query_session_id,
        )
        return api_error_response(
            status_code=status.HTTP_403_FORBIDDEN,
            code="EXPANDED_SEARCH_DISABLED",
            message="扩展检索当前未启用，请先使用主结果或修改案情后重新检索。",
            query_session_id=query_session_id,
        )

    return _handle_search_request(
        payload=payload,
        request=request,
        endpoint="/api/search/expand",
        include_relaxed_recall=True,
    )


def _handle_search_request(
    *,
    payload: SearchRequest | SearchExpandRequest,
    request: Request,
    endpoint: str,
    include_relaxed_recall: bool,
):
    """处理 /api/search 与 /api/search/expand 的检索请求。

    E3-3：核心检索执行已切到 InternalSearchService.execute()。本函数负责：
    1. 把 SearchRequest 转成内部 SearchProfile（query_text=payload.query），构造 InternalSearchRequest。
       注意：query 仅作为内部脱敏短查询透传给服务，不写入持久层或日志（日志仍只用 input_hash）。
    2. 还原既有的外部错误码 / 降级早退语义（400 校验失败 / 503 召回失败）。
    3. 把内核富执行结果映射为 SearchResponse（沿用既有单一权威 helper）。
    """
    query_session_id = _query_session_id(request)
    service = _build_internal_search_service()
    internal_request = InternalSearchRequest(
        profile=SearchProfile(query_text=payload.query),
        mode="expanded" if include_relaxed_recall else "standard",
        limit=payload.limit,
        include_relaxed_recall=include_relaxed_recall,
    )
    execution = service.execute(internal_request, query_session_id=query_session_id)

    # 查询校验失败：还原既有 400/413/422 错误码（状态码 / code / message 来自异常本身）。
    if execution.query_validation_error is not None:
        exc = execution.query_validation_error
        logger.warning(
            "search_query_rejected query_session_id=%s degraded_reasons=%s timings=%s",
            query_session_id,
            [exc.code],
            execution.timings.__dict__,
        )
        return api_error_response(
            status_code=exc.status_code,
            code=exc.code,
            message=exc.message,
            query_session_id=query_session_id,
        )

    # 校验通过后：暴露 query_plan 给请求态，并按既有口径记录回退 flag 事件（用 input_hash，不含正文）。
    query_plan = execution.query_plan
    request.state.query_plan = query_plan
    _log_search_rollback_flags(
        endpoint=endpoint,
        query_session_id=query_session_id,
        input_hash=query_plan.input_hash,
    )

    # 召回失败：还原既有 503 SEARCH_RETRIEVAL_FAILED（错误脱敏，仅 error_type 进日志）。
    if execution.retrieval_error_type is not None:
        logger.error(
            "search_retrieval_unhandled endpoint=%s query_session_id=%s input_hash=%s "
            "error_type=%s timings=%s",
            endpoint,
            query_session_id,
            query_plan.input_hash,
            execution.retrieval_error_type,
            execution.timings.__dict__,
        )
        return api_error_response(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="SEARCH_RETRIEVAL_FAILED",
            message="检索召回暂时不可用，请稍后重试。",
            query_session_id=query_session_id,
        )

    return _execution_to_response(
        execution=execution,
        query_session_id=query_session_id,
        endpoint=endpoint,
    )


def _execution_to_response(
    *,
    execution: InternalSearchExecutionResult,
    query_session_id: str,
    endpoint: str,
) -> SearchResponse:
    """把内核富执行结果映射为 SearchResponse（单一权威映射实现）。

    summary 异常已在服务层归一为 SUMMARY_LLM_UNAVAILABLE 降级（presentation 占位），
    本函数沿用既有 _candidate_to_result / _build_coverage / build_risk_hints helper，
    保证 SearchResponse 字段、排序、降级与锚点行为与 E3-3 前逐位一致。
    """
    presentation_by_rank = execution.presentation_by_rank
    results = [
        _candidate_to_result(layered, presentation_by_rank[layered.original_rank])
        for layered in execution.results
    ]
    low_confidence_candidates = [
        _candidate_to_result(layered, presentation_by_rank[layered.original_rank])
        for layered in execution.low_confidence_candidates
    ]
    degraded_reasons = execution.degraded_reasons
    degraded = execution.degraded
    risk_hints = build_risk_hints(
        results=results,
        low_confidence_candidates=low_confidence_candidates,
        degraded_reasons=degraded_reasons,
    )
    timings = execution.timings
    logger.info(
        "search_completed endpoint=%s query_session_id=%s input_hash=%s candidate_count=%s "
        "result_count=%s low_confidence_count=%s risk_hint_count=%s degraded=%s "
        "degraded_reasons=%s timings=%s",
        endpoint,
        query_session_id,
        execution.query_plan.input_hash,
        len(execution.case_candidates),
        len(results),
        len(low_confidence_candidates),
        len(risk_hints),
        degraded,
        degraded_reasons,
        timings.__dict__,
    )
    return SearchResponse(
        query_session_id=query_session_id,
        candidates=results,
        results=results,
        low_confidence_candidates=low_confidence_candidates,
        risk_hints=risk_hints,
        coverage=_build_coverage(
            case_candidates=execution.case_candidates,
            search_mode=execution.search_mode,
            degraded_reasons=degraded_reasons,
        ),
        degraded=degraded,
        degraded_reasons=degraded_reasons,
        retrieval_duration_ms=timings.retrieval_duration_ms,
        timings=timings,
    )


def _build_coverage(
    *,
    case_candidates: list[CaseCandidate],
    search_mode: str,
    degraded_reasons: list[str],
) -> DataCoverage:
    data_source = _coverage_data_source(case_candidates)
    data_until = _coverage_data_until(case_candidates)
    index_version = _coverage_index_version(case_candidates, degraded_reasons)
    coverage_reasons = list(degraded_reasons)
    if data_source == "unavailable":
        coverage_reasons.append(DATA_SOURCE_UNAVAILABLE)
    if data_until == "unknown":
        coverage_reasons.append(DATA_UNTIL_UNKNOWN)
    if index_version == "unknown":
        coverage_reasons.append(INDEX_VERSION_UNKNOWN)
    return DataCoverage(
        data_source=data_source,
        data_until=data_until,
        index_version=index_version,
        total_candidate_count=len(case_candidates),
        search_mode="expanded" if search_mode == "expanded" else "standard",
        degraded_reasons=_unique_reasons(coverage_reasons),
    )


def _coverage_data_source(case_candidates: list[CaseCandidate]) -> str:
    values = _unique_clean_values(
        _metadata_str(candidate.metadata, key)
        for candidate in case_candidates
        for key in ("source_name", "data_source")
    )
    if len(values) == 1:
        return values[0]
    return "unavailable"


def _coverage_data_until(case_candidates: list[CaseCandidate]) -> str:
    values = _unique_clean_values(
        _metadata_str(candidate.metadata, key)
        for candidate in case_candidates
        for key in ("data_until", "coverage_until", "source_data_until", "index_data_until")
    )
    if len(values) == 1:
        return values[0]
    return "unknown"


def _coverage_index_version(
    case_candidates: list[CaseCandidate],
    degraded_reasons: list[str],
) -> str:
    if not _uses_vector_index(case_candidates):
        return "unknown"
    if any(reason.startswith("CHROMA_") for reason in degraded_reasons):
        return "unknown"
    collection = settings.CHROMA_COLLECTION.strip()
    return collection or "unknown"


def _uses_vector_index(case_candidates: list[CaseCandidate]) -> bool:
    return any(
        any(not source.startswith("bm25_fallback") for source in candidate.retrieval_source)
        for candidate in case_candidates
    )


def _candidate_to_result(layered: LayeredRankedCandidate, presentation: ResultPresentation) -> SearchResultItem:
    ranked = layered.ranked
    candidate = ranked.candidate
    metadata = dict(candidate.metadata)
    retrieval_score = round(candidate.retrieval_score, 6)
    final_score = round(ranked.final_score, 6)
    score_breakdown: dict[str, Any] = dict(ranked.score_breakdown)
    source_chunk_ids = _source_chunk_ids(candidate)
    source_url = _metadata_str(metadata, "source_url")
    source_ref = _source_ref(metadata, candidate.source)
    chunk_type = _metadata_str(metadata, "chunk_type")
    source_anchors = [
        anchor
        for anchor in (
            _source_anchor(
                case_id=candidate.case_id,
                source_chunk_id=chunk_id,
                chunk_type=chunk_type if chunk_id == candidate.top_chunk_id else None,
                anchor_type="result",
                source_url=source_url,
                source_ref=source_ref,
            )
            for chunk_id in source_chunk_ids
        )
        if anchor is not None
    ]
    return SearchResultItem(
        case_id=candidate.case_id,
        chunk_id=candidate.top_chunk_id,
        top_chunk_id=candidate.top_chunk_id,
        source_chunk_ids=source_chunk_ids,
        source_anchors=source_anchors,
        hit_chunk_ids=candidate.hit_chunk_ids,
        retrieval_source=candidate.retrieval_source,
        candidate_source=candidate.candidate_source,
        recall_stage=candidate.recall_stage,
        matched_by_vector=candidate.matched_by_vector,
        matched_by_bm25=candidate.matched_by_bm25,
        matched_by_rewrite=candidate.matched_by_rewrite,
        filtered_reason=candidate.filtered_reason,
        dedup_reason=candidate.dedup_reason,
        vector_score=_round_optional(candidate.vector_score),
        fallback_score=_round_optional(candidate.fallback_score),
        retrieval_score=retrieval_score,
        final_score=final_score,
        score_breakdown=score_breakdown,
        title=_metadata_str(metadata, "title"),
        case_no=_metadata_str(metadata, "case_no"),
        court=_metadata_str(metadata, "court"),
        court_level=_metadata_str(metadata, "court_level"),
        trial_level=_metadata_str(metadata, "trial_level"),
        case_cause=_metadata_str(metadata, "case_cause") or _metadata_str(metadata, "crime_type"),
        judgment_date=_metadata_str(metadata, "judgment_date"),
        similarity_score=final_score,
        confidence=layered.confidence.confidence_level,
        confidence_level=layered.confidence.confidence_level,
        confidence_reasons=list(layered.confidence.confidence_reasons),
        confidence_score_band=layered.confidence.score_band,
        original_rank=layered.original_rank,
        summary=_summary_to_dict(
            presentation,
            chunk_type=chunk_type,
            source_url=source_url,
            source_ref=source_ref,
        ),
        highlights=_highlights_to_dicts(
            presentation,
            case_id=candidate.case_id,
            chunk_type=chunk_type,
            source_url=source_url,
            source_ref=source_ref,
        ),
        source_url=source_url,
        metadata=metadata,
        matched_text=candidate.matched_text,
    )


def _summary_to_dict(
    presentation: ResultPresentation,
    *,
    chunk_type: str | None,
    source_url: str | None,
    source_ref: str | None,
) -> dict[str, Any] | None:
    if presentation.summary is None:
        return None
    anchor = _source_anchor(
        case_id=presentation.summary.source_case_id,
        source_chunk_id=presentation.summary.source_chunk_id,
        chunk_type=chunk_type,
        anchor_type="summary",
        source_url=source_url,
        source_ref=source_ref,
    )
    if anchor is None:
        return None
    summary = {
        "text": presentation.summary.text,
        "source_chunk_id": presentation.summary.source_chunk_id,
        "source_case_id": presentation.summary.source_case_id,
        "source_anchors": [anchor],
        "method": presentation.summary.method,
    }
    if presentation.summary.degraded_reason:
        summary["degraded_reason"] = presentation.summary.degraded_reason
    return summary


def _highlights_to_dicts(
    presentation: ResultPresentation,
    *,
    case_id: str,
    chunk_type: str | None,
    source_url: str | None,
    source_ref: str | None,
) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    for highlight in presentation.highlights:
        anchor = _source_anchor(
            case_id=case_id,
            source_chunk_id=highlight.source_chunk_id,
            chunk_type=chunk_type,
            anchor_type="highlight",
            source_url=source_url,
            source_ref=source_ref,
        )
        if anchor is None:
            continue
        item: dict[str, Any] = {
            "text": highlight.text,
            "source_chunk_id": highlight.source_chunk_id,
            "source_anchors": [anchor],
        }
        if highlight.start_offset is not None:
            item["start_offset"] = highlight.start_offset
        if highlight.end_offset is not None:
            item["end_offset"] = highlight.end_offset
        if highlight.matched_terms:
            item["matched_terms"] = highlight.matched_terms
        if highlight.reason:
            item["reason"] = highlight.reason
        values.append(item)
    return values


def _source_anchor(
    *,
    case_id: str | None,
    source_chunk_id: str | None,
    chunk_type: str | None,
    anchor_type: str,
    source_url: str | None,
    source_ref: str | None,
) -> SourceAnchor | None:
    clean_case_id = str(case_id or "").strip()
    clean_chunk_id = str(source_chunk_id or "").strip()
    if not clean_case_id or not clean_chunk_id:
        return None
    return SourceAnchor(
        case_id=clean_case_id,
        source_chunk_id=clean_chunk_id,
        chunk_type=_clean_optional(chunk_type),
        anchor_type=anchor_type,
        source_url=_clean_optional(source_url),
        source_ref=_clean_optional(source_ref) or "local_case_store",
    )


def _source_chunk_ids(candidate: CaseCandidate) -> list[str]:
    values = [candidate.top_chunk_id, *candidate.source_chunk_ids, *candidate.hit_chunk_ids]
    unique: list[str] = []
    for value in values:
        chunk_id = str(value or "").strip()
        if chunk_id and chunk_id not in unique:
            unique.append(chunk_id)
    return unique


def _metadata_str(metadata: dict[str, Any], key: str) -> str | None:
    value = metadata.get(key)
    if value is None or value == "":
        return None
    return str(value)


def _source_ref(metadata: dict[str, Any], candidate_source: str | None) -> str | None:
    for key in ("source_name", "source", "data_source", "collection_name"):
        value = _metadata_str(metadata, key)
        if value:
            return value
    return _clean_optional(candidate_source)


def _clean_optional(value: str | None) -> str | None:
    clean = str(value or "").strip()
    return clean or None


def _confidence(candidate: CaseCandidate) -> str:
    if any(source.endswith("relaxed_recall") for source in candidate.retrieval_source):
        return "low"
    if candidate.retrieval_score >= 0.75:
        return "high"
    if candidate.retrieval_score >= 0.45:
        return "medium"
    return "low"


def _round_optional(value: float | None) -> float | None:
    return round(float(value), 6) if value is not None else None


def _unique_reasons(reasons: list[str]) -> list[str]:
    unique: list[str] = []
    for reason in reasons:
        if reason and reason not in unique:
            unique.append(reason)
    return unique


def _unique_clean_values(values) -> list[str]:
    unique: list[str] = []
    for value in values:
        clean = _clean_optional(value)
        if clean and clean not in unique:
            unique.append(clean)
    return unique


def _log_search_rollback_flags(*, endpoint: str, query_session_id: str, input_hash: str) -> None:
    active_flags: list[str] = []
    if not settings.ENABLE_QUERY_REWRITE:
        active_flags.append("ENABLE_QUERY_REWRITE")
    if not settings.ENABLE_WEIGHTED_RERANK:
        active_flags.append("ENABLE_WEIGHTED_RERANK")
    if not settings.ENABLE_SUMMARY:
        active_flags.append("ENABLE_SUMMARY")
    _log_rollback_event(
        endpoint=endpoint,
        query_session_id=query_session_id,
        input_hash=input_hash,
        active_flags=active_flags,
    )


def _log_rollback_event(
    *,
    endpoint: str,
    query_session_id: str,
    input_hash: str | None,
    active_flags: list[str],
) -> None:
    if not active_flags:
        return
    logger.info(
        "rollback_event endpoint=%s query_session_id=%s input_hash=%s active_flags=%s "
        "behaviors=%s rebuild_index_required=false",
        endpoint,
        query_session_id,
        input_hash or "",
        active_flags,
        [ROLLBACK_FLAG_BEHAVIORS[flag] for flag in active_flags],
    )
