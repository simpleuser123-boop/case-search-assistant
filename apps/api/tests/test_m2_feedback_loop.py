from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient

from app.api import feedback as feedback_api
from app.api import search as search_api
from app.core.config import Settings
from app.core.feedback_events import FEEDBACK_EVENT_FIELDS, FeedbackEventStore
from app.main import app
from app.query_processing import QueryProcessingService
from app.rerank.models import RankedCaseCandidate
from app.retrieval.models import VectorCandidate, VectorRetrievalResult
from app.retrieval.service import ORIGINAL_VECTOR_SOURCE
from app.summary import SummaryService

client = TestClient(app)

SAFE_SESSION_HASH = "sha256_111111111111111111111111"
SAFE_QUERY_HASH = "sha256_222222222222222222222222"
SAFE_CASE_HASH = "sha256_333333333333333333333333"


def test_feedback_accepts_and_stores_only_sanitized_fields(monkeypatch, caplog):
    store = FeedbackEventStore(path=None)
    monkeypatch.setattr(feedback_api, "feedback_event_store", store)
    caplog.set_level(logging.INFO, logger="case_search")

    response = client.post("/api/feedback", json=_feedback_payload())

    assert response.status_code == 202
    body = response.json()
    assert body["accepted"] is True
    assert body["stored"] is True
    assert body["feedback_value"] == "relevant"
    assert len(store.records) == 1
    assert tuple(store.records[0].keys()) == FEEDBACK_EVENT_FIELDS
    assert store.records[0] == _feedback_payload()
    assert "feedback_event_accepted" in caplog.text
    assert "event_type=result_feedback" in caplog.text
    assert "session_hash=" in caplog.text


@pytest.mark.parametrize(
    "forbidden_field",
    ["query", "raw_query", "case_text", "candidate_body", "chunk_body", "free_text_reason"],
)
def test_feedback_rejects_body_fields_without_logging_values(
    monkeypatch,
    caplog,
    forbidden_field,
):
    store = FeedbackEventStore(path=None)
    monkeypatch.setattr(feedback_api, "feedback_event_store", store)
    caplog.set_level(logging.WARNING, logger="case_search")
    forbidden_value = "RAW_BODY_SENTINEL_SHOULD_NOT_APPEAR"
    payload = {**_feedback_payload(), forbidden_field: forbidden_value}

    response = client.post("/api/feedback", json=payload)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert store.records == []
    assert forbidden_value not in response.text
    assert forbidden_value not in caplog.text


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("event_type", "search_result_render"),
        ("feedback_value", "maybe_related"),
        ("search_mode", "auto"),
        ("confidence_level", "certain"),
        ("rank", 0),
        ("query_hash", "raw-query-not-a-hash"),
    ],
)
def test_feedback_rejects_invalid_enum_rank_and_hash(monkeypatch, field, value):
    store = FeedbackEventStore(path=None)
    monkeypatch.setattr(feedback_api, "feedback_event_store", store)
    payload = {**_feedback_payload(), field: value}

    response = client.post("/api/feedback", json=payload)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert store.records == []


def test_feedback_storage_failure_returns_explicit_degraded_state(monkeypatch, caplog):
    class FailingStore:
        def append(self, _payload):
            raise RuntimeError("storage failed with raw text sentinel")

    monkeypatch.setattr(feedback_api, "feedback_event_store", FailingStore())
    caplog.set_level(logging.WARNING, logger="case_search")

    response = client.post("/api/feedback", json=_feedback_payload(feedback_value="cleared"))

    assert response.status_code == 202
    body = response.json()
    assert body["accepted"] is True
    assert body["stored"] is False
    assert body["degraded"] is True
    assert body["degraded_reasons"] == ["FEEDBACK_EVENT_STORAGE_UNAVAILABLE"]
    assert body["feedback_value"] == "cleared"
    assert "storage failed with raw text sentinel" not in caplog.text
    assert "error_type=RuntimeError" in caplog.text


def test_feedback_does_not_enter_rerank_or_change_same_search_order(monkeypatch):
    store = FeedbackEventStore(path=None)
    fake_retrieval = FakeRetrievalService()
    fake_reranker = RecordingReranker()
    config = Settings(
        DEEPSEEK_API_KEY="test-key",
        ENABLE_QUERY_REWRITE=False,
        ENABLE_WEIGHTED_RERANK=False,
        ENABLE_SUMMARY=False,
        ENABLE_EXPANDED_SEARCH=False,
    )
    monkeypatch.setattr(feedback_api, "feedback_event_store", store)
    monkeypatch.setattr(search_api, "settings", config)
    monkeypatch.setattr(search_api, "query_processing_service", QueryProcessingService(config=config))
    monkeypatch.setattr(search_api, "retrieval_service", fake_retrieval)
    monkeypatch.setattr(search_api, "rerank_service", fake_reranker)
    monkeypatch.setattr(search_api, "summary_service", SummaryService(config=config))

    first = client.post("/api/search", json={"query": "相同检索请求", "limit": 10})
    feedback = client.post(
        "/api/feedback",
        json=_feedback_payload(case_id_hash=SAFE_CASE_HASH, feedback_value="not_relevant"),
    )
    second = client.post("/api/search", json={"query": "相同检索请求", "limit": 10})

    assert first.status_code == 200
    assert feedback.status_code == 202
    assert second.status_code == 200
    assert _result_case_ids(first) == ["case-feedback-a", "case-feedback-b"]
    assert _result_case_ids(second) == _result_case_ids(first)
    assert [call["include_relaxed_recall"] for call in fake_retrieval.calls] == [
        False,
        False,
    ]
    assert len(fake_reranker.calls) == 2
    assert all(call["candidate_ids"] == ["case-feedback-a", "case-feedback-b"] for call in fake_reranker.calls)
    assert all("feedback" not in field for call in fake_reranker.calls for field in call["query_plan_fields"])
    assert store.records == [_feedback_payload(case_id_hash=SAFE_CASE_HASH, feedback_value="not_relevant")]


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
        return VectorRetrievalResult(
            candidates=[
                _candidate(case_id="case-feedback-a", chunk_id="case-feedback-a-c1", score=0.92),
                _candidate(case_id="case-feedback-b", chunk_id="case-feedback-b-c1", score=0.86),
            ],
            embedding_duration_ms=1,
            retrieval_duration_ms=2,
        )


class RecordingReranker:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def rerank(self, query_plan, candidates):
        candidate_list = list(candidates)
        self.calls.append(
            {
                "candidate_ids": [candidate.case_id for candidate in candidate_list],
                "query_plan_fields": list(vars(query_plan).keys()),
            }
        )
        ranked = [
            RankedCaseCandidate(
                candidate=candidate,
                final_score=candidate.retrieval_score,
                score_breakdown={
                    "score_mode": "base_retrieval",
                    "weighted_rerank_enabled": False,
                    "input_rank": index,
                    "legal_element_hits": 2,
                },
            )
            for index, candidate in enumerate(candidate_list)
        ]
        return sorted(ranked, key=lambda item: item.final_score, reverse=True)


def _candidate(*, case_id: str, chunk_id: str, score: float) -> VectorCandidate:
    return VectorCandidate(
        case_id=case_id,
        chunk_id=chunk_id,
        vector_score=score,
        retrieval_score=score,
        retrieval_source=ORIGINAL_VECTOR_SOURCE,
        metadata={
            "case_id": case_id,
            "chunk_id": chunk_id,
            "title": f"{case_id} sanitized title",
            "court": "sanitized court",
            "trial_level": "sanitized trial",
            "case_cause": "sanitized cause",
            "judgment_date": "2025-01-01",
            "chunk_type": "court_found",
            "source_name": "m2-feedback-loop-test-source",
        },
        matched_text="sanitized runtime snippet",
        source="m2-feedback-loop-test",
    )


def _feedback_payload(
    *,
    case_id_hash: str = SAFE_CASE_HASH,
    feedback_value: str = "relevant",
    search_mode: str = "standard",
    confidence_level: str = "high",
) -> dict[str, object]:
    return {
        "event_type": "result_feedback",
        "session_hash": SAFE_SESSION_HASH,
        "query_hash": SAFE_QUERY_HASH,
        "case_id_hash": case_id_hash,
        "rank": 1,
        "feedback_value": feedback_value,
        "search_mode": search_mode,
        "confidence_level": confidence_level,
    }


def _result_case_ids(response):
    return [item["case_id"] for item in response.json()["results"]]
