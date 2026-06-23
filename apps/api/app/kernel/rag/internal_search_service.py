"""E3-2 检索执行服务适配层（内部服务，纯 Python import 面，不接 HTTP 端点）。

E 系列多产品生态 E-3 第二步：把「检索助手」现有的查询处理 / 召回 / 排序 /
摘要展示准备 / 来源锚点校验，组织成可复用的内部检索服务：

    InternalSearchRequest(SearchProfile)
      -> InternalSearchService.execute(...)               -> InternalSearchExecutionResult（富结果，供 E3-3 复用 /api/search）
      -> InternalSearchService.search_candidate_refs(...)  -> InternalSearchResult（跨产品输出，只含 CandidateRef[]）

第一性约束（文档 18 §3 / §6）：
- 服务经各 RAG 子包公开面消费 QueryProcessingService / VectorRetrievalService /
  FactSimilarityReranker / SummaryService，不深引私有实现、不引旧路径。
- 查询文本来自 SearchProfile.query_text，视为已脱敏短查询；日志只写 input_hash /
  query_session_id，绝不写 query_text / 原始案情。
- 对跨产品调用只暴露 CandidateRef[]，不暴露正文 / summary / highlight / matched_text /
  chunk 正文 / 裁判文书全文。CandidateRef 字段严格受 E-1 白名单约束。
- 富执行结果只承载内核级对象（CaseCandidate / LayeredRankedCandidate /
  ResultPresentation / SearchTimings / QueryPlan），供 E3-3 在 /api/search 内用既有 helper
  映射为 SearchResponse；本模块不 import app.schemas、不构造 SearchResultItem，保持
  导入方向 api -> kernel 单向。
- 本步不改排序 / 召回 / summary 策略，编排顺序与 api/search._handle_search_request 一致；
  SearchProfile 的 case_cause / region / trial_level_preference / dispute_focus_keywords
  本步仅作为结构化参数保留透传，不新增复杂检索策略，不为提指标改排序。

临时重复说明：execute() 的编排目前与 api/search._handle_search_request 同形（必要的
行为对齐）。E3-3 将让 /api/search 改为消费本服务、删除 search.py 内重复编排，使二者收敛为
单一权威实现；本步不改 /api/search，故暂存最小重复，且保证语义不分叉（同序、同 helper 语义）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from time import perf_counter
from typing import Any, Mapping

from app.core.logging import logger
from app.core.timing import SearchTimings, TimingRecorder
from app.kernel.guardrails.contracts import ContractViolationError
from app.kernel.rag.internal_search_contracts import (
    CandidateRef,
    InternalSearchRequest,
    InternalSearchResult,
    SearchProfile,
    sanitize_candidate_ref,
)
# 经各 RAG 子包的公开面（package __init__）消费内核符号，不深引私有实现。
# 不从 app.kernel.rag 聚合 __init__ 取符号：本模块由该聚合 __init__ 在其装配过程中
# import，直接回引会触发部分初始化的循环导入（与 E3-1 契约模块同款规避：走子包公开面）。
from app.kernel.rag.query_processing import (
    QueryPlan,
    QueryProcessingService,
    QueryValidationError,
)
from app.kernel.rag.rerank import FactSimilarityReranker
from app.kernel.rag.retrieval import (
    CaseCandidate,
    VectorRetrievalService,
    merge_case_candidates,
)
from app.kernel.rag.retrieval.confidence import (
    LayeredRankedCandidate,
    split_low_confidence_candidates,
)
from app.kernel.rag.summary import (
    SUMMARY_LLM_UNAVAILABLE,
    ResultPresentation,
    SummaryService,
)

# CandidateRef 丢弃原因码（跨产品输出时锚点不完整 / 不可溯源的候选不暴露）。
CANDIDATE_REF_DROPPED_NO_ANCHOR = "CANDIDATE_REF_DROPPED_NO_ANCHOR"


@dataclass
class InternalSearchExecutionResult:
    """检索执行的富结果（供 E3-3 在 /api/search 内复用既有 helper 映射 SearchResponse）。

    只承载内核级对象，不含 app.schemas 类型，不直接对跨产品暴露。
    query_validation_error / retrieval_error_type 用于让 E3-3 还原既有错误码 / 降级早退语义。
    """

    query_plan: QueryPlan | None = None
    case_candidates: list[CaseCandidate] = field(default_factory=list)
    results: list[LayeredRankedCandidate] = field(default_factory=list)
    low_confidence_candidates: list[LayeredRankedCandidate] = field(default_factory=list)
    presentation_by_rank: dict[int, ResultPresentation] = field(default_factory=dict)
    degraded: bool = False
    degraded_reasons: list[str] = field(default_factory=list)
    summary_degraded_reasons: list[str] = field(default_factory=list)
    timings: SearchTimings = field(default_factory=SearchTimings)
    search_mode: str = "standard"
    # 早退 / 失败信号（不含正文）：E3-3 用于还原 400/413/503 行为，本步只承载不抛。
    query_validation_error: QueryValidationError | None = None
    retrieval_error_type: str | None = None

    @property
    def visible_ranked(self) -> list[LayeredRankedCandidate]:
        return [*self.results, *self.low_confidence_candidates]


class InternalSearchService:
    """内部检索服务适配层：编排查询处理 / 召回 / 排序 / 摘要展示 / 锚点校验。

    依赖经构造函数注入（默认用内核公开面的服务类），便于测试用 fake/mock 替换；
    本服务不持有任何持久层句柄，不写库 / 不写搜索历史 / 不写收藏 / 不写报告。
    """

    def __init__(
        self,
        *,
        query_processing_service: QueryProcessingService | None = None,
        retrieval_service: VectorRetrievalService | None = None,
        rerank_service: FactSimilarityReranker | None = None,
        summary_service: SummaryService | None = None,
    ) -> None:
        self._query_processing_service = query_processing_service or QueryProcessingService()
        self._retrieval_service = retrieval_service or VectorRetrievalService()
        self._rerank_service = rerank_service or FactSimilarityReranker()
        self._summary_service = summary_service or SummaryService()

    # --- 主入口：富执行结果（供 E3-3 复用既有 /api/search 行为）------------------

    def execute(
        self,
        request: InternalSearchRequest,
        *,
        query_session_id: str | None = None,
    ) -> InternalSearchExecutionResult:
        """执行检索，返回富结果。编排顺序与 api/search._handle_search_request 一致。

        - QueryValidationError / 召回异常不向外抛，转为富结果上的信号字段，
          交由 E3-3 还原既有错误码（本步不改 /api/search，仅承载）。
        - 日志只写 input_hash / query_session_id，绝不写 query_text。
        """
        profile = request.profile
        query_text = profile.query_text or ""
        recorder = TimingRecorder()
        search_mode = "expanded" if request.include_relaxed_recall else "standard"

        # 1) 查询处理（脱敏短查询 -> QueryPlan）。校验失败转信号字段，不抛。
        try:
            query_plan = self._query_processing_service.process(query_text)
        except QueryValidationError as exc:
            timings = recorder.finish()
            logger.warning(
                "internal_search_query_rejected query_session_id=%s degraded_reasons=%s",
                query_session_id or "",
                [exc.code],
            )
            return InternalSearchExecutionResult(
                query_validation_error=exc,
                timings=timings,
                search_mode=search_mode,
                degraded=True,
                degraded_reasons=[exc.code],
            )

        recorder.timings.rewrite_duration_ms = query_plan.rewrite_duration_ms

        # 2) 召回。异常转信号字段（error_type，不含正文），不抛。
        try:
            retrieval_result = self._retrieval_service.retrieve(
                query_plan,
                include_relaxed_recall=request.include_relaxed_recall,
            )
        except Exception as exc:  # noqa: BLE001 - 内部服务边界，保持错误脱敏
            timings = recorder.finish()
            logger.error(
                "internal_search_retrieval_unhandled query_session_id=%s input_hash=%s error_type=%s",
                query_session_id or "",
                query_plan.input_hash,
                exc.__class__.__name__,
            )
            return InternalSearchExecutionResult(
                query_plan=query_plan,
                retrieval_error_type=exc.__class__.__name__,
                timings=timings,
                search_mode=search_mode,
                degraded=True,
                degraded_reasons=_unique_reasons(query_plan.degraded_reasons),
            )

        recorder.timings.embedding_duration_ms = retrieval_result.embedding_duration_ms
        recorder.timings.retrieval_duration_ms = retrieval_result.retrieval_duration_ms

        # 3) 候选合并 + 排序（不改排序 / 召回策略）。
        case_candidates = merge_case_candidates(retrieval_result.candidates)
        rerank_started = perf_counter()
        ranked_candidates = self._rerank_service.rerank(query_plan, case_candidates)
        recorder.timings.rerank_duration_ms = _elapsed_ms(rerank_started)

        # 4) 置信度分层（不改 limit / 顺序语义）。
        confidence_split = split_low_confidence_candidates(
            ranked_candidates,
            limit=request.limit,
            degraded_reasons=[
                *query_plan.degraded_reasons,
                *retrieval_result.degraded_reasons,
            ],
        )
        visible_ranked = [
            *confidence_split.results,
            *confidence_split.low_confidence_candidates,
        ]

        # 5) 摘要 / 展示准备。摘要异常绝不打断检索（与 search.py 同口径）。
        summary_started = perf_counter()
        try:
            presentations = self._summary_service.build_presentations(
                query_plan,
                [layered.ranked.candidate for layered in visible_ranked],
            )
            summary_degraded_reasons = _unique_reasons(
                [reason for presentation in presentations for reason in presentation.degraded_reasons]
            )
        except Exception as exc:  # noqa: BLE001 - summary 不得打断检索
            logger.warning(
                "internal_search_summary_unhandled query_session_id=%s input_hash=%s error_type=%s",
                query_session_id or "",
                query_plan.input_hash,
                exc.__class__.__name__,
            )
            presentations = [ResultPresentation(summary=None, highlights=[]) for _ in visible_ranked]
            summary_degraded_reasons = [SUMMARY_LLM_UNAVAILABLE]
        recorder.timings.summary_duration_ms = _elapsed_ms(summary_started)

        presentation_by_rank = {
            layered.original_rank: presentation
            for layered, presentation in zip(visible_ranked, presentations, strict=True)
        }
        degraded_reasons = _unique_reasons(
            [
                *query_plan.degraded_reasons,
                *retrieval_result.degraded_reasons,
                *summary_degraded_reasons,
            ]
        )
        degraded = query_plan.degraded or retrieval_result.degraded or bool(degraded_reasons)
        timings = recorder.finish()

        logger.info(
            "internal_search_completed query_session_id=%s input_hash=%s candidate_count=%s "
            "result_count=%s low_confidence_count=%s degraded=%s degraded_reasons=%s",
            query_session_id or "",
            query_plan.input_hash,
            len(case_candidates),
            len(confidence_split.results),
            len(confidence_split.low_confidence_candidates),
            degraded,
            degraded_reasons,
        )
        return InternalSearchExecutionResult(
            query_plan=query_plan,
            case_candidates=case_candidates,
            results=confidence_split.results,
            low_confidence_candidates=confidence_split.low_confidence_candidates,
            presentation_by_rank=presentation_by_rank,
            degraded=degraded,
            degraded_reasons=degraded_reasons,
            summary_degraded_reasons=summary_degraded_reasons,
            timings=timings,
            search_mode=search_mode,
        )

    # --- 跨产品输出：只暴露 CandidateRef[]（零正文）----------------------------

    def search_candidate_refs(
        self,
        request: InternalSearchRequest | SearchProfile,
        *,
        query_session_id: str | None = None,
    ) -> InternalSearchResult:
        """跨产品检索入口：输入 SearchProfile / InternalSearchRequest，输出 CandidateRef[]。

        只把可见候选（主结果 + 低置信）转成 CandidateRef（白名单 + 锚点 fail-closed）。
        锚点不完整 / 不可溯源的候选被丢弃并记一条降级原因码，不暴露给下游。
        结果只含引用与结构化元信息，绝不携带正文 / summary / highlight 文本。
        """
        normalized = _as_request(request)
        execution = self.execute(normalized, query_session_id=query_session_id)

        # 校验失败 / 召回失败：返回空候选 + 降级，不携带正文。
        if execution.query_validation_error is not None or execution.retrieval_error_type is not None:
            return InternalSearchResult(
                candidate_refs=[],
                degraded=True,
                degraded_reasons=_unique_reasons(execution.degraded_reasons),
                coverage=_coverage_meta([], execution.search_mode, execution.degraded_reasons),
                timings=_timings_meta(execution.timings),
            )

        candidate_refs: list[CandidateRef] = []
        drop_reasons: list[str] = []
        for layered in execution.visible_ranked:
            ref = _safe_candidate_ref(layered.ranked.candidate)
            if ref is None:
                drop_reasons.append(CANDIDATE_REF_DROPPED_NO_ANCHOR)
                continue
            candidate_refs.append(ref)

        degraded_reasons = _unique_reasons([*execution.degraded_reasons, *drop_reasons])
        return InternalSearchResult(
            candidate_refs=candidate_refs,
            degraded=execution.degraded or bool(drop_reasons),
            degraded_reasons=degraded_reasons,
            coverage=_coverage_meta(
                execution.case_candidates, execution.search_mode, degraded_reasons
            ),
            timings=_timings_meta(execution.timings),
        )


# --- 纯转换 / 辅助函数（无副作用，不含正文）----------------------------------

def _as_request(request: InternalSearchRequest | SearchProfile) -> InternalSearchRequest:
    if isinstance(request, InternalSearchRequest):
        return request
    if isinstance(request, SearchProfile):
        return InternalSearchRequest(profile=request)
    raise TypeError("search_candidate_refs 仅接受 InternalSearchRequest 或 SearchProfile")


def _safe_candidate_ref(candidate: CaseCandidate) -> CandidateRef | None:
    """把 CaseCandidate 转为 CandidateRef；锚点不完整则丢弃（返回 None）。

    只搬运 E-1 白名单字段 + 锚点元数据；summary / highlights / matched_text / metadata
    等富展示 / 正文型字段一律不进入输出。case_no -> case_number 映射。
    """
    metadata = candidate.metadata if isinstance(candidate.metadata, Mapping) else {}
    source_chunk_ids = _source_chunk_ids(candidate)
    anchors = [
        {"case_id": candidate.case_id, "source_chunk_id": chunk_id, "anchor_type": "result"}
        for chunk_id in source_chunk_ids
    ]
    payload: dict[str, Any] = {
        "case_id": candidate.case_id,
        "case_number": _metadata_str(metadata, "case_no"),
        "court": _metadata_str(metadata, "court"),
        "trial_level": _metadata_str(metadata, "trial_level"),
        "case_cause": _metadata_str(metadata, "case_cause") or _metadata_str(metadata, "crime_type"),
        "judgment_date": _metadata_str(metadata, "judgment_date"),
        "source_anchors": anchors,
    }
    payload = {k: v for k, v in payload.items() if v is not None}
    try:
        return sanitize_candidate_ref(payload)
    except ContractViolationError:
        # 锚点缺失 / 不完整 / 不可溯源 -> fail-closed，不暴露该候选。
        return None


def _source_chunk_ids(candidate: CaseCandidate) -> list[str]:
    values = [candidate.top_chunk_id, *candidate.source_chunk_ids, *candidate.hit_chunk_ids]
    unique: list[str] = []
    for value in values:
        chunk_id = str(value or "").strip()
        if chunk_id and chunk_id not in unique:
            unique.append(chunk_id)
    return unique


def _metadata_str(metadata: Mapping[str, Any], key: str) -> str | None:
    value = metadata.get(key)
    if value is None or value == "":
        return None
    return str(value)


def _coverage_meta(
    case_candidates: list[CaseCandidate],
    search_mode: str,
    degraded_reasons: list[str],
) -> dict[str, Any]:
    """结构化覆盖元信息（不含正文）。完整 DataCoverage 由 E3-3 在 /api/search 内构造。"""
    return {
        "total_candidate_count": len(case_candidates),
        "search_mode": "expanded" if search_mode == "expanded" else "standard",
        "degraded_reasons": _unique_reasons(degraded_reasons),
    }


def _timings_meta(timings: SearchTimings) -> dict[str, Any]:
    """计时元信息（纯整数毫秒，不含正文 / query_text）。"""
    return dict(timings.__dict__)


def _elapsed_ms(started: float) -> int:
    return int((perf_counter() - started) * 1000)


def _unique_reasons(reasons: list[str]) -> list[str]:
    unique: list[str] = []
    for reason in reasons:
        if reason and reason not in unique:
            unique.append(reason)
    return unique


__all__ = [
    "InternalSearchService",
    "InternalSearchExecutionResult",
    "CANDIDATE_REF_DROPPED_NO_ANCHOR",
]
