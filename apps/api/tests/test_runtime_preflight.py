from __future__ import annotations

from copy import deepcopy

from scripts.runtime_preflight import compare_health


def _health_payload(source_root: str = "C:/repo") -> dict:
    return {
        "status": "ok",
        "feature_flags": {
            "ENABLE_QUERY_REWRITE": False,
            "ENABLE_WEIGHTED_RERANK": False,
            "ENABLE_SUMMARY": False,
            "ENABLE_EXPANDED_SEARCH": False,
        },
        "runtime": {
            "app_version": "0.0.0",
            "source_root": source_root,
            "process_id": 1234,
            "started_at": "2026-06-08T00:00:00Z",
            "config_digest": "a" * 64,
        },
        "embedding_provider": "ollama",
        "embedding_model": "bge-m3",
        "embedding_dimension": 1024,
        "embedding_distance_metric": "cosine",
        "chroma_collection": "case_chunks_bge_m3_v1",
        "dependencies": {
            "db": {"reachable": False, "degraded_reason": "connection_failed"},
            "ollama": {"reachable": False, "degraded_reason": "URLError"},
            "chroma": {"reachable": False, "degraded_reason": "CHROMA_UNAVAILABLE"},
        },
    }


def test_preflight_accepts_matching_runtime_key_fields():
    live = _health_payload()
    source = deepcopy(live)
    source["runtime"]["process_id"] = 5678
    source["runtime"]["started_at"] = "2026-06-08T00:01:00Z"

    result = compare_health(live, source, current_source_root="C:/repo")

    assert result.ok is True
    assert result.errors == []


def test_preflight_rejects_live_process_from_other_source_root():
    live = _health_payload(source_root="D:/old-repo")
    source = _health_payload(source_root="C:/repo")

    result = compare_health(live, source, current_source_root="C:/repo")

    assert result.ok is False
    assert any("Restart the API process" in error for error in result.errors)


def test_preflight_rejects_live_health_missing_current_fields():
    live = _health_payload()
    source = _health_payload()
    del live["feature_flags"]
    del live["runtime"]["config_digest"]

    result = compare_health(live, source, current_source_root="C:/repo")

    assert result.ok is False
    assert "live /health missing field: feature_flags" in result.errors
    assert "live /health missing field: runtime.config_digest" in result.errors
