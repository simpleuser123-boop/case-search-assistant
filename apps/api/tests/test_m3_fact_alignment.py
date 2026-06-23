from __future__ import annotations

import logging
from pathlib import Path

from fastapi.testclient import TestClient

from app.api import cases as cases_api
from app.main import app
from app.summary import (
    FACT_ALIGNMENT_INSUFFICIENT_SOURCE,
    FACT_ALIGNMENT_MISSING_SOURCE_ANCHOR,
    MATCH_DIFFERENCE,
    MATCH_SAME,
    MATCH_TYPES,
    QUERY_SIGNAL_ABSENT,
    QUERY_SIGNAL_PRESENT,
    FactAlignmentService,
)

client = TestClient(app)


def _chunk(*, case_id: str, chunk_id: str, chunk_type: str, text: str) -> dict:
    return {
        "chunk_id": chunk_id,
        "chunk_type": chunk_type,
        "source_anchors": [
            {
                "case_id": case_id,
                "source_chunk_id": chunk_id,
                "chunk_type": chunk_type,
                "anchor_type": "detail_chunk",
                "source_url": None,
                "source_ref": "m3-4-test-source",
            }
        ],
        "start_offset": 0,
        "end_offset": 60,
        "text": text,
    }


def test_case_side_facts_require_traceable_source_anchors():
    service = FactAlignmentService()
    result = service.build_fact_alignment(
        case_id="case-m3-4",
        query_signal_text="对方盗窃了我的现金",
        chunks=[
            _chunk(
                case_id="case-m3-4",
                chunk_id="case-m3-4-c1",
                chunk_type="court_found",
                text="法院查明被告人盗窃现金5万元，后退赔并取得谅解。",
            )
        ],
    )

    assert result["generation_status"] == "generated"
    assert result["items"]
    for item in result["items"]:
        assert item["match_type"] in MATCH_TYPES
        assert item["degrade_reason"] is None
        assert item["source_anchors"], "case-side fact must be anchored"
        anchor = item["source_anchors"][0]
        assert anchor["case_id"] == "case-m3-4"
        assert anchor["source_chunk_id"]


def test_matched_dimension_marks_same_unmatched_marks_difference():
    service = FactAlignmentService()
    result = service.build_fact_alignment(
        case_id="case-m3-4",
        query_signal_text="对方盗窃了现金",
        chunks=[
            _chunk(
                case_id="case-m3-4",
                chunk_id="case-m3-4-c1",
                chunk_type="court_found",
                text="法院查明被告人盗窃现金，事后自首并退赔取得谅解。",
            )
        ],
    )
    by_dim = {item["dimension_key"]: item for item in result["items"]}
    assert by_dim["act_type"]["match_type"] == MATCH_SAME
    assert by_dim["act_type"]["query_side_signal"] == QUERY_SIGNAL_PRESENT
    # subjective (自首/退赔/谅解) not mentioned in the query -> review difference.
    assert by_dim["subjective"]["match_type"] == MATCH_DIFFERENCE
    assert by_dim["subjective"]["query_side_signal"] == QUERY_SIGNAL_ABSENT


def test_unanchored_case_facts_degrade_with_reason():
    service = FactAlignmentService()
    chunk = _chunk(
        case_id="case-m3-4",
        chunk_id="case-m3-4-c1",
        chunk_type="court_found",
        text="法院查明被告人盗窃现金。",
    )
    chunk["source_anchors"] = []
    result = service.build_fact_alignment(
        case_id="case-m3-4",
        query_signal_text="盗窃",
        chunks=[chunk],
    )
    assert result["items"] == []
    assert result["generation_status"] == "degraded"
    assert result["degrade_reason"] == FACT_ALIGNMENT_MISSING_SOURCE_ANCHOR


def test_anchor_mismatch_is_not_displayed():
    service = FactAlignmentService()
    chunk = _chunk(
        case_id="case-m3-4",
        chunk_id="case-m3-4-c1",
        chunk_type="court_found",
        text="法院查明被告人盗窃现金。",
    )
    chunk["source_anchors"][0]["source_chunk_id"] = "case-m3-4-other"
    result = service.build_fact_alignment(
        case_id="case-m3-4",
        query_signal_text="盗窃",
        chunks=[chunk],
    )
    assert result["items"] == []
    assert result["generation_status"] == "degraded"
    assert result["degrade_reason"] == FACT_ALIGNMENT_MISSING_SOURCE_ANCHOR


def test_empty_query_still_shows_anchored_case_dimensions_as_difference():
    service = FactAlignmentService()
    result = service.build_fact_alignment(
        case_id="case-m3-4",
        query_signal_text="",
        chunks=[
            _chunk(
                case_id="case-m3-4",
                chunk_id="case-m3-4-c1",
                chunk_type="court_found",
                text="法院查明被告人盗窃现金5万元。",
            )
        ],
    )
    assert result["generation_status"] == "generated"
    assert result["query_signal_present"] is False
    assert all(item["match_type"] == MATCH_DIFFERENCE for item in result["items"])


def test_no_outcome_or_certainty_copy_in_visible_facts():
    service = FactAlignmentService()
    result = service.build_fact_alignment(
        case_id="case-m3-4",
        query_signal_text="盗窃 胜诉 概率",
        chunks=[
            _chunk(
                case_id="case-m3-4",
                chunk_id="case-m3-4-c1",
                chunk_type="court_found",
                text="法院查明被告人盗窃现金，胜诉概率高，必然支持，足以适用本案。",
            )
        ],
    )
    visible = " ".join(
        fact for item in result["items"] for fact in item["case_side_facts"]
    )
    for forbidden in ("胜诉", "败诉", "概率", "必然支持", "足以适用"):
        assert forbidden not in visible


def test_fact_alignment_endpoint_logs_only_sanitized_fields(caplog, monkeypatch):
    sentinel = "M3_4_FACT_BODY_SENTINEL_SHOULD_NOT_APPEAR"
    raw_query_sentinel = "RAW_QUERY_SHOULD_NOT_APPEAR_IN_LOG"
    monkeypatch.setattr(
        cases_api,
        "get_case_detail",
        lambda _case_id: {
            "case_id": "case-m3-4",
            "case_cause": "盗窃",
            "chunks": [
                _chunk(
                    case_id="case-m3-4",
                    chunk_id="case-m3-4-c1",
                    chunk_type="court_found",
                    text=f"法院查明被告人盗窃现金5万元。{sentinel}",
                )
            ],
        },
    )
    caplog.set_level(logging.INFO, logger="case_search")

    response = client.post(
        "/api/cases/case-m3-4/fact-alignment",
        json={"query_signal": f"对方盗窃现金 {raw_query_sentinel}"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["generation_status"] == "generated"
    assert body["items"]
    assert "fact_alignment_built" in caplog.text
    assert "status=generated" in caplog.text
    assert "item_count=" in caplog.text
    assert "match_type_count=" in caplog.text
    # Privacy: neither case body nor raw query may appear in logs.
    assert sentinel not in caplog.text
    assert raw_query_sentinel not in caplog.text


def test_fact_alignment_endpoint_404_for_unknown_case(monkeypatch):
    monkeypatch.setattr(cases_api, "get_case_detail", lambda _case_id: None)
    response = client.post(
        "/api/cases/missing/fact-alignment",
        json={"query_signal": "盗窃"},
    )
    assert response.status_code == 404


def test_fact_alignment_not_used_by_search_runtime():
    api_root = Path(__file__).resolve().parents[1]
    search_source = (api_root / "app" / "api" / "search.py").read_text(encoding="utf-8")
    config_source = (api_root / "app" / "core" / "config.py").read_text(encoding="utf-8")
    rerank_source = (api_root / "app" / "rerank" / "service.py").read_text(encoding="utf-8")

    assert "fact_alignment" not in search_source
    assert "FactAlignment" not in search_source
    assert "fact_alignment" not in rerank_source
    assert "ENABLE_WEIGHTED_RERANK: bool = False" in config_source


def test_fact_alignment_does_not_use_qrels_label_or_relevance():
    api_root = Path(__file__).resolve().parents[1]
    source = (api_root / "app" / "summary" / "fact_alignment.py").read_text(encoding="utf-8")
    lowered = source.lower()
    assert "qrels" not in lowered
    assert "relevance" not in lowered
    # no per-id special casing of query/case identities for quality
    assert "query_id" not in lowered
    assert "case_id ==" not in source.replace("anchor_case_id == case_id", "")
