"""E4-3 intake 检索端点（flag-gated，无状态透传 + 检索）。

端点：POST /api/intake/search
- ENABLE_INTAKE=false（默认）：返回 403 INTAKE_DISABLED，不检索、不落库，回到 E-1 关闭末态。
- ENABLE_INTAKE=true：接收已脱敏 SearchProfile 白名单五字段，经 E4-2 后端防御层二次校验，
  调 app.kernel.rag InternalSearchService.search_candidate_refs 出 CandidateRef[]（零正文）。

鉴权 / 租户隔离与 /api/search 同款：检索端点为会话态（query_session_id 中间件注入），
不要求登录令牌；ENABLE_INTAKE 作为访问闸（默认 false 即不对外暴露）。不新增任何未鉴权
检索端点之外的对外端点。

红线：
- 请求体 schema extra=forbid（schemas.IntakeSearchRequest）：raw_case / raw_query / PII /
  正文型键在 pydantic 层即 422（第一道闸）；E4-2 防御层在 service 层 fail-closed（第二道闸）。
- 日志只写 query_session_id / 计数 / degraded_reasons；绝不写 query_text / 原始案情 / PII。
- 无状态：不持久化 SearchProfile / CandidateRef、不写搜索历史、不落库。
- ENABLE_INTAKE_AI_EXTRACTION 仍 off、不接线：不调用任何服务端 AI 增强抽取。
"""
from __future__ import annotations

from fastapi import APIRouter, Request, status

from app.api.errors import api_error_response
from app.core.config import settings
from app.core.logging import logger
from app.intake.schemas import (
    IntakeCandidateRefView,
    IntakeSearchRequest,
    IntakeSearchResponse,
    IntakeSourceAnchorView,
)
from app.intake.service import IntakeSearchService
from app.kernel.guardrails import ContractViolationError
from app.kernel.rag import CandidateRef, InternalSearchResult
from app.schemas import ErrorResponse

router = APIRouter(prefix="/api/intake", tags=["intake"])

INTAKE_DISABLED_CODE = "INTAKE_DISABLED"
INTAKE_PROFILE_REJECTED_CODE = "INTAKE_PROFILE_REJECTED"

# SearchProfile 白名单五字段（仅这些从请求体进入 profile 载荷；mode/limit 是检索参数）。
_PROFILE_FIELDS = (
    "case_cause",
    "region",
    "trial_level_preference",
    "dispute_focus_keywords",
    "query_text",
)

# 模块级服务实例（懒构造，供测试 set_intake_search_service_for_test 替换）。
_intake_search_service: IntakeSearchService | None = None


def _get_service() -> IntakeSearchService:
    global _intake_search_service
    if _intake_search_service is None:
        _intake_search_service = IntakeSearchService()
    return _intake_search_service


def set_intake_search_service_for_test(service: IntakeSearchService | None) -> None:
    """测试注入钩子：替换 / 复位模块级 intake 检索服务实例。"""
    global _intake_search_service
    _intake_search_service = service


def _enabled() -> bool:
    return bool(getattr(settings, "ENABLE_INTAKE", False))


def _query_session_id(request: Request) -> str:
    return str(getattr(request.state, "query_session_id", "") or "")


def _disabled_response(request: Request):
    query_session_id = _query_session_id(request)
    logger.info(
        "intake_search_disabled query_session_id=%s feature_flag=ENABLE_INTAKE",
        query_session_id,
    )
    return api_error_response(
        status_code=status.HTTP_403_FORBIDDEN,
        code=INTAKE_DISABLED_CODE,
        message="案情录入端检索未启用（ENABLE_INTAKE=false），当前为单产品末态。",
        query_session_id=query_session_id,
    )


def _result_to_response(
    *,
    result: InternalSearchResult,
    query_session_id: str,
    mode: str,
) -> IntakeSearchResponse:
    """把内核 InternalSearchResult 映射为 intake 响应（白名单七字段 + 锚点，零正文）。

    只搬运 CandidateRef 已冻结的白名单字段与锚点元数据；degraded / degraded_reasons 是
    结构化原因码（不含正文）。timings / coverage 明细不回传（避免承载或反射正文）。
    """
    views: list[IntakeCandidateRefView] = [
        _candidate_ref_to_view(ref) for ref in result.candidate_refs
    ]
    return IntakeSearchResponse(
        query_session_id=query_session_id,
        candidate_refs=views,
        candidate_count=len(views),
        degraded=bool(result.degraded),
        degraded_reasons=list(result.degraded_reasons),
        search_mode="expanded" if mode == "expanded" else "standard",
    )


def _candidate_ref_to_view(ref: CandidateRef) -> IntakeCandidateRefView:
    return IntakeCandidateRefView(
        case_id=ref.case_id,
        case_number=ref.case_number,
        court=ref.court,
        trial_level=ref.trial_level,
        case_cause=ref.case_cause,
        judgment_date=ref.judgment_date,
        source_anchors=[
            IntakeSourceAnchorView(
                case_id=anchor.case_id,
                source_chunk_id=anchor.source_chunk_id,
                anchor_type=anchor.anchor_type,
            )
            for anchor in ref.source_anchors
        ],
    )


@router.post(
    "/search",
    response_model=IntakeSearchResponse,
    responses={
        status.HTTP_400_BAD_REQUEST: {"model": ErrorResponse},
        status.HTTP_403_FORBIDDEN: {"model": ErrorResponse},
        status.HTTP_422_UNPROCESSABLE_ENTITY: {"model": ErrorResponse},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ErrorResponse},
    },
)
def intake_search(payload: IntakeSearchRequest, request: Request):
    """案情录入端检索：已脱敏 SearchProfile -> CandidateRef[]（经 E3 内部检索服务）。"""
    if not _enabled():
        return _disabled_response(request)

    query_session_id = _query_session_id(request)

    # 只取 SearchProfile 白名单五字段进入 profile 载荷（mode/limit 是检索参数，不入 profile）。
    profile_payload = {
        field: getattr(payload, field) for field in _PROFILE_FIELDS
    }

    try:
        result = _get_service().search_candidate_refs(
            profile_payload,
            mode=payload.mode,
            limit=payload.limit,
            query_session_id=query_session_id,
        )
    except ContractViolationError as exc:
        # E4-2 后端防御层 fail-closed：含正文 / PII 键。异常消息只含键名，不回显键值。
        logger.warning(
            "intake_search_profile_rejected query_session_id=%s reason_code=%s",
            query_session_id,
            "INTAKE_PROFILE_REJECTED",
        )
        return api_error_response(
            status_code=status.HTTP_400_BAD_REQUEST,
            code=INTAKE_PROFILE_REJECTED_CODE,
            message="录入端检索入参不符合脱敏白名单契约（原始案情 / PII 零上送）。",
            query_session_id=query_session_id,
        )

    logger.info(
        "intake_search_completed query_session_id=%s candidate_count=%s degraded=%s degraded_reasons=%s",
        query_session_id,
        len(result.candidate_refs),
        bool(result.degraded),
        list(result.degraded_reasons),
    )
    return _result_to_response(
        result=result,
        query_session_id=query_session_id,
        mode=payload.mode,
    )


__all__ = [
    "router",
    "set_intake_search_service_for_test",
    "INTAKE_DISABLED_CODE",
    "INTAKE_PROFILE_REJECTED_CODE",
]
