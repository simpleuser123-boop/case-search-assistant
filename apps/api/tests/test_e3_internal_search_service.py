"""E3-2 检索执行服务适配层 focused 单元测试。

验证（对应文档 18 §6 验收 / 测试要求）：
- InternalSearchService 可从 app.kernel.rag 与 app.kernel 公开面导入（身份保持）。
- search_candidate_refs 接受 SearchProfile / InternalSearchRequest，输出 CandidateRef[]。
- 用 fake QueryProcessing / Retrieval / Rerank / Summary 构造短元数据候选，验证服务输出 CandidateRef。
- source_anchors 不完整（缺 chunk_id）时候选被丢弃（fail-closed），记降级原因码，不暴露。
- 输出 CandidateRef 严格受 E-1 白名单约束，不含 summary/highlights/matched_text/content/body。
- degraded/degraded_reasons/timings 能透传或汇总，但不携带正文。
- QueryValidationError / 召回异常转富结果信号字段，不抛；跨产品输出为空候选 + 降级。
- 服务模块只经 app.kernel.rag / 子模块公开面消费内核，不深引旧路径；不注册 HTTP 端点。

红线：fixture 只用短假数据 / hash / case_id / source_chunk_id / 元数据，绝不写真实长案情或裁判正文。
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.core.timing import SearchTimings
from app.kernel.rag import (
    CandidateRef,
    InternalSearchExecutionResult,
    InternalSearchRequest,
    InternalSearchResult,
    InternalSearchService,
    QueryValidationError,
    SearchProfile,
)
from app.kernel.rag.internal_search_service import CANDIDATE_REF_DROPPED_NO_ANCHOR
from app.kernel.rag.query_processing.models import QueryPlan
from app.kernel.rag.rerank.models import RankedCaseCandidate
from app.kernel.rag.retrieval.models import VectorCandidate, VectorRetrievalResult
from app.kernel.rag.summary.models import ResultPresentation

APP_DIR = Path(__file__).resolve().parents[1] / "app"
SERVICE_MODULE = APP_DIR / "kernel" / "rag" / "internal_search_service.py"

# CandidateRef 绝不允许出现的正文 / 富展示型字段。
FORBIDDEN_ON_CANDIDATE_REF = (
    "summary",
    "summary_text",
    "highlights",
    "highlight",
    "highlight_text",
    "matched_text",
    "holding_summary",
    "chunk_text",
    "full_text",
    "content",
    "body",
    "text",
    "metadata",
    "raw_query",
    "raw_case",
)

# 正文型键不得作为「数据字段」出现在服务源码（仅可作被拒键名常量 / 注释）。
FORBIDDEN_SOURCE_TOKENS = (
    "query_text",
    "raw_query",
    "raw_case",
    "full_text",
    "chunk_text",
    "judgment_full_text",
)


# --- fakes（短假数据，无副作用，不写库）-------------------------------------

def _query_plan(*, degraded: bool = False, reasons: list[str] | None = None) -> QueryPlan:
    return QueryPlan(
        cleaned_query="盗窃 5000元 自首",
        input_hash="hash_" + "0" * 8,
        queries=["盗窃 5000元 自首"],
        degraded=degraded,
        degraded_reasons=reasons or [],
        rewrite_duration_ms=1,
    )


def _vector_candidate(case_id: str, chunk_id: str, *, score: float = 0.9, **meta) -> VectorCandidate:
    metadata = {
        "case_no": f"({case_id})刑初字第1号",
        "court": "某基层人民法院",
        "trial_level": "一审",
        "case_cause": "盗窃",
        "judgment_date": "2023-01-01",
    }
    metadata.update(meta)
    return VectorCandidate(
        case_id=case_id,
        chunk_id=chunk_id,
        vector_score=score,
        retrieval_source="vector",
        metadata=metadata,
        matched_text="短匹配片段",  # 不进入 CandidateRef
        source="local",
        retrieval_score=score,
        matched_by_vector=True,
    )


class FakeQueryProcessing:
    def __init__(self, *, plan: QueryPlan | None = None, error: QueryValidationError | None = None):
        self._plan = plan or _query_plan()
        self._error = error

    def process(self, raw_query: str) -> QueryPlan:
        if self._error is not None:
            raise self._error
        return self._plan


class FakeRetrieval:
    def __init__(self, *, candidates: list[VectorCandidate] | None = None, error: Exception | None = None,
                 degraded: bool = False, reasons: list[str] | None = None):
        self._candidates = candidates or []
        self._error = error
        self._degraded = degraded
        self._reasons = reasons or []
        self.calls: list[bool] = []

    def retrieve(self, query_plan, *, include_relaxed_recall: bool = False) -> VectorRetrievalResult:
        self.calls.append(include_relaxed_recall)
        if self._error is not None:
            raise self._error
        return VectorRetrievalResult(
            candidates=self._candidates,
            retrieval_duration_ms=2,
            embedding_duration_ms=1,
            degraded=self._degraded,
            degraded_reasons=self._reasons,
        )


class FakeRerank:
    """把 case 级候选原样按输入顺序包成 RankedCaseCandidate（不改排序）。"""

    def rerank(self, query_plan, candidates):
        ranked = []
        for index, candidate in enumerate(candidates):
            ranked.append(
                RankedCaseCandidate(
                    candidate=candidate,
                    final_score=round(candidate.retrieval_score, 6),
                    score_breakdown={"base": candidate.retrieval_score},
                )
            )
        return ranked


class FakeSummary:
    def __init__(self, *, error: Exception | None = None):
        self._error = error

    def build_presentations(self, query_plan, candidates) -> list[ResultPresentation]:
        if self._error is not None:
            raise self._error
        return [ResultPresentation(summary=None, highlights=[]) for _ in candidates]


def _service(**overrides) -> InternalSearchService:
    return InternalSearchService(
        query_processing_service=overrides.get("qp", FakeQueryProcessing()),
        retrieval_service=overrides.get("retrieval", FakeRetrieval(candidates=[_vector_candidate("c1", "c1_chunk0")])),
        rerank_service=overrides.get("rerank", FakeRerank()),
        summary_service=overrides.get("summary", FakeSummary()),
    )


def _profile() -> SearchProfile:
    return SearchProfile(
        case_cause="盗窃",
        region="某省",
        trial_level_preference="一审",
        dispute_focus_keywords=["自首", "数额"],
        query_text="盗窃 5000元 自首",
    )


# --- 公开面 / 身份保持 -------------------------------------------------------

def test_service_importable_from_kernel_surfaces():
    from app import kernel
    from app.kernel import rag
    from app.kernel.rag import internal_search_service as mod

    assert rag.InternalSearchService is mod.InternalSearchService
    assert kernel.InternalSearchService is mod.InternalSearchService
    assert "InternalSearchService" in rag.__all__
    assert "InternalSearchService" in kernel.__all__


# --- search_candidate_refs：基本输出 ----------------------------------------

def test_search_candidate_refs_accepts_profile_and_outputs_candidate_refs():
    service = _service()
    result = service.search_candidate_refs(_profile())
    assert isinstance(result, InternalSearchResult)
    assert len(result.candidate_refs) == 1
    ref = result.candidate_refs[0]
    assert isinstance(ref, CandidateRef)
    assert ref.case_id == "c1"
    assert ref.case_number == "(c1)刑初字第1号"
    assert ref.court == "某基层人民法院"
    assert len(ref.source_anchors) >= 1
    assert ref.source_anchors[0].case_id == "c1"
    assert ref.source_anchors[0].source_chunk_id == "c1_chunk0"


def test_search_candidate_refs_accepts_internal_request():
    service = _service()
    request = InternalSearchRequest(profile=_profile(), mode="standard", limit=5)
    result = service.search_candidate_refs(request)
    assert len(result.candidate_refs) == 1


def test_candidate_ref_only_whitelist_fields():
    service = _service()
    result = service.search_candidate_refs(_profile())
    ref = result.candidate_refs[0]
    dumped = ref.model_dump()
    for forbidden in FORBIDDEN_ON_CANDIDATE_REF:
        assert forbidden not in dumped
    assert set(dumped) == {
        "case_id", "case_number", "court", "trial_level",
        "case_cause", "judgment_date", "source_anchors",
    }


def test_expanded_mode_passes_relaxed_recall_through():
    retrieval = FakeRetrieval(candidates=[_vector_candidate("c1", "c1_chunk0")])
    service = _service(retrieval=retrieval)
    request = InternalSearchRequest(profile=_profile(), include_relaxed_recall=True)
    result = service.search_candidate_refs(request)
    assert retrieval.calls == [True]
    assert result.coverage["search_mode"] == "expanded"


# --- fail-closed：锚点不完整候选被丢弃 --------------------------------------

def test_candidate_without_chunk_id_is_dropped_fail_closed():
    # 一个候选无 chunk_id（top_chunk_id 与 source/hit 均空），锚点不完整 -> 丢弃。
    bad = _vector_candidate("c_bad", "")
    good = _vector_candidate("c_good", "c_good_chunk0")
    retrieval = FakeRetrieval(candidates=[bad, good])
    service = _service(retrieval=retrieval)
    result = service.search_candidate_refs(_profile())
    ids = [ref.case_id for ref in result.candidate_refs]
    assert "c_bad" not in ids
    assert "c_good" in ids
    assert result.degraded is True
    assert CANDIDATE_REF_DROPPED_NO_ANCHOR in result.degraded_reasons


# --- degraded / timings 透传，不含正文 --------------------------------------

def test_degraded_reasons_and_timings_propagated_without_body():
    retrieval = FakeRetrieval(
        candidates=[_vector_candidate("c1", "c1_chunk0")],
        degraded=True,
        reasons=["CHROMA_EMPTY"],
    )
    service = _service(retrieval=retrieval)
    result = service.search_candidate_refs(_profile())
    assert result.degraded is True
    assert "CHROMA_EMPTY" in result.degraded_reasons
    assert isinstance(result.timings, dict)
    # timings 只含整数毫秒字段，不含 query_text / 正文。
    for key, value in result.timings.items():
        assert key.endswith("_duration_ms")
        assert isinstance(value, int)


def test_query_validation_error_becomes_empty_degraded_result():
    qp = FakeQueryProcessing(error=QueryValidationError(code="QUERY_TOO_SHORT", message="过短"))
    service = _service(qp=qp)
    result = service.search_candidate_refs(_profile())
    assert result.candidate_refs == []
    assert result.degraded is True
    assert "QUERY_TOO_SHORT" in result.degraded_reasons


def test_retrieval_exception_becomes_empty_degraded_result():
    retrieval = FakeRetrieval(error=RuntimeError("boom"))
    service = _service(retrieval=retrieval)
    result = service.search_candidate_refs(_profile())
    assert result.candidate_refs == []
    assert result.degraded is True


def test_summary_exception_does_not_break_candidate_refs():
    service = _service(summary=FakeSummary(error=RuntimeError("summary down")))
    result = service.search_candidate_refs(_profile())
    # summary 失败不打断检索，候选仍输出。
    assert len(result.candidate_refs) == 1


# --- execute：富执行结果（供 E3-3 复用）-------------------------------------

def test_execute_returns_rich_result_with_kernel_objects():
    service = _service()
    request = InternalSearchRequest(profile=_profile())
    execution = service.execute(request)
    assert isinstance(execution, InternalSearchExecutionResult)
    assert execution.query_plan is not None
    assert len(execution.case_candidates) == 1
    assert isinstance(execution.timings, SearchTimings)
    assert execution.search_mode == "standard"
    # 富结果按 original_rank 映射展示，供 E3-3 用既有 helper 构造 SearchResponse。
    assert isinstance(execution.presentation_by_rank, dict)


def test_execute_query_validation_error_carried_not_raised():
    qp = FakeQueryProcessing(error=QueryValidationError(code="QUERY_EMPTY", message="空"))
    service = _service(qp=qp)
    execution = service.execute(InternalSearchRequest(profile=_profile()))
    assert execution.query_validation_error is not None
    assert execution.query_validation_error.code == "QUERY_EMPTY"
    assert execution.case_candidates == []


def test_execute_retrieval_error_carried_not_raised():
    retrieval = FakeRetrieval(error=ValueError("chroma down"))
    service = _service(retrieval=retrieval)
    execution = service.execute(InternalSearchRequest(profile=_profile()))
    assert execution.retrieval_error_type == "ValueError"
    assert execution.case_candidates == []


# --- 静态边界：不深引旧路径 / 不注册端点 / 不引 schemas / 无正文数据字段 ------

def test_service_module_no_http_router_and_no_legacy_deep_import():
    source = SERVICE_MODULE.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
        elif isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)

    # 不得 import FastAPI / router / app.schemas（保持 api -> kernel 单向，不引 HTTP/响应模型）。
    for banned in ("fastapi", "app.schemas", "app.api"):
        assert not any(mod == banned or mod.startswith(banned + ".") for mod in imported), banned

    # 内核消费只能经 app.kernel(.rag) 公开面或其 service 子模块，不得深引非 kernel 旧路径。
    for mod in imported:
        if mod.startswith("app.") and not (
            mod == "app.core.logging"
            or mod == "app.core.timing"
            or mod.startswith("app.kernel")
        ):
            raise AssertionError(f"unexpected deep import: {mod}")

    # 不注册 HTTP 端点。
    assert "APIRouter" not in source
    assert "@router" not in source


def test_service_source_has_no_body_data_fields():
    source = SERVICE_MODULE.read_text(encoding="utf-8")
    # 正文型 token 仅允许出现在被拒键名常量 / 注释 / docstring；不得作为赋值数据字段键。
    # 这里做保守断言：service 源码不得把 query_text 写进任何 logger / dict 输出键。
    assert 'logger' in source
    # 不得出现把 query_text 作为日志/输出值的写法。
    for banned in ('query_text=', '"query_text":', "'query_text':", 'raw_query=', 'raw_case='):
        assert banned not in source, banned
