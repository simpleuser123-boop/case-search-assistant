"""M1.2-6 sanitized bad-case regression runner.

The runner reads raw product eval queries only in memory. Reports never write
raw query text, candidate text, or chunk text.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any


API_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from app.core.config import Settings, settings  # noqa: E402
from app.eval.product_eval import (  # noqa: E402
    COMPARISON_MODE_BM25_POOL_RERANK,
    COMPARISON_MODE_PRODUCT_CHAIN,
    DEFAULT_PRODUCT_CASES,
    DEFAULT_PRODUCT_CHUNKS,
    DEFAULT_PRODUCT_QRELS,
    DEFAULT_PRODUCT_QUERIES,
    DEFAULT_TOP_K,
    RELEVANCE_THRESHOLD,
    _bm25_pool_rows_and_candidates,
    load_product_case_ids,
    load_product_qrels,
    ndcg_at,
    precision_at,
    read_jsonl,
    top_k_has_hit,
    write_json,
)
from app.eval.result_format import (  # noqa: E402
    UNIFIED_EVAL_RESULT_VERSION,
    build_m13_regression_gate_summary,
    build_unified_eval_result,
    count_recall_misses_from_per_query,
    count_top10_misses_from_per_query,
)
from app.query_processing import QueryProcessingService, QueryValidationError  # noqa: E402
from app.query_processing.models import QueryPlan  # noqa: E402
from app.rerank import DEFAULT_RERANK_WEIGHTS, FactSimilarityReranker, RankedCaseCandidate  # noqa: E402
from app.rerank.service import (  # noqa: E402
    CASE_CAUSE_LOW_VECTOR_FLOOR,
    CASE_CAUSE_ONLY_CAP,
    FACT_SIGNAL_EPSILON,
)
from app.retrieval import BM25FallbackRetriever, VectorRetrievalService, merge_case_candidates  # noqa: E402
from app.retrieval.models import VectorCandidate  # noqa: E402


REPORT_VERSION = "m1_2_regression_report_v1"
PARAMETER_VERSION_NAME = "m1_2_6_guarded_rerank_v1"
DEFAULT_REGRESSION_SET = PROJECT_ROOT / "data/eval/m1_2_regression_set_20260609.json"


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _resolve(path: str | Path) -> Path:
    value = Path(path)
    return value.resolve() if value.is_absolute() else (PROJECT_ROOT / value).resolve()


def _relative(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def _round_metric(value: float | int | None) -> float | None:
    if value is None:
        return None
    return round(float(value), 4)


def _load_regression_set(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    queries = payload.get("queries", [])
    if not isinstance(queries, list) or not queries:
        raise ValueError("regression set must contain non-empty queries[]")
    seen: set[str] = set()
    for query in queries:
        query_id = str(query.get("queryId") or "").strip()
        if not query_id:
            raise ValueError("regression set query missing queryId")
        if query_id in seen:
            raise ValueError(f"duplicate regression queryId: {query_id}")
        seen.add(query_id)
    return payload


def _queries_by_id(queries: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(row.get("eval_query_id") or "").strip(): row for row in queries}


def _metric_row(ranked_ids: list[str], rels: dict[str, int]) -> dict[str, Any]:
    return {
        "Precision@5": _round_metric(precision_at(ranked_ids, rels, k=5)),
        "NDCG@10": _round_metric(ndcg_at(ranked_ids, rels, k=10)),
        "Top10 hit": top_k_has_hit(ranked_ids, rels, k=10),
    }


def _metric_summary(rows: list[dict[str, Any]], mode: str) -> dict[str, Any]:
    values = [row["metrics"][mode] for row in rows if row.get("evaluated")]
    if not values:
        return {
            "Precision@5": 0.0,
            "NDCG@10": 0.0,
            "Top10 hit rate": 0.0,
            "evaluatedQueryCount": 0,
        }
    return {
        "Precision@5": _round_metric(sum(float(item["Precision@5"]) for item in values) / len(values)),
        "NDCG@10": _round_metric(sum(float(item["NDCG@10"]) for item in values) / len(values)),
        "Top10 hit rate": _round_metric(sum(1 for item in values if item["Top10 hit"]) / len(values)),
        "evaluatedQueryCount": len(values),
    }


def _ranked_ids(rows: list[dict[str, Any]]) -> list[str]:
    return [str(row.get("case_id") or "") for row in rows]


def _row_for_ranked_item(
    item: RankedCaseCandidate,
    *,
    rank: int,
    score_field: str,
    final_score_source: str,
) -> dict[str, Any]:
    candidate = item.candidate
    breakdown = item.score_breakdown
    if score_field == "final_score":
        score = item.final_score
    else:
        score = breakdown.get(score_field)
    return {
        "rank": rank,
        "case_id": candidate.case_id,
        "score": _round_metric(float(score or 0.0)),
        "retrieval_score": _round_metric(float(candidate.retrieval_score)),
        "retrieval_source": candidate.retrieval_source,
        "candidateSource": candidate.candidate_source,
        "recallStage": candidate.recall_stage,
        "matchedByVector": candidate.matched_by_vector,
        "matchedByBm25": candidate.matched_by_bm25,
        "matchedByRewrite": candidate.matched_by_rewrite,
        "filteredReason": candidate.filtered_reason,
        "dedupReason": candidate.dedup_reason,
        "score_mode": breakdown.get("score_mode"),
        "final_score_source": final_score_source,
        "fusion_guards": breakdown.get("fusion_guards", []),
        "base_retrieval_score": breakdown.get("base_retrieval_score"),
        "raw_weighted_score": breakdown.get("raw_weighted_score"),
        "weighted_score": breakdown.get("weighted_score"),
        "feature_scores": {
            "vector_similarity": breakdown.get("vector_similarity"),
            "legal_element_overlap": breakdown.get("legal_element_overlap"),
            "case_cause_match": breakdown.get("case_cause_match"),
            "key_paragraph_match": breakdown.get("key_paragraph_match"),
            "authority_signal": breakdown.get("authority_signal"),
        },
        "effective_feature_scores": {
            "vector_similarity": breakdown.get("vector_similarity"),
            "legal_element_overlap": breakdown.get("effective_legal_element_overlap"),
            "case_cause_match": breakdown.get("effective_case_cause_match"),
            "key_paragraph_match": breakdown.get("effective_key_paragraph_match"),
            "authority_signal": breakdown.get("effective_authority_signal"),
        },
    }


def _ranked_to_rows(
    ranked: list[RankedCaseCandidate],
    *,
    top_k: int,
    score_field: str,
    final_score_source: str,
) -> list[dict[str, Any]]:
    return [
        _row_for_ranked_item(
            item,
            rank=index,
            score_field=score_field,
            final_score_source=final_score_source,
        )
        for index, item in enumerate(ranked[:top_k], 1)
    ]


def _before_ranked_from_scored(scored: list[RankedCaseCandidate]) -> list[RankedCaseCandidate]:
    return sorted(
        scored,
        key=lambda item: (
            float(item.score_breakdown.get("raw_weighted_score") or 0.0),
            float(item.score_breakdown.get("base_retrieval_score") or 0.0),
            -int(item.score_breakdown.get("input_rank") or 0),
        ),
        reverse=True,
    )


def _m1_2_guarded_ranked_from_scored(scored: list[RankedCaseCandidate]) -> list[RankedCaseCandidate]:
    return sorted(
        scored,
        key=lambda item: (
            float(item.score_breakdown.get("m1_2_guarded_score") or item.final_score or 0.0),
            float(item.score_breakdown.get("base_retrieval_score") or 0.0),
            -int(item.score_breakdown.get("input_rank") or 0),
        ),
        reverse=True,
    )


def _top10_with_relevance(rows: list[dict[str, Any]], rels: dict[str, int]) -> list[dict[str, Any]]:
    return [
        {
            "rank": row["rank"],
            "caseId": str(row.get("case_id") or ""),
            "score": row.get("score"),
            "relevance": int(rels.get(str(row.get("case_id") or ""), 0)),
            "finalScoreSource": row.get("final_score_source"),
            "fusionGuards": row.get("fusion_guards", []),
        }
        for row in rows[:10]
    ]


def _case_ids(rows: list[dict[str, Any]]) -> list[str]:
    return [str(row.get("case_id") or "") for row in rows[:10]]


def classify_change(
    before: dict[str, Any],
    after: dict[str, Any],
    *,
    comparable: bool = True,
) -> tuple[str, str, list[str]]:
    if not comparable:
        return "NOT_COMPARABLE", "不可比", ["not_comparable"]

    tags: list[str] = []
    before_p5 = float(before["Precision@5"])
    after_p5 = float(after["Precision@5"])
    before_ndcg = float(before["NDCG@10"])
    after_ndcg = float(after["NDCG@10"])
    before_hit = bool(before["Top10 hit"])
    after_hit = bool(after["Top10 hit"])
    eps = 1e-9

    improved = False
    regressed = False
    if after_hit and not before_hit:
        improved = True
        tags.append("top10_hit_recovered")
    if before_hit and not after_hit:
        regressed = True
        tags.append("top10_hit_lost")
    if after_p5 > before_p5 + eps:
        improved = True
        tags.append("precision_at_5_up")
    if after_p5 + eps < before_p5:
        regressed = True
        tags.append("precision_at_5_down")
    if after_ndcg > before_ndcg + eps:
        improved = True
        tags.append("ndcg_at_10_up")
    if after_ndcg + eps < before_ndcg:
        regressed = True
        tags.append("ndcg_at_10_down")

    if regressed:
        return "REGRESSED", "退化", tags
    if improved:
        return "IMPROVED", "改善", tags
    return "STABLE", "持平", ["metric_equal"]


def _current_candidate_rows(
    *,
    query_plan: QueryPlan,
    bm25_pool_candidates: list[VectorCandidate],
    retrieval_service: VectorRetrievalService,
    reranker: FactSimilarityReranker,
    top_k: int,
    comparison_mode: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    if comparison_mode == COMPARISON_MODE_BM25_POOL_RERANK:
        candidates = merge_case_candidates(bm25_pool_candidates)
        degraded_reasons: list[str] = []
    else:
        retrieval_result = retrieval_service.retrieve(query_plan, include_relaxed_recall=False)
        candidates = merge_case_candidates(retrieval_result.candidates)
        degraded_reasons = list(retrieval_result.degraded_reasons)

    scored_current = reranker.rerank(query_plan, candidates)
    scored_before = _before_ranked_from_scored(scored_current)
    scored_after = _m1_2_guarded_ranked_from_scored(scored_current)
    before_rows = _ranked_to_rows(
        scored_before,
        top_k=top_k,
        score_field="raw_weighted_score",
        final_score_source="raw_weighted_before_guard",
    )
    after_rows = _ranked_to_rows(
        scored_after,
        top_k=top_k,
        score_field="m1_2_guarded_score",
        final_score_source="m1_2_guarded_after",
    )
    return before_rows, after_rows, degraded_reasons


def _parameter_version_record(
    *,
    run_id: str,
    generated_at: str,
    comparison_mode: str,
    overall_metrics: dict[str, Any],
    label_distribution: dict[str, int],
    after_vs_baseline_label_distribution: dict[str, int],
    m13_regression_gate: dict[str, Any],
) -> dict[str, Any]:
    return {
        "versionName": PARAMETER_VERSION_NAME,
        "runId": run_id,
        "generatedAt": generated_at,
        "sourceStep": "M1.2-6 bad case regression and parameter freeze",
        "basedOn": [
            "M1.2-3 query understanding m1_2_query_understanding_v1",
            "M1.2-4 recall repair 20260609",
            "M1.2-5 guarded rerank fusion repair 20260609",
        ],
        "configItems": {
            "ENABLE_WEIGHTED_RERANK_global_default": bool(settings.ENABLE_WEIGHTED_RERANK),
            "evalWeightedRerankOnly": True,
            "comparisonMode": comparison_mode,
            "rerankWeights": DEFAULT_RERANK_WEIGHTS.as_dict(),
            "fusionGuards": {
                "noFactSupportUsesBaseRetrieval": True,
                "factSignalEpsilon": FACT_SIGNAL_EPSILON,
                "caseCauseLowVectorFloor": CASE_CAUSE_LOW_VECTOR_FLOOR,
                "caseCauseOnlyCap": CASE_CAUSE_ONLY_CAP,
            },
            "embeddingTimeoutSeconds": settings.EMBEDDING_TIMEOUT_SECONDS,
            "embeddingWarmupTimeoutSeconds": settings.EMBEDDING_WARMUP_TIMEOUT_SECONDS,
        },
        "metrics": overall_metrics,
        "labelDistribution": label_distribution,
        "afterVsBaselineLabelDistribution": after_vs_baseline_label_distribution,
        "m13RegressionGate": m13_regression_gate,
        "weightedRerankGrayCandidate": bool(m13_regression_gate["weightedRerankGrayCandidate"]),
        "scope": [
            "product_local_eval",
            "offline weighted rerank evaluation only",
            "does not enable public/default weighted rerank path",
        ],
        "rollback": [
            "Keep ENABLE_WEIGHTED_RERANK=false to stay on default base retrieval behavior.",
            "For code rollback, revert M1.2-5 guarded fusion changes in app/rerank/service.py and eval-only field additions.",
            "For evaluation rollback, use baseline BM25 product case-dedup rows from app.eval.product_eval.",
        ],
    }


def evaluate_regression(
    *,
    regression_set_path: Path,
    queries_path: Path,
    qrels_path: Path,
    cases_path: Path,
    chunks_path: Path,
    output_path: Path,
    parameter_version_path: Path | None,
    comparison_mode: str,
    top_k: int,
    enable_targeted_recall_repairs: bool = True,
) -> dict[str, Any]:
    if comparison_mode not in {COMPARISON_MODE_PRODUCT_CHAIN, COMPARISON_MODE_BM25_POOL_RERANK}:
        raise ValueError(f"Unsupported comparison mode: {comparison_mode}")

    generated_at = datetime.now().isoformat(timespec="seconds")
    run_id = f"m1_2_regression_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    regression_set = _load_regression_set(regression_set_path)
    set_items = regression_set["queries"]
    query_rows = _queries_by_id(read_jsonl(queries_path))
    qrels = load_product_qrels(qrels_path)
    product_case_ids = load_product_case_ids(cases_path)

    eval_config = Settings(
        ENABLE_QUERY_REWRITE=False,
        ENABLE_SUMMARY=False,
        ENABLE_EXPANDED_SEARCH=False,
        ENABLE_WEIGHTED_RERANK=True,
    )
    query_service = QueryProcessingService(config=eval_config)
    fallback_retriever = BM25FallbackRetriever(cases_path=cases_path, chunks_path=chunks_path)
    retrieval_service = VectorRetrievalService(
        fallback_retriever=fallback_retriever,
        enable_targeted_recall_repairs=enable_targeted_recall_repairs,
    )
    reranker = FactSimilarityReranker(config=eval_config, enabled=True)

    per_query: list[dict[str, Any]] = []
    missing_ids: list[str] = []
    blocked_items: set[str] = set()

    for item in set_items:
        query_id = str(item.get("queryId") or "").strip()
        query = query_rows.get(query_id)
        rels = qrels.get(query_id, {})
        if not query:
            missing_ids.append(query_id)
            per_query.append({
                "queryId": query_id,
                "sampleType": item.get("sampleType"),
                "tags": item.get("tags", []),
                "evaluated": False,
                "status": "missing_query",
                "changeLabel": "NOT_COMPARABLE",
                "changeLabelZh": "不可比",
                "changeTags": ["missing_query"],
            })
            blocked_items.add("regression_query_missing_from_product_eval_queries")
            continue
        if not rels:
            per_query.append({
                "queryId": query_id,
                "sampleType": item.get("sampleType"),
                "tags": item.get("tags", []),
                "evaluated": False,
                "status": "missing_qrels",
                "changeLabel": "NOT_COMPARABLE",
                "changeLabelZh": "不可比",
                "changeTags": ["missing_qrels"],
            })
            blocked_items.add("regression_query_missing_qrels")
            continue

        query_text = str(query.get("query_text") or "")
        expected_case_ids = sorted(
            case_id for case_id, score in rels.items() if score >= RELEVANCE_THRESHOLD
        )
        qrel_missing_from_corpus = sorted(set(expected_case_ids) - product_case_ids)

        try:
            query_plan = query_service.process(query_text)
            baseline_rows, bm25_pool_candidates = _bm25_pool_rows_and_candidates(
                query_plan=query_plan,
                fallback_retriever=fallback_retriever,
                top_k=top_k,
            )
            before_rows, after_rows, degraded_reasons = _current_candidate_rows(
                query_plan=query_plan,
                bm25_pool_candidates=bm25_pool_candidates,
                retrieval_service=retrieval_service,
                reranker=reranker,
                top_k=top_k,
                comparison_mode=comparison_mode,
            )
            status = "ok"
            error_type = None
        except QueryValidationError as exc:
            baseline_rows = []
            before_rows = []
            after_rows = []
            degraded_reasons = []
            status = "query_validation_error"
            error_type = exc.code
            blocked_items.add("query_validation_error")
        except Exception as exc:  # noqa: BLE001 - sanitized report only records class
            baseline_rows = []
            before_rows = []
            after_rows = []
            degraded_reasons = ["DEPENDENCY_UNAVAILABLE"]
            status = "partial"
            error_type = exc.__class__.__name__
            blocked_items.add("dependency_unavailable")

        baseline_ids = _ranked_ids(baseline_rows)
        before_ids = _ranked_ids(before_rows)
        after_ids = _ranked_ids(after_rows)
        metrics = {
            "baseline": _metric_row(baseline_ids, rels),
            "currentBefore": _metric_row(before_ids, rels),
            "currentAfter": _metric_row(after_ids, rels),
        }
        comparable = status == "ok"
        change_label, change_label_zh, change_tags = classify_change(
            metrics["currentBefore"],
            metrics["currentAfter"],
            comparable=comparable,
        )
        baseline_label, baseline_label_zh, baseline_tags = classify_change(
            metrics["baseline"],
            metrics["currentAfter"],
            comparable=comparable,
        )
        if _case_ids(before_rows) != _case_ids(after_rows) and comparable:
            change_tags = [*change_tags, "top10_order_or_membership_changed"]
        if _case_ids(baseline_rows) != _case_ids(after_rows) and comparable:
            baseline_tags = [*baseline_tags, "top10_order_or_membership_changed"]
        for reason in degraded_reasons:
            if any(marker in str(reason) for marker in ("UNAVAILABLE", "TIMEOUT", "FAILED")):
                blocked_items.add(str(reason))

        per_query.append({
            "queryId": query_id,
            "sampleType": item.get("sampleType"),
            "tags": item.get("tags", []),
            "sourceEvidence": item.get("sourceEvidence", []),
            "evaluated": True,
            "status": status,
            "errorType": error_type,
            "expectedCaseIds": expected_case_ids,
            "qrelsRef": {
                "path": _relative(qrels_path),
                "queryId": query_id,
                "relevanceThreshold": RELEVANCE_THRESHOLD,
            },
            "qrelCaseIdsMissingFromCorpus": qrel_missing_from_corpus,
            "baselineTop10": _top10_with_relevance(baseline_rows, rels),
            "currentBeforeTop10": _top10_with_relevance(before_rows, rels),
            "currentAfterTop10": _top10_with_relevance(after_rows, rels),
            "metrics": metrics,
            "changeLabel": change_label,
            "changeLabelZh": change_label_zh,
            "changeTags": change_tags,
            "afterVsBaselineLabel": baseline_label,
            "afterVsBaselineLabelZh": baseline_label_zh,
            "afterVsBaselineTags": baseline_tags,
            "degradedReasons": degraded_reasons,
        })

    label_distribution = dict(sorted(Counter(row["changeLabel"] for row in per_query).items()))
    after_vs_baseline_label_distribution = dict(
        sorted(Counter(row.get("afterVsBaselineLabel", "NOT_COMPARABLE") for row in per_query).items())
    )
    sample_distribution = dict(sorted(Counter(str(row.get("sampleType") or "") for row in per_query).items()))
    overall_metrics = {
        "baseline": _metric_summary(per_query, "baseline"),
        "currentBefore": _metric_summary(per_query, "currentBefore"),
        "currentAfter": _metric_summary(per_query, "currentAfter"),
    }
    metric_delta = {
        "afterVsBaseline": {
            "Precision@5": _round_metric(
                overall_metrics["currentAfter"]["Precision@5"] - overall_metrics["baseline"]["Precision@5"]
            ),
            "NDCG@10": _round_metric(
                overall_metrics["currentAfter"]["NDCG@10"] - overall_metrics["baseline"]["NDCG@10"]
            ),
            "Top10 hit rate": _round_metric(
                overall_metrics["currentAfter"]["Top10 hit rate"] - overall_metrics["baseline"]["Top10 hit rate"]
            ),
        },
        "afterVsBefore": {
            "Precision@5": _round_metric(
                overall_metrics["currentAfter"]["Precision@5"] - overall_metrics["currentBefore"]["Precision@5"]
            ),
            "NDCG@10": _round_metric(
                overall_metrics["currentAfter"]["NDCG@10"] - overall_metrics["currentBefore"]["NDCG@10"]
            ),
            "Top10 hit rate": _round_metric(
                overall_metrics["currentAfter"]["Top10 hit rate"]
                - overall_metrics["currentBefore"]["Top10 hit rate"]
            ),
        },
    }
    m13_regression_gate = build_m13_regression_gate_summary(
        top10_hit_rate=overall_metrics["currentAfter"]["Top10 hit rate"],
        evaluated_query_count=overall_metrics["currentAfter"]["evaluatedQueryCount"],
        before_vs_after_label_distribution=label_distribution,
        after_vs_baseline_label_distribution=after_vs_baseline_label_distribution,
        metric_regression_count=int(after_vs_baseline_label_distribution.get("REGRESSED", 0)),
        recall_miss_count=count_recall_misses_from_per_query(per_query, current_mode="currentAfter"),
        top10_miss_count=count_top10_misses_from_per_query(per_query, mode="currentAfter"),
        blocked_items=sorted(blocked_items),
    )
    unified_results = [
        build_unified_eval_result(
            run_id=run_id,
            generated_at=generated_at,
            eval_line="product_local_regression",
            dataset={
                "name": "m1_2_fixed_regression_set",
                "regressionSet": _relative(regression_set_path),
                "sourceQueries": _relative(queries_path),
                "sourceQrels": _relative(qrels_path),
                "queryCount": len(set_items),
                "sampleDistribution": sample_distribution,
            },
            candidate_corpus={
                "type": "product_local_cases_chunks",
                "cases": _relative(cases_path),
                "chunks": _relative(chunks_path),
                "comparisonMode": comparison_mode,
                "topK": top_k,
            },
            mode=mode,
            precision_at_5=overall_metrics[key]["Precision@5"],
            ndcg_at_10=overall_metrics[key]["NDCG@10"],
            top10_hit_rate=overall_metrics[key]["Top10 hit rate"],
            blocked_items=sorted(blocked_items),
            notes=notes,
        )
        for mode, key, notes in [
            ("baseline", "baseline", ["BM25 product case-dedup baseline."]),
            (
                "current_before",
                "currentBefore",
                ["Raw weighted rerank order reconstructed from score_breakdown.raw_weighted_score."],
            ),
            (
                "current_after",
                "currentAfter",
                ["Guarded rerank order from current M1.2-5 fusion repair code."],
            ),
        ]
    ]
    parameter_record = _parameter_version_record(
        run_id=run_id,
        generated_at=generated_at,
        comparison_mode=comparison_mode,
        overall_metrics=overall_metrics,
        label_distribution=label_distribution,
        after_vs_baseline_label_distribution=after_vs_baseline_label_distribution,
        m13_regression_gate=m13_regression_gate,
    )
    report = {
        "version": REPORT_VERSION,
        "runId": run_id,
        "generatedAt": generated_at,
        "unifiedResultVersion": UNIFIED_EVAL_RESULT_VERSION,
        "unifiedResults": unified_results,
        "privacy": {
            "rawQueryTextWritten": False,
            "candidateFullTextWritten": False,
            "chunkTextWritten": False,
            "newDataContainsRawFacts": False,
        },
        "inputs": {
            "regressionSet": _relative(regression_set_path),
            "queries": _relative(queries_path),
            "qrels": _relative(qrels_path),
            "cases": _relative(cases_path),
            "chunks": _relative(chunks_path),
        },
        "modes": {
            "baseline": "bm25_product_case_dedup",
            "currentBefore": "raw_weighted_rerank_before_guard_replay",
            "currentAfter": "guarded_weighted_rerank_after_m1_2_5",
            "comparisonMode": comparison_mode,
            "targetedRecallRepairsEnabled": enable_targeted_recall_repairs,
            "global_ENABLE_WEIGHTED_RERANK": bool(settings.ENABLE_WEIGHTED_RERANK),
            "featureFlagChanged": False,
        },
        "regressionSet": {
            "version": regression_set.get("version"),
            "queryCount": len(set_items),
            "sampleDistribution": sample_distribution,
            "missingQueryIds": missing_ids,
        },
        "overallMetrics": overall_metrics,
        "metricDelta": metric_delta,
        "labelDistribution": label_distribution,
        "afterVsBaselineLabelDistribution": after_vs_baseline_label_distribution,
        "m13RegressionGate": m13_regression_gate,
        "beforeVsAfterRegressedCount": m13_regression_gate["beforeVsAfterRegressedCount"],
        "afterVsBaselineRegressedCount": m13_regression_gate["afterVsBaselineRegressedCount"],
        "top10MissCount": m13_regression_gate["top10MissCount"],
        "metricRegressionCount": m13_regression_gate["metricRegressionCount"],
        "recallMissCount": m13_regression_gate["recallMissCount"],
        "grayCandidateHardGatePassed": m13_regression_gate["grayCandidateHardGatePassed"],
        "weightedRerankGrayCandidate": m13_regression_gate["weightedRerankGrayCandidate"],
        "blockedItems": sorted(blocked_items),
        "parameterVersion": parameter_record,
        "perQuery": per_query,
    }
    write_json(output_path, report)
    if parameter_version_path is not None:
        write_json(parameter_version_path, parameter_record)
    return report


def render_m13_regression_gate_markdown(report: dict[str, Any]) -> str:
    gate = report["m13RegressionGate"]
    overall = report["overallMetrics"]
    fields = [
        ("beforeVsAfterRegressedCount", gate["beforeVsAfterRegressedCount"]),
        ("afterVsBaselineRegressedCount", gate["afterVsBaselineRegressedCount"]),
        ("top10MissCount", gate["top10MissCount"]),
        ("metricRegressionCount", gate["metricRegressionCount"]),
        ("recallMissCount", gate["recallMissCount"]),
        ("grayCandidateHardGatePassed", gate["grayCandidateHardGatePassed"]),
        ("weightedRerankGrayCandidate", gate["weightedRerankGrayCandidate"]),
    ]
    predicate_rows = [
        ("Top10 hit rate >= 0.60", overall["currentAfter"]["Top10 hit rate"] >= 0.60),
        ("before -> after REGRESSED == 0", gate["beforeVsAfterRegressedCount"] == 0),
        ("after vs baseline REGRESSED == 0", gate["afterVsBaselineRegressedCount"] == 0),
        ("METRIC_REGRESSION == 0", gate["metricRegressionCount"] == 0),
    ]
    lines = [
        "# M1.3-2 Regression Gate",
        "",
        f"- Generated at: `{report['generatedAt']}`",
        "- Scope: M1.3-2 only, regression gate and runner hardening.",
        f"- Run id: `{report['runId']}`",
        f"- Weighted rerank gray candidate: `{str(gate['weightedRerankGrayCandidate']).lower()}`",
        f"- Gray candidate hard gate passed: `{str(gate['grayCandidateHardGatePassed']).lower()}`",
        "- Privacy: no raw query text, case fact text, candidate text, or chunk text is included.",
        "",
        "## Gate Fields",
        "",
        "| Field | Value |",
        "| --- | ---: |",
    ]
    for name, value in fields:
        lines.append(f"| `{name}` | `{str(value).lower() if isinstance(value, bool) else value}` |")

    lines.extend([
        "",
        "## Hard Gate Formula",
        "",
        f"`{gate['hardGateFormula']}`",
        "",
        "| Predicate | Passed |",
        "| --- | --- |",
    ])
    for label, passed in predicate_rows:
        lines.append(f"| {label} | `{str(passed).lower()}` |")

    failed = ", ".join(gate["hardGateFailedReasons"]) or "-"
    blocked = ", ".join(gate["blockedItems"]) or "-"
    lines.extend([
        "",
        f"- Failed reasons: `{failed}`",
        f"- Blocked items: `{blocked}`",
        "",
        "## Metrics",
        "",
        "| Mode | Precision@5 | NDCG@10 | Top10 hit rate | Evaluated queries |",
        "| --- | ---: | ---: | ---: | ---: |",
    ])
    for key in ("baseline", "currentBefore", "currentAfter"):
        metrics = overall[key]
        lines.append(
            f"| `{key}` | `{metrics['Precision@5']}` | `{metrics['NDCG@10']}` | "
            f"`{metrics['Top10 hit rate']}` | `{metrics['evaluatedQueryCount']}` |"
        )

    lines.extend([
        "",
        "## Regression Counts",
        "",
        f"- before -> after labels: `{json.dumps(report['labelDistribution'], ensure_ascii=False)}`",
        f"- after vs baseline labels: `{json.dumps(report['afterVsBaselineLabelDistribution'], ensure_ascii=False)}`",
        "",
        "## Boundaries",
        "",
        "- Search online logic was not modified.",
        "- Recall strategy, candidate merge strategy, rerank weights, and feature flag defaults were not modified.",
        "- This step does not implement M1.3-3 recall repair or M1.3-4 ranking repair.",
    ])
    return "\n".join(lines) + "\n"


def main() -> None:
    timestamp = _timestamp()
    parser = argparse.ArgumentParser()
    parser.add_argument("--regression-set", default=str(DEFAULT_REGRESSION_SET))
    parser.add_argument("--queries", default=str(DEFAULT_PRODUCT_QUERIES))
    parser.add_argument("--qrels", default=str(DEFAULT_PRODUCT_QRELS))
    parser.add_argument("--cases", default=str(DEFAULT_PRODUCT_CASES))
    parser.add_argument("--chunks", default=str(DEFAULT_PRODUCT_CHUNKS))
    parser.add_argument("--out", default=str(PROJECT_ROOT / f"docs/development/m1.2-regression-run-{timestamp}.json"))
    parser.add_argument("--parameter-version-out", default="")
    parser.add_argument("--gate-md-out", default="")
    parser.add_argument(
        "--comparison-mode",
        choices=[COMPARISON_MODE_PRODUCT_CHAIN, COMPARISON_MODE_BM25_POOL_RERANK],
        default=COMPARISON_MODE_PRODUCT_CHAIN,
    )
    parser.add_argument("--top-k", type=int, default=DEFAULT_TOP_K)
    parser.add_argument(
        "--targeted-recall-repairs",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Enable M1.3-3 targeted recall repairs; disable only for baseline replay.",
    )
    args = parser.parse_args()

    report = evaluate_regression(
        regression_set_path=_resolve(args.regression_set),
        queries_path=_resolve(args.queries),
        qrels_path=_resolve(args.qrels),
        cases_path=_resolve(args.cases),
        chunks_path=_resolve(args.chunks),
        output_path=_resolve(args.out),
        parameter_version_path=_resolve(args.parameter_version_out) if args.parameter_version_out else None,
        comparison_mode=args.comparison_mode,
        top_k=args.top_k,
        enable_targeted_recall_repairs=args.targeted_recall_repairs,
    )
    if args.gate_md_out:
        md_path = _resolve(args.gate_md_out)
        md_path.parent.mkdir(parents=True, exist_ok=True)
        md_path.write_text(render_m13_regression_gate_markdown(report), encoding="utf-8")
    print(json.dumps(
        {
            "status": "ok" if not report["blockedItems"] else "partial",
            "runId": report["runId"],
            "reportPath": str(_resolve(args.out)),
            "regressionSet": report["regressionSet"],
            "overallMetrics": report["overallMetrics"],
            "metricDelta": report["metricDelta"],
            "labelDistribution": report["labelDistribution"],
            "afterVsBaselineLabelDistribution": report["afterVsBaselineLabelDistribution"],
            "m13RegressionGate": report["m13RegressionGate"],
            "weightedRerankGrayCandidate": report["weightedRerankGrayCandidate"],
            "blockedItems": report["blockedItems"],
            "parameterVersion": {
                "versionName": report["parameterVersion"]["versionName"],
                "global_ENABLE_WEIGHTED_RERANK": report["parameterVersion"]["configItems"][
                    "ENABLE_WEIGHTED_RERANK_global_default"
                ],
            },
        },
        ensure_ascii=False,
        indent=2,
    ))


if __name__ == "__main__":
    main()
