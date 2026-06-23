"""M1.3x legal candidate robustness and anti-overfit gate.

This runner validates the offline-only legal score-shape router v2 candidate
without changing online search/rerank behavior. Qrels are used only after a
candidate order has been selected from non-label score-shape features.
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

from app.core.config import settings  # noqa: E402
from app.eval.product_eval import (  # noqa: E402
    DEFAULT_PRODUCT_CASES,
    DEFAULT_PRODUCT_CHUNKS,
    DEFAULT_PRODUCT_QRELS,
    DEFAULT_PRODUCT_QUERIES,
    DEFAULT_TOP_K,
    load_product_case_ids,
    load_product_qrels,
    read_jsonl,
    write_json,
)
from app.eval.result_format import (  # noqa: E402
    build_m13_regression_gate_summary,
    count_recall_misses_from_per_query,
    count_top10_misses_from_per_query,
)
from app.query_processing import QueryProcessingService, QueryValidationError  # noqa: E402
from app.rerank import FactSimilarityReranker  # noqa: E402
from app.retrieval import BM25FallbackRetriever, VectorRetrievalService  # noqa: E402
from scripts.m1_2_regression import (  # noqa: E402
    DEFAULT_REGRESSION_SET,
    _load_regression_set,
    _metric_row,
    _metric_summary,
    _ranked_ids,
    classify_change,
)
from scripts.m1_3_candidate_comparison import (  # noqa: E402
    CANDIDATES,
    M13X_LEGAL_SCORE_SHAPE_CURRENT_STD_MAX,
    M13X_LEGAL_SCORE_SHAPE_GUARD_MEAN_FLOOR,
    M13X_LEGAL_SCORE_SHAPE_TOP_N,
    M13X_LEGAL_SCORE_SHAPE_V2_CURRENT_RANK8_GAP_MAX,
    _baseline_protection_fields,
    _blocked_from_degraded,
    _candidate_decision_audit,
    _candidate_query_row,
    _candidate_scope_failed_reasons,
    _case_ids,
    _feature_flag_file_state,
    _legal_score_shape_router_rows,
    _m1_3x_regression_zero_upper_bound_rows,
    _privacy_check,
    _relative,
    _resolve,
    _rows_for_query,
)


REPORT_VERSION = "m1_3x_legal_candidate_robustness_v1"
PARAMETER_VERSION_NAME = "m1_3x_legal_candidate_robustness"
SELECTED_CANDIDATE_ID = "m1_3x_legal_score_shape_router_v2_candidate"
UPPER_BOUND_CANDIDATE_ID = "m1_3x_regression_zero_upper_bound_candidate"
SENSITIVITY_GRID = [
    0.0,
    0.00025,
    0.0005,
    0.00075,
    0.001,
    0.00125,
    0.0015,
    0.002,
    0.003,
]
DEFAULT_FOLD_COUNT = 5
DEFAULT_REPEAT_COUNT = 3
SOURCE_ARTIFACTS = [
    "docs/development/m1.3x-legal-regression-zero-candidate-comparison-20260611-131141.md",
    "docs/development/m1.3x-legal-regression-zero-candidate-comparison-20260611-131141.json",
    "docs/development/m1.3x-legal-regression-zero-candidate-parameter-version-20260611-131141.json",
    "docs/development/m1.3x-legal-regression-zero-candidate-comparison-20260611-125336.json",
    "apps/api/scripts/m1_3_candidate_comparison.py",
    "apps/api/tests/test_m1_3_candidate_comparison.py",
    "apps/api/app/core/config.py",
    ".env.example",
]


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _selected_candidate_spec() -> Any:
    return next(row for row in CANDIDATES if row.candidate_id == SELECTED_CANDIDATE_ID)


def _upper_bound_candidate_spec() -> Any:
    return next(row for row in CANDIDATES if row.candidate_id == UPPER_BOUND_CANDIDATE_ID)


def _round(value: float | int | None, digits: int = 4) -> float | None:
    if value is None:
        return None
    return round(float(value), digits)


def _router_rows_for_threshold(
    rows_by_mode: dict[str, list[dict[str, Any]]],
    *,
    current_rank8_gap_max: float,
    top_k: int,
) -> list[dict[str, Any]]:
    """Route by score shape only; no qrels/query-id/case-id inputs."""

    return _legal_score_shape_router_rows(
        rows_by_mode,
        top_k=top_k,
        current_rank8_gap_max=current_rank8_gap_max,
        final_score_source=SELECTED_CANDIDATE_ID,
        router_reason_code="legal_score_shape_router_v2_robustness_non_label_only",
    )


def _collect_query_contexts(
    *,
    queries_path: Path,
    qrels_path: Path,
    cases_path: Path,
    chunks_path: Path,
    top_k: int,
) -> dict[str, Any]:
    queries = read_jsonl(queries_path)
    qrels = load_product_qrels(qrels_path)
    product_case_ids = load_product_case_ids(cases_path)
    qrel_candidate_ids = {case_id for rels in qrels.values() for case_id in rels}
    missing_qrel_ids = sorted(qrel_candidate_ids - product_case_ids)

    query_service = QueryProcessingService()
    fallback_retriever = BM25FallbackRetriever(cases_path=cases_path, chunks_path=chunks_path)
    retrieval_services = {
        False: VectorRetrievalService(
            fallback_retriever=fallback_retriever,
            enable_targeted_recall_repairs=False,
        ),
        True: VectorRetrievalService(
            fallback_retriever=fallback_retriever,
            enable_targeted_recall_repairs=True,
        ),
    }
    reranker = FactSimilarityReranker(enabled=True)

    contexts: list[dict[str, Any]] = []
    raw_queries: list[str] = []
    blocked_items: set[str] = set()
    if missing_qrel_ids:
        blocked_items.add("product_qrels_case_id_missing_from_candidate_corpus")
    if len(queries) < 20:
        blocked_items.add("product_query_count_below_20")

    for query in queries:
        query_id = str(query.get("eval_query_id") or "").strip()
        query_text = str(query.get("query_text") or "")
        raw_queries.append(query_text)
        rels = qrels.get(query_id, {})
        if not query_id or not rels:
            blocked_items.add("product_query_missing_qrels")
            contexts.append({
                "queryId": query_id,
                "evaluated": False,
                "status": "missing_qrels",
                "rowsByMode": {"baseline": []},
                "rels": {},
                "degradedReasons": [],
            })
            continue

        try:
            query_plan = query_service.process(query_text)
            rows_by_mode, degraded_reasons = _rows_for_query(
                query_plan=query_plan,
                fallback_retriever=fallback_retriever,
                retrieval_services=retrieval_services,
                reranker=reranker,
                top_k=top_k,
            )
            status = "ok"
            blocked_items.update(_blocked_from_degraded(degraded_reasons))
        except QueryValidationError as exc:
            rows_by_mode = {"baseline": []}
            degraded_reasons = []
            status = f"query_validation_error:{exc.code}"
            blocked_items.add("query_validation_error")
        except Exception as exc:  # noqa: BLE001 - sanitized class only.
            rows_by_mode = {"baseline": []}
            degraded_reasons = ["DEPENDENCY_UNAVAILABLE"]
            status = f"partial:{exc.__class__.__name__}"
            blocked_items.add("dependency_unavailable")

        contexts.append({
            "queryId": query_id,
            "evaluated": status == "ok",
            "status": status,
            "rowsByMode": rows_by_mode,
            "rels": rels,
            "degradedReasons": list(degraded_reasons),
        })

    fold_map = _deterministic_fold_map(
        [str(row["queryId"]) for row in contexts if row.get("evaluated")],
        fold_count=DEFAULT_FOLD_COUNT,
    )
    for context in contexts:
        context["foldId"] = fold_map.get(str(context["queryId"]))

    return {
        "contexts": contexts,
        "rawQueries": raw_queries,
        "globalBlockedItems": sorted(blocked_items),
    }


def _deterministic_fold_map(query_ids: list[str], *, fold_count: int) -> dict[str, int]:
    return {
        query_id: index % fold_count
        for index, query_id in enumerate(sorted(query_ids))
    }


def _candidate_row_for_threshold(
    context: dict[str, Any],
    *,
    current_rank8_gap_max: float,
    top_k: int,
    regression_ids: set[str],
) -> dict[str, Any]:
    query_id = str(context.get("queryId") or "")
    rows_by_mode = context.get("rowsByMode") or {"baseline": []}
    rels = context.get("rels") or {}
    comparable = bool(context.get("evaluated"))
    current_rows = (
        _router_rows_for_threshold(
            rows_by_mode,
            current_rank8_gap_max=current_rank8_gap_max,
            top_k=top_k,
        )
        if comparable
        else []
    )
    baseline_rows = rows_by_mode.get("baseline", [])
    before_rows = rows_by_mode.get("raw_weighted_with_recall", [])
    metrics = _metric_row(_ranked_ids(current_rows), rels)
    baseline_metrics = _metric_row(_ranked_ids(baseline_rows), rels)
    before_metrics = _metric_row(_ranked_ids(before_rows), rels)
    before_label, _, before_tags = classify_change(
        before_metrics,
        metrics,
        comparable=comparable,
    )
    after_baseline_label, _, after_baseline_tags = classify_change(
        baseline_metrics,
        metrics,
        comparable=comparable,
    )
    if comparable and _case_ids(before_rows) != _case_ids(current_rows):
        before_tags = [*before_tags, "top10_order_or_membership_changed"]
    if comparable and _case_ids(baseline_rows) != _case_ids(current_rows):
        after_baseline_tags = [*after_baseline_tags, "top10_order_or_membership_changed"]
    baseline_protection = _baseline_protection_fields(
        baseline_rows=baseline_rows,
        candidate_rows=current_rows,
        rels=rels,
        after_baseline_label=after_baseline_label,
        after_baseline_tags=after_baseline_tags,
    )
    return _candidate_query_row(
        query_id=query_id,
        evaluated=comparable,
        status=str(context.get("status") or "partial"),
        metrics=metrics,
        baseline_metrics=baseline_metrics,
        before_metrics=before_metrics,
        before_label=before_label,
        after_baseline_label=after_baseline_label,
        degraded_reasons=list(context.get("degradedReasons") or []),
        is_regression=query_id in regression_ids,
        before_tags=before_tags,
        after_baseline_tags=after_baseline_tags,
        baseline_protection=baseline_protection,
        decision_audit=_candidate_decision_audit(current_rows),
    )


def _rows_for_threshold(
    contexts: list[dict[str, Any]],
    *,
    current_rank8_gap_max: float,
    top_k: int,
    regression_ids: set[str],
    query_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    selected_contexts = [
        context
        for context in contexts
        if query_ids is None or str(context.get("queryId") or "") in query_ids
    ]
    return [
        _candidate_row_for_threshold(
            context,
            current_rank8_gap_max=current_rank8_gap_max,
            top_k=top_k,
            regression_ids=regression_ids,
        )
        for context in selected_contexts
    ]


def _summary_for_rows(
    rows: list[dict[str, Any]],
    *,
    gate_rows: list[dict[str, Any]],
    threshold: float,
    blocked_items: list[str],
) -> dict[str, Any]:
    product_rows = [
        {"evaluated": row["evaluated"], "metrics": {"candidate": row["metrics"]["candidate"]}}
        for row in rows
    ]
    baseline_rows = [
        {"evaluated": row["evaluated"], "metrics": {"baseline": row["metrics"]["baseline"]}}
        for row in rows
    ]
    candidate_metrics = _metric_summary(product_rows, "candidate")
    baseline_metrics = _metric_summary(baseline_rows, "baseline")
    before_distribution = dict(sorted(Counter(
        row["beforeVsAfterLabel"] for row in gate_rows if row.get("evaluated")
    ).items()))
    after_distribution = dict(sorted(Counter(
        row["afterVsBaselineLabel"] for row in gate_rows if row.get("evaluated")
    ).items()))
    top10_miss_count = count_top10_misses_from_per_query(
        [{"evaluated": row["evaluated"], "metrics": {"candidate": row["metrics"]["candidate"]}} for row in rows],
        mode="candidate",
    )
    recall_miss_count = count_recall_misses_from_per_query(
        [
            {
                "evaluated": row["evaluated"],
                "metrics": {
                    "baseline": row["metrics"]["baseline"],
                    "candidate": row["metrics"]["candidate"],
                },
            }
            for row in rows
        ],
        current_mode="candidate",
    )
    gate = build_m13_regression_gate_summary(
        top10_hit_rate=candidate_metrics["Top10 hit rate"],
        evaluated_query_count=candidate_metrics["evaluatedQueryCount"],
        before_vs_after_label_distribution=before_distribution,
        after_vs_baseline_label_distribution=after_distribution,
        metric_regression_count=int(after_distribution.get("REGRESSED", 0)),
        recall_miss_count=recall_miss_count,
        top10_miss_count=top10_miss_count,
        blocked_items=blocked_items,
    )
    failed_reasons = list(gate["hardGateFailedReasons"])
    if float(candidate_metrics["NDCG@10"]) <= float(baseline_metrics["NDCG@10"]):
        failed_reasons.append("NDCG_AT_10_NOT_GT_BASELINE")
    if float(candidate_metrics["Precision@5"]) < float(baseline_metrics["Precision@5"]):
        failed_reasons.append("PRECISION_AT_5_LT_BASELINE")
    go_no_go = "GO" if not failed_reasons else "NO_GO"
    return {
        "threshold": threshold,
        "currentRank8GapMax": threshold,
        "goNoGo": go_no_go,
        "failedReasons": failed_reasons,
        "Precision@5": candidate_metrics["Precision@5"],
        "baselinePrecision@5": baseline_metrics["Precision@5"],
        "NDCG@10": candidate_metrics["NDCG@10"],
        "baselineNDCG@10": baseline_metrics["NDCG@10"],
        "Top10 hit rate": candidate_metrics["Top10 hit rate"],
        "baselineTop10 hit rate": baseline_metrics["Top10 hit rate"],
        "evaluatedQueryCount": candidate_metrics["evaluatedQueryCount"],
        "beforeVsAfterRegressedCount": gate["beforeVsAfterRegressedCount"],
        "afterVsBaselineRegressedCount": gate["afterVsBaselineRegressedCount"],
        "METRIC_REGRESSION count": gate["metricRegressionCount"],
        "RECALL_MISS count": gate["recallMissCount"],
        "top10MissCount": gate["top10MissCount"],
        "labelDistribution": before_distribution,
        "afterVsBaselineLabelDistribution": after_distribution,
        "hardGateFailedReasons": gate["hardGateFailedReasons"],
    }


def _threshold_summary(
    contexts: list[dict[str, Any]],
    *,
    threshold: float,
    top_k: int,
    regression_ids: set[str],
    blocked_items: list[str],
    query_ids: set[str] | None = None,
    gate_query_ids: set[str] | None = None,
) -> dict[str, Any]:
    rows = _rows_for_threshold(
        contexts,
        current_rank8_gap_max=threshold,
        top_k=top_k,
        regression_ids=regression_ids,
        query_ids=query_ids,
    )
    if gate_query_ids is None:
        gate_rows = [
            row for row in rows if str(row.get("queryId") or "") in regression_ids
        ]
    else:
        gate_rows = [
            row for row in rows if str(row.get("queryId") or "") in gate_query_ids
        ]
    return _summary_for_rows(
        rows,
        gate_rows=gate_rows,
        threshold=threshold,
        blocked_items=blocked_items,
    )


def _grid_summaries(
    contexts: list[dict[str, Any]],
    *,
    top_k: int,
    regression_ids: set[str],
    blocked_items: list[str],
) -> list[dict[str, Any]]:
    return [
        _threshold_summary(
            contexts,
            threshold=threshold,
            top_k=top_k,
            regression_ids=regression_ids,
            blocked_items=blocked_items,
        )
        for threshold in SENSITIVITY_GRID
    ]


def _select_training_threshold(training_results: list[dict[str, Any]]) -> dict[str, Any]:
    def key(row: dict[str, Any]) -> tuple[int, float, float, float, float, float]:
        passed = 1 if row["goNoGo"] == "GO" else 0
        return (
            passed,
            float(row["NDCG@10"]),
            float(row["Top10 hit rate"]),
            float(row["Precision@5"]),
            -abs(float(row["threshold"]) - M13X_LEGAL_SCORE_SHAPE_V2_CURRENT_RANK8_GAP_MAX),
            -float(row["threshold"]),
        )

    return max(training_results, key=key)


def _cross_validation(
    contexts: list[dict[str, Any]],
    *,
    top_k: int,
    regression_ids: set[str],
) -> dict[str, Any]:
    evaluated_ids = sorted(str(row["queryId"]) for row in contexts if row.get("evaluated"))
    folds = sorted({int(row["foldId"]) for row in contexts if row.get("foldId") is not None})
    fold_results: list[dict[str, Any]] = []
    validation_rows_all: list[dict[str, Any]] = []
    fixed_v2_fold_results: list[dict[str, Any]] = []
    fixed_v2_rows_all: list[dict[str, Any]] = []

    for fold_id in folds:
        validation_ids = {
            str(row["queryId"])
            for row in contexts
            if row.get("evaluated") and int(row.get("foldId")) == fold_id
        }
        training_ids = set(evaluated_ids) - validation_ids
        training_results = [
            _threshold_summary(
                contexts,
                threshold=threshold,
                top_k=top_k,
                regression_ids=regression_ids,
                blocked_items=[],
                query_ids=training_ids,
                gate_query_ids=training_ids,
            )
            for threshold in SENSITIVITY_GRID
        ]
        selected_training = _select_training_threshold(training_results)
        validation_summary = _threshold_summary(
            contexts,
            threshold=float(selected_training["threshold"]),
            top_k=top_k,
            regression_ids=regression_ids,
            blocked_items=[],
            query_ids=validation_ids,
            gate_query_ids=validation_ids,
        )
        fixed_v2_summary = _threshold_summary(
            contexts,
            threshold=M13X_LEGAL_SCORE_SHAPE_V2_CURRENT_RANK8_GAP_MAX,
            top_k=top_k,
            regression_ids=regression_ids,
            blocked_items=[],
            query_ids=validation_ids,
            gate_query_ids=validation_ids,
        )
        validation_rows_all.extend(_rows_for_threshold(
            contexts,
            current_rank8_gap_max=float(selected_training["threshold"]),
            top_k=top_k,
            regression_ids=regression_ids,
            query_ids=validation_ids,
        ))
        fixed_v2_rows_all.extend(_rows_for_threshold(
            contexts,
            current_rank8_gap_max=M13X_LEGAL_SCORE_SHAPE_V2_CURRENT_RANK8_GAP_MAX,
            top_k=top_k,
            regression_ids=regression_ids,
            query_ids=validation_ids,
        ))
        fold_results.append({
            "foldId": fold_id,
            "validationQueryCount": len(validation_ids),
            "trainingSelectedThreshold": selected_training["threshold"],
            "trainingSelectedGoNoGo": selected_training["goNoGo"],
            "trainingSelectedFailedReasons": selected_training["failedReasons"],
            "validationGoNoGo": validation_summary["goNoGo"],
            "validationFailedReasons": validation_summary["failedReasons"],
            "validation": validation_summary,
        })
        fixed_v2_fold_results.append({
            "foldId": fold_id,
            "validationQueryCount": len(validation_ids),
            "threshold": M13X_LEGAL_SCORE_SHAPE_V2_CURRENT_RANK8_GAP_MAX,
            "validationGoNoGo": fixed_v2_summary["goNoGo"],
            "validationFailedReasons": fixed_v2_summary["failedReasons"],
            "validation": fixed_v2_summary,
        })

    aggregate = _summary_for_rows(
        validation_rows_all,
        gate_rows=validation_rows_all,
        threshold=-1.0,
        blocked_items=[],
    )
    fixed_v2_aggregate = _summary_for_rows(
        fixed_v2_rows_all,
        gate_rows=fixed_v2_rows_all,
        threshold=M13X_LEGAL_SCORE_SHAPE_V2_CURRENT_RANK8_GAP_MAX,
        blocked_items=[],
    )
    return {
        "foldCount": len(folds),
        "method": "deterministic_query_id_sorted_round_robin_k_fold",
        "folds": fold_results,
        "aggregateValidation": aggregate,
        "fixedV2Folds": fixed_v2_fold_results,
        "fixedV2AggregateValidation": fixed_v2_aggregate,
    }


def _upper_bound_difference_audit(
    contexts: list[dict[str, Any]],
    *,
    top_k: int,
) -> list[dict[str, Any]]:
    audit_rows: list[dict[str, Any]] = []
    for context in contexts:
        if not context.get("evaluated"):
            continue
        rows_by_mode = context.get("rowsByMode") or {}
        rels = context.get("rels") or {}
        baseline_rows = rows_by_mode.get("baseline", [])
        before_rows = rows_by_mode.get("raw_weighted_with_recall", [])
        v2_rows = _router_rows_for_threshold(
            rows_by_mode,
            current_rank8_gap_max=M13X_LEGAL_SCORE_SHAPE_V2_CURRENT_RANK8_GAP_MAX,
            top_k=top_k,
        )
        upper_rows = _m1_3x_regression_zero_upper_bound_rows(
            rows_by_mode,
            rels=rels,
            top_k=top_k,
        )
        baseline_metrics = _metric_row(_ranked_ids(baseline_rows), rels)
        before_metrics = _metric_row(_ranked_ids(before_rows), rels)
        v2_metrics = _metric_row(_ranked_ids(v2_rows), rels)
        upper_metrics = _metric_row(_ranked_ids(upper_rows), rels)
        v2_before_label, _, _ = classify_change(before_metrics, v2_metrics)
        v2_baseline_label, _, _ = classify_change(baseline_metrics, v2_metrics)
        upper_before_label, _, _ = classify_change(before_metrics, upper_metrics)
        upper_baseline_label, _, _ = classify_change(baseline_metrics, upper_metrics)
        v2_decision = _candidate_decision_audit(v2_rows)
        upper_decision = _candidate_decision_audit(upper_rows)
        v2_top10 = set(_ranked_ids(v2_rows)[:10])
        upper_top10 = set(_ranked_ids(upper_rows)[:10])
        audit_rows.append({
            "queryId": str(context.get("queryId") or ""),
            "v2SourceMode": v2_decision.get("selectedSourceMode"),
            "upperBoundSourceMode": upper_decision.get("selectedSourceMode"),
            "sourceModeDiffers": v2_decision.get("selectedSourceMode") != upper_decision.get("selectedSourceMode"),
            "v2BeforeVsAfterLabel": v2_before_label,
            "v2AfterVsBaselineLabel": v2_baseline_label,
            "upperBeforeVsAfterLabel": upper_before_label,
            "upperAfterVsBaselineLabel": upper_baseline_label,
            "precisionDeltaUpperMinusV2": _round(
                float(upper_metrics["Precision@5"]) - float(v2_metrics["Precision@5"])
            ),
            "ndcgDeltaUpperMinusV2": _round(
                float(upper_metrics["NDCG@10"]) - float(v2_metrics["NDCG@10"])
            ),
            "top10HitSame": bool(upper_metrics["Top10 hit"]) == bool(v2_metrics["Top10 hit"]),
            "top10OverlapCount": len(v2_top10 & upper_top10),
            "v2UsesQrelsForRanking": False,
            "upperBoundUsesQrelsForRanking": True,
            "upperBoundScopeBlocked": True,
            "v2ReasonCodes": list(v2_decision.get("reasonCodes") or []),
            "upperBoundReasonCodes": list(upper_decision.get("reasonCodes") or []),
        })
    return audit_rows


def _repeatability(
    *,
    repeats: list[dict[str, Any]],
) -> dict[str, Any]:
    signatures = [
        {
            "selectedCandidateId": SELECTED_CANDIDATE_ID,
            "goNoGo": repeat["v2Frozen"]["goNoGo"],
            "Precision@5": repeat["v2Frozen"]["Precision@5"],
            "NDCG@10": repeat["v2Frozen"]["NDCG@10"],
            "Top10 hit rate": repeat["v2Frozen"]["Top10 hit rate"],
            "beforeVsAfterRegressedCount": repeat["v2Frozen"]["beforeVsAfterRegressedCount"],
            "afterVsBaselineRegressedCount": repeat["v2Frozen"]["afterVsBaselineRegressedCount"],
            "METRIC_REGRESSION count": repeat["v2Frozen"]["METRIC_REGRESSION count"],
            "RECALL_MISS count": repeat["v2Frozen"]["RECALL_MISS count"],
        }
        for repeat in repeats
    ]
    first = signatures[0] if signatures else {}
    return {
        "repeatCount": len(signatures),
        "consistent": bool(signatures) and all(signature == first for signature in signatures),
        "signatures": signatures,
    }


def _sensitivity_gate(grid_results: list[dict[str, Any]]) -> dict[str, Any]:
    passing = [row for row in grid_results if row["goNoGo"] == "GO"]
    neighborhood = [
        row
        for row in grid_results
        if 0.00075 <= float(row["threshold"]) <= 0.00125
    ]
    passing_neighborhood = [row for row in neighborhood if row["goNoGo"] == "GO"]
    selected_passes = any(
        float(row["threshold"]) == M13X_LEGAL_SCORE_SHAPE_V2_CURRENT_RANK8_GAP_MAX
        and row["goNoGo"] == "GO"
        for row in grid_results
    )
    return {
        "selectedThresholdPasses": selected_passes,
        "passingThresholds": [row["threshold"] for row in passing],
        "passingNeighborhoodThresholds": [row["threshold"] for row in passing_neighborhood],
        "notSinglePointPass": selected_passes and len(passing) >= 2 and len(passing_neighborhood) >= 2,
    }


def _robust_gate(
    *,
    v2_frozen: dict[str, Any],
    cross_validation: dict[str, Any],
    sensitivity_gate: dict[str, Any],
    repeatability: dict[str, Any],
    privacy: dict[str, Any],
    default_feature_flag_state: dict[str, Any],
) -> dict[str, Any]:
    failures: list[str] = []
    if v2_frozen["goNoGo"] != "GO":
        failures.append("FROZEN_HARD_GATE_FAILED")
    for fold in cross_validation["folds"]:
        validation = fold["validation"]
        if int(validation["beforeVsAfterRegressedCount"]) != 0:
            failures.append(f"CV_FOLD_{fold['foldId']}_BEFORE_AFTER_REGRESSION")
        if int(validation["afterVsBaselineRegressedCount"]) != 0:
            failures.append(f"CV_FOLD_{fold['foldId']}_AFTER_BASELINE_REGRESSION")
        if int(validation["METRIC_REGRESSION count"]) != 0:
            failures.append(f"CV_FOLD_{fold['foldId']}_METRIC_REGRESSION")
    for fold in cross_validation["fixedV2Folds"]:
        validation = fold["validation"]
        if int(validation["beforeVsAfterRegressedCount"]) != 0:
            failures.append(f"FIXED_V2_FOLD_{fold['foldId']}_BEFORE_AFTER_REGRESSION")
        if int(validation["afterVsBaselineRegressedCount"]) != 0:
            failures.append(f"FIXED_V2_FOLD_{fold['foldId']}_AFTER_BASELINE_REGRESSION")
        if int(validation["METRIC_REGRESSION count"]) != 0:
            failures.append(f"FIXED_V2_FOLD_{fold['foldId']}_METRIC_REGRESSION")

    aggregate = cross_validation["aggregateValidation"]
    if float(aggregate["Top10 hit rate"]) < 0.60:
        failures.append("CV_AGG_TOP10_HIT_RATE_BELOW_0_60")
    if float(aggregate["NDCG@10"]) <= float(aggregate["baselineNDCG@10"]):
        failures.append("CV_AGG_NDCG_NOT_GT_BASELINE")
    if float(aggregate["Precision@5"]) < float(aggregate["baselinePrecision@5"]):
        failures.append("CV_AGG_PRECISION_LT_BASELINE")
    fixed_aggregate = cross_validation["fixedV2AggregateValidation"]
    if float(fixed_aggregate["Top10 hit rate"]) < 0.60:
        failures.append("FIXED_V2_CV_AGG_TOP10_HIT_RATE_BELOW_0_60")
    if float(fixed_aggregate["NDCG@10"]) <= float(fixed_aggregate["baselineNDCG@10"]):
        failures.append("FIXED_V2_CV_AGG_NDCG_NOT_GT_BASELINE")
    if float(fixed_aggregate["Precision@5"]) < float(fixed_aggregate["baselinePrecision@5"]):
        failures.append("FIXED_V2_CV_AGG_PRECISION_LT_BASELINE")
    if not sensitivity_gate["notSinglePointPass"]:
        failures.append("SENSITIVITY_SINGLE_POINT_OR_NEIGHBORHOOD_FAILURE")
    if not repeatability["consistent"] or repeatability["repeatCount"] < DEFAULT_REPEAT_COUNT:
        failures.append("REPEATABILITY_FAILED")
    if default_feature_flag_state["settings_ENABLE_WEIGHTED_RERANK"] is not False:
        failures.append("ENABLE_WEIGHTED_RERANK_NOT_FALSE")
    if not privacy["passed"]:
        failures.append("PRIVACY_CHECK_FAILED")
    return {
        "status": "ROBUST_GO" if not failures else "NO_GO",
        "failedReasons": sorted(set(failures)),
    }


def _render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# M1.3x Legal Candidate Robustness",
        "",
        f"- Generated at: `{report['generated_at']}`",
        f"- Selected candidate: `{report['selected_candidate_id']}`",
        f"- Robust status: `{report['robust_status']}`",
        f"- No-Go reason: `{', '.join(report['robust_gate']['failedReasons']) or '-'}`",
        "- Scope: offline candidate comparison and robustness only; no online retrieval/rerank/default flag change; M2 not entered.",
        "- Privacy: no raw query text, case fact text, candidate text, or chunk text is included.",
        "",
        "## Parameter Grid",
        "",
        f"- currentRank8GapMax grid: `{report['parameter_grid']['currentRank8GapMax']}`",
        f"- Frozen selected threshold: `{report['selected_threshold']}`",
        "",
        "## Frozen Sensitivity",
        "",
        "| Threshold | GO/NO_GO | P@5 | NDCG@10 | Top10 | before->after REGRESSED | after-vs-baseline REGRESSED | METRIC_REGRESSION | RECALL_MISS | Failed reasons |",
        "| ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for row in report["threshold_sensitivity"]:
        lines.append(
            f"| `{row['threshold']}` | `{row['goNoGo']}` | `{row['Precision@5']}` | "
            f"`{row['NDCG@10']}` | `{row['Top10 hit rate']}` | "
            f"`{row['beforeVsAfterRegressedCount']}` | `{row['afterVsBaselineRegressedCount']}` | "
            f"`{row['METRIC_REGRESSION count']}` | `{row['RECALL_MISS count']}` | "
            f"`{', '.join(row['failedReasons']) or '-'}` |"
        )
    lines.extend([
        "",
        "## Cross Validation",
        "",
        "| Fold | Train selected threshold | Validation GO/NO_GO | P@5 | NDCG@10 | Top10 | before->after REGRESSED | after-vs-baseline REGRESSED | METRIC_REGRESSION | Failed reasons |",
        "| ---: | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ])
    for fold in report["cross_validation"]["folds"]:
        validation = fold["validation"]
        lines.append(
            f"| `{fold['foldId']}` | `{fold['trainingSelectedThreshold']}` | "
            f"`{fold['validationGoNoGo']}` | `{validation['Precision@5']}` | "
            f"`{validation['NDCG@10']}` | `{validation['Top10 hit rate']}` | "
            f"`{validation['beforeVsAfterRegressedCount']}` | "
            f"`{validation['afterVsBaselineRegressedCount']}` | "
            f"`{validation['METRIC_REGRESSION count']}` | "
            f"`{', '.join(fold['validationFailedReasons']) or '-'}` |"
        )
    lines.extend([
        "",
        "## Fixed V2 Validation Folds",
        "",
        "| Fold | Threshold | Validation GO/NO_GO | P@5 | NDCG@10 | Top10 | before->after REGRESSED | after-vs-baseline REGRESSED | METRIC_REGRESSION | Failed reasons |",
        "| ---: | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ])
    for fold in report["cross_validation"]["fixedV2Folds"]:
        validation = fold["validation"]
        lines.append(
            f"| `{fold['foldId']}` | `{fold['threshold']}` | `{fold['validationGoNoGo']}` | "
            f"`{validation['Precision@5']}` | `{validation['NDCG@10']}` | "
            f"`{validation['Top10 hit rate']}` | `{validation['beforeVsAfterRegressedCount']}` | "
            f"`{validation['afterVsBaselineRegressedCount']}` | "
            f"`{validation['METRIC_REGRESSION count']}` | "
            f"`{', '.join(fold['validationFailedReasons']) or '-'}` |"
        )
    aggregate = report["cross_validation"]["aggregateValidation"]
    fixed_aggregate = report["cross_validation"]["fixedV2AggregateValidation"]
    lines.extend([
        "",
        "## Aggregates",
        "",
        f"- CV selected-threshold Top10 hit rate: `{aggregate['Top10 hit rate']}`",
        f"- CV selected-threshold NDCG@10: `{aggregate['NDCG@10']}` vs baseline `{aggregate['baselineNDCG@10']}`",
        f"- CV selected-threshold Precision@5: `{aggregate['Precision@5']}` vs baseline `{aggregate['baselinePrecision@5']}`",
        f"- Fixed v2 CV Top10 hit rate: `{fixed_aggregate['Top10 hit rate']}`",
        f"- Fixed v2 CV NDCG@10: `{fixed_aggregate['NDCG@10']}` vs baseline `{fixed_aggregate['baselineNDCG@10']}`",
        f"- Fixed v2 CV Precision@5: `{fixed_aggregate['Precision@5']}` vs baseline `{fixed_aggregate['baselinePrecision@5']}`",
        "",
        "## Upper-Bound Difference Audit",
        "",
        "- Sanitized fields only: query id, source modes, labels, metric deltas, top10 overlap counts, reason codes.",
        "| Query | V2 source | Upper source | Differs | V2 baseline label | Upper baseline label | NDCG delta upper-v2 | P@5 delta upper-v2 | Top10 overlap |",
        "| --- | --- | --- | --- | --- | --- | ---: | ---: | ---: |",
    ])
    for row in report["upper_bound_difference_audit"]:
        lines.append(
            f"| `{row['queryId']}` | `{row['v2SourceMode']}` | `{row['upperBoundSourceMode']}` | "
            f"`{str(row['sourceModeDiffers']).lower()}` | `{row['v2AfterVsBaselineLabel']}` | "
            f"`{row['upperAfterVsBaselineLabel']}` | `{row['ndcgDeltaUpperMinusV2']}` | "
            f"`{row['precisionDeltaUpperMinusV2']}` | `{row['top10OverlapCount']}` |"
        )
    lines.extend([
        "",
        "## Repeatability",
        "",
        f"- Repeat count: `{report['repeatability']['repeatCount']}`",
        f"- Consistent: `{str(report['repeatability']['consistent']).lower()}`",
        "",
        "## Rollback And Defaults",
        "",
        f"- ENABLE_WEIGHTED_RERANK default: `{str(report['default_feature_flag_state']['settings_ENABLE_WEIGHTED_RERANK']).lower()}`",
        f"- .env.example ENABLE_WEIGHTED_RERANK: `{report['default_feature_flag_state']['env_example_ENABLE_WEIGHTED_RERANK']}`",
        f"- Entered M2: `{str(report['scope_confirmation']['enteredM2']).lower()}`",
        "",
        "## Privacy Check",
        "",
        f"- Passed: `{str(report['privacy_check']['passed']).lower()}`",
        f"- Forbidden text fields present: `{str(report['privacy_check']['forbiddenTextFieldsPresent']).lower()}`",
        f"- Raw query text present: `{str(report['privacy_check']['rawQueryTextPresent']).lower()}`",
        "",
    ])
    return "\n".join(lines)


def _parameter_record(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "version_name": PARAMETER_VERSION_NAME,
        "generated_at": report["generated_at"],
        "selected_status": report["robust_status"],
        "selected_candidate_id": report["selected_candidate_id"],
        "selected_threshold": report["selected_threshold"],
        "parameter_grid": report["parameter_grid"],
        "robust_gate": report["robust_gate"],
        "threshold_sensitivity": report["threshold_sensitivity"],
        "cross_validation": report["cross_validation"],
        "repeatability": report["repeatability"],
        "default_feature_flag_state": report["default_feature_flag_state"],
        "privacy_check": report["privacy_check"],
        "scope_confirmation": report["scope_confirmation"],
    }


def build_report(
    *,
    regression_set_path: Path,
    queries_path: Path,
    qrels_path: Path,
    cases_path: Path,
    chunks_path: Path,
    output_json: Path,
    output_md: Path,
    parameter_version_out: Path,
    top_k: int,
    repeat_count: int,
) -> dict[str, Any]:
    generated_at = datetime.now().isoformat(timespec="seconds")
    regression_set = _load_regression_set(regression_set_path)
    regression_ids = {
        str(row.get("queryId") or "").strip()
        for row in regression_set.get("queries", [])
    }
    repeats: list[dict[str, Any]] = []
    first_run: dict[str, Any] | None = None
    for _ in range(repeat_count):
        collected = _collect_query_contexts(
            queries_path=queries_path,
            qrels_path=qrels_path,
            cases_path=cases_path,
            chunks_path=chunks_path,
            top_k=top_k,
        )
        if first_run is None:
            first_run = collected
        v2_frozen = _threshold_summary(
            collected["contexts"],
            threshold=M13X_LEGAL_SCORE_SHAPE_V2_CURRENT_RANK8_GAP_MAX,
            top_k=top_k,
            regression_ids=regression_ids,
            blocked_items=collected["globalBlockedItems"],
        )
        repeats.append({
            "v2Frozen": v2_frozen,
            "blockedItems": collected["globalBlockedItems"],
        })

    assert first_run is not None
    contexts = first_run["contexts"]
    blocked_items = first_run["globalBlockedItems"]
    threshold_sensitivity = _grid_summaries(
        contexts,
        top_k=top_k,
        regression_ids=regression_ids,
        blocked_items=blocked_items,
    )
    v2_frozen = next(
        row for row in threshold_sensitivity
        if float(row["threshold"]) == M13X_LEGAL_SCORE_SHAPE_V2_CURRENT_RANK8_GAP_MAX
    )
    cross_validation = _cross_validation(
        contexts,
        top_k=top_k,
        regression_ids=regression_ids,
    )
    sensitivity = _sensitivity_gate(threshold_sensitivity)
    repeatability = _repeatability(repeats=repeats)
    default_feature_flag_state = _feature_flag_file_state()
    upper_bound_blockers = _candidate_scope_failed_reasons(_upper_bound_candidate_spec())

    report_without_privacy = {
        "version": REPORT_VERSION,
        "run_id": f"m1_3x_legal_candidate_robustness_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
        "generated_at": generated_at,
        "selected_candidate_id": SELECTED_CANDIDATE_ID,
        "selected_candidate_name": _selected_candidate_spec().name,
        "selected_threshold": M13X_LEGAL_SCORE_SHAPE_V2_CURRENT_RANK8_GAP_MAX,
        "inputs": {
            "regressionSet": _relative(regression_set_path),
            "queries": _relative(queries_path),
            "qrels": _relative(qrels_path),
            "cases": _relative(cases_path),
            "chunks": _relative(chunks_path),
            "topK": top_k,
        },
        "source_artifacts": SOURCE_ARTIFACTS,
        "parameter_grid": {
            "currentRank8GapMax": SENSITIVITY_GRID,
            "predeclared": True,
            "selectionSignals": ["rank", "score", "score_gap", "top_k_score_distribution"],
            "topN": M13X_LEGAL_SCORE_SHAPE_TOP_N,
            "guardMeanFloor": M13X_LEGAL_SCORE_SHAPE_GUARD_MEAN_FLOOR,
            "currentStdMax": M13X_LEGAL_SCORE_SHAPE_CURRENT_STD_MAX,
        },
        "threshold_sensitivity": threshold_sensitivity,
        "sensitivity_gate": sensitivity,
        "cross_validation": cross_validation,
        "upper_bound_scope": {
            "candidateId": UPPER_BOUND_CANDIDATE_ID,
            "goNoGo": "NO_GO",
            "blockedReasons": upper_bound_blockers,
        },
        "upper_bound_difference_audit": _upper_bound_difference_audit(
            contexts,
            top_k=top_k,
        ),
        "repeatability": repeatability,
        "default_feature_flag_state": default_feature_flag_state,
        "scope_confirmation": {
            "qrelsUsedForRouterRanking": False,
            "qrelsUsedForOfflineScoringOnly": True,
            "queryIdUsedForRanking": False,
            "queryIdUsedForDeterministicFoldSplitOnly": True,
            "caseIdUsedForRankingSpecialCase": False,
            "manualRankOverride": False,
            "onlineRerankLogicChanged": False,
            "onlineRecallLogicChanged": False,
            "featureFlagDefaultModified": False,
            "enteredM2": False,
        },
    }
    md_without_privacy = _render_markdown({
        **report_without_privacy,
        "robust_status": "PENDING",
        "robust_gate": {"failedReasons": []},
        "privacy_check": {
            "passed": True,
            "rawQueryTextPresent": False,
            "forbiddenTextFieldsPresent": False,
        },
    })
    privacy = _privacy_check(md_without_privacy, report_without_privacy, first_run["rawQueries"])
    robust_gate = _robust_gate(
        v2_frozen=v2_frozen,
        cross_validation=cross_validation,
        sensitivity_gate=sensitivity,
        repeatability=repeatability,
        privacy=privacy,
        default_feature_flag_state=default_feature_flag_state,
    )
    report = {
        **report_without_privacy,
        "robust_status": robust_gate["status"],
        "robust_gate": robust_gate,
        "privacy_check": privacy,
    }
    markdown = _render_markdown(report)
    final_privacy = _privacy_check(markdown, report, first_run["rawQueries"])
    if not final_privacy["passed"]:
        raise ValueError("privacy check failed for M1.3x robustness output")
    report["privacy_check"] = final_privacy
    report["robust_gate"] = _robust_gate(
        v2_frozen=v2_frozen,
        cross_validation=cross_validation,
        sensitivity_gate=sensitivity,
        repeatability=repeatability,
        privacy=final_privacy,
        default_feature_flag_state=default_feature_flag_state,
    )
    report["robust_status"] = report["robust_gate"]["status"]
    markdown = _render_markdown(report)

    output_json.parent.mkdir(parents=True, exist_ok=True)
    write_json(output_json, report)
    output_md.write_text(markdown, encoding="utf-8")
    write_json(parameter_version_out, _parameter_record(report))
    return report


def main() -> None:
    timestamp = _timestamp()
    parser = argparse.ArgumentParser()
    parser.add_argument("--regression-set", default=str(DEFAULT_REGRESSION_SET))
    parser.add_argument("--queries", default=str(DEFAULT_PRODUCT_QUERIES))
    parser.add_argument("--qrels", default=str(DEFAULT_PRODUCT_QRELS))
    parser.add_argument("--cases", default=str(DEFAULT_PRODUCT_CASES))
    parser.add_argument("--chunks", default=str(DEFAULT_PRODUCT_CHUNKS))
    parser.add_argument("--top-k", type=int, default=DEFAULT_TOP_K)
    parser.add_argument("--repeat-count", type=int, default=DEFAULT_REPEAT_COUNT)
    parser.add_argument(
        "--out-json",
        default=f"docs/development/m1.3x-legal-candidate-robustness-{timestamp}.json",
    )
    parser.add_argument(
        "--out-md",
        default=f"docs/development/m1.3x-legal-candidate-robustness-{timestamp}.md",
    )
    parser.add_argument(
        "--parameter-version-out",
        default=(
            "docs/development/"
            f"m1.3x-legal-candidate-robustness-parameter-version-{timestamp}.json"
        ),
    )
    args = parser.parse_args()
    report = build_report(
        regression_set_path=_resolve(args.regression_set),
        queries_path=_resolve(args.queries),
        qrels_path=_resolve(args.qrels),
        cases_path=_resolve(args.cases),
        chunks_path=_resolve(args.chunks),
        output_json=_resolve(args.out_json),
        output_md=_resolve(args.out_md),
        parameter_version_out=_resolve(args.parameter_version_out),
        top_k=args.top_k,
        repeat_count=args.repeat_count,
    )
    print(json.dumps(
        {
            "status": report["robust_status"],
            "selected_candidate_id": report["selected_candidate_id"],
            "selected_threshold": report["selected_threshold"],
            "robust_gate_failed_reasons": report["robust_gate"]["failedReasons"],
            "v2_frozen": next(
                row for row in report["threshold_sensitivity"]
                if float(row["threshold"]) == report["selected_threshold"]
            ),
            "repeatability": report["repeatability"],
        },
        ensure_ascii=False,
        indent=2,
    ))


if __name__ == "__main__":
    main()
