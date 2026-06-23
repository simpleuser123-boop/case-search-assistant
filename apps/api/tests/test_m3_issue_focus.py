from __future__ import annotations

import logging
from pathlib import Path

from fastapi.testclient import TestClient

from app.api import cases as cases_api
from app.core.config import Settings
from app.main import app
from app.summary import (
    HOLDING_MISSING_SOURCE_ANCHOR,
    HOLDING_SOURCE_MISMATCH,
    READING_ALLOWED_CATEGORIES,
    SummaryService,
)

client = TestClient(app)


def _settings(**overrides) -> Settings:
    values = {
        "DEEPSEEK_API_KEY": "",
        "ENABLE_SUMMARY": False,
        "SUMMARY_TIMEOUT_SECONDS": 1,
    }
    values.update(overrides)
    return Settings(**values)


def test_issue_focus_and_key_elements_require_traceable_source_anchors():
    service = SummaryService(config=_settings())

    navigation = service.build_issue_focus_and_key_elements(
        case_id="case-m3-3",
        chunks=[
            _chunk(
                case_id="case-m3-3",
                chunk_id="case-m3-3-c1",
                chunk_type="court_found",
                text="法院查明双方围绕产品缺陷、损害原因存在争议，并提交鉴定材料。",
            ),
            _chunk(
                case_id="case-m3-3",
                chunk_id="case-m3-3-c2",
                chunk_type="court_opinion",
                text="法院认为应结合产品缺陷、因果关系和举证情况进行说理。",
            ),
        ],
    )

    assert navigation["issue_focus"]["generation_status"] == "generated"
    assert navigation["key_elements"]["generation_status"] == "generated"
    assert navigation["issue_focus"]["items"]
    assert navigation["key_elements"]["items"]

    for section_name in ("issue_focus", "key_elements"):
        for item in navigation[section_name]["items"]:
            assert item["category"] in READING_ALLOWED_CATEGORIES
            assert item["degrade_reason"] is None
            assert item["source_anchors"]
            anchor = item["source_anchors"][0]
            assert anchor["case_id"] == "case-m3-3"
            assert anchor["source_chunk_id"]


def test_issue_focus_and_key_elements_degrade_when_anchor_is_missing():
    service = SummaryService(config=_settings())
    chunk = _chunk(
        case_id="case-m3-3",
        chunk_id="case-m3-3-c1",
        chunk_type="court_found",
        text="M3_ISSUE_FOCUS_MISSING_ANCHOR_SENTINEL",
    )
    chunk["source_anchors"] = []

    navigation = service.build_issue_focus_and_key_elements(
        case_id="case-m3-3",
        chunks=[chunk],
    )

    assert navigation["issue_focus"]["items"] == []
    assert navigation["key_elements"]["items"] == []
    assert navigation["issue_focus"]["generation_status"] == "degraded"
    assert navigation["key_elements"]["generation_status"] == "degraded"
    assert navigation["issue_focus"]["degrade_reason"] == HOLDING_MISSING_SOURCE_ANCHOR
    assert navigation["key_elements"]["degrade_reason"] == HOLDING_MISSING_SOURCE_ANCHOR


def test_issue_focus_and_key_elements_degrade_when_anchor_mismatches_chunk():
    service = SummaryService(config=_settings())
    chunk = _chunk(
        case_id="case-m3-3",
        chunk_id="case-m3-3-c1",
        chunk_type="court_opinion",
        text="SHOULD_NOT_BECOME_VISIBLE_READING_NAVIGATION",
    )
    chunk["source_anchors"][0]["source_chunk_id"] = "case-m3-3-other"

    navigation = service.build_issue_focus_and_key_elements(
        case_id="case-m3-3",
        chunks=[chunk],
    )

    assert navigation["issue_focus"]["items"] == []
    assert navigation["key_elements"]["items"] == []
    assert navigation["issue_focus"]["degrade_reason"] == HOLDING_SOURCE_MISMATCH
    assert navigation["key_elements"]["degrade_reason"] == HOLDING_SOURCE_MISMATCH


def test_forbidden_outcome_copy_is_filtered_from_visible_items():
    service = SummaryService(config=_settings())

    navigation = service.build_issue_focus_and_key_elements(
        case_id="case-m3-3",
        chunks=[
            _chunk(
                case_id="case-m3-3",
                chunk_id="case-m3-3-c1",
                chunk_type="court_found",
                text="双方围绕产品缺陷存在争议，同时出现胜诉概率、保证无遗漏等不应展示话术。",
            )
        ],
    )

    visible_text = " ".join(
        item["label"]
        for section in navigation.values()
        for item in section["items"]
    )
    assert "胜诉" not in visible_text
    assert "概率" not in visible_text
    assert "保证无遗漏" not in visible_text
    assert "必然支持" not in visible_text


def test_case_detail_issue_focus_logs_only_sanitized_status(caplog, monkeypatch):
    sentinel = "M3_ISSUE_FOCUS_BODY_SENTINEL_SHOULD_NOT_APPEAR"
    monkeypatch.setattr(cases_api, "summary_service", SummaryService(config=_settings()))
    monkeypatch.setattr(
        cases_api,
        "get_case_detail",
        lambda _case_id: {
            "case_id": "case-m3-3",
            "title": "sanitized title",
            "case_cause": "产品责任纠纷",
            "chunks": [
                _chunk(
                    case_id="case-m3-3",
                    chunk_id="case-m3-3-c1",
                    chunk_type="court_found",
                    text=sentinel,
                ),
                _chunk(
                    case_id="case-m3-3",
                    chunk_id="case-m3-3-c2",
                    chunk_type="court_opinion",
                    text="双方围绕产品缺陷存在争议，法院结合举证情况进行说理。",
                ),
            ],
        },
    )
    caplog.set_level(logging.INFO, logger="case_search")

    response = client.get("/api/cases/case-m3-3")

    assert response.status_code == 200
    assert "issue_focus_status=generated" in caplog.text
    assert "issue_focus_item_count=" in caplog.text
    assert "key_elements_status=generated" in caplog.text
    assert "key_elements_item_count=" in caplog.text
    assert "争议焦点" in caplog.text
    assert sentinel not in caplog.text


def test_issue_focus_fields_are_not_used_by_search_runtime():
    api_root = Path(__file__).resolve().parents[1]
    search_source = (api_root / "app" / "api" / "search.py").read_text(encoding="utf-8")
    config_source = (api_root / "app" / "core" / "config.py").read_text(encoding="utf-8")

    assert "issue_focus" not in search_source
    assert "key_elements" not in search_source
    assert "ENABLE_WEIGHTED_RERANK: bool = False" in config_source


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
