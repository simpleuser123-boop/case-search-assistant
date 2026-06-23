from __future__ import annotations

import json
import logging

from fastapi.testclient import TestClient

from app.api import search as search_api
from app.core.config import Settings
from app.main import app
from app.query_processing import QueryProcessingService
from app.query_processing.service import QUERY_REWRITE_DISABLED
from app.rerank import FactSimilarityReranker
from app.retrieval.models import VectorCandidate, VectorRetrievalResult
from app.retrieval.service import ORIGINAL_VECTOR_SOURCE
from app.summary import SUMMARY_DISABLED, SummaryService

client = TestClient(app)


class FakeRewriteClient:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def rewrite_query(self, cleaned_query: str) -> str:
        self.calls.append(cleaned_query)
        return json.dumps(
            {
                "legal_elements": ["盗窃现金", "退赔谅解"],
                "query_variants": [
                    f"{cleaned_query} 类案 相似事实",
                    f"{cleaned_query} 裁判文书 同类事实",
                ],
                "case_cause_hint": "盗窃罪",
                "confidence": 0.8,
                "notes": "保留核心事实。",
            },
            ensure_ascii=False,
        )


class FakeSummaryClient:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def summarize_chunk(
        self,
        *,
        chunk_excerpt: str,
        source_chunk_id: str,
        query_terms: list[str],
        case_cause_hint: str,
    ) -> str:
        self.calls.append(source_chunk_id)
        return json.dumps({"text": "被告人盗窃现金后退赔并取得谅解。"}, ensure_ascii=False)


class FakeRetrievalService:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self.rebuild_index_calls = 0

    def retrieve(self, query_plan, *, include_relaxed_recall: bool = False) -> VectorRetrievalResult:
        self.calls.append(
            {
                "queries": list(query_plan.queries),
                "input_hash": query_plan.input_hash,
                "include_relaxed_recall": include_relaxed_recall,
            }
        )
        return VectorRetrievalResult(
            candidates=[
                _candidate(
                    case_id="case-high-base",
                    chunk_id="case-high-base-c1",
                    score=0.92,
                    matched_text="普通段落,被告人盗窃现金后退赔。",
                ),
                _candidate(
                    case_id="case-low-base",
                    chunk_id="case-low-base-c1",
                    score=0.62,
                    matched_text="本院查明,被告人盗窃现金后退赔并取得谅解。",
                ),
            ],
            embedding_duration_ms=1,
            retrieval_duration_ms=2,
        )

    def rebuild_index(self) -> None:
        self.rebuild_index_calls += 1
        raise AssertionError("rollback must not rebuild the index")


def _candidate(*, case_id: str, chunk_id: str, score: float, matched_text: str) -> VectorCandidate:
    return VectorCandidate(
        case_id=case_id,
        chunk_id=chunk_id,
        vector_score=score,
        retrieval_score=score,
        retrieval_source=ORIGINAL_VECTOR_SOURCE,
        metadata={
            "case_id": case_id,
            "chunk_id": chunk_id,
            "title": f"{case_id}判决书",
            "court": "测试法院",
            "trial_level": "一审",
            "case_cause": "盗窃罪",
            "judgment_date": "2024-01-01",
        },
        matched_text=matched_text,
        source="rollback-test",
    )


def _install_services(monkeypatch, config: Settings):
    rewrite_client = FakeRewriteClient()
    retrieval_service = FakeRetrievalService()
    summary_client = FakeSummaryClient()
    monkeypatch.setattr(search_api, "settings", config)
    monkeypatch.setattr(
        search_api,
        "query_processing_service",
        QueryProcessingService(config=config, rewrite_client=rewrite_client),
    )
    monkeypatch.setattr(search_api, "retrieval_service", retrieval_service)
    monkeypatch.setattr(search_api, "rerank_service", FactSimilarityReranker(config=config))
    monkeypatch.setattr(
        search_api,
        "summary_service",
        SummaryService(config=config, summary_client=summary_client),
    )
    return rewrite_client, retrieval_service, summary_client


def _settings(**overrides) -> Settings:
    values = {
        "DEEPSEEK_API_KEY": "test-key",
        "ENABLE_QUERY_REWRITE": True,
        "ENABLE_WEIGHTED_RERANK": True,
        "ENABLE_SUMMARY": True,
        "ENABLE_EXPANDED_SEARCH": True,
    }
    values.update(overrides)
    return Settings(**values)


def test_query_rewrite_rollback_uses_original_query_and_logs_event(caplog, monkeypatch):
    raw_query = "不得进入日志的原始案情ABC123,夜间盗窃现金后退赔"
    _, retrieval_service, _ = _install_services(
        monkeypatch,
        _settings(ENABLE_QUERY_REWRITE=False),
    )
    caplog.set_level(logging.INFO, logger="case_search")

    resp = client.post("/api/search", json={"query": raw_query, "limit": 2})

    assert resp.status_code == 200
    body = resp.json()
    assert QUERY_REWRITE_DISABLED in body["degraded_reasons"]
    assert retrieval_service.calls[0]["queries"] == [raw_query]
    assert "rollback_event" in caplog.text
    assert "ENABLE_QUERY_REWRITE" in caplog.text
    assert "input_hash=" in caplog.text
    assert raw_query not in caplog.text
    assert "test-key" not in caplog.text


def test_weighted_rerank_rollback_returns_base_score_and_logs_event(caplog, monkeypatch):
    raw_query = "夜间盗窃现金后退赔并取得谅解"
    _, retrieval_service, _ = _install_services(monkeypatch, _settings(ENABLE_WEIGHTED_RERANK=False))
    caplog.set_level(logging.INFO, logger="case_search")

    resp = client.post("/api/search", json={"query": raw_query, "limit": 2})

    assert resp.status_code == 200
    assert len(retrieval_service.calls) == 1
    assert retrieval_service.rebuild_index_calls == 0
    top = resp.json()["results"][0]
    assert top["case_id"] == "case-high-base"
    assert top["score_breakdown"]["score_mode"] == "base_retrieval"
    assert top["score_breakdown"]["weighted_rerank_enabled"] is False
    assert top["final_score"] == top["retrieval_score"]
    assert "rollback_event" in caplog.text
    assert "ENABLE_WEIGHTED_RERANK" in caplog.text
    assert raw_query not in caplog.text


def test_summary_rollback_uses_source_snippet_and_logs_event(caplog, monkeypatch):
    raw_query = "夜间盗窃现金后退赔并取得谅解"
    _, _, summary_client = _install_services(monkeypatch, _settings(ENABLE_SUMMARY=False))
    caplog.set_level(logging.INFO, logger="case_search")

    resp = client.post("/api/search", json={"query": raw_query, "limit": 1})

    assert resp.status_code == 200
    body = resp.json()
    result = body["results"][0]
    assert SUMMARY_DISABLED in body["degraded_reasons"]
    assert result["summary"]["method"] == "source_snippet"
    assert result["summary"]["source_chunk_id"] == result["top_chunk_id"]
    assert result["summary"]["degraded_reason"] == SUMMARY_DISABLED
    assert summary_client.calls == []
    assert "rollback_event" in caplog.text
    assert "ENABLE_SUMMARY" in caplog.text
    assert raw_query not in caplog.text


def test_expanded_search_rollback_forbids_expand_without_retrieval_and_logs_event(
    caplog,
    monkeypatch,
):
    raw_query = "扩展检索不得执行的原始案情ABC123"
    _, retrieval_service, _ = _install_services(
        monkeypatch,
        _settings(ENABLE_EXPANDED_SEARCH=False),
    )
    caplog.set_level(logging.INFO, logger="case_search")

    resp = client.post("/api/search/expand", json={"query": raw_query, "limit": 5})

    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "EXPANDED_SEARCH_DISABLED"
    assert retrieval_service.calls == []
    assert "rollback_event" in caplog.text
    assert "ENABLE_EXPANDED_SEARCH" in caplog.text
    assert raw_query not in caplog.text
