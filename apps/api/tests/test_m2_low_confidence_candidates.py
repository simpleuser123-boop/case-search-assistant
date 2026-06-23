from __future__ import annotations

import copy
import logging

from fastapi.testclient import TestClient

from app.api import search as search_api
from app.core.config import Settings
from app.main import app
from app.query_processing import QueryProcessingService
from app.query_processing.models import QueryPlan
from app.rerank import FactSimilarityReranker
from app.rerank.models import RankedCaseCandidate
from app.retrieval.confidence import build_confidence_profile, split_low_confidence_candidates
from app.retrieval.models import CaseCandidate, VectorCandidate, VectorRetrievalResult
from app.retrieval.service import ORIGINAL_VECTOR_SOURCE
from app.summary import ResultPresentation

client = TestClient(app)


class FakeRetrievalService:
    def __init__(self, candidates: list[VectorCandidate]) -> None:
        self.candidates = candidates

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
            degraded=False,
            degraded_reasons=[],
        )


class EmptySummaryService:
    def build_presentations(self, _query_plan, candidates):
        return [ResultPresentation(summary=None, highlights=[]) for _ in candidates]


def test_api_separates_results_and_low_confidence_candidates_without_reordering(monkeypatch):
    _install_search_services(
        monkeypatch,
        [
            _vector_candidate("case-main-1", "case-main-1-c1", 0.92),
            _vector_candidate("case-low-1", "case-low-1-c1", 0.62),
            _vector_candidate("case-main-2", "case-main-2-c1", 0.81),
            _vector_candidate("case-low-2", "case-low-2-c1", 0.58),
        ],
    )

    response = client.post(
        "/api/search",
        json={
            "query": "夜间入户盗窃现金并逃离现场",
            "limit": 10,
            "qrels": {"case-low-1": 2},
            "relevance": 2,
            "label": "positive",
            "query_id": "offline-query-id",
            "case_id": "offline-case-id",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert [item["case_id"] for item in body["results"]] == ["case-main-1", "case-main-2"]
    assert [item["case_id"] for item in body["low_confidence_candidates"]] == [
        "case-low-1",
        "case-low-2",
    ]
    assert [item["case_id"] for item in body["candidates"]] == ["case-main-1", "case-main-2"]
    assert body["results"][0]["confidence_level"] in {"high", "medium"}
    assert body["low_confidence_candidates"][0]["confidence_level"] == "low"
    assert "LOW_SCORE_BAND" in body["low_confidence_candidates"][0]["confidence_reasons"]
    assert "MAIN_RESULT_COUNT_BELOW_TARGET" in body["low_confidence_candidates"][0]["confidence_reasons"]
    assert body["low_confidence_candidates"][0]["original_rank"] == 3
    assert body["low_confidence_candidates"][1]["original_rank"] == 4


def test_runtime_layering_ignores_qrels_relevance_label_query_id_and_case_id():
    ranked = [
        _ranked_candidate(
            case_id="case-a",
            score=0.64,
            metadata={
                "qrels": "HIGHLY_RELEVANT",
                "relevance": 2,
                "label": "positive",
                "query_id": "query-special",
                "case_id": "case-a",
            },
        ),
        _ranked_candidate(
            case_id="case-b",
            score=0.64,
            metadata={
                "qrels": "NOT_RELEVANT",
                "relevance": 0,
                "label": "negative",
                "query_id": "different-query",
                "case_id": "case-b",
            },
        ),
    ]

    split_a = split_low_confidence_candidates(ranked, limit=10, degraded_reasons=[])

    mutated = copy.deepcopy(ranked)
    mutated[0].candidate.metadata.update(
        {
            "qrels": "NOT_RELEVANT",
            "relevance": 0,
            "label": "negative",
            "query_id": "another-query",
            "case_id": "case-z",
        }
    )
    mutated[1].candidate.metadata.update(
        {
            "qrels": "HIGHLY_RELEVANT",
            "relevance": 2,
            "label": "positive",
            "query_id": "query-special",
            "case_id": "case-y",
        }
    )
    split_b = split_low_confidence_candidates(mutated, limit=10, degraded_reasons=[])

    assert _split_signature(split_a) == _split_signature(split_b)
    assert all(
        "LOW_SCORE_BAND" in item.confidence.confidence_reasons
        for item in split_b.low_confidence_candidates
    )


def test_label_relevance_and_qrels_metadata_do_not_create_legal_element_hits():
    query_plan = QueryPlan(
        cleaned_query="sanitized query",
        input_hash="a" * 64,
        queries=["sanitized query"],
        legal_elements=["label-leak-a", "label-leak-b"],
    )
    candidate = CaseCandidate(
        case_id="case-with-forbidden-metadata",
        top_chunk_id="case-with-forbidden-metadata-c1",
        source_chunk_ids=["case-with-forbidden-metadata-c1"],
        hit_chunk_ids=["case-with-forbidden-metadata-c1"],
        retrieval_source=[ORIGINAL_VECTOR_SOURCE],
        metadata={
            "case_id": "case-with-forbidden-metadata",
            "chunk_id": "case-with-forbidden-metadata-c1",
            "label": "label-leak-a label-leak-b",
            "relevance": "label-leak-a",
            "qrels": "label-leak-b",
            "query_id": "label-leak-a",
            "title": "neutral title",
        },
        matched_text="neutral runtime fact text",
        source="runtime-test",
        vector_score=0.82,
        retrieval_score=0.82,
        top_chunk_score=0.82,
        matched_by_vector=True,
    )

    ranked = FactSimilarityReranker(enabled=False).rerank(query_plan, [candidate])[0]
    confidence = build_confidence_profile(ranked, degraded_reasons=[])

    assert ranked.score_breakdown["legal_element_hit_count"] == 0
    assert confidence.confidence_level == "medium"
    assert confidence.confidence_reasons == ()


def test_runtime_router_query_plan_has_no_offline_label_fields(monkeypatch):
    fake_retrieval = FakeRetrievalService(
        [
            _vector_candidate("case-main-1", "case-main-1-c1", 0.9),
            _vector_candidate("case-low-1", "case-low-1-c1", 0.59),
        ]
    )
    _install_search_services(monkeypatch, fake_retrieval.candidates)
    monkeypatch.setattr(search_api, "retrieval_service", fake_retrieval)

    response = client.post("/api/search", json={"query": "夜间入户盗窃现金并逃离现场", "limit": 10})

    assert response.status_code == 200
    assert response.json()["low_confidence_candidates"][0]["confidence_reasons"]


def test_logs_do_not_include_query_candidate_or_chunk_body(caplog, monkeypatch):
    _install_search_services(
        monkeypatch,
        [
            _vector_candidate(
                "case-main-1",
                "case-main-1-c1",
                0.9,
                matched_text="CANDIDATE_BODY_SENTINEL_SHOULD_NOT_APPEAR",
            ),
            _vector_candidate(
                "case-low-1",
                "case-low-1-c1",
                0.59,
                matched_text="CHUNK_BODY_SENTINEL_SHOULD_NOT_APPEAR",
            ),
        ],
    )
    caplog.set_level(logging.INFO, logger="case_search")
    raw_query = "RAW_QUERY_SENTINEL_SHOULD_NOT_APPEAR"

    response = client.post("/api/search", json={"query": raw_query, "limit": 10})

    assert response.status_code == 200
    assert "low_confidence_count=1" in caplog.text
    assert raw_query not in caplog.text
    assert "CANDIDATE_BODY_SENTINEL_SHOULD_NOT_APPEAR" not in caplog.text
    assert "CHUNK_BODY_SENTINEL_SHOULD_NOT_APPEAR" not in caplog.text


def _install_search_services(monkeypatch, candidates: list[VectorCandidate]) -> None:
    config = Settings(
        DEEPSEEK_API_KEY="test-key",
        ENABLE_QUERY_REWRITE=False,
        ENABLE_SUMMARY=False,
        ENABLE_WEIGHTED_RERANK=False,
        ENABLE_EXPANDED_SEARCH=False,
    )
    monkeypatch.setattr(search_api, "settings", config)
    monkeypatch.setattr(search_api, "query_processing_service", QueryProcessingService(config=config))
    monkeypatch.setattr(search_api, "retrieval_service", FakeRetrievalService(candidates))
    monkeypatch.setattr(search_api, "rerank_service", FactSimilarityReranker(config=config))
    monkeypatch.setattr(search_api, "summary_service", EmptySummaryService())


def _vector_candidate(
    case_id: str,
    chunk_id: str,
    score: float,
    *,
    matched_text: str = "本院查明,行为人夜间进入他人场所盗窃现金。",
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
            "case_cause": "盗窃罪",
            "chunk_type": "court_found",
            "source_name": "low-confidence-test-source",
        },
        matched_text=matched_text,
        source="low-confidence-test",
    )


def _ranked_candidate(
    *,
    case_id: str,
    score: float,
    metadata: dict[str, object],
) -> RankedCaseCandidate:
    return RankedCaseCandidate(
        candidate=CaseCandidate(
            case_id=case_id,
            top_chunk_id=f"{case_id}-c1",
            source_chunk_ids=[f"{case_id}-c1"],
            hit_chunk_ids=[f"{case_id}-c1"],
            retrieval_source=[ORIGINAL_VECTOR_SOURCE],
            metadata=dict(metadata),
            matched_text="runtime matched text",
            source="runtime-test",
            vector_score=score,
            retrieval_score=score,
            top_chunk_score=score,
            matched_by_vector=True,
        ),
        final_score=score,
        score_breakdown={
            "retrieval_source": [ORIGINAL_VECTOR_SOURCE],
            "legal_element_hit_count": 0,
            "input_rank": 0,
        },
    )


def _split_signature(split) -> tuple[list[tuple[float, tuple[str, ...]]], list[tuple[float, tuple[str, ...]]]]:
    return (
        [
            (item.ranked.final_score, item.confidence.confidence_reasons)
            for item in split.results
        ],
        [
            (item.ranked.final_score, item.confidence.confidence_reasons)
            for item in split.low_confidence_candidates
        ],
    )
