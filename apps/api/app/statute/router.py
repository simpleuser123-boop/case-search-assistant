"""E5-4 statute 法条检索端点（flag-gated，无状态透传 + 检索 + 互跳）。

端点（均 prefix=/api/statute）：
- POST /search             查询/已脱敏 SearchProfile -> StatuteRef[]（带锚点，零正文）。
- POST /by-case            case_id -> 关联 StatuteRef[]（类案→法条互跳）。
- POST /cases-by-statute   statute_id -> 关联 CandidateRef[]（法条→类案互跳，白名单七字段 + 锚点）。

门控：ENABLE_STATUTE_SEARCH=false（默认）-> 403 STATUTE_SEARCH_DISABLED（安全降级，与
intake INTAKE_DISABLED 同款语义）；ENABLE_STATUTE_SEARCH=true 才走检索。前端入口本步不接。

鉴权 / 租户隔离与 /api/search、/api/intake/search 同款：检索端点为会话态
（query_session_id 中间件注入），ENABLE_STATUTE_SEARCH 作为访问闸；不新增任何检索端点之外的
对外端点。

红线：
- 请求体 schema extra=forbid：raw_case / raw_query / PII / 裁判正文 / 模型生成条文型键在
  pydantic 层即 422（第一道闸）；E4-2 防御层在 service 层 fail-closed（第二道闸）。
- StatuteRef 经内核 sanitize_statute_ref 收敛：条文只来自语料、带 text_id 锚点；缺锚点不返回。
- 日志只写 query_session_id / 计数 / degraded_reasons；绝不写 query_text / 原始案情 / 裁判正文 / 条文。
- 无状态：不持久化 SearchProfile / StatuteRef / CandidateRef、不写搜索历史、不落库。
"""
from __future__ import annotations

from fastapi import APIRouter, Request, status

from app.api.errors import api_error_response
from app.core.config import settings
from app.core.logging import logger
from app.kernel.guardrails import ContractViolationError, StatuteRef
from app.kernel.rag import (
    CandidateRef,
    StatuteCaseRefResult,
    StatuteSearchResult,
)
from app.schemas import ErrorResponse
from app.statute.schemas import (
    StatuteByCaseRequest,
    StatuteCandidateRefView,
    StatuteCasesByStatuteRequest,
    StatuteCasesResponse,
    StatuteRefView,
    StatuteRelatedCaseView,
    StatuteSearchRequest,
    StatuteSearchResponse,
    StatuteSourceAnchorView,
    StatuteAnchorView,
)
from app.statute.service import StatuteQueryService

router = APIRouter(prefix="/api/statute", tags=["statute"])

STATUTE_SEARCH_DISABLED_CODE = "STATUTE_SEARCH_DISABLED"
STATUTE_PROFILE_REJECTED_CODE = "STATUTE_PROFILE_REJECTED"

# SearchProfile 白名单五字段（仅这些从请求体进入 profile 载荷；mode/limit 是检索参数）。
_PROFILE_FIELDS = (
    "case_cause",
    "region",
    "trial_level_preference",
    "dispute_focus_keywords",
    "query_text",
)

# 模块级服务实例（懒构造，供测试 set_statute_query_service_for_test 替换）。
_statute_query_service: StatuteQueryService | None = None


def _get_service() -> StatuteQueryService:
    global _statute_query_service
    if _statute_query_service is None:
        _statute_query_service = StatuteQueryService()
    return _statute_query_service


def set_statute_query_service_for_test(service: StatuteQueryService | None) -> None:
    """测试注入钩子：替换 / 复位模块级 statute 检索服务实例。"""
    global _statute_query_service
    _statute_query_service = service


def _enabled() -> bool:
    return bool(getattr(settings, "ENABLE_STATUTE_SEARCH", False))


def _query_session_id(request: Request) -> str:
    return str(getattr(request.state, "query_session_id", "") or "")


def _disabled_response(request: Request):
    query_session_id = _query_session_id(request)
    logger.info(
        "statute_search_disabled query_session_id=%s feature_flag=ENABLE_STATUTE_SEARCH",
        query_session_id,
    )
    return api_error_response(
        status_code=status.HTTP_403_FORBIDDEN,
        code=STATUTE_SEARCH_DISABLED_CODE,
        message="法条检索未启用（ENABLE_STATUTE_SEARCH=false），当前为单产品末态。",
        query_session_id=query_session_id,
    )


# --- 内核契约对象 -> 响应视图（只搬运白名单字段与锚点，零正文）-------------------

def _anchor_to_view(anchor) -> StatuteAnchorView:
    return StatuteAnchorView(
        text_id=anchor.text_id,
        law_name=anchor.law_name,
        article_no=anchor.article_no,
        anchor_type=anchor.anchor_type,
    )


def _case_anchor_to_view(anchor) -> StatuteSourceAnchorView:
    return StatuteSourceAnchorView(
        case_id=anchor["case_id"] if isinstance(anchor, dict) else anchor.case_id,
        source_chunk_id=(
            anchor["source_chunk_id"]
            if isinstance(anchor, dict)
            else anchor.source_chunk_id
        ),
        anchor_type=(
            anchor.get("anchor_type")
            if isinstance(anchor, dict)
            else anchor.anchor_type
        ),
    )


def _related_case_to_view(ref) -> StatuteRelatedCaseView:
    return StatuteRelatedCaseView(
        case_id=ref.case_id,
        case_number=ref.case_number,
        court=ref.court,
        trial_level=ref.trial_level,
        case_cause=ref.case_cause,
        judgment_date=ref.judgment_date,
        source_anchors=[_case_anchor_to_view(a) for a in ref.source_anchors],
    )


def _statute_ref_to_view(ref: StatuteRef) -> StatuteRefView:
    return StatuteRefView(
        statute_id=ref.statute_id,
        law_name=ref.law_name,
        article_no=ref.article_no,
        statute_anchors=[_anchor_to_view(a) for a in ref.statute_anchors],
        article_text=ref.article_text,
        source_corpus=ref.source_corpus,
        effective_status=ref.effective_status,
        related_case_refs=[_related_case_to_view(r) for r in ref.related_case_refs],
    )


def _candidate_ref_to_view(ref: CandidateRef) -> StatuteCandidateRefView:
    return StatuteCandidateRefView(
        case_id=ref.case_id,
        case_number=ref.case_number,
        court=ref.court,
        trial_level=ref.trial_level,
        case_cause=ref.case_cause,
        judgment_date=ref.judgment_date,
        source_anchors=[_case_anchor_to_view(a) for a in ref.source_anchors],
    )


def _statute_result_to_response(
    *,
    result: StatuteSearchResult,
    query_session_id: str,
    mode: str,
) -> StatuteSearchResponse:
    views = [_statute_ref_to_view(ref) for ref in result.statute_refs]
    return StatuteSearchResponse(
        query_session_id=query_session_id,
        statute_refs=views,
        statute_count=len(views),
        degraded=bool(result.degraded),
        degraded_reasons=list(result.degraded_reasons),
        search_mode="expanded" if mode == "expanded" else "standard",
    )


def _cases_result_to_response(
    *,
    result: StatuteCaseRefResult,
    query_session_id: str,
    mode: str,
) -> StatuteCasesResponse:
    views = [_candidate_ref_to_view(ref) for ref in result.candidate_refs]
    return StatuteCasesResponse(
        query_session_id=query_session_id,
        candidate_refs=views,
        candidate_count=len(views),
        degraded=bool(result.degraded),
        degraded_reasons=list(result.degraded_reasons),
        search_mode="expanded" if mode == "expanded" else "standard",
    )


def _profile_rejected_response(request: Request, query_session_id: str):
    logger.warning(
        "statute_search_profile_rejected query_session_id=%s reason_code=%s",
        query_session_id,
        STATUTE_PROFILE_REJECTED_CODE,
    )
    return api_error_response(
        status_code=status.HTTP_400_BAD_REQUEST,
        code=STATUTE_PROFILE_REJECTED_CODE,
        message="法条检索入参不符合脱敏白名单契约（原始案情 / PII / 裁判正文零上送）。",
        query_session_id=query_session_id,
    )


# --- 端点 ----------------------------------------------------------------------

_RESPONSES = {
    status.HTTP_400_BAD_REQUEST: {"model": ErrorResponse},
    status.HTTP_403_FORBIDDEN: {"model": ErrorResponse},
    status.HTTP_422_UNPROCESSABLE_ENTITY: {"model": ErrorResponse},
    status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ErrorResponse},
}


@router.post("/search", response_model=StatuteSearchResponse, responses=_RESPONSES)
def statute_search(payload: StatuteSearchRequest, request: Request):
    """法条检索：已脱敏 SearchProfile -> StatuteRef[]（经内核法条检索服务）。"""
    if not _enabled():
        return _disabled_response(request)

    query_session_id = _query_session_id(request)
    profile_payload = {field: getattr(payload, field) for field in _PROFILE_FIELDS}

    try:
        result = _get_service().search_statutes(
            profile_payload,
            mode=payload.mode,
            limit=payload.limit,
            query_session_id=query_session_id,
        )
    except ContractViolationError:
        return _profile_rejected_response(request, query_session_id)

    logger.info(
        "statute_search_completed query_session_id=%s statute_count=%s degraded=%s degraded_reasons=%s",
        query_session_id,
        len(result.statute_refs),
        bool(result.degraded),
        list(result.degraded_reasons),
    )
    return _statute_result_to_response(
        result=result, query_session_id=query_session_id, mode=payload.mode
    )


@router.post("/by-case", response_model=StatuteSearchResponse, responses=_RESPONSES)
def statute_by_case(payload: StatuteByCaseRequest, request: Request):
    """类案→法条互跳：case_id -> 关联 StatuteRef[]（带锚点，零正文）。"""
    if not _enabled():
        return _disabled_response(request)

    query_session_id = _query_session_id(request)
    result = _get_service().statutes_by_case(
        payload.case_id,
        limit=payload.limit,
        query_session_id=query_session_id,
    )
    logger.info(
        "statute_by_case_completed query_session_id=%s statute_count=%s degraded=%s degraded_reasons=%s",
        query_session_id,
        len(result.statute_refs),
        bool(result.degraded),
        list(result.degraded_reasons),
    )
    return _statute_result_to_response(
        result=result, query_session_id=query_session_id, mode=payload.mode
    )


@router.post(
    "/cases-by-statute", response_model=StatuteCasesResponse, responses=_RESPONSES
)
def statute_cases_by_statute(payload: StatuteCasesByStatuteRequest, request: Request):
    """法条→类案互跳：statute_id -> 关联 CandidateRef[]（白名单七字段 + 锚点，零正文）。"""
    if not _enabled():
        return _disabled_response(request)

    query_session_id = _query_session_id(request)
    result = _get_service().cases_by_statute(
        payload.statute_id,
        limit=payload.limit,
        query_session_id=query_session_id,
    )
    logger.info(
        "statute_cases_by_statute_completed query_session_id=%s candidate_count=%s degraded=%s degraded_reasons=%s",
        query_session_id,
        len(result.candidate_refs),
        bool(result.degraded),
        list(result.degraded_reasons),
    )
    return _cases_result_to_response(
        result=result, query_session_id=query_session_id, mode=payload.mode
    )


__all__ = [
    "router",
    "set_statute_query_service_for_test",
    "STATUTE_SEARCH_DISABLED_CODE",
    "STATUTE_PROFILE_REJECTED_CODE",
]
