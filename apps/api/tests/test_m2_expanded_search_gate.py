from __future__ import annotations

import logging
from pathlib import Path

from fastapi.testclient import TestClient

from app.api import search as search_api
from app.core.config import PROJECT_ROOT, Settings
from app.main import app
from app.query_processing import QueryProcessingService
from app.rerank import FactSimilarityReranker
from app.retrieval.bm25_fallback import BM25_RELAXED_RECALL_SOURCE
from app.retrieval.models import VectorCandidate, VectorRetrievalResult
from app.retrieval.service import ORIGINAL_VECTOR_SOURCE
from app.summary import SummaryService

client = TestClient(app)

ABSOLUTE_RECALL_COPY = (
    "".join(["已", "查全"]),
    "".join(["保证", "无遗漏"]),
    "".join(["查全", "率"]),
)


class FakeRetrievalService:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def retrieve(self, query_plan, *, include_relaxed_recall: bool = False) -> VectorRetrievalResult:
        self.calls.append(
            {
                "input_hash": query_plan.input_hash,
                "include_relaxed_recall": include_relaxed_recall,
            }
        )
        candidates = [_candidate("case-standard", "case-standard-c1", 0.91)]
        if include_relaxed_recall:
            candidates.append(
                _candidate(
                    "case-expanded-low",
                    "case-expanded-low-c1",
                    0.58,
                    retrieval_source=BM25_RELAXED_RECALL_SOURCE,
                    matched_text="CANDIDATE_BODY_SENTINEL_SHOULD_NOT_APPEAR",
                )
            )
        return VectorRetrievalResult(
            candidates=candidates,
            embedding_duration_ms=1,
            retrieval_duration_ms=2,
            degraded=False,
            degraded_reasons=[],
        )


def test_expanded_search_disabled_returns_safe_disabled_state_without_retrieval(
    caplog,
    monkeypatch,
):
    fake_retrieval = _install_services(monkeypatch, enable_expanded_search=False)
    caplog.set_level(logging.INFO, logger="case_search")
    raw_query = "RAW_QUERY_SENTINEL_SHOULD_NOT_APPEAR"

    response = client.post("/api/search/expand", json={"query": raw_query, "limit": 5})

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "EXPANDED_SEARCH_DISABLED"
    assert fake_retrieval.calls == []
    assert "rollback_event" in caplog.text
    assert "ENABLE_EXPANDED_SEARCH" in caplog.text
    assert raw_query not in caplog.text
    assert_no_absolute_recall_copy(response.text)


def test_expanded_search_enabled_returns_expanded_mode_and_sanitized_logs(
    caplog,
    monkeypatch,
):
    fake_retrieval = _install_services(monkeypatch, enable_expanded_search=True)
    caplog.set_level(logging.INFO, logger="case_search")
    raw_query = "RAW_QUERY_SENTINEL_SHOULD_NOT_APPEAR"

    response = client.post("/api/search/expand", json={"query": raw_query, "limit": 10})

    assert response.status_code == 200
    body = response.json()
    assert fake_retrieval.calls == [
        {
            "input_hash": fake_retrieval.calls[0]["input_hash"],
            "include_relaxed_recall": True,
        }
    ]
    assert body["coverage"]["search_mode"] == "expanded"
    assert body["results"][0]["case_id"] == "case-standard"
    assert [item["case_id"] for item in body["low_confidence_candidates"]] == [
        "case-expanded-low"
    ]
    assert raw_query not in caplog.text
    assert "CANDIDATE_BODY_SENTINEL_SHOULD_NOT_APPEAR" not in caplog.text
    assert_no_absolute_recall_copy(response.text)
    assert_no_absolute_recall_copy(caplog.text)


def test_standard_search_is_not_overridden_by_expanded_search(monkeypatch):
    fake_retrieval = _install_services(monkeypatch, enable_expanded_search=True)

    standard_before = client.post("/api/search", json={"query": "标准检索请求A", "limit": 10})
    expanded = client.post("/api/search/expand", json={"query": "扩大复核范围请求B", "limit": 10})
    standard_after = client.post("/api/search", json={"query": "标准检索请求C", "limit": 10})

    assert standard_before.status_code == 200
    assert expanded.status_code == 200
    assert standard_after.status_code == 200
    assert [call["include_relaxed_recall"] for call in fake_retrieval.calls] == [
        False,
        True,
        False,
    ]
    assert standard_before.json()["coverage"]["search_mode"] == "standard"
    assert expanded.json()["coverage"]["search_mode"] == "expanded"
    assert standard_after.json()["coverage"]["search_mode"] == "standard"
    assert [item["case_id"] for item in standard_before.json()["results"]] == [
        "case-standard"
    ]
    assert standard_before.json()["low_confidence_candidates"] == []
    assert [item["case_id"] for item in standard_after.json()["results"]] == [
        "case-standard"
    ]
    assert standard_after.json()["low_confidence_candidates"] == []


def test_expanded_search_flags_remain_default_closed():
    env_values = _env_example_values()

    assert Settings.model_fields["ENABLE_EXPANDED_SEARCH"].default is False
    assert Settings.model_fields["ENABLE_WEIGHTED_RERANK"].default is False
    assert env_values["ENABLE_EXPANDED_SEARCH"] == "false"
    assert env_values["VITE_ENABLE_EXPANDED_SEARCH"] == "false"
    assert env_values["ENABLE_WEIGHTED_RERANK"] == "false"


def _install_services(monkeypatch, *, enable_expanded_search: bool) -> FakeRetrievalService:
    config = Settings(
        DEEPSEEK_API_KEY="test-key",
        ENABLE_QUERY_REWRITE=False,
        ENABLE_SUMMARY=False,
        ENABLE_WEIGHTED_RERANK=False,
        ENABLE_EXPANDED_SEARCH=enable_expanded_search,
    )
    fake_retrieval = FakeRetrievalService()
    monkeypatch.setattr(search_api, "settings", config)
    monkeypatch.setattr(search_api, "query_processing_service", QueryProcessingService(config=config))
    monkeypatch.setattr(search_api, "retrieval_service", fake_retrieval)
    monkeypatch.setattr(search_api, "rerank_service", FactSimilarityReranker(config=config))
    monkeypatch.setattr(search_api, "summary_service", SummaryService(config=config))
    return fake_retrieval


def _candidate(
    case_id: str,
    chunk_id: str,
    score: float,
    *,
    retrieval_source: str = ORIGINAL_VECTOR_SOURCE,
    matched_text: str = "sanitized runtime text",
) -> VectorCandidate:
    return VectorCandidate(
        case_id=case_id,
        chunk_id=chunk_id,
        vector_score=score,
        retrieval_score=score,
        retrieval_source=retrieval_source,
        metadata={
            "case_id": case_id,
            "chunk_id": chunk_id,
            "title": f"{case_id} sanitized title",
            "case_cause": "sanitized cause",
            "chunk_type": "court_found",
            "source_name": "m2-expanded-search-test-source",
        },
        matched_text=matched_text,
        source="m2-expanded-search-test",
    )


def _env_example_values() -> dict[str, str]:
    values: dict[str, str] = {}
    for line in (Path(PROJECT_ROOT) / ".env.example").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key] = value
    return values


def assert_no_absolute_recall_copy(text: str) -> None:
    for copy in ABSOLUTE_RECALL_COPY:
        assert copy not in text
