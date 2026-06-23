"""M1.3-4 sanitized rerank regression repair runner.

The runner reads raw product queries only in memory. Reports write query ids,
case ids, ranks, scores, guard states, and metrics only.
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
    COMPARISON_MODE_PRODUCT_CHAIN,
    DEFAULT_PRODUCT_CASES,
    DEFAULT_PRODUCT_CHUNKS,
    DEFAULT_PRODUCT_QRELS,
    DEFAULT_PRODUCT_QUERIES,
    DEFAULT_TOP_K,
    RELEVANCE_THRESHOLD,
    _bm25_pool_rows_and_candidates,
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
    NO_FACT_GUARD_MULTI_SOURCE_BUCKET_BONUS,
    NO_FACT_GUARD_MULTI_SOURCE_VECTOR_FLOOR,
    NO_FACT_GUARD_STRONG_VECTOR_FLOOR,
    NO_FACT_GUARD_VECTOR_BUCKET_SIZE,
    NO_FACT_GUARD_WEAK_TIE_BREAK_WEIGHT,
)
from app.retrieval import BM25FallbackRetriever, VectorRetrievalService, merge_case_candidates  # noqa: E402
from app.retrieval.models import VectorCandidate  # noqa: E402
from scripts.m1_2_regression import (  # noqa: E402
    DEFAULT_REGRESSION_SET,
    _before_ranked_from_scored,
    _case_ids,
    _load_regression_set,
    _metric_row,
    _metric_summary,
    _queries_by_id,
    _ranked_ids,
    _ranked_to_rows,
    _relative,
    _round_metric,
    _top10_with_relevance,
    classify_change,
)


REPORT_VERSION = "m1_3_rerank_regression_repair_v1"
DEFAULT_TRIAGE = PROJECT_ROOT / "docs/development/m1.3-regression-triage-20260609-205353.json"
TARGET_CAUSES = {
    "RERANK_SUPPRESSION",
    "GUARD_OVER_CORRECTION",
    "SUCCESS_SAMPLE_PROTECTION",
}


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _resolve(path: str | Path) -> Path:
    value = Path(path)
    return value.resolve() if value.is_absolute() else (PROJECT_ROOT / value).resolve()


def _load_triage(path: Path) -> dict[str, dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("triage_items", [])
    if not isinstance(rows, list) or not rows:
        raise ValueError("M1.3 triage artifact must contain triage_items[]")
    return {
        str(row.get("query_id") or ""): row
        for row in rows
        if str(row.get("primary_cause") or "") in TARGET_CAUSES
    }


def _current_rows_with_candidate(
    *,
    query_plan: QueryPlan,
    bm25_pool_candidates: list[VectorCandidate],
    retrieval_service: VectorRetrievalService,
    reranker: FactSimilarityReranker,
    top_k: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    retrieval_result = retrieval_service.retrieve(query_plan, include_relaxed_recall=False)
    candidates = merge_case_candidates(retrieval_result.candidates or bm25_pool_candidates)
    scored_candidate = reranker.rerank(query_plan, candidates)
    scored_before = _before_ranked_from_scored(scored_candidate)
    scored_m1_2_after = sorted(
        scored_candidate,
        key=lambda item: (
            float(item.score_breakdown.get("m1_2_guarded_score") or 0.0),
            float(item.score_breakdown.get("base_retrieval_score") or 0.0),
            -int(item.score_breakdown.get("input_rank") or 0),
        ),
        reverse=True,
    )
    before_rows = _ranked_to_rows(
        scored_before,
        top_k=top_k,
        score_field="raw_weighted_score",
        final_score_source="raw_weighted_before_guard",
    )
    m1_2_after_rows = _ranked_to_rows(
        scored_m1_2_after,
        top_k=top_k,
        score_field="m1_2_guarded_score",
        final_score_source="m1_2_guarded_after",
    )
    candidate_rows = _ranked_to_rows(
        scored_candidate,
        top_k=top_k,
        score_field="final_score",
        final_score_source="m1_3_guarded_candidate",
    )
    return before_rows, m1_2_after_rows, candidate_rows, list(retrieval_result.degraded_reasons)


def _rows_by_case(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(row.get("case_id") or ""): row for row in rows}


def _compact_row(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "rank": row.get("rank"),
        "caseId": row.get("case_id"),
        "score": row.get("score"),
        "baseRetrievalScore": row.get("base_retrieval_score"),
        "rawWeightedScore": row.get("raw_weighted_score"),
        "weightedScore": row.get("weighted_score"),
        "finalScoreSource": row.get("final_score_source"),
        "fusionGuards": row.get("fusion_guards", []),
        "m1_2GuardedScore": row.get("m1_2_guarded_score"),
        "featureScores": row.get("feature_scores"),
        "effectiveFeatureScores": row.get("effective_feature_scores"),
        "retrievalSource": row.get("retrieval_source"),
        "recallStage": row.get("recallStage"),
        "matchedByVector": row.get("matchedByVector"),
        "matchedByBm25": row.get("matchedByBm25"),
        "matchedByRewrite": row.get("matchedByRewrite"),
    }


def _target_breakdown(
    *,
    query_id: str,
    triage_row: dict[str, Any],
    rels: dict[str, int],
    baseline_rows: list[dict[str, Any]],
    before_rows: list[dict[str, Any]],
    m1_2_after_rows: list[dict[str, Any]],
    candidate_rows: list[dict[str, Any]],
    metrics: dict[str, Any],
) -> dict[str, Any]:
    relevant_case_ids = [
        case_id for case_id, relevance in sorted(rels.items())
        if int(relevance) >= RELEVANCE_THRESHOLD
    ]
    baseline_by_case = _rows_by_case(baseline_rows)
    before_by_case = _rows_by_case(before_rows)
    m1_2_after_by_case = _rows_by_case(m1_2_after_rows)
    candidate_by_case = _rows_by_case(candidate_rows)
    relevant_rows = []
    for case_id in relevant_case_ids:
        candidate_row = candidate_by_case.get(case_id)
        guard_reasons = list((candidate_row or {}).get("fusion_guards", []))
        relevant_rows.append({
            "caseId": case_id,
            "relevance": int(rels.get(case_id, 0)),
            "baseline": _compact_row(baseline_by_case.get(case_id)),
            "currentBefore": _compact_row(before_by_case.get(case_id)),
            "m1_2GuardedAfter": _compact_row(m1_2_after_by_case.get(case_id)),
            "m1_3Candidate": _compact_row(candidate_row),
            "guardTriggered": "no_fact_support_base_retrieval" in guard_reasons,
            "guardReasons": guard_reasons,
            "finalRankChange": {
                "baseline": (baseline_by_case.get(case_id) or {}).get("rank"),
                "currentBefore": (before_by_case.get(case_id) or {}).get("rank"),
                "m1_2GuardedAfter": (m1_2_after_by_case.get(case_id) or {}).get("rank"),
                "m1_3Candidate": (candidate_row or {}).get("rank"),
            },
        })
    return {
        "queryId": query_id,
        "primaryCause": triage_row.get("primary_cause"),
        "priority": triage_row.get("priority"),
        "metrics": metrics,
        "degradationCauseEvidence": {
            "beforeVsM1_2After": classify_change(metrics["currentBefore"], metrics["m1_2GuardedAfter"])[0],
            "m1_2AfterVsBaseline": classify_change(metrics["baseline"], metrics["m1_2GuardedAfter"])[0],
            "beforeVsM1_3Candidate": classify_change(metrics["currentBefore"], metrics["m1_3Candidate"])[0],
            "m1_3CandidateVsBaseline": classify_change(metrics["baseline"], metrics["m1_3Candidate"])[0],
        },
        "top10GuardSummary": {
            "m1_2NoFactGuardCount": sum(
                1 for row in m1_2_after_rows[:10]
                if "no_fact_support_base_retrieval" in row.get("fusion_guards", [])
            ),
            "m1_3RelaxedGuardCount": sum(
                1 for row in candidate_rows[:10]
                if row.get("final_score_source") == "m1_3_guarded_candidate"
                and any(
                    str(reason).startswith("no_fact_guard_relaxed_")
                    for reason in row.get("fusion_guards", [])
                )
            ),
        },
        "relevantCaseBreakdown": relevant_rows,
    }


def _metric_delta(overall: dict[str, Any]) -> dict[str, Any]:
    return {
        "m1_3CandidateVsBaseline": {
            key: _round_metric(overall["m1_3Candidate"][key] - overall["baseline"][key])
            for key in ("Precision@5", "NDCG@10", "Top10 hit rate")
        },
        "m1_3CandidateVsM1_2GuardedAfter": {
            key: _round_metric(overall["m1_3Candidate"][key] - overall["m1_2GuardedAfter"][key])
            for key in ("Precision@5", "NDCG@10", "Top10 hit rate")
        },
        "m1_3CandidateVsCurrentBefore": {
            key: _round_metric(overall["m1_3Candidate"][key] - overall["currentBefore"][key])
            for key in ("Precision@5", "NDCG@10", "Top10 hit rate")
        },
    }


def evaluate_rerank_repair(
    *,
    regression_set_path: Path,
    triage_path: Path,
    queries_path: Path,
    qrels_path: Path,
    cases_path: Path,
    chunks_path: Path,
    output_path: Path,
    markdown_path: Path | None,
    top_k: int,
) -> dict[str, Any]:
    generated_at = datetime.now().isoformat(timespec="seconds")
    run_id = f"m1_3_rerank_regression_repair_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    regression_set = _load_regression_set(regression_set_path)
    set_items = regression_set["queries"]
    triage_targets = _load_triage(triage_path)
    query_rows = _queries_by_id(read_jsonl(queries_path))
    qrels = load_product_qrels(qrels_path)

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
        enable_targeted_recall_repairs=True,
    )
    reranker = FactSimilarityReranker(config=eval_config, enabled=True)

    per_query: list[dict[str, Any]] = []
    target_breakdowns: list[dict[str, Any]] = []
    blocked_items: set[str] = set()

    for item in set_items:
        query_id = str(item.get("queryId") or "").strip()
        query = query_rows.get(query_id)
        rels = qrels.get(query_id, {})
        if not query or not rels:
            blocked_items.add("regression_query_missing_input")
            continue

        try:
            query_plan = query_service.process(str(query.get("query_text") or ""))
            baseline_rows, bm25_pool_candidates = _bm25_pool_rows_and_candidates(
                query_plan=query_plan,
                fallback_retriever=fallback_retriever,
                top_k=top_k,
            )
            before_rows, m1_2_after_rows, candidate_rows, degraded_reasons = _current_rows_with_candidate(
                query_plan=query_plan,
                bm25_pool_candidates=bm25_pool_candidates,
                retrieval_service=retrieval_service,
                reranker=reranker,
                top_k=top_k,
            )
            status = "ok"
            error_type = None
        except QueryValidationError as exc:
            baseline_rows = []
            before_rows = []
            m1_2_after_rows = []
            candidate_rows = []
            degraded_reasons = []
            status = "query_validation_error"
            error_type = exc.code
            blocked_items.add("query_validation_error")
        except Exception as exc:  # noqa: BLE001 - report records sanitized class only
            baseline_rows = []
            before_rows = []
            m1_2_after_rows = []
            candidate_rows = []
            degraded_reasons = ["DEPENDENCY_UNAVAILABLE"]
            status = "partial"
            error_type = exc.__class__.__name__
            blocked_items.add("dependency_unavailable")

        metrics = {
            "baseline": _metric_row(_ranked_ids(baseline_rows), rels),
            "currentBefore": _metric_row(_ranked_ids(before_rows), rels),
            "m1_2GuardedAfter": _metric_row(_ranked_ids(m1_2_after_rows), rels),
            "m1_3Candidate": _metric_row(_ranked_ids(candidate_rows), rels),
        }
        comparable = status == "ok"
        m1_2_change_label, _, m1_2_change_tags = classify_change(
            metrics["currentBefore"],
            metrics["m1_2GuardedAfter"],
            comparable=comparable,
        )
        candidate_change_label, _, candidate_change_tags = classify_change(
            metrics["currentBefore"],
            metrics["m1_3Candidate"],
            comparable=comparable,
        )
        m1_2_baseline_label, _, m1_2_baseline_tags = classify_change(
            metrics["baseline"],
            metrics["m1_2GuardedAfter"],
            comparable=comparable,
        )
        candidate_baseline_label, _, candidate_baseline_tags = classify_change(
            metrics["baseline"],
            metrics["m1_3Candidate"],
            comparable=comparable,
        )
        if _case_ids(before_rows) != _case_ids(candidate_rows) and comparable:
            candidate_change_tags = [*candidate_change_tags, "top10_order_or_membership_changed"]
        if _case_ids(baseline_rows) != _case_ids(candidate_rows) and comparable:
            candidate_baseline_tags = [*candidate_baseline_tags, "top10_order_or_membership_changed"]
        for reason in degraded_reasons:
            if any(marker in str(reason) for marker in ("UNAVAILABLE", "TIMEOUT", "FAILED")):
                blocked_items.add(str(reason))

        row = {
            "queryId": query_id,
            "sampleType": item.get("sampleType"),
            "tags": item.get("tags", []),
            "evaluated": comparable,
            "status": status,
            "errorType": error_type,
            "expectedCaseIds": sorted(
                case_id for case_id, score in rels.items()
                if int(score) >= RELEVANCE_THRESHOLD
            ),
            "qrelsRef": {
                "path": _relative(qrels_path),
                "queryId": query_id,
                "relevanceThreshold": RELEVANCE_THRESHOLD,
            },
            "baselineTop10": _top10_with_relevance(baseline_rows, rels),
            "currentBeforeTop10": _top10_with_relevance(before_rows, rels),
            "m1_2GuardedAfterTop10": _top10_with_relevance(m1_2_after_rows, rels),
            "m1_3CandidateTop10": _top10_with_relevance(candidate_rows, rels),
            "metrics": metrics,
            "m1_2BeforeVsAfterLabel": m1_2_change_label,
            "m1_2BeforeVsAfterTags": m1_2_change_tags,
            "m1_2AfterVsBaselineLabel": m1_2_baseline_label,
            "m1_2AfterVsBaselineTags": m1_2_baseline_tags,
            "candidateBeforeVsAfterLabel": candidate_change_label,
            "candidateBeforeVsAfterTags": candidate_change_tags,
            "candidateAfterVsBaselineLabel": candidate_baseline_label,
            "candidateAfterVsBaselineTags": candidate_baseline_tags,
            "degradedReasons": degraded_reasons,
        }
        per_query.append(row)
        if query_id in triage_targets:
            target_breakdowns.append(_target_breakdown(
                query_id=query_id,
                triage_row=triage_targets[query_id],
                rels=rels,
                baseline_rows=baseline_rows,
                before_rows=before_rows,
                m1_2_after_rows=m1_2_after_rows,
                candidate_rows=candidate_rows,
                metrics=metrics,
            ))

    overall_metrics = {
        "baseline": _metric_summary(per_query, "baseline"),
        "currentBefore": _metric_summary(per_query, "currentBefore"),
        "m1_2GuardedAfter": _metric_summary(per_query, "m1_2GuardedAfter"),
        "m1_3Candidate": _metric_summary(per_query, "m1_3Candidate"),
    }
    candidate_label_distribution = dict(
        sorted(Counter(row["candidateBeforeVsAfterLabel"] for row in per_query).items())
    )
    candidate_after_vs_baseline_distribution = dict(
        sorted(Counter(row["candidateAfterVsBaselineLabel"] for row in per_query).items())
    )
    m1_2_label_distribution = dict(
        sorted(Counter(row["m1_2BeforeVsAfterLabel"] for row in per_query).items())
    )
    m1_2_after_vs_baseline_distribution = dict(
        sorted(Counter(row["m1_2AfterVsBaselineLabel"] for row in per_query).items())
    )
    m13_gate = build_m13_regression_gate_summary(
        top10_hit_rate=overall_metrics["m1_3Candidate"]["Top10 hit rate"],
        evaluated_query_count=overall_metrics["m1_3Candidate"]["evaluatedQueryCount"],
        before_vs_after_label_distribution=candidate_label_distribution,
        after_vs_baseline_label_distribution=candidate_after_vs_baseline_distribution,
        metric_regression_count=int(candidate_after_vs_baseline_distribution.get("REGRESSED", 0)),
        recall_miss_count=count_recall_misses_from_per_query(per_query, current_mode="m1_3Candidate"),
        top10_miss_count=count_top10_misses_from_per_query(per_query, mode="m1_3Candidate"),
        blocked_items=sorted(blocked_items),
    )
    unified_results = [
        build_unified_eval_result(
            run_id=run_id,
            generated_at=generated_at,
            eval_line="product_local_m1_3_rerank_regression_repair",
            dataset={
                "name": "m1_2_fixed_regression_set",
                "regressionSet": _relative(regression_set_path),
                "sourceQueries": _relative(queries_path),
                "sourceQrels": _relative(qrels_path),
                "queryCount": len(set_items),
            },
            candidate_corpus={
                "type": "product_local_cases_chunks",
                "cases": _relative(cases_path),
                "chunks": _relative(chunks_path),
                "comparisonMode": COMPARISON_MODE_PRODUCT_CHAIN,
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
            ("m1_2_current_before", "currentBefore", ["Raw weighted rerank before M1.2 guard."]),
            ("m1_2_guarded_after", "m1_2GuardedAfter", ["Legacy M1.2 no-fact base retrieval guard replay."]),
            ("m1_3_candidate", "m1_3Candidate", ["M1.3 vector-bucket no-fact guard candidate."]),
        ]
    ]
    report = {
        "version": REPORT_VERSION,
        "runId": run_id,
        "generatedAt": generated_at,
        "unifiedResultVersion": UNIFIED_EVAL_RESULT_VERSION,
        "unifiedResults": unified_results,
        "privacy": {
            "rawQueryTextWritten": False,
            "caseFactTextWritten": False,
            "candidateFullTextWritten": False,
            "chunkTextWritten": False,
            "newDataContainsRawFacts": False,
        },
        "inputs": {
            "regressionSet": _relative(regression_set_path),
            "triage": _relative(triage_path),
            "queries": _relative(queries_path),
            "qrels": _relative(qrels_path),
            "cases": _relative(cases_path),
            "chunks": _relative(chunks_path),
        },
        "modes": {
            "baseline": "bm25_product_case_dedup",
            "currentBefore": "raw_weighted_rerank_before_guard_replay",
            "m1_2GuardedAfter": "legacy_m1_2_guarded_no_fact_base_retrieval",
            "m1_3Candidate": "m1_3_no_fact_vector_bucket_guard_candidate",
            "comparisonMode": COMPARISON_MODE_PRODUCT_CHAIN,
            "targetedRecallRepairsEnabled": True,
            "global_ENABLE_WEIGHTED_RERANK": bool(settings.ENABLE_WEIGHTED_RERANK),
            "featureFlagChanged": False,
        },
        "repairRules": {
            "noFactSupportGuardRelaxation": {
                "strongVectorFloor": NO_FACT_GUARD_STRONG_VECTOR_FLOOR,
                "multiSourceVectorFloor": NO_FACT_GUARD_MULTI_SOURCE_VECTOR_FLOOR,
                "vectorBucketSize": NO_FACT_GUARD_VECTOR_BUCKET_SIZE,
                "multiSourceBucketBonus": NO_FACT_GUARD_MULTI_SOURCE_BUCKET_BONUS,
                "weakSignalTieBreakWeight": NO_FACT_GUARD_WEAK_TIE_BREAK_WEIGHT,
                "weakSignalsEffectiveScoreStillZeroWithoutFactSupport": True,
            },
            "m1_2Compatibility": {
                "factSignalEpsilon": FACT_SIGNAL_EPSILON,
                "caseCauseLowVectorFloor": CASE_CAUSE_LOW_VECTOR_FLOOR,
                "caseCauseOnlyCap": CASE_CAUSE_ONLY_CAP,
                "legacyGuardedScoreField": "m1_2_guarded_score",
            },
        },
        "overallMetrics": overall_metrics,
        "metricDelta": _metric_delta(overall_metrics),
        "m1_2LabelDistribution": m1_2_label_distribution,
        "m1_2AfterVsBaselineLabelDistribution": m1_2_after_vs_baseline_distribution,
        "candidateLabelDistribution": candidate_label_distribution,
        "candidateAfterVsBaselineLabelDistribution": candidate_after_vs_baseline_distribution,
        "m13RegressionGate": m13_gate,
        "beforeVsAfterRegressedCount": m13_gate["beforeVsAfterRegressedCount"],
        "afterVsBaselineRegressedCount": m13_gate["afterVsBaselineRegressedCount"],
        "top10MissCount": m13_gate["top10MissCount"],
        "metricRegressionCount": m13_gate["metricRegressionCount"],
        "recallMissCount": m13_gate["recallMissCount"],
        "grayCandidateHardGatePassed": m13_gate["grayCandidateHardGatePassed"],
        "weightedRerankGrayCandidate": m13_gate["weightedRerankGrayCandidate"],
        "blockedItems": sorted(blocked_items),
        "targetQueryIds": sorted(triage_targets),
        "targetScoreBreakdowns": target_breakdowns,
        "perQuery": per_query,
    }
    write_json(output_path, report)
    if markdown_path is not None:
        markdown_path.parent.mkdir(parents=True, exist_ok=True)
        markdown_path.write_text(render_markdown(report), encoding="utf-8")
    return report


def render_markdown(report: dict[str, Any]) -> str:
    def best_relevant_breakdown(item: dict[str, Any]) -> dict[str, Any]:
        rows = item.get("relevantCaseBreakdown", [])
        ranked_rows = [
            row for row in rows
            if (row.get("m1_3Candidate") or {}).get("rank") is not None
        ]
        if ranked_rows:
            return min(ranked_rows, key=lambda row: int((row.get("m1_3Candidate") or {}).get("rank") or 9999))
        return rows[0] if rows else {}

    def compact_mode(row: dict[str, Any], mode: str) -> str:
        score_row = row.get(mode) or {}
        rank = score_row.get("rank")
        score = score_row.get("score")
        if rank is None:
            return "-"
        return f"r{rank}/s{score}"

    def compact_guard(row: dict[str, Any]) -> str:
        guards = (row.get("m1_3Candidate") or {}).get("fusionGuards") or []
        return ",".join(str(guard) for guard in guards) or "-"

    def compact_signal(row: dict[str, Any], key: str) -> str:
        signals = (row.get("m1_3Candidate") or {}).get("featureScores") or {}
        value = signals.get(key)
        return "-" if value is None else str(value)

    def compact_effective_weak(row: dict[str, Any]) -> str:
        signals = (row.get("m1_3Candidate") or {}).get("effectiveFeatureScores") or {}
        key_value = signals.get("key_paragraph_match")
        authority_value = signals.get("authority_signal")
        return f"key={key_value},auth={authority_value}"

    gate = report["m13RegressionGate"]
    overall = report["overallMetrics"]
    lines = [
        "# M1.3-4 Rerank Regression Repair",
        "",
        f"- Generated at: `{report['generatedAt']}`",
        f"- Run id: `{report['runId']}`",
        "- Scope: M1.3-4 only, rerank guard targeted repair.",
        "- Privacy: no raw query, case fact, candidate, or chunk text is included.",
        f"- grayCandidateHardGatePassed: `{str(gate['grayCandidateHardGatePassed']).lower()}`",
        f"- weightedRerankGrayCandidate: `{str(gate['weightedRerankGrayCandidate']).lower()}`",
        "",
        "## Four-Line Metrics",
        "",
        "| Line | Precision@5 | NDCG@10 | Top10 hit rate | Evaluated |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for key in ("baseline", "currentBefore", "m1_2GuardedAfter", "m1_3Candidate"):
        metrics = overall[key]
        lines.append(
            f"| `{key}` | `{metrics['Precision@5']}` | `{metrics['NDCG@10']}` | "
            f"`{metrics['Top10 hit rate']}` | `{metrics['evaluatedQueryCount']}` |"
        )
    lines.extend([
        "",
        "## Regression Counts",
        "",
        f"- M1.2 before -> guarded after: `{json.dumps(report['m1_2LabelDistribution'], ensure_ascii=False)}`",
        "- M1.3 before -> candidate: "
        f"`{json.dumps(report['candidateLabelDistribution'], ensure_ascii=False)}`",
        "- M1.2 guarded after vs baseline: "
        f"`{json.dumps(report['m1_2AfterVsBaselineLabelDistribution'], ensure_ascii=False)}`",
        "- M1.3 candidate vs baseline: "
        f"`{json.dumps(report['candidateAfterVsBaselineLabelDistribution'], ensure_ascii=False)}`",
        f"- METRIC_REGRESSION: `{report['metricRegressionCount']}`",
        f"- RECALL_MISS: `{report['recallMissCount']}`",
        f"- Top10 miss: `{report['top10MissCount']}`",
        "",
        "## Guard Rule",
        "",
        "- Relax no-fact-support base guard only when actual vector similarity is strong "
        "or a vector candidate has multi-source consensus.",
        "- Candidate score uses nearest `0.1` vector bucket, a `0.01` multi-source bonus, "
        "and a `0.002` raw weighted tie-break inside the bucket.",
        "- Key paragraph and authority effective scores remain `0` without fact support.",
        "",
        "## Target Query Outcomes",
        "",
        "| Query id | Cause | before->M1.2 | before->M1.3 | M1.2 vs baseline | M1.3 vs baseline | Candidate relevant ranks |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ])
    for item in report["targetScoreBreakdowns"]:
        ranks = [
            str((row["m1_3Candidate"] or {}).get("rank"))
            for row in item["relevantCaseBreakdown"]
            if (row["m1_3Candidate"] or {}).get("rank") is not None
        ]
        evidence = item["degradationCauseEvidence"]
        lines.append(
            f"| `{item['queryId']}` | `{item['primaryCause']}` | "
            f"`{evidence['beforeVsM1_2After']}` | `{evidence['beforeVsM1_3Candidate']}` | "
            f"`{evidence['m1_2AfterVsBaseline']}` | `{evidence['m1_3CandidateVsBaseline']}` | "
            f"`{','.join(ranks) or '-'}` |"
        )
    lines.extend([
        "",
        "## Best Relevant Score Split",
        "",
        "| Query id | Cause | Baseline | Current before | M1.2 guarded | M1.3 candidate | Vector | Key raw | Auth raw | Effective weak | M1.3 guard reasons |",
        "| --- | --- | --- | --- | --- | --- | ---: | ---: | ---: | --- | --- |",
    ])
    for item in report["targetScoreBreakdowns"]:
        row = best_relevant_breakdown(item)
        lines.append(
            f"| `{item['queryId']}` | `{item['primaryCause']}` | "
            f"`{compact_mode(row, 'baseline')}` | `{compact_mode(row, 'currentBefore')}` | "
            f"`{compact_mode(row, 'm1_2GuardedAfter')}` | `{compact_mode(row, 'm1_3Candidate')}` | "
            f"`{compact_signal(row, 'vector_similarity')}` | `{compact_signal(row, 'key_paragraph_match')}` | "
            f"`{compact_signal(row, 'authority_signal')}` | `{compact_effective_weak(row)}` | "
            f"`{compact_guard(row)}` |"
        )
    lines.extend([
        "",
        "## Hard Gate",
        "",
        f"- Formula: `{gate['hardGateFormula']}`",
        f"- Failed reasons: `{', '.join(gate['hardGateFailedReasons']) or '-'}`",
        f"- Blocked items: `{', '.join(gate['blockedItems']) or '-'}`",
        "",
        "## Scope Confirmation",
        "",
        "- Recall pool, candidate merge, BM25, query mapping, TopK, qrels, and eval samples were not modified.",
        "- `ENABLE_WEIGHTED_RERANK` default was not changed.",
        "- This report does not enter M1.3-5 candidate comparison or parameter finalization.",
    ])
    return "\n".join(lines) + "\n"


def main() -> None:
    timestamp = _timestamp()
    parser = argparse.ArgumentParser()
    parser.add_argument("--regression-set", default=str(DEFAULT_REGRESSION_SET))
    parser.add_argument("--triage", default=str(DEFAULT_TRIAGE))
    parser.add_argument("--queries", default=str(DEFAULT_PRODUCT_QUERIES))
    parser.add_argument("--qrels", default=str(DEFAULT_PRODUCT_QRELS))
    parser.add_argument("--cases", default=str(DEFAULT_PRODUCT_CASES))
    parser.add_argument("--chunks", default=str(DEFAULT_PRODUCT_CHUNKS))
    parser.add_argument("--top-k", type=int, default=DEFAULT_TOP_K)
    parser.add_argument(
        "--out",
        default=str(PROJECT_ROOT / f"docs/development/m1.3-rerank-regression-repair-{timestamp}.json"),
    )
    parser.add_argument(
        "--md-out",
        default=str(PROJECT_ROOT / f"docs/development/m1.3-rerank-regression-repair-{timestamp}.md"),
    )
    args = parser.parse_args()
    report = evaluate_rerank_repair(
        regression_set_path=_resolve(args.regression_set),
        triage_path=_resolve(args.triage),
        queries_path=_resolve(args.queries),
        qrels_path=_resolve(args.qrels),
        cases_path=_resolve(args.cases),
        chunks_path=_resolve(args.chunks),
        output_path=_resolve(args.out),
        markdown_path=_resolve(args.md_out) if args.md_out else None,
        top_k=args.top_k,
    )
    print(json.dumps(
        {
            "status": "ok" if not report["blockedItems"] else "partial",
            "runId": report["runId"],
            "reportPath": str(_resolve(args.out)),
            "overallMetrics": report["overallMetrics"],
            "candidateLabelDistribution": report["candidateLabelDistribution"],
            "candidateAfterVsBaselineLabelDistribution": report["candidateAfterVsBaselineLabelDistribution"],
            "m13RegressionGate": report["m13RegressionGate"],
            "weightedRerankGrayCandidate": report["weightedRerankGrayCandidate"],
        },
        ensure_ascii=False,
        indent=2,
    ))


if __name__ == "__main__":
    main()
