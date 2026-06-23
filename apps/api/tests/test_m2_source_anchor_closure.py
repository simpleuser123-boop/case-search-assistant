from __future__ import annotations

import json

from app.case_store import jsonl_store


def test_case_detail_chunks_include_traceable_source_anchors(tmp_path, monkeypatch):
    cases_path = tmp_path / "cases.jsonl"
    chunks_path = tmp_path / "chunks.jsonl"
    case_id = "case-detail-anchor"
    chunk_id = "case-detail-anchor-c1"

    cases_path.write_text(
        json.dumps(
            {
                "case_id": case_id,
                "title": "sanitized title",
                "source_url": "https://example.test/detail-anchor",
                "source_name": "detail-fixture-source",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    chunks_path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "case_id": case_id,
                        "chunk_id": chunk_id,
                        "chunk_type": "court_opinion",
                        "text": "DETAIL_CHUNK_BODY_SENTINEL_SHOULD_NOT_APPEAR",
                    }
                ),
                json.dumps(
                    {
                        "case_id": case_id,
                        "chunk_id": "",
                        "chunk_type": "court_found",
                        "text": "DETAIL_UNANCHORED_BODY_SENTINEL_SHOULD_NOT_APPEAR",
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(jsonl_store, "CASES_PATH", cases_path)
    monkeypatch.setattr(jsonl_store, "CHUNKS_PATH", chunks_path)

    detail = jsonl_store.get_case_detail(case_id)

    assert detail is not None
    assert detail["chunks"][0]["source_anchors"] == [
        {
            "case_id": case_id,
            "source_chunk_id": chunk_id,
            "chunk_type": "court_opinion",
            "anchor_type": "detail_chunk",
            "source_url": "https://example.test/detail-anchor",
            "source_ref": "detail-fixture-source",
        }
    ]
    assert detail["chunks"][1]["source_anchors"] == []
