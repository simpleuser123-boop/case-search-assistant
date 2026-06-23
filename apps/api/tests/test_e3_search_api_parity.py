"""E3-3 /api/search 消费内部服务的行为兼容（parity）测试。

验证（对应文档 18 §7 验收 / 回归测试建议）：
- /api/search 与 /api/search/expand 经 InternalSearchService.execute 执行检索主路径，
  SearchResponse 外部字段、错误码、降级、coverage、timings、risk_hints 与 E3-3 前一致。
- query 校验失败 -> 既有 400/413 错误码；召回异常 -> 503；summary 异常不打断检索。
- ENABLE_EXPANDED_SEARCH=false 时 /api/search/expand 仍 403，不执行召回。
- 日志只写 input_hash / query_session_id，绝不写 query_text。
- api/search.py 不再绕过服务复制检索主路径：主路径只经 InternalSearchService 执行
  （静态断言 + 运行时 spy）。

红线：fixture 只用短假数据 / hash / case_id / source_chunk_id / 元数据，不写真实长案情或裁判正文。
"""
from __future__ import annotations

import ast
import logging
from pathlib import Path

from fastapi.testclient import TestClient

from app.api import search as search_api
from app.core.config import Settings
from app.main import app
from app.query_processing import QueryProcessingService
from app.rerank import FactSimilarityReranker
from app.retrieval.embedding_cache import QueryEmbeddingCache
from app.retrieval.models import RetrievalDependencyError, RetrievedChunk
from app.retrieval.service import ORIGINAL_VECTOR_SOURCE, VectorRetrievalService
from app.summary import SummaryService

client = TestClient(app)

API_SEARCH_MODULE = Path(__file__).resolve().parents[1] / "app" / "api" / "search.py"


# --- fakes（短假数据，无副作用，不写库）-------------------------------------

class FakeEmbeddingClient:
    config = type(
        "Cfg",
        (),
        {
            "EMBEDDING_PROVIDER": "ollama",
            "EMBEDDING_MODEL": "bge-m3",
            "EMBEDDING_DIMENSION": 1024,
            "EMBEDDING_DISTANCE_METRIC": "cosine",
        },
    )()

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail

    def embed_queries(self, texts: list[str]) -> list[list[float]]:
        if self.fail:
            raise RetrievalDependencyError("EMBEDDING_TIMEOUT", "timed out")
        return [[0.1] * 1024 for _ in texts]


class FakeVectorStore:
    def query(self, embedding, *, top_k, retrieval_source):
        return [_chunk(retrieval_source=retrieval_source, vector_score=0.88)]


class FakeFallbackRetriever:
    def search(self, query_text, *, top_k, retrieval_source):
        return [_chunk(retrieval_source=retrieval_source, vector_score=0.66)]


class ExplodingRetrievalService:
    """召回阶段抛异常，验证 /api/search 还原 503。"""

    def retrieve(self, query_plan, *, include_relaxed_recall=False):
        raise RuntimeError("RETRIEVAL_BOOM_SENTINEL")


class ExplodingSummaryService:
    """summary 阶段抛异常，验证 /api/search 不被打断、降级返回。"""

    def build_presentations(self, *_args, **_kwargs):
        raise RuntimeError("SUMMARY_BOOM_SENTINEL")


def _chunk(*, retrieval_source: str, vector_score: float) -> RetrievedChunk:
    return RetrievedChunk(
        case_id="case-parity",
        chunk_id=f"{retrieval_source}-chunk",
        score=vector_score,
        vector_score=vector_score,
        distance=1.0 - vector_score,
        metadata={
            "case_id": "case-parity",
            "chunk_id": f"{retrieval_source}-chunk",
            "title": "盗窃罪测试判决书",
            "case_cause": "盗窃罪",
            "chunk_type": "court_found",
            "court": "测试法院",
            "trial_level": "一审",
            "judgment_date": "2020-01-02",
            "source_name": "parity-source",
        },
        text="CHUNK_BODY_SENTINEL。本院查明,被告人夜间进入店铺盗窃现金5000元。",
        source="parity-test",
        retrieval_source=retrieval_source,
    )


def _install_services(monkeypatch, *, retrieval=None, summary=None):
    config = Settings(
        DEEPSEEK_API_KEY="test-key",
        ENABLE_QUERY_REWRITE=False,
        ENABLE_WEIGHTED_RERANK=False,
        ENABLE_SUMMARY=False,
        ENABLE_EXPANDED_SEARCH=False,
    )
    monkeypatch.setattr(search_api, "settings", config)
    monkeypatch.setattr(search_api, "query_processing_service", QueryProcessingService(config=config))
    monkeypatch.setattr(
        search_api,
        "retrieval_service",
        retrieval
        or VectorRetrievalService(
            embedding_client=FakeEmbeddingClient(),
            vector_store=FakeVectorStore(),
            fallback_retriever=FakeFallbackRetriever(),
            embedding_cache=QueryEmbeddingCache(ttl_seconds=60, max_entries=8),
            min_candidates_before_relaxed_recall=1,
        ),
    )
    monkeypatch.setattr(search_api, "rerank_service", FactSimilarityReranker(config=config))
    monkeypatch.setattr(search_api, "summary_service", summary or SummaryService(config=config))
    return config


# --- parity：成功路径 SearchResponse 字段稳定 ---------------------------------

def test_search_consumes_internal_service_and_returns_stable_fields(monkeypatch):
    _install_services(monkeypatch)

    response = client.post("/api/search", json={"query": "夜间进入店铺盗窃现金5000元", "limit": 1})

    assert response.status_code == 200
    body = response.json()
    # 外部 SearchResponse 字段集稳定（前端无需修改）。
    assert set(body.keys()) == {
        "query_session_id",
        "candidates",
        "results",
        "low_confidence_candidates",
        "risk_hints",
        "coverage",
        "degraded",
        "degraded_reasons",
        "retrieval_duration_ms",
        "timings",
    }
    assert body["results"][0]["retrieval_source"] == [ORIGINAL_VECTOR_SOURCE]
    assert body["results"][0]["source_anchors"][0]["anchor_type"] == "result"
    assert body["coverage"]["search_mode"] == "standard"
    assert body["coverage"]["total_candidate_count"] == 1
    assert body["timings"]["total_duration_ms"] >= 0
    # candidates 与 results 仍是同一映射（兼容旧字段）。
    assert body["candidates"] == body["results"]


def test_search_routes_main_path_through_internal_service(monkeypatch):
    """运行时 spy：主路径必须经 InternalSearchService.execute 执行（不绕过服务）。"""
    _install_services(monkeypatch)
    calls: list[str] = []
    original_execute = search_api.InternalSearchService.execute

    def _spy_execute(self, request, **kwargs):
        calls.append(getattr(request.profile, "query_text", None) or "")
        return original_execute(self, request, **kwargs)

    monkeypatch.setattr(search_api.InternalSearchService, "execute", _spy_execute)

    response = client.post("/api/search", json={"query": "夜间进入店铺盗窃现金5000元", "limit": 1})

    assert response.status_code == 200
    assert len(calls) == 1  # 主路径只经服务执行一次


# --- parity：错误码 / 降级 ---------------------------------------------------

def test_query_validation_error_returns_400(monkeypatch):
    _install_services(monkeypatch)
    response = client.post("/api/search", json={"query": "。。。", "limit": 1})
    assert response.status_code == 400
    assert response.json()["error"]["code"] in {"QUERY_PUNCTUATION_ONLY", "QUERY_EMPTY", "QUERY_TOO_SHORT"}


def test_query_too_long_returns_413(monkeypatch):
    config = _install_services(monkeypatch)
    too_long = "盗" * (config.QUERY_MAX_LENGTH + 5)
    response = client.post("/api/search", json={"query": too_long, "limit": 1})
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "QUERY_TOO_LONG"


def test_retrieval_exception_returns_503(monkeypatch):
    _install_services(monkeypatch, retrieval=ExplodingRetrievalService())
    response = client.post("/api/search", json={"query": "夜间进入店铺盗窃现金5000元", "limit": 1})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "SEARCH_RETRIEVAL_FAILED"


def test_summary_exception_does_not_break_search(monkeypatch):
    _install_services(monkeypatch, summary=ExplodingSummaryService())
    response = client.post("/api/search", json={"query": "夜间进入店铺盗窃现金5000元", "limit": 1})
    assert response.status_code == 200
    body = response.json()
    assert "SUMMARY_LLM_UNAVAILABLE" in body["degraded_reasons"]
    # summary 异常时占位，不泄露任何正文。
    assert body["results"][0]["summary"] is None


def test_expanded_search_disabled_returns_403_without_retrieval(monkeypatch):
    retrieval = ExplodingRetrievalService()
    _install_services(monkeypatch, retrieval=retrieval)
    response = client.post("/api/search/expand", json={"query": "扩展检索不得执行", "limit": 5})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "EXPANDED_SEARCH_DISABLED"


# --- parity：日志脱敏 --------------------------------------------------------

def test_logs_do_not_include_query_text(caplog, monkeypatch):
    _install_services(monkeypatch)
    caplog.set_level(logging.INFO, logger="case_search")
    raw_query = "RAW_QUERY_PARITY_SENTINEL_SHOULD_NOT_APPEAR"
    response = client.post("/api/search", json={"query": raw_query, "limit": 1})
    assert response.status_code == 200
    assert raw_query not in caplog.text
    assert "CHUNK_BODY_SENTINEL" not in caplog.text
    assert "test-key" not in caplog.text


# --- 静态：api/search.py 主路径不再复制检索编排 ------------------------------

def _api_search_ast() -> ast.Module:
    return ast.parse(API_SEARCH_MODULE.read_text(encoding="utf-8"))


def test_api_search_does_not_duplicate_retrieval_orchestration():
    """api/search.py 不得再直接调用底层编排（merge/split），主路径单一权威在内核服务。"""
    src = API_SEARCH_MODULE.read_text(encoding="utf-8")
    # 主路径编排符号不应在 API 层被直接调用（已收敛到 InternalSearchService）。
    assert "merge_case_candidates(" not in src
    assert "split_low_confidence_candidates(" not in src
    # 仍必须经内部服务执行。
    assert "InternalSearchService" in src
    assert ".execute(" in src


def test_api_search_imports_only_kernel_public_face():
    """导入方向正确：api/search.py 只从 app.kernel(.rag) 或 app.* 应用层导入，不深引内核私有子模块。"""
    tree = _api_search_ast()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            mod = node.module
            if mod.startswith("app.kernel"):
                # 只允许从 app.kernel 或 app.kernel.rag 公开面导入。
                assert mod in {"app.kernel", "app.kernel.rag"}, f"deep kernel import: {mod}"
