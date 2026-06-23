"""Day 3 step 7.3 performance and stability smoke.

Runs a small in-process /api/search sample through the current app and reports
stage timings, API wall-clock P95/P99, dependency health, error rates, and
degradation counts. This is intentionally a smoke, not a load test.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections import Counter
from pathlib import Path
from time import perf_counter
from typing import Any

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.timing import SEARCH_TIMING_FIELDS
from app.main import app


DEFAULT_QUERIES = [
    "被告人夜间进入便利店盗窃现金和香烟，店内监控拍到过程，后退赃并取得谅解。",
    "多人酒后与他人发生争执并持械殴打，造成被害人轻伤，部分赔偿后认罪认罚。",
    "驾驶人醉酒后驾车追尾前车，血液酒精含量较高，事故后主动报警并赔偿损失。",
    "以虚构投资项目方式向熟人收款，承诺高额回报，到期无法返还并失联。",
    "员工利用保管公司货款便利，多次将收款转入个人账户，用于个人消费。",
]

LLM_TIMEOUT_REASONS = {"LLM_TIMEOUT", "SUMMARY_LLM_TIMEOUT"}
VECTOR_ERROR_PREFIXES = ("CHROMA_", "EMBEDDING_")
STOPLOSS_THRESHOLD_MS = 3000


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--queries-file", default="", help="Optional JSONL/text file with one query per line.")
    parser.add_argument("--max-queries", type=int, default=5, help="Sample size, capped to 20.")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--out", default="", help="Optional JSON output path.")
    args = parser.parse_args()

    queries = _load_queries(args.queries_file) if args.queries_file else DEFAULT_QUERIES
    max_queries = max(1, min(args.max_queries, 20))
    queries = queries[:max_queries]

    samples: list[dict[str, Any]] = []

    with TestClient(app) as client:
        health_response = client.get("/health")
        health_text = health_response.text
        health = _safe_json(health_response)

        for index, query in enumerate(queries, 1):
            started = perf_counter()
            response = client.post("/api/search", json={"query": query, "limit": args.limit})
            wall_ms = _elapsed_ms(started)
            body = _safe_json(response)
            sample = _sample_row(
                index=index,
                sample_group="cold" if index == 1 else "warm",
                query=query,
                status_code=response.status_code,
                wall_ms=wall_ms,
                body=body,
            )
            samples.append(sample)

    report = _build_report(
        health_status_code=health_response.status_code,
        health=health,
        health_text=health_text,
        samples=samples,
    )

    output = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        output_path = Path(args.out)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(output + "\n", encoding="utf-8")
    print(output)


def _load_queries(path: str) -> list[str]:
    rows: list[str] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("{"):
            payload = json.loads(line)
            value = payload.get("query") or payload.get("query_text") or payload.get("text")
            if value:
                rows.append(str(value))
        else:
            rows.append(line)
    return rows


def _safe_json(response: Any) -> dict[str, Any]:
    try:
        body = response.json()
    except Exception:  # noqa: BLE001 - smoke output should stay robust
        return {}
    return body if isinstance(body, dict) else {}


def _sample_row(
    *,
    index: int,
    sample_group: str,
    query: str,
    status_code: int,
    wall_ms: int,
    body: dict[str, Any],
) -> dict[str, Any]:
    timings = body.get("timings") if isinstance(body.get("timings"), dict) else {}
    degraded_reasons = body.get("degraded_reasons") if isinstance(body.get("degraded_reasons"), list) else []
    error = body.get("error") if isinstance(body.get("error"), dict) else None
    return {
        "sample_id": f"sample_{index:02d}",
        "sample_group": sample_group,
        "query_hash": hashlib.sha256(query.encode("utf-8")).hexdigest()[:16],
        "input_length": len(query),
        "status_code": status_code,
        "api_wall_ms": wall_ms,
        "query_session_id": body.get("query_session_id") or (error or {}).get("query_session_id"),
        "result_count": len(body.get("results") or []),
        "degraded": bool(body.get("degraded")) or bool(degraded_reasons),
        "degraded_reasons": degraded_reasons,
        "missing_timing_fields": [field for field in SEARCH_TIMING_FIELDS if field not in timings],
        "timings": {field: _int_value(timings.get(field)) for field in SEARCH_TIMING_FIELDS},
        "error_code": (error or {}).get("code"),
    }


def _build_report(
    *,
    health_status_code: int,
    health: dict[str, Any],
    health_text: str,
    samples: list[dict[str, Any]],
) -> dict[str, Any]:
    successful = [sample for sample in samples if sample["status_code"] == 200]
    cold_successful = [
        sample for sample in successful if sample.get("sample_group") == "cold"
    ]
    warm_successful = [
        sample for sample in successful if sample.get("sample_group") == "warm"
    ]
    status_counts = Counter(str(sample["status_code"]) for sample in samples)
    degraded_reason_counts = Counter(
        reason for sample in samples for reason in sample.get("degraded_reasons", [])
    )
    stage_stats = {
        field: _stats([sample["timings"][field] for sample in successful])
        for field in SEARCH_TIMING_FIELDS
    }
    cold_stage_stats = {
        field: _stats([sample["timings"][field] for sample in cold_successful])
        for field in SEARCH_TIMING_FIELDS
    }
    warm_stage_stats = {
        field: _stats([sample["timings"][field] for sample in warm_successful])
        for field in SEARCH_TIMING_FIELDS
    }
    api_wall_stats = _stats([sample["api_wall_ms"] for sample in samples])
    cold_api_wall_stats = _stats([
        sample["api_wall_ms"] for sample in samples if sample.get("sample_group") == "cold"
    ])
    warm_api_wall_stats = _stats([
        sample["api_wall_ms"] for sample in samples if sample.get("sample_group") == "warm"
    ])
    total_stats = _stats([sample["timings"]["total_duration_ms"] for sample in successful])
    cold_total_stats = _stats([sample["timings"]["total_duration_ms"] for sample in cold_successful])
    warm_total_stats = _stats([sample["timings"]["total_duration_ms"] for sample in warm_successful])
    slowest_stage = _slowest_stage(warm_stage_stats)
    stoploss_recommendations = _stoploss_recommendations(
        total_p95=warm_total_stats["p95"],
        api_wall_p95=warm_api_wall_stats["p95"],
        stage_stats=warm_stage_stats,
        degraded_reason_counts=degraded_reason_counts,
        warm_sample_count=len(warm_successful),
    )
    sample_count = len(samples)
    llm_timeout_count = sum(
        1
        for sample in samples
        if any(reason in LLM_TIMEOUT_REASONS for reason in sample.get("degraded_reasons", []))
    )
    vector_error_count = sum(
        1
        for sample in samples
        if any(
            str(reason).startswith(VECTOR_ERROR_PREFIXES)
            for reason in sample.get("degraded_reasons", [])
        )
    )
    missing_timing_samples = [
        sample["sample_id"]
        for sample in samples
        if sample.get("missing_timing_fields")
    ]

    return {
        "version": "day3_7_3_performance_smoke_v2",
        "scope": "small_sample_smoke_not_load_test",
        "sample_count": sample_count,
        "cold_sample_count": len([sample for sample in samples if sample.get("sample_group") == "cold"]),
        "warm_sample_count": len([sample for sample in samples if sample.get("sample_group") == "warm"]),
        "health": {
            "status_code": health_status_code,
            "returned_200_when_dependencies_unavailable": health_status_code == 200,
            "secret_value_leaked": _health_leaks_known_secret(health_text),
            "secrets_present": health.get("secrets_present"),
            "ollama_reachable": health.get("ollama_reachable"),
            "chroma_collection_queryable": health.get("chroma_collection_queryable"),
            "chroma_chunk_count": health.get("chroma_chunk_count"),
            "dependency_reasons": {
                "ollama": ((health.get("dependencies") or {}).get("ollama") or {}).get("degraded_reason"),
                "chroma": ((health.get("dependencies") or {}).get("chroma") or {}).get("degraded_reason"),
            },
        },
        "api": {
            "status_counts": dict(status_counts),
            "error_rate": _rate(sample_count - len(successful), sample_count),
            "p95_under_3s": bool(warm_total_stats["p95"] is not None and warm_total_stats["p95"] < STOPLOSS_THRESHOLD_MS),
            "warm_p95_under_3s": bool(
                warm_total_stats["p95"] is not None and warm_total_stats["p95"] < STOPLOSS_THRESHOLD_MS
            ),
            "api_wall_ms": api_wall_stats,
            "response_total_duration_ms": total_stats,
            "cold_api_wall_ms": cold_api_wall_stats,
            "cold_response_total_duration_ms": cold_total_stats,
            "warm_api_wall_ms": warm_api_wall_stats,
            "warm_response_total_duration_ms": warm_total_stats,
            "missing_timing_samples": missing_timing_samples,
        },
        "stability": {
            "llm_timeout_count": llm_timeout_count,
            "llm_timeout_rate": _rate(llm_timeout_count, sample_count),
            "vector_error_count": vector_error_count,
            "vector_error_rate": _rate(vector_error_count, sample_count),
            "degraded_query_count": sum(1 for sample in samples if sample.get("degraded")),
            "degraded_reason_counts": dict(degraded_reason_counts),
        },
        "stage_duration_ms": stage_stats,
        "cold_stage_duration_ms": cold_stage_stats,
        "warm_stage_duration_ms": warm_stage_stats,
        "slowest_stage_by_p95": slowest_stage,
        "slowest_warm_stage_by_p95": slowest_stage,
        "stoploss_recommendations": stoploss_recommendations,
        "samples": samples,
    }


def _stats(values: list[int]) -> dict[str, int | None]:
    if not values:
        return {"min": None, "max": None, "avg": None, "p95": None, "p99": None}
    return {
        "min": min(values),
        "max": max(values),
        "avg": round(sum(values) / len(values)),
        "p95": _percentile(values, 95),
        "p99": _percentile(values, 99),
    }


def _percentile(values: list[int], percentile: int) -> int:
    ordered = sorted(values)
    rank = max(1, (len(ordered) * percentile + 99) // 100)
    return ordered[min(rank - 1, len(ordered) - 1)]


def _slowest_stage(stage_stats: dict[str, dict[str, int | None]]) -> str | None:
    candidates = [
        (field, stats.get("p95"))
        for field, stats in stage_stats.items()
        if field != "total_duration_ms" and stats.get("p95") is not None
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda item: int(item[1] or 0))[0]


def _stoploss_recommendations(
    *,
    total_p95: int | None,
    api_wall_p95: int | None,
    stage_stats: dict[str, dict[str, int | None]],
    degraded_reason_counts: Counter[str],
    warm_sample_count: int,
) -> list[str]:
    recommendations: list[str] = []
    if warm_sample_count <= 0:
        return ["Warm P95 unavailable; run at least two samples so the first cold sample can be excluded."]
    p95 = max(total_p95 or 0, api_wall_p95 or 0)
    if p95 < STOPLOSS_THRESHOLD_MS:
        return ["P95 is below 3s in this smoke; keep current performance flags."]

    if int(stage_stats["summary_duration_ms"].get("p95") or 0) >= 1000:
        recommendations.append("Set ENABLE_SUMMARY=false to show source snippets without LLM summaries.")
    if (
        int(stage_stats["rewrite_duration_ms"].get("p95") or 0) >= 1000
        or degraded_reason_counts.get("LLM_TIMEOUT", 0) > 0
    ):
        recommendations.append(
            "Set ENABLE_QUERY_REWRITE=false, or lower QUERY_REWRITE_TIMEOUT_SECONDS, to bypass slow DeepSeek rewrite."
        )
    if int(stage_stats["embedding_duration_ms"].get("p95") or 0) >= 1000:
        recommendations.append("Lower EMBEDDING_TIMEOUT_SECONDS and rely on BM25 fallback when Ollama is slow.")
    if int(stage_stats["retrieval_duration_ms"].get("p95") or 0) >= 1000:
        recommendations.append("Lower CHROMA_QUERY_TIMEOUT_SECONDS and rely on BM25 fallback when Chroma is slow.")
    if not recommendations:
        recommendations.append("P95 exceeds 3s, but no single slow stage dominates; inspect per-sample timings before changing strategy.")
    return recommendations


def _health_leaks_known_secret(health_text: str) -> bool:
    secret_values = [
        value
        for key, value in os.environ.items()
        if "KEY" in key.upper() or "SECRET" in key.upper() or "TOKEN" in key.upper()
    ]
    return any(value and len(value) >= 8 and value in health_text for value in secret_values)


def _elapsed_ms(started: float) -> int:
    return int((perf_counter() - started) * 1000)


def _int_value(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _rate(count: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return round(count / total, 4)


if __name__ == "__main__":
    main()
