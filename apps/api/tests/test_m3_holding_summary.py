from __future__ import annotations

import logging

from fastapi.testclient import TestClient

from app.api import cases as cases_api
from app.core.config import Settings
from app.main import app
from app.summary import (
    HOLDING_MISSING_SOURCE_ANCHOR,
    HOLDING_MODEL_FAILED,
    HOLDING_SOURCE_MISMATCH,
    SummaryService,
)
from app.summary.client import SummaryLLMTimeoutError

client = TestClient(app)


class FailingSummaryClient:
    def summarize_chunk(self, **_kwargs) -> str:
        raise SummaryLLMTimeoutError("timeout")


def _settings(**overrides) -> Settings:
    values = {
        "DEEPSEEK_API_KEY": "test-key",
        "ENABLE_SUMMARY": False,
        "SUMMARY_TIMEOUT_SECONDS": 1,
    }
    values.update(overrides)
    return Settings(**values)


def test_holding_summary_items_require_traceable_source_anchors():
    service = SummaryService(config=_settings())

    summary = service.build_holding_summary(
        case_id="case-m3-2",
        case_cause_hint="产品责任纠纷",
        chunks=[
            _chunk(
                case_id="case-m3-2",
                chunk_id="case-m3-2-c2",
                chunk_type="court_opinion",
                text="法院认为，本案应围绕产品缺陷与损害原因进行复核。",
            )
        ],
    )

    assert summary["generation_status"] == "generated"
    assert summary["degrade_reason"] is None
    assert len(summary["summary_items"]) == 1
    item = summary["summary_items"][0]
    assert item["text"]
    assert item["source_anchors"] == summary["source_anchors"]
    assert item["source_anchors"][0]["case_id"] == "case-m3-2"
    assert item["source_anchors"][0]["source_chunk_id"] == "case-m3-2-c2"


def test_holding_summary_degrades_when_anchor_is_missing():
    service = SummaryService(config=_settings())
    chunk = _chunk(
        case_id="case-m3-2",
        chunk_id="case-m3-2-c2",
        chunk_type="court_opinion",
        text="SHOULD_NOT_BECOME_VISIBLE_HOLDING_SUMMARY",
    )
    chunk["source_anchors"] = []

    summary = service.build_holding_summary(
        case_id="case-m3-2",
        case_cause_hint="",
        chunks=[chunk],
    )

    assert summary == {
        "summary_items": [],
        "source_anchors": [],
        "confidence": "low",
        "generation_status": "degraded",
        "degrade_reason": HOLDING_MISSING_SOURCE_ANCHOR,
    }


def test_holding_summary_degrades_when_source_anchor_mismatches_chunk():
    service = SummaryService(config=_settings())
    chunk = _chunk(
        case_id="case-m3-2",
        chunk_id="case-m3-2-c2",
        chunk_type="judgment_result",
        text="SHOULD_NOT_BECOME_VISIBLE_HOLDING_SUMMARY",
    )
    chunk["source_anchors"][0]["source_chunk_id"] = "case-m3-2-other"

    summary = service.build_holding_summary(
        case_id="case-m3-2",
        case_cause_hint="",
        chunks=[chunk],
    )

    assert summary["summary_items"] == []
    assert summary["source_anchors"] == []
    assert summary["generation_status"] == "degraded"
    assert summary["degrade_reason"] == HOLDING_SOURCE_MISMATCH


def test_holding_summary_degrades_when_model_fails():
    service = SummaryService(
        config=_settings(ENABLE_SUMMARY=True),
        summary_client=FailingSummaryClient(),
    )

    summary = service.build_holding_summary(
        case_id="case-m3-2",
        case_cause_hint="产品责任纠纷",
        chunks=[
            _chunk(
                case_id="case-m3-2",
                chunk_id="case-m3-2-c2",
                chunk_type="court_opinion",
                text="MODEL_FAILURE_SOURCE_TEXT_SHOULD_NOT_APPEAR_IN_SUMMARY",
            )
        ],
    )

    assert summary["summary_items"] == []
    assert summary["source_anchors"] == []
    assert summary["generation_status"] == "degraded"
    assert summary["degrade_reason"] == HOLDING_MODEL_FAILED


def test_case_detail_holding_summary_logs_only_sanitized_status(caplog, monkeypatch):
    sentinel = "M3_HOLDING_BODY_SENTINEL_SHOULD_NOT_APPEAR"
    monkeypatch.setattr(cases_api, "summary_service", SummaryService(config=_settings()))
    monkeypatch.setattr(
        cases_api,
        "get_case_detail",
        lambda _case_id: {
            "case_id": "case-m3-2",
            "title": "sanitized title",
            "case_cause": "产品责任纠纷",
            "chunks": [
                _chunk(
                    case_id="case-m3-2",
                    chunk_id="case-m3-2-c2",
                    chunk_type="court_opinion",
                    text=sentinel,
                )
            ],
        },
    )
    caplog.set_level(logging.INFO, logger="case_search")

    response = client.get("/api/cases/case-m3-2")

    assert response.status_code == 200
    body = response.json()
    assert body["holding_summary"]["generation_status"] == "generated"
    assert len(body["holding_summary"]["summary_items"]) == 1
    assert "holding_summary_status=generated" in caplog.text
    assert "holding_summary_item_count=1" in caplog.text
    assert sentinel not in caplog.text


def _chunk(
    *,
    case_id: str,
    chunk_id: str,
    chunk_type: str,
    text: str,
) -> dict:
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
                "source_ref": "m3-test-source",
            }
        ],
        "start_offset": 0,
        "end_offset": 40,
        "text": text,
    }
