"""Day 3 step 7.5 feature-flag rollback drill.

This script exercises the rollback switches in-process with mocked external
dependencies. It proves flag behavior without changing .env, touching Chroma,
rebuilding indexes, or logging raw query text.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
from io import StringIO
from pathlib import Path
from time import perf_counter
from typing import Any

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.api import search as search_api
from app.core.config import Settings
from app.core.logging import logger
from app.main import app
from app.query_processing import QueryProcessingService
from app.query_processing.service import QUERY_REWRITE_DISABLED
from app.rerank import FactSimilarityReranker
from app.retrieval.models import VectorCandidate, VectorRetrievalResult
from app.retrieval.service import ORIGINAL_VECTOR_SOURCE
from app.summary import SUMMARY_DISABLED, SummaryService

DRILL_QUERY = "夜间盗窃现金后退赔并取得谅解"


class FakeRewriteClient:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def rewrite_query(self, cleaned_query: str) -> str:
        self.calls.append(cleaned_query)
        return json.dumps(
            {
                "legal_elements": ["盗窃现金", "退赔谅解"],
                "query_variants": [
                    f"{cleaned_query} 类案 相似事实",
                    f"{cleaned_query} 裁判文书 同类事实",
                ],
                "case_cause_hint": "盗窃罪",
                "confidence": 0.8,
                "notes": "保留核心事实。",
            },
            ensure_ascii=False,
        )


class FakeSummaryClient:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def summarize_chunk(
        self,
        *,
        chunk_excerpt: str,
        source_chunk_id: str,
        query_terms: list[str],
        case_cause_hint: str,
    ) -> str:
        self.calls.append(source_chunk_id)
        return json.dumps({"text": "被告人盗窃现金后退赔并取得谅解。"}, ensure_ascii=False)


class FakeRetrievalService:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def retrieve(self, query_plan, *, include_relaxed_recall: bool = False) -> VectorRetrievalResult:
        self.calls.append(
            {
                "queries": list(query_plan.queries),
                "input_hash": query_plan.input_hash,
                "include_relaxed_recall": include_relaxed_recall,
            }
        )
        return VectorRetrievalResult(
            candidates=[
                _candidate(
                    case_id="case-high-base",
                    chunk_id="case-high-base-c1",
                    score=0.92,
                    matched_text="普通段落,被告人盗窃现金后退赔。",
                ),
                _candidate(
                    case_id="case-low-base",
                    chunk_id="case-low-base-c1",
                    score=0.62,
                    matched_text="本院查明,被告人盗窃现金后退赔并取得谅解。",
                ),
            ],
            embedding_duration_ms=1,
            retrieval_duration_ms=2,
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="", help="Optional JSON report output path.")
    args = parser.parse_args()

    original_services = _capture_original_services()
    try:
        report = _build_report()
    finally:
        _restore_services(original_services)

    output = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        output_path = Path(args.out)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(output + "\n", encoding="utf-8")
    print(output)


def _build_report() -> dict[str, Any]:
    scenarios = [
        _run_scenario(
            flag="ENABLE_QUERY_REWRITE",
            overrides={"ENABLE_QUERY_REWRITE": False},
            endpoint="/api/search",
            verifier=_verify_query_rewrite_disabled,
        ),
        _run_scenario(
            flag="ENABLE_WEIGHTED_RERANK",
            overrides={"ENABLE_WEIGHTED_RERANK": False},
            endpoint="/api/search",
            verifier=_verify_weighted_rerank_disabled,
        ),
        _run_scenario(
            flag="ENABLE_SUMMARY",
            overrides={"ENABLE_SUMMARY": False},
            endpoint="/api/search",
            verifier=_verify_summary_disabled,
        ),
        _run_scenario(
            flag="ENABLE_EXPANDED_SEARCH",
            overrides={"ENABLE_EXPANDED_SEARCH": False},
            endpoint="/api/search/expand",
            verifier=_verify_expanded_search_disabled,
        ),
    ]
    elapsed_values = [scenario["rollback_elapsed_ms"] for scenario in scenarios]
    all_passed = all(scenario["status"] == "passed" for scenario in scenarios)
    return {
        "version": "day3_7_5_rollback_drill_v1",
        "scope": "in_process_mocked_dependencies_no_index_rebuild",
        "query_hash": hashlib.sha256(DRILL_QUERY.encode("utf-8")).hexdigest()[:16],
        "input_length": len(DRILL_QUERY),
        "raw_query_written": False,
        "external_dependencies_used": False,
        "vector_index_rebuild_required": False,
        "recovery_within_60_seconds": all(value < 60000 for value in elapsed_values),
        "max_rollback_elapsed_ms": max(elapsed_values) if elapsed_values else 0,
        "status": "passed" if all_passed else "failed",
        "scenarios": scenarios,
    }


def _run_scenario(
    *,
    flag: str,
    overrides: dict[str, bool],
    endpoint: str,
    verifier,
) -> dict[str, Any]:
    original_services = _capture_original_services()
    log_buffer = StringIO()
    handler = logging.StreamHandler(log_buffer)
    original_propagate = logger.propagate
    logger.propagate = False
    logger.addHandler(handler)
    started = perf_counter()
    config = _settings(**overrides)
    rewrite_client, retrieval_service, summary_client = _install_services(config)
    try:
        client = TestClient(app)
        if endpoint == "/api/search/expand":
            response = client.post(endpoint, json={"query": DRILL_QUERY, "limit": 5})
        else:
            response = client.post(endpoint, json={"query": DRILL_QUERY, "limit": 2})
        body = _safe_json(response)
        verifier(body, retrieval_service, summary_client)
        log_text = log_buffer.getvalue()
        assert "rollback_event" in log_text, "rollback_event was not logged"
        assert flag in log_text, f"{flag} was not present in rollback log"
        assert DRILL_QUERY not in log_text, "raw query leaked into rollback log"
        status = "passed"
        failure = ""
    except AssertionError as exc:
        status = "failed"
        failure = str(exc)
    finally:
        _restore_services(original_services)
        logger.removeHandler(handler)
        logger.propagate = original_propagate

    elapsed_ms = int((perf_counter() - started) * 1000)
    return {
        "flag": flag,
        "current_state_before_drill": True,
        "rollback_state": False,
        "reload_method": "in_process_service_reload",
        "endpoint": endpoint,
        "http_status": response.status_code if "response" in locals() else None,
        "rollback_elapsed_ms": elapsed_ms,
        "restore_method": "module_globals_restored",
        "vector_index_rebuild_required": False,
        "rollback_event_logged": "rollback_event" in log_buffer.getvalue(),
        "raw_query_logged": DRILL_QUERY in log_buffer.getvalue(),
        "status": status,
        "failure": failure,
        "observed": _observed_fields(
            body if "body" in locals() else {},
            retrieval_service,
            summary_client,
        ),
    }


def _settings(**overrides) -> Settings:
    values = {
        "DEEPSEEK_API_KEY": "test-key",
        "ENABLE_QUERY_REWRITE": True,
        "ENABLE_WEIGHTED_RERANK": True,
        "ENABLE_SUMMARY": True,
        "ENABLE_EXPANDED_SEARCH": True,
    }
    values.update(overrides)
    return Settings(**values)


def _install_services(config: Settings) -> tuple[FakeRewriteClient, FakeRetrievalService, FakeSummaryClient]:
    rewrite_client = FakeRewriteClient()
    retrieval_service = FakeRetrievalService()
    summary_client = FakeSummaryClient()
    search_api.settings = config
    search_api.query_processing_service = QueryProcessingService(
        config=config,
        rewrite_client=rewrite_client,
    )
    search_api.retrieval_service = retrieval_service
    search_api.rerank_service = FactSimilarityReranker(config=config)
    search_api.summary_service = SummaryService(config=config, summary_client=summary_client)
    return rewrite_client, retrieval_service, summary_client


def _capture_original_services() -> dict[str, Any]:
    return {
        "settings": search_api.settings,
        "query_processing_service": search_api.query_processing_service,
        "retrieval_service": search_api.retrieval_service,
        "rerank_service": search_api.rerank_service,
        "summary_service": search_api.summary_service,
    }


def _restore_services(original_services: dict[str, Any]) -> None:
    for name, value in original_services.items():
        setattr(search_api, name, value)


def _candidate(*, case_id: str, chunk_id: str, score: float, matched_text: str) -> VectorCandidate:
    return VectorCandidate(
        case_id=case_id,
        chunk_id=chunk_id,
        vector_score=score,
        retrieval_score=score,
        retrieval_source=ORIGINAL_VECTOR_SOURCE,
        metadata={
            "case_id": case_id,
            "chunk_id": chunk_id,
            "title": f"{case_id}判决书",
            "court": "测试法院",
            "trial_level": "一审",
            "case_cause": "盗窃罪",
            "judgment_date": "2024-01-01",
        },
        matched_text=matched_text,
        source="rollback-drill",
    )


def _verify_query_rewrite_disabled(
    body: dict[str, Any],
    retrieval_service: FakeRetrievalService,
    summary_client: FakeSummaryClient,
) -> None:
    assert QUERY_REWRITE_DISABLED in body.get("degraded_reasons", [])
    assert retrieval_service.calls[0]["queries"] == [DRILL_QUERY]
    assert body.get("results"), "search result list is empty"


def _verify_weighted_rerank_disabled(
    body: dict[str, Any],
    retrieval_service: FakeRetrievalService,
    summary_client: FakeSummaryClient,
) -> None:
    top = body["results"][0]
    assert top["case_id"] == "case-high-base"
    assert top["score_breakdown"]["score_mode"] == "base_retrieval"
    assert top["score_breakdown"]["weighted_rerank_enabled"] is False
    assert top["final_score"] == top["retrieval_score"]


def _verify_summary_disabled(
    body: dict[str, Any],
    retrieval_service: FakeRetrievalService,
    summary_client: FakeSummaryClient,
) -> None:
    top = body["results"][0]
    assert SUMMARY_DISABLED in body.get("degraded_reasons", [])
    assert top["summary"]["method"] == "source_snippet"
    assert top["summary"]["source_chunk_id"] == top["top_chunk_id"]
    assert top["summary"]["degraded_reason"] == SUMMARY_DISABLED
    assert summary_client.calls == []


def _verify_expanded_search_disabled(
    body: dict[str, Any],
    retrieval_service: FakeRetrievalService,
    summary_client: FakeSummaryClient,
) -> None:
    assert body["error"]["code"] == "EXPANDED_SEARCH_DISABLED"
    assert retrieval_service.calls == []


def _observed_fields(
    body: dict[str, Any],
    retrieval_service: FakeRetrievalService,
    summary_client: FakeSummaryClient,
) -> dict[str, Any]:
    first_result = (body.get("results") or [{}])[0]
    error = body.get("error") or {}
    return {
        "result_count": len(body.get("results") or []),
        "degraded_reasons": body.get("degraded_reasons") or [],
        "error_code": error.get("code"),
        "retrieval_call_count": len(retrieval_service.calls),
        "summary_llm_call_count": len(summary_client.calls),
        "score_mode": (first_result.get("score_breakdown") or {}).get("score_mode"),
        "summary_method": (first_result.get("summary") or {}).get("method"),
    }


def _safe_json(response: Any) -> dict[str, Any]:
    try:
        body = response.json()
    except Exception:  # noqa: BLE001 - drill report must stay robust
        return {}
    return body if isinstance(body, dict) else {}


if __name__ == "__main__":
    main()
