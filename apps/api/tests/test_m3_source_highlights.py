"""M3-5 focused tests: similarity highlight derivation (metadata only)."""
from __future__ import annotations

import json

from app.summary.highlights import (
    REASON_SOURCE_CHUNK_UNAVAILABLE,
    STATUS_AVAILABLE,
    STATUS_DEGRADED,
    build_similarity_highlights,
    summarize_highlights,
)


def _anchor(case_id: str, chunk_id: str, anchor_type: str = "detail_chunk") -> dict:
    return {
        "case_id": case_id,
        "source_chunk_id": chunk_id,
        "anchor_type": anchor_type,
    }


def _chunk(case_id: str, chunk_id: str, *, text: str = "占位事实文本") -> dict:
    return {
        "chunk_id": chunk_id,
        "chunk_type": "court_opinion",
        "text": text,
        "source_anchors": [_anchor(case_id, chunk_id)],
    }


def _generated_section(items_key: str, anchors: list[dict]) -> dict:
    return {
        "generation_status": "generated",
        items_key: [{"source_anchors": [anchor]} for anchor in anchors],
    }


def test_highlight_min_anchor_contains_case_and_chunk():
    chunks = [_chunk("case-A", "case-A-c1")]
    holding = _generated_section("summary_items", [_anchor("case-A", "case-A-c1")])

    highlights = build_similarity_highlights(
        case_id="case-A",
        holding_summary=holding,
        issue_focus=None,
        key_elements=None,
        chunks=chunks,
    )

    assert len(highlights) == 1
    hl = highlights[0]
    assert hl["case_id"] == "case-A"
    assert hl["source_chunk_id"] == "case-A-c1"
    assert hl["related_module"] == "holding_summary"
    assert hl["display_status"] == STATUS_AVAILABLE
    assert hl["degrade_reason"] is None
    assert hl["highlight_id"]


def test_highlight_degrades_when_chunk_not_navigable():
    # issue_focus anchor points to a chunk that is not in the navigable set.
    issue = _generated_section("items", [_anchor("case-A", "case-A-cX")])

    highlights = build_similarity_highlights(
        case_id="case-A",
        holding_summary=None,
        issue_focus=issue,
        key_elements=None,
        chunks=[_chunk("case-A", "case-A-c1")],
    )

    assert len(highlights) == 1
    hl = highlights[0]
    assert hl["display_status"] == STATUS_DEGRADED
    assert hl["degrade_reason"] == REASON_SOURCE_CHUNK_UNAVAILABLE


def test_highlight_skips_foreign_case_and_blank_anchor():
    holding = _generated_section(
        "summary_items",
        [
            _anchor("OTHER-case", "OTHER-c1"),  # foreign case id -> skipped
            _anchor("case-A", ""),  # blank chunk id -> skipped
            _anchor("case-A", "case-A-c1"),  # valid
        ],
    )

    highlights = build_similarity_highlights(
        case_id="case-A",
        holding_summary=holding,
        issue_focus=None,
        key_elements=None,
        chunks=[_chunk("case-A", "case-A-c1")],
    )

    assert [hl["source_chunk_id"] for hl in highlights] == ["case-A-c1"]


def test_highlight_ignores_degraded_section():
    degraded = {
        "generation_status": "degraded",
        "summary_items": [{"source_anchors": [_anchor("case-A", "case-A-c1")]}],
    }
    highlights = build_similarity_highlights(
        case_id="case-A",
        holding_summary=degraded,
        issue_focus=None,
        key_elements=None,
        chunks=[_chunk("case-A", "case-A-c1")],
    )
    assert highlights == []


def test_highlight_covers_three_modules_and_dedupes_within_module():
    holding = _generated_section(
        "summary_items",
        [_anchor("case-A", "case-A-c1"), _anchor("case-A", "case-A-c1")],  # dup
    )
    issue = _generated_section("items", [_anchor("case-A", "case-A-c2")])
    key = _generated_section("items", [_anchor("case-A", "case-A-c3")])

    highlights = build_similarity_highlights(
        case_id="case-A",
        holding_summary=holding,
        issue_focus=issue,
        key_elements=key,
        chunks=[
            _chunk("case-A", "case-A-c1"),
            _chunk("case-A", "case-A-c2"),
            _chunk("case-A", "case-A-c3"),
        ],
    )

    modules = [hl["related_module"] for hl in highlights]
    assert modules.count("holding_summary") == 1  # deduped
    assert "issue_focus" in modules
    assert "key_elements" in modules


def test_summary_counts_are_sanitized_and_have_no_body():
    holding = _generated_section("summary_items", [_anchor("case-A", "case-A-c1")])
    issue = _generated_section("items", [_anchor("case-A", "case-A-cX")])
    highlights = build_similarity_highlights(
        case_id="case-A",
        holding_summary=holding,
        issue_focus=issue,
        key_elements=None,
        chunks=[_chunk("case-A", "case-A-c1")],
    )
    summary = summarize_highlights(highlights)
    assert summary["count"] == 2
    assert summary["by_status"][STATUS_AVAILABLE] == 1
    assert summary["by_status"][STATUS_DEGRADED] == 1
    assert summary["by_reason"][REASON_SOURCE_CHUNK_UNAVAILABLE] == 1


def test_no_body_text_leaks_into_highlight_payload():
    secret = "SHOULD_NOT_LEAK_本院查明被告人盗窃现金两万元"
    chunks = [_chunk("case-A", "case-A-c1", text=secret)]
    holding = _generated_section("summary_items", [_anchor("case-A", "case-A-c1")])
    highlights = build_similarity_highlights(
        case_id="case-A",
        holding_summary=holding,
        issue_focus=None,
        key_elements=None,
        chunks=chunks,
    )
    blob = json.dumps(highlights, ensure_ascii=False)
    assert secret not in blob
    assert "SHOULD_NOT_LEAK" not in blob


def test_blank_case_id_yields_no_highlights():
    assert build_similarity_highlights(
        case_id="  ",
        holding_summary=_generated_section("summary_items", [_anchor("case-A", "case-A-c1")]),
        issue_focus=None,
        key_elements=None,
        chunks=[_chunk("case-A", "case-A-c1")],
    ) == []
