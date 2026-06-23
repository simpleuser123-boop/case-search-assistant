"""M5-8 法院/法官倾向分析（F19）API 路由（gate + flag 双闸）。

展示前置（缺一即 403）：
- ENABLE_TENDENCY_ANALYSIS=true；
- M5-7 数据门禁 f19_can_go_live=True。
关闭 / 门禁回退 → 403 TENDENCY_ANALYSIS_UNAVAILABLE，不返回任何聚合，回到 M5-7 末态。

红线：
- 只读聚合统计；不改数据 / 向量库 / 主排序。
- 只回聚合计数 + 占比 + case_id 引用 + 覆盖/样本/免责说明；绝不回个案正文 / 当事人。
- 不输出个案预测 / 胜负概率 / 确定性法律结论 / 具名法官预测。
- 日志只记路径 + 原因码 + 计数；不落正文 / 凭据。
"""
from __future__ import annotations

from fastapi import APIRouter, Request, status
from pydantic import BaseModel

from app.api.errors import api_error_response
from app.core.config import settings
from app.core.logging import logger
from app.schemas import ErrorResponse
from app.tendency_analysis import (
    ForbiddenAnalysisContentError,
    TendencyAnalysisService,
    TendencyUnavailable,
)

router = APIRouter(prefix="/api/tendency", tags=["tendency"])

TENDENCY_UNAVAILABLE_CODE = "TENDENCY_ANALYSIS_UNAVAILABLE"


class TendencyBucketView(BaseModel):
    label: str
    sample_size: int
    share: float
    sample_sufficient: bool
    case_id_refs: list[str]
    case_id_total: int


class TendencyAggregationView(BaseModel):
    dimension: str
    name: str
    sample_size: int
    coverage_range: str
    data_source: str
    confidence_note: str
    insufficient_dimension: bool
    buckets: list[TendencyBucketView]


class TendencyAnalysisView(BaseModel):
    version: str
    enabled: bool
    gate_passed: bool
    data_source: str
    coverage_range: str
    total_sample_size: int
    min_sample_threshold: int
    disclaimer: str
    aggregations: list[TendencyAggregationView]


def _flag_enabled() -> bool:
    return bool(getattr(settings, "ENABLE_TENDENCY_ANALYSIS", False))


def _make_service() -> TendencyAnalysisService:
    return TendencyAnalysisService(flag_enabled=_flag_enabled())


# 测试可替换的服务工厂。
_service_factory = _make_service


def set_tendency_service_factory_for_test(factory) -> None:
    global _service_factory
    _service_factory = factory or _make_service


def _unavailable_response(request: Request, reason_code: str, message: str):
    logger.info(
        "tendency_analysis_unavailable path=%s reason_code=%s",
        request.url.path, reason_code,
    )
    return api_error_response(
        status_code=status.HTTP_403_FORBIDDEN,
        code=TENDENCY_UNAVAILABLE_CODE,
        message=message,
        query_session_id=getattr(request.state, "query_session_id", None),
    )


@router.get(
    "/analysis",
    response_model=TendencyAnalysisView,
    responses={status.HTTP_403_FORBIDDEN: {"model": ErrorResponse}},
)
def get_tendency_analysis(request: Request):
    service = _service_factory()
    try:
        result = service.build()
    except TendencyUnavailable as exc:
        return _unavailable_response(request, exc.reason_code, exc.message)
    except ForbiddenAnalysisContentError:
        # 护栏命中视为不可展示（fail-closed），不泄露细节。
        logger.warning(
            "tendency_analysis_guard_block path=%s reason_code=%s",
            request.url.path, "privacy_guard_block",
        )
        return _unavailable_response(
            request, "privacy_guard_block", "倾向分析输出未通过边界校验，已阻断展示。"
        )
    logger.info(
        "tendency_analysis_served path=%s reason_code=%s total_sample=%s dims=%s",
        request.url.path, "ok", result.total_sample_size, len(result.aggregations),
    )
    return TendencyAnalysisView(**result.as_dict())
