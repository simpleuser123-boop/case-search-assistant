from __future__ import annotations

import copy
import json
import logging

from fastapi.testclient import TestClient

from app.api import search as search_api
from app.core.config import Settings
from app.main import app
from app.query_processing import QueryProcessingService
from app.rerank import FactSimilarityReranker
from app.retrieval.models import VectorCandidate, VectorRetrievalResult
from app.retrieval.risk_hints import build_risk_hints
from app.retrieval.service import ORIGINAL_VECTOR_SOURCE
from app.schemas import SearchResultItem, SourceAnchor
from app.summary import ResultPresentation

client = TestClient(app)


class FakeRetrievalService:
    def __init__(self, candidates: list[VectorCandidate], *, degraded_reasons: list[str] | None = None) -> None:
        self.candidates = candidates
        self.degraded_reasons = degraded_reasons or []

    def retrieve(self, query_plan, *, include_relaxed_recall: bool = False) -> VectorRetrievalResult:
        assert not hasattr(query_plan, "qrels")
        assert not hasattr(query_plan, "relevance")
        assert not hasattr(query_plan, "label")
        assert not hasattr(query_plan, "query_id")
        assert not hasattr(query_plan, "case_id")
        return VectorRetrievalResult(
            candidates=self.candidates,
            embedding_duration_ms=1,
            retrieval_duration_ms=2,
            degraded=bool(self.degraded_reasons),
            degraded_reasons=self.degraded_reasons,
        )


class EmptySummaryService:
    def build_presentations(self, _query_plan, candidates):
        return [ResultPresentation(summary=None, highlights=[]) for _ in candidates]


def test_api_returns_only_source_anchored_risk_hints_without_reordering(monkeypatch):
    _install_search_services(
        monkeypatch,
        [
            _vector_candidate("case-main-1", "case-main-1-c1", 0.92),
            _vector_candidate(
                "case-low-1",
                "case-low-1-c1",
                0.58,
                metadata_extra={
                    "risk_type": "adverse_tendency_source",
                    "risk_reason_code": "ADVERSE_TENDENCY_SOURCE_REVIEW",
                    "risk_confidence_level": "medium",
                },
            ),
            _vector_candidate("case-main-2", "case-main-2-c1", 0.83),
        ],
    )

    response = client.post(
        "/api/search",
        json={
            "query": "sanitized risk hint query",
            "limit": 10,
            "qrels": {"case-low-1": 3},
            "relevance": 3,
            "label": "positive",
            "query_id": "offline-query-id",
            "case_id": "offline-case-id",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert [item["case_id"] for item in body["results"]] == ["case-main-1", "case-main-2"]
    assert [item["case_id"] for item in body["low_confidence_candidates"]] == ["case-low-1"]
    assert body["risk_hints"]
    assert any(hint["risk_type"] == "adverse_tendency_source" for hint in body["risk_hints"])
    assert any(hint["risk_type"] == "low_confidence_candidate" for hint in body["risk_hints"])
    for hint in body["risk_hints"]:
        assert hint["source_anchors"]
        for anchor in hint["source_anchors"]:
            assert anchor["case_id"]
            assert anchor["source_chunk_id"]
        assert hint["reason_code"].isupper()
        assert "胜诉概率" not in json.dumps(hint, ensure_ascii=False)
        assert "败诉概率" not in json.dumps(hint, ensure_ascii=False)
        assert "法律结论" not in json.dumps(hint, ensure_ascii=False)


def test_unanchored_risk_hints_are_filtered():
    hints = build_risk_hints(
        results=[],
        low_confidence_candidates=[
            SearchResultItem(
                case_id="case-without-anchor",
                source_anchors=[],
                confidence_level="low",
                confidence_reasons=["LOW_SCORE_BAND"],
            )
        ],
        degraded_reasons=[],
    )

    assert hints == []


def test_qrels_label_relevance_and_ids_do_not_change_risk_hint_generation():
    base_item = _search_result_item(
        metadata={
            "qrels": "HIGHLY_RELEVANT",
            "relevance": 3,
            "label": "positive",
            "query_id": "query-special",
            "case_id": "metadata-case-special",
        }
    )
    mutated_item = copy.deepcopy(base_item)
    mutated_item.metadata.update(
        {
            "qrels": "NOT_RELEVANT",
            "relevance": 0,
            "label": "negative",
            "query_id": "another-query",
            "case_id": "metadata-case-other",
        }
    )

    base_hints = build_risk_hints(
        results=[],
        low_confidence_candidates=[base_item],
        degraded_reasons=[],
    )
    mutated_hints = build_risk_hints(
        results=[],
        low_confidence_candidates=[mutated_item],
        degraded_reasons=[],
    )

    assert _risk_signature(base_hints) == _risk_signature(mutated_hints)
    serialized = json.dumps([hint.model_dump() for hint in mutated_hints], ensure_ascii=False)
    assert "NOT_RELEVANT" not in serialized
    assert "negative" not in serialized
    assert "another-query" not in serialized


def test_metadata_risk_signal_requires_safe_reason_code_and_source_anchor():
    item = _search_result_item(
        confidence_level="medium",
        confidence_reasons=[],
        metadata={
            "risk_type": "adverse_tendency_source",
            "risk_reason_code": "正文型原因 SHOULD_NOT_APPEAR",
            "risk_confidence_level": "medium",
            "review_note": "BODY_SENTINEL_SHOULD_NOT_APPEAR",
        },
    )

    hints = build_risk_hints(results=[item], low_confidence_candidates=[], degraded_reasons=[])

    assert len(hints) == 1
    hint = hints[0]
    assert hint.risk_type == "adverse_tendency_source"
    assert hint.reason_code == "SOURCE_RISK_SIGNAL_REVIEW"
    assert hint.confidence_level == "medium"
    serialized = hint.model_dump_json()
    assert "SHOULD_NOT_APPEAR" not in serialized
    assert "BODY_SENTINEL_SHOULD_NOT_APPEAR" not in serialized


def test_risk_hint_logs_and_json_do_not_include_query_candidate_or_chunk_body(caplog, monkeypatch):
    _install_search_services(
        monkeypatch,
        [
            _vector_candidate("case-main-1", "case-main-1-c1", 0.91),
            _vector_candidate(
                "case-low-1",
                "case-low-1-c1",
                0.57,
                matched_text="CANDIDATE_BODY_SENTINEL_SHOULD_NOT_APPEAR",
                metadata_extra={
                    "risk_type": "adverse_tendency_source",
                    "risk_reason_code": "ADVERSE_TENDENCY_SOURCE_REVIEW",
                    "review_note": "CHUNK_BODY_SENTINEL_SHOULD_NOT_APPEAR",
                },
            ),
        ],
    )
    caplog.set_level(logging.INFO, logger="case_search")
    raw_query = "RAW_QUERY_SENTINEL_SHOULD_NOT_APPEAR"

    response = client.post("/api/search", json={"query": raw_query, "limit": 10})

    assert response.status_code == 200
    risk_hints_json = json.dumps(response.json()["risk_hints"], ensure_ascii=False)
    assert "risk_hint_count=" in caplog.text
    assert raw_query not in caplog.text
    assert raw_query not in risk_hints_json
    assert "CANDIDATE_BODY_SENTINEL_SHOULD_NOT_APPEAR" not in caplog.text
    assert "CANDIDATE_BODY_SENTINEL_SHOULD_NOT_APPEAR" not in risk_hints_json
    assert "CHUNK_BODY_SENTINEL_SHOULD_NOT_APPEAR" not in caplog.text
    assert "CHUNK_BODY_SENTINEL_SHOULD_NOT_APPEAR" not in risk_hints_json


def test_enable_weighted_rerank_default_remains_false():
    assert Settings.model_fields["ENABLE_WEIGHTED_RERANK"].default is False


def _install_search_services(
    monkeypatch,
    candidates: list[VectorCandidate],
    *,
    degraded_reasons: list[str] | None = None,
) -> None:
    config = Settings(
        DEEPSEEK_API_KEY="test-key",
        ENABLE_QUERY_REWRITE=False,
        ENABLE_SUMMARY=False,
        ENABLE_WEIGHTED_RERANK=False,
        ENABLE_EXPANDED_SEARCH=False,
    )
    monkeypatch.setattr(search_api, "settings", config)
    monkeypatch.setattr(search_api, "query_processing_service", QueryProcessingService(config=config))
    monkeypatch.setattr(
        search_api,
        "retrieval_service",
        FakeRetrievalService(candidates, degraded_reasons=degraded_reasons),
    )
    monkeypatch.setattr(search_api, "rerank_service", FactSimilarityReranker(config=config))
    monkeypatch.setattr(search_api, "summary_service", EmptySummaryService())


def _vector_candidate(
    case_id: str,
    chunk_id: str,
    score: float,
    *,
    matched_text: str = "sanitized runtime snippet",
    metadata_extra: dict[str, object] | None = None,
) -> VectorCandidate:
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
            "case_cause": "sanitized cause",
            "chunk_type": "court_found",
            "source_name": "m2-risk-hints-test-source",
            **(metadata_extra or {}),
        },
        matched_text=matched_text,
        source="m2-risk-hints-test",
    )


def _search_result_item(
    *,
    confidence_level: str = "low",
    confidence_reasons: list[str] | None = None,
    metadata: dict[str, object] | None = None,
) -> SearchResultItem:
    return SearchResultItem(
        case_id="case-risk",
        source_anchors=[
            SourceAnchor(
                case_id="case-risk",
                source_chunk_id="case-risk-c1",
                chunk_type="court_found",
                anchor_type="result",
                source_ref="m2-risk-hints-test-source",
            )
        ],
        confidence_level=confidence_level,  # type: ignore[arg-type]
        confidence_reasons=confidence_reasons or ["LOW_SCORE_BAND"],
        metadata=metadata or {},
    )


def _risk_signature(hints) -> list[tuple[str, str, tuple[str, ...]]]:
    return [
        (
            hint.risk_type,
            hint.reason_code,
            tuple(hint.confidence_reasons),
        )
        for hint in hints
    ]
