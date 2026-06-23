from __future__ import annotations

from fastapi.testclient import TestClient

from app.api import search as search_api
from app.core.config import Settings
from app.main import app
from app.query_processing import QueryProcessingService
from app.rerank import FactSimilarityReranker
from app.retrieval.embedding_cache import QueryEmbeddingCache
from app.retrieval.models import RetrievalDependencyError, RetrievedChunk
from app.retrieval.service import (
    BM25_FALLBACK_USED,
    CHROMA_QUERY_TIMEOUT,
    EMBEDDING_TIMEOUT,
    ORIGINAL_VECTOR_SOURCE,
    VectorRetrievalService,
)
from app.summary import HighlightItem, ResultPresentation, SummaryItem, SummaryService


client = TestClient(app)


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
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail

    def query(self, embedding: list[float], *, top_k: int, retrieval_source: str) -> list[RetrievedChunk]:
        if self.fail:
            raise RetrievalDependencyError("CHROMA_QUERY_TIMEOUT", "timed out")
        return [_chunk(retrieval_source=retrieval_source, vector_score=0.88)]


class FakeFallbackRetriever:
    def search(self, query_text: str, *, top_k: int, retrieval_source: str) -> list[RetrievedChunk]:
        return [_chunk(retrieval_source=retrieval_source, vector_score=0.66)]


def _install_vector_service(monkeypatch, *, embedding_fails: bool = False, chroma_fails: bool = False) -> None:
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
        VectorRetrievalService(
            embedding_client=FakeEmbeddingClient(fail=embedding_fails),
            vector_store=FakeVectorStore(fail=chroma_fails),
            fallback_retriever=FakeFallbackRetriever(),
            embedding_cache=QueryEmbeddingCache(ttl_seconds=60, max_entries=8),
            min_candidates_before_relaxed_recall=1,
        ),
    )
    monkeypatch.setattr(search_api, "rerank_service", FactSimilarityReranker(config=config))
    monkeypatch.setattr(search_api, "summary_service", SummaryService(config=config))


def test_search_api_smoke_normal_vector_recall(monkeypatch):
    _install_vector_service(monkeypatch)

    response = client.post("/api/search", json={"query": "夜间进入店铺盗窃现金5000元", "limit": 1})

    assert response.status_code == 200
    body = response.json()
    assert body["results"][0]["retrieval_source"] == [ORIGINAL_VECTOR_SOURCE]
    assert EMBEDDING_TIMEOUT not in body["degraded_reasons"]
    assert BM25_FALLBACK_USED not in body["degraded_reasons"]
    assert body["timings"]["embedding_duration_ms"] >= 0
    assert body["timings"]["retrieval_duration_ms"] >= 0
    assert body["timings"]["total_duration_ms"] >= 0
    assert body["coverage"] == {
        "data_source": "smoke-source",
        "data_until": "unknown",
        "index_version": "case_chunks_bge_m3_v1",
        "total_candidate_count": 1,
        "search_mode": "standard",
        "degraded_reasons": [
            "QUERY_REWRITE_DISABLED",
            "SUMMARY_DISABLED",
            "DATA_UNTIL_UNKNOWN",
        ],
    }


def test_search_api_returns_source_anchors_for_visible_processed_fields(monkeypatch):
    _install_vector_service(monkeypatch)

    response = client.post("/api/search", json={"query": "夜间进入店铺盗窃现金5000元", "limit": 1})

    assert response.status_code == 200
    result = response.json()["results"][0]
    assert result["source_anchors"][0] == {
        "case_id": "case-smoke",
        "source_chunk_id": f"{ORIGINAL_VECTOR_SOURCE}-chunk",
        "chunk_type": "court_found",
        "anchor_type": "result",
        "source_url": None,
        "source_ref": "smoke-source",
    }
    assert result["summary"]["source_anchors"][0]["anchor_type"] == "summary"
    assert result["summary"]["source_anchors"][0]["case_id"] == "case-smoke"
    assert result["summary"]["source_anchors"][0]["source_chunk_id"] == f"{ORIGINAL_VECTOR_SOURCE}-chunk"
    assert result["highlights"]
    assert all(item["source_anchors"][0]["anchor_type"] == "highlight" for item in result["highlights"])
    assert all(item["source_anchors"][0]["case_id"] == "case-smoke" for item in result["highlights"])


def test_search_api_filters_unanchored_generated_summary_and_highlights(monkeypatch):
    _install_vector_service(monkeypatch)
    monkeypatch.setattr(search_api, "summary_service", UnsafeSummaryService())

    response = client.post("/api/search", json={"query": "夜间进入店铺盗窃现金5000元", "limit": 1})

    assert response.status_code == 200
    result = response.json()["results"][0]
    assert result["source_anchors"]
    assert result["summary"] is None
    assert result["highlights"] == []


def test_search_api_logs_do_not_include_query_or_chunk_body(caplog, monkeypatch):
    _install_vector_service(monkeypatch)
    raw_query = "RAW_QUERY_SENTINEL_SHOULD_NOT_APPEAR"

    response = client.post("/api/search", json={"query": raw_query, "limit": 1})

    assert response.status_code == 200
    assert raw_query not in caplog.text
    assert "CHUNK_BODY_SENTINEL_SHOULD_NOT_APPEAR" not in caplog.text


def test_search_api_smoke_embedding_timeout_returns_bm25(monkeypatch):
    _install_vector_service(monkeypatch, embedding_fails=True)

    response = client.post("/api/search", json={"query": "夜间进入店铺盗窃现金5000元", "limit": 1})

    assert response.status_code == 200
    body = response.json()
    assert EMBEDDING_TIMEOUT in body["degraded_reasons"]
    assert BM25_FALLBACK_USED in body["degraded_reasons"]
    assert body["results"][0]["retrieval_source"] == ["bm25_fallback"]
    assert body["timings"]["embedding_duration_ms"] >= 0
    assert body["timings"]["retrieval_duration_ms"] >= 0
    assert body["timings"]["total_duration_ms"] >= 0
    assert body["coverage"]["data_source"] == "smoke-source"
    assert body["coverage"]["data_until"] == "unknown"
    assert body["coverage"]["index_version"] == "unknown"
    assert body["coverage"]["total_candidate_count"] == 1
    assert body["coverage"]["search_mode"] == "standard"
    assert "INDEX_VERSION_UNKNOWN" in body["coverage"]["degraded_reasons"]


def test_search_api_smoke_chroma_timeout_returns_bm25(monkeypatch):
    _install_vector_service(monkeypatch, chroma_fails=True)

    response = client.post("/api/search", json={"query": "夜间进入店铺盗窃现金5000元", "limit": 1})

    assert response.status_code == 200
    body = response.json()
    assert CHROMA_QUERY_TIMEOUT in body["degraded_reasons"]
    assert BM25_FALLBACK_USED in body["degraded_reasons"]
    assert body["results"][0]["retrieval_source"] == ["bm25_fallback"]
    assert body["timings"]["embedding_duration_ms"] >= 0
    assert body["timings"]["retrieval_duration_ms"] >= 0
    assert body["timings"]["total_duration_ms"] >= 0


def _chunk(*, retrieval_source: str, vector_score: float) -> RetrievedChunk:
    return RetrievedChunk(
        case_id="case-smoke",
        chunk_id=f"{retrieval_source}-chunk",
        score=vector_score,
        vector_score=vector_score,
        distance=1.0 - vector_score,
        metadata={
            "case_id": "case-smoke",
            "chunk_id": f"{retrieval_source}-chunk",
            "title": "盗窃罪测试判决书",
            "case_cause": "盗窃罪",
            "chunk_type": "court_found",
            "court": "测试法院",
            "trial_level": "一审",
            "judgment_date": "2020-01-02",
            "source_name": "smoke-source",
        },
        text="CHUNK_BODY_SENTINEL_SHOULD_NOT_APPEAR。本院查明,被告人夜间进入店铺盗窃现金5000元。",
        source="smoke-test",
        retrieval_source=retrieval_source,
    )


class UnsafeSummaryService:
    def build_presentations(self, *_args, **_kwargs):
        return [
            ResultPresentation(
                summary=SummaryItem(
                    text="UNANCHORED_GENERATED_SUMMARY",
                    source_chunk_id="",
                    source_case_id="case-smoke",
                    method="llm_deepseek",
                ),
                highlights=[
                    HighlightItem(
                        text="UNANCHORED_GENERATED_HIGHLIGHT",
                        source_chunk_id="",
                    )
                ],
            )
        ]
