from __future__ import annotations

import re
import logging

import pytest
from fastapi.testclient import TestClient

from app.api import events as events_api
from app.api import search as search_api
from app.core.config import Settings
from app.main import app
from app.query_processing import QueryProcessingService
from app.rerank import FactSimilarityReranker
from app.retrieval.bm25_fallback import BM25_FALLBACK_SOURCE, BM25_RELAXED_RECALL_SOURCE
from app.retrieval.models import VectorCandidate, VectorRetrievalResult
from app.retrieval.service import BM25_FALLBACK_USED, CHROMA_QUERY_FAILED, ORIGINAL_VECTOR_SOURCE, VARIANT_VECTOR_SOURCE
from app.summary import SummaryService

client = TestClient(app)


class FakeRetrievalService:
    def __init__(
        self,
        *,
        candidates: list[VectorCandidate] | None = None,
        expanded_candidates: list[VectorCandidate] | None = None,
        degraded: bool = False,
        degraded_reasons: list[str] | None = None,
    ) -> None:
        self.candidates = candidates if candidates is not None else _standard_candidates()
        self.expanded_candidates = expanded_candidates
        self.degraded = degraded
        self.degraded_reasons = degraded_reasons or []
        self.calls: list[dict[str, object]] = []

    def retrieve(self, query_plan, *, include_relaxed_recall: bool = False) -> VectorRetrievalResult:
        self.calls.append(
            {
                "input_hash": query_plan.input_hash,
                "include_relaxed_recall": include_relaxed_recall,
            }
        )
        candidates = self.candidates
        if include_relaxed_recall:
            candidates = self.expanded_candidates if self.expanded_candidates is not None else _expanded_candidates()
        return VectorRetrievalResult(
            candidates=candidates,
            embedding_duration_ms=3,
            retrieval_duration_ms=7,
            degraded=self.degraded,
            degraded_reasons=list(self.degraded_reasons),
        )


def _candidate(
    *,
    case_id: str = "case-1",
    chunk_id: str = "case-1-c1",
    score: float = 0.8,
    retrieval_source: str = ORIGINAL_VECTOR_SOURCE,
    matched_text: str = "本院查明,被告人夜间进入店铺盗窃现金。",
    metadata: dict | None = None,
) -> VectorCandidate:
    return VectorCandidate(
        case_id=case_id,
        chunk_id=chunk_id,
        vector_score=score,
        retrieval_score=score,
        retrieval_source=retrieval_source,
        metadata=metadata
        or {
            "case_id": case_id,
            "chunk_id": chunk_id,
            "title": f"{case_id}刑事判决书",
            "case_no": f"({case_id})号",
            "court": "测试法院",
            "trial_level": "一审",
            "case_cause": "盗窃罪",
            "judgment_date": "2020-01-02",
            "source_url": "https://example.test/case",
        },
        matched_text=matched_text,
        source="fake-retrieval",
    )


def _standard_candidates() -> list[VectorCandidate]:
    return [
        _candidate(case_id="case-dup", chunk_id="case-dup-low", score=0.42, retrieval_source=ORIGINAL_VECTOR_SOURCE),
        _candidate(
            case_id="case-dup",
            chunk_id="case-dup-high",
            score=0.91,
            retrieval_source=VARIANT_VECTOR_SOURCE,
            matched_text="本院查明,被告人夜间入户盗窃现金及手机。",
        ),
        _candidate(case_id="case-other", chunk_id="case-other-c1", score=0.63, retrieval_source=ORIGINAL_VECTOR_SOURCE),
    ]


def _expanded_candidates() -> list[VectorCandidate]:
    return [
        *_standard_candidates(),
        _candidate(
            case_id="case-expanded",
            chunk_id="case-expanded-c1",
            score=0.37,
            retrieval_source=BM25_RELAXED_RECALL_SOURCE,
            matched_text="被告人进入商铺盗窃少量财物，事实相关度较低。",
        ),
    ]


def _install_search_services(
    monkeypatch,
    retrieval_service: FakeRetrievalService | None = None,
    *,
    enable_weighted_rerank: bool = False,
    enable_expanded_search: bool = True,
):
    config = Settings(
        DEEPSEEK_API_KEY="test-key",
        ENABLE_QUERY_REWRITE=False,
        ENABLE_SUMMARY=False,
        ENABLE_WEIGHTED_RERANK=enable_weighted_rerank,
        ENABLE_EXPANDED_SEARCH=enable_expanded_search,
    )
    service = QueryProcessingService(
        config=config,
    )
    monkeypatch.setattr(search_api.settings, "ENABLE_EXPANDED_SEARCH", enable_expanded_search)
    monkeypatch.setattr(search_api, "query_processing_service", service)
    fake_retrieval = retrieval_service or FakeRetrievalService()
    monkeypatch.setattr(search_api, "retrieval_service", fake_retrieval)
    monkeypatch.setattr(search_api, "rerank_service", FactSimilarityReranker(config=config))
    monkeypatch.setattr(search_api, "summary_service", SummaryService(config=config))
    return fake_retrieval


def test_openapi_and_day1_routes_exist():
    resp = client.get("/openapi.json")
    assert resp.status_code == 200
    paths = resp.json()["paths"]
    assert "/api/search" in paths
    assert "/api/search/expand" in paths
    assert "/api/cases/{case_id}" in paths
    assert "/api/events" in paths
    assert "/health" in paths


def test_swagger_docs_are_accessible():
    resp = client.get("/docs")

    assert resp.status_code == 200
    assert "Swagger UI" in resp.text


def test_search_returns_real_candidates_with_query_session_id(caplog, monkeypatch):
    fake_retrieval = _install_search_services(monkeypatch)
    caplog.set_level(logging.INFO, logger="case_search")
    raw_query = "这是不会进入日志的原始案情文本ABC123"
    resp = client.post("/api/search", json={"query": raw_query, "limit": 10})

    assert resp.status_code == 200
    body = resp.json()
    query_session_id = body["query_session_id"]
    assert re.match(r"^qs_\d{14}_[0-9a-f]{12}$", query_session_id)
    assert fake_retrieval.calls[0]["include_relaxed_recall"] is False
    assert re.match(r"^[0-9a-f]{64}$", str(fake_retrieval.calls[0]["input_hash"]))
    assert body["retrieval_duration_ms"] == 7
    assert body["timings"]["retrieval_duration_ms"] == 7
    assert body["timings"]["embedding_duration_ms"] == 3
    assert "rerank_duration_ms" in body["timings"]
    assert "summary_duration_ms" in body["timings"]
    assert len(body["results"]) == 2
    case_ids = [item["case_id"] for item in body["results"]]
    assert case_ids == ["case-dup", "case-other"]
    assert len(case_ids) == len(set(case_ids))
    top = body["results"][0]
    assert top["top_chunk_id"] == "case-dup-high"
    assert top["chunk_id"] == "case-dup-high"
    assert top["source_chunk_ids"] == ["case-dup-high", "case-dup-low"]
    assert top["hit_chunk_ids"] == ["case-dup-high", "case-dup-low"]
    assert top["vector_score"] == 0.91
    assert top["retrieval_score"] == 0.91
    assert top["final_score"] == 0.91
    assert top["similarity_score"] == 0.91
    assert {
        "vector_similarity",
        "legal_element_overlap",
        "case_cause_match",
        "key_paragraph_match",
        "authority_signal",
    }.issubset(top["score_breakdown"])
    assert top["score_breakdown"]["weighted_rerank_enabled"] is False
    assert top["score_breakdown"]["score_mode"] == "base_retrieval"
    assert top["retrieval_source"] == [ORIGINAL_VECTOR_SOURCE, VARIANT_VECTOR_SOURCE]
    assert top["metadata"]["title"] == "case-dup刑事判决书"
    assert top["matched_text"] == "本院查明,被告人夜间入户盗窃现金及手机。"
    assert top["summary"]["source_chunk_id"] == "case-dup-high"
    assert top["summary"]["source_case_id"] == "case-dup"
    assert top["summary"]["method"] == "source_snippet"
    assert top["summary"]["text"]
    assert top["highlights"]
    assert top["highlights"][0]["source_chunk_id"] == "case-dup-high"
    assert raw_query not in caplog.text
    assert top["matched_text"] not in caplog.text
    assert "test-key" not in caplog.text
    assert query_session_id in caplog.text
    assert "input_hash=" in caplog.text
    assert "QUERY_REWRITE_DISABLED" in caplog.text
    assert "rewrite_duration_ms" in caplog.text
    assert "retrieval_duration_ms" in caplog.text
    assert "rerank_duration_ms" in caplog.text
    assert "summary_duration_ms" in caplog.text
    assert "total_duration_ms" in caplog.text


def test_search_uses_weighted_rerank_when_enabled(monkeypatch):
    _install_search_services(
        monkeypatch,
        FakeRetrievalService(
            candidates=[
                _candidate(
                    case_id="plain-high-vector",
                    chunk_id="plain-high-vector-c1",
                    score=0.8,
                    retrieval_source=ORIGINAL_VECTOR_SOURCE,
                    matched_text="普通段落,合同履行争议。",
                    metadata={"case_id": "plain-high-vector", "chunk_id": "plain-high-vector-c1"},
                ),
                _candidate(
                    case_id="key-lower-vector",
                    chunk_id="key-lower-vector-c1",
                    score=0.69,
                    retrieval_source=ORIGINAL_VECTOR_SOURCE,
                    matched_text="本院查明,程序性审理意见。",
                    metadata={
                        "case_id": "key-lower-vector",
                        "chunk_id": "key-lower-vector-c1",
                        "court_level": "高级法院",
                        "trial_level": "二审",
                        "judgment_date": "2021-01-01",
                    },
                ),
            ],
        ),
        enable_weighted_rerank=True,
    )

    resp = client.post("/api/search", json={"query": "夜间进入店铺盗窃现金5000元", "limit": 2})

    assert resp.status_code == 200
    body = resp.json()
    assert body["results"][0]["case_id"] == "plain-high-vector"
    assert body["results"][1]["case_id"] == "key-lower-vector"
    assert body["results"][1]["final_score"] == body["results"][1]["retrieval_score"]
    assert body["results"][0]["score_breakdown"]["weighted_rerank_enabled"] is True
    assert body["results"][0]["score_breakdown"]["score_mode"] == "weighted_rerank"
    assert body["results"][1]["score_breakdown"]["final_score_source"] == "base_retrieval_guard"
    assert "key_paragraph_without_fact_support" in body["results"][1]["score_breakdown"]["fusion_guards"]
    assert "rerank_duration_ms" in body["timings"]
    assert "summary_duration_ms" in body["timings"]


def test_search_expand_returns_expanded_or_low_confidence_candidates(monkeypatch):
    fake_retrieval = _install_search_services(monkeypatch)
    resp = client.post("/api/search/expand", json={"query": "扩展检索请求", "limit": 5})

    assert resp.status_code == 200
    body = resp.json()
    assert body["query_session_id"].startswith("qs_")
    assert fake_retrieval.calls[0]["include_relaxed_recall"] is True
    expanded = next(item for item in body["results"] if item["case_id"] == "case-expanded")
    assert expanded["retrieval_source"] == [BM25_RELAXED_RECALL_SOURCE]
    assert expanded["fallback_score"] == 0.37
    assert expanded["confidence"] == "low"
    assert expanded["summary"]["source_chunk_id"] == "case-expanded-c1"
    assert expanded["highlights"][0]["source_chunk_id"] == "case-expanded-c1"


def test_search_expand_respects_feature_flag_without_retrieval(monkeypatch):
    fake_retrieval = _install_search_services(monkeypatch, enable_expanded_search=False)

    resp = client.post("/api/search/expand", json={"query": "扩展检索请求", "limit": 5})

    assert resp.status_code == 403
    assert fake_retrieval.calls == []
    body = resp.json()
    assert body["error"]["code"] == "EXPANDED_SEARCH_DISABLED"
    assert body["error"]["query_session_id"].startswith("qs_")


def test_search_validation_error_uses_unified_error_shape(monkeypatch):
    _install_search_services(monkeypatch)
    resp = client.post("/api/search", json={"query": "   "})

    assert resp.status_code == 400
    body = resp.json()
    assert body == {
        "error": {
            "code": "QUERY_EMPTY",
            "message": "query 不能为空，请输入需要检索的案情或关键事实。",
            "query_session_id": body["error"]["query_session_id"],
        }
    }
    assert body["error"]["query_session_id"].startswith("qs_")


@pytest.mark.parametrize(
    ("query", "expected_status", "expected_code"),
    [
        ("，，！！；；", 400, "QUERY_PUNCTUATION_ONLY"),
        ("甲", 400, "QUERY_TOO_SHORT"),
        ("盗" * 5001, 413, "QUERY_TOO_LONG"),
    ],
)
def test_search_validation_boundaries_use_unified_error_shape(
    monkeypatch,
    query: str,
    expected_status: int,
    expected_code: str,
):
    _install_search_services(monkeypatch)

    resp = client.post("/api/search", json={"query": query})

    assert resp.status_code == expected_status
    body = resp.json()
    assert set(body.keys()) == {"error"}
    assert body["error"]["code"] == expected_code
    assert body["error"]["query_session_id"].startswith("qs_")
    assert "results" not in body


class RaisingRetrievalService:
    def retrieve(self, query_plan, *, include_relaxed_recall: bool = False):
        raise RuntimeError("dependency failed with sanitized boundary")


def test_search_retrieval_failure_uses_unified_error_with_query_session_id(caplog, monkeypatch):
    config = Settings(DEEPSEEK_API_KEY="test-key", ENABLE_QUERY_REWRITE=False)
    monkeypatch.setattr(search_api, "query_processing_service", QueryProcessingService(config=config))
    monkeypatch.setattr(search_api, "retrieval_service", RaisingRetrievalService())
    caplog.set_level(logging.ERROR, logger="case_search")
    raw_query = "不能出现在失败日志里的原始案情ABC123"

    resp = client.post("/api/search", json={"query": raw_query})

    assert resp.status_code == 503
    body = resp.json()
    assert body["error"]["code"] == "SEARCH_RETRIEVAL_FAILED"
    assert body["error"]["query_session_id"].startswith("qs_")
    assert raw_query not in caplog.text
    assert "test-key" not in caplog.text
    assert "input_hash=" in caplog.text


def test_search_degraded_reasons_are_exposed(monkeypatch):
    _install_search_services(
        monkeypatch,
        FakeRetrievalService(
            candidates=[
                _candidate(
                    case_id="case-fallback",
                    chunk_id="case-fallback-c1",
                    score=0.77,
                    retrieval_source=BM25_FALLBACK_SOURCE,
                )
            ],
            degraded=True,
            degraded_reasons=[CHROMA_QUERY_FAILED, BM25_FALLBACK_USED],
        ),
    )

    resp = client.post("/api/search", json={"query": "夜间进入店铺盗窃现金5000元"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["degraded"] is True
    assert body["degraded_reasons"] == [
        "QUERY_REWRITE_DISABLED",
        CHROMA_QUERY_FAILED,
        BM25_FALLBACK_USED,
        "SUMMARY_DISABLED",
    ]
    result = body["results"][0]
    assert result["case_id"] == "case-fallback"
    assert result["fallback_score"] == 0.77
    assert result["vector_score"] is None
    assert result["retrieval_source"] == [BM25_FALLBACK_SOURCE]


def test_search_does_not_fabricate_results_when_retrieval_is_empty(monkeypatch):
    _install_search_services(monkeypatch, FakeRetrievalService(candidates=[]))

    resp = client.post("/api/search", json={"query": "夜间进入店铺盗窃现金5000元"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["results"] == []
    assert body["candidates"] == []


def test_events_accepts_safe_metadata_and_rejects_sensitive_metadata(caplog):
    caplog.set_level(logging.INFO, logger="case_search")
    safe = client.post(
        "/api/events",
        json={
            "event_name": "search_submit",
            "query_session_id": "qs_test",
            "metadata": {"result_count": 0, "client": "web"},
        },
    )
    assert safe.status_code == 202
    assert safe.json()["accepted"] is True

    raw_query = "不得记录的案情全文"
    rejected = client.post(
        "/api/events",
        json={
            "event_name": "search_submit",
            "query_session_id": "qs_test",
            "metadata": {"query": raw_query},
        },
    )
    assert rejected.status_code == 400
    assert rejected.json()["error"]["code"] == "SENSITIVE_EVENT_METADATA"
    assert raw_query not in caplog.text
    assert "query" in caplog.text


@pytest.mark.parametrize(
    "metadata",
    [
        {"raw_text": "不得进入埋点的原文A"},
        {"content": "不得进入埋点的正文B"},
        {"filters": {"raw_query": "不得进入埋点的嵌套queryC"}},
        {"items": [{"content": "不得进入埋点的列表contentD"}]},
    ],
)
def test_events_rejects_sensitive_metadata_keys_without_logging_values(caplog, metadata):
    caplog.set_level(logging.INFO, logger="case_search")

    resp = client.post(
        "/api/events",
        json={
            "event_name": "search_submit",
            "query_session_id": "qs_test",
            "metadata": metadata,
        },
    )

    assert resp.status_code == 400
    body = resp.json()
    assert body["error"]["code"] == "SENSITIVE_EVENT_METADATA"
    assert body["error"]["query_session_id"] == "qs_test"
    serialized_values = repr(metadata)
    for value in ["原文A", "正文B", "嵌套queryC", "列表contentD"]:
        assert value not in caplog.text
        assert value not in resp.text
    assert serialized_values not in caplog.text


def test_events_db_unreachable_returns_degraded_without_logging_metadata_values(caplog, monkeypatch):
    caplog.set_level(logging.WARNING, logger="case_search")
    monkeypatch.setattr(events_api, "_event_db_status", lambda: (False, "connection_failed"))
    metadata = {
        "client": "safe-client-value-that-must-not-be-logged",
        "input_hash": "sha256:test",
        "input_length": 18,
    }

    resp = client.post(
        "/api/events",
        json={
            "event_name": "search_result_render",
            "query_session_id": "qs_test",
            "metadata": metadata,
        },
    )

    body = resp.json()
    assert resp.status_code == 202
    assert body["accepted"] is True
    assert body["degraded"] is True
    assert body["degraded_reasons"] == [events_api.EVENT_DB_DEGRADED_REASON]
    assert "connection_failed" in caplog.text
    assert "client" in caplog.text
    assert "input_hash" in caplog.text
    assert "safe-client-value-that-must-not-be-logged" not in caplog.text
    assert "safe-client-value-that-must-not-be-logged" not in resp.text


def test_case_detail_route_uses_unified_error_for_missing_case():
    resp = client.get("/api/cases/not-a-real-case-id")

    assert resp.status_code == 404
    body = resp.json()
    assert body["error"]["code"] == "CASE_NOT_FOUND"
    assert "query_session_id" in body["error"]


def test_health_includes_day1_dependency_fields():
    resp = client.get("/health")

    assert resp.status_code == 200
    body = resp.json()
    assert "DEEPSEEK_API_KEY" in body["secrets_present"]
    assert "ollama_reachable" in body
    assert "chroma_collection_queryable" in body
    assert "chroma_chunk_count" in body
    assert "dependencies" in body
    assert "ollama" in body["dependencies"]
    assert "chroma" in body["dependencies"]


def test_health_does_not_leak_secret_values(monkeypatch):
    secret_value = "sk-test-secret-value-that-must-not-leak"
    monkeypatch.setattr("app.api.health.settings", Settings(DEEPSEEK_API_KEY=secret_value))
    monkeypatch.setattr("app.api.health.check_secrets_present", lambda: {"DEEPSEEK_API_KEY": True})
    monkeypatch.setattr("app.api.health._ollama_status", lambda: {"reachable": False, "model_available": False})
    monkeypatch.setattr(
        "app.api.health._chroma_status",
        lambda: {"queryable": False, "chunk_count": 0, "degraded_reason": "mocked"},
    )
    monkeypatch.setattr("app.api.health._chroma_dir_writable", lambda: False)
    monkeypatch.setattr("app.api.health._db_reachable", lambda: False)

    resp = client.get("/health")

    assert resp.status_code == 200
    assert secret_value not in resp.text
    assert resp.json()["secrets_present"]["DEEPSEEK_API_KEY"] is True
