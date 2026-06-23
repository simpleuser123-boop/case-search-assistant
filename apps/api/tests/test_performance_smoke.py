from __future__ import annotations

from app.core.timing import SEARCH_TIMING_FIELDS
from scripts.day3_7_3_performance_smoke import _build_report, _sample_row


def test_sample_row_marks_cold_and_warm_groups():
    body = {
        "query_session_id": "qs_1",
        "degraded": True,
        "degraded_reasons": ["EMBEDDING_TIMEOUT", "BM25_FALLBACK_USED"],
        "results": [{"case_id": "case-1"}],
        "timings": {
            "rewrite_duration_ms": 0,
            "embedding_duration_ms": 1800,
            "retrieval_duration_ms": 120,
            "rerank_duration_ms": 0,
            "summary_duration_ms": 0,
            "total_duration_ms": 1920,
        },
    }

    sample = _sample_row(
        index=1,
        sample_group="cold",
        query="夜间盗窃现金",
        status_code=200,
        wall_ms=1930,
        body=body,
    )

    assert sample["sample_group"] == "cold"
    assert sample["missing_timing_fields"] == []
    assert sample["timings"]["embedding_duration_ms"] == 1800


def test_build_report_uses_warm_p95_for_gate():
    samples = [
        {
            "sample_id": "sample_01",
            "sample_group": "cold",
            "status_code": 200,
            "api_wall_ms": 3800,
            "degraded": True,
            "degraded_reasons": ["EMBEDDING_TIMEOUT", "BM25_FALLBACK_USED"],
            "timings": {field: 0 for field in SEARCH_TIMING_FIELDS} | {
                "embedding_duration_ms": 3600,
                "retrieval_duration_ms": 120,
                "total_duration_ms": 3720,
            },
        },
        {
            "sample_id": "sample_02",
            "sample_group": "warm",
            "status_code": 200,
            "api_wall_ms": 820,
            "degraded": False,
            "degraded_reasons": [],
            "timings": {field: 0 for field in SEARCH_TIMING_FIELDS} | {
                "embedding_duration_ms": 610,
                "retrieval_duration_ms": 110,
                "total_duration_ms": 760,
            },
        },
        {
            "sample_id": "sample_03",
            "sample_group": "warm",
            "status_code": 200,
            "api_wall_ms": 910,
            "degraded": False,
            "degraded_reasons": [],
            "timings": {field: 0 for field in SEARCH_TIMING_FIELDS} | {
                "embedding_duration_ms": 640,
                "retrieval_duration_ms": 130,
                "total_duration_ms": 820,
            },
        },
    ]

    report = _build_report(
        health_status_code=200,
        health={},
        health_text="",
        samples=samples,
    )

    assert report["api"]["p95_under_3s"] is True
    assert report["api"]["response_total_duration_ms"]["p95"] == 3720
    assert report["api"]["warm_response_total_duration_ms"]["p95"] == 820
    assert report["samples"][0]["sample_group"] == "cold"
    assert report["slowest_warm_stage_by_p95"] == "embedding_duration_ms"
