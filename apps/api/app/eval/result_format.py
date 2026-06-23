"""Shared sanitized evaluation result shape for M1.2/M1.3 reports."""
from __future__ import annotations

from typing import Any


UNIFIED_EVAL_RESULT_VERSION = "m1_2_eval_result_v1"
M13_REGRESSION_GATE_VERSION = "m1_3_regression_gate_v1"
M13_TOP10_HIT_RATE_THRESHOLD = 0.60


def build_unified_eval_result(
    *,
    run_id: str,
    generated_at: str,
    eval_line: str,
    dataset: str | dict[str, Any],
    candidate_corpus: str | dict[str, Any],
    mode: str,
    precision_at_5: float | int | None,
    ndcg_at_10: float | int | None,
    top10_hit_rate: float | int | None,
    blocked_items: list[str] | None = None,
    notes: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "runId": run_id,
        "generatedAt": generated_at,
        "evalLine": eval_line,
        "dataset": dataset,
        "candidateCorpus": candidate_corpus,
        "mode": mode,
        "Precision@5": _round_or_none(precision_at_5),
        "NDCG@10": _round_or_none(ndcg_at_10),
        "Top10 hit rate": _round_or_none(top10_hit_rate),
        "blockedItems": list(blocked_items or []),
        "notes": list(notes or []),
    }


def _round_or_none(value: float | int | None) -> float | None:
    if value is None:
        return None
    return round(float(value), 4)


def _as_int(value: Any, *, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _count_distribution_label(distribution: dict[str, Any] | None, label: str) -> int:
    if distribution is None:
        return 0
    return max(0, _as_int(distribution.get(label), default=0))


def _mode_metrics(row: dict[str, Any], mode: str) -> dict[str, Any]:
    metrics = row.get("metrics")
    if isinstance(metrics, dict):
        value = metrics.get(mode)
        if isinstance(value, dict):
            return value
    return {}


def _top10_hit(row: dict[str, Any], mode: str) -> bool | None:
    metrics = _mode_metrics(row, mode)
    if metrics:
        value = metrics.get("Top10 hit")
        return bool(value) if value is not None else None
    if mode in {"current", "currentAfter"} and "current_top10_has_hit" in row:
        return bool(row.get("current_top10_has_hit"))
    if mode == "baseline" and "baseline_top10_has_hit" in row:
        return bool(row.get("baseline_top10_has_hit"))
    return None


def count_top10_misses_from_per_query(per_query: list[dict[str, Any]], *, mode: str = "currentAfter") -> int:
    return sum(
        1
        for row in per_query
        if row.get("evaluated") and _top10_hit(row, mode) is False
    )


def count_recall_misses_from_per_query(
    per_query: list[dict[str, Any]],
    *,
    baseline_mode: str = "baseline",
    current_mode: str = "currentAfter",
) -> int:
    return sum(
        1
        for row in per_query
        if row.get("evaluated")
        and _top10_hit(row, baseline_mode) is False
        and _top10_hit(row, current_mode) is False
    )


def _top10_miss_count_from_rate(evaluated_query_count: int | None, top10_hit_rate: float | None) -> int:
    if evaluated_query_count is None or top10_hit_rate is None:
        return 0
    return max(0, int(round(evaluated_query_count * (1.0 - top10_hit_rate))))


def _candidate_matrix_row_for_gate(report: dict[str, Any]) -> dict[str, Any] | None:
    matrix = report.get("candidate_matrix")
    if not isinstance(matrix, list):
        return None

    rows = [row for row in matrix if isinstance(row, dict)]
    if not rows:
        return None

    selected_candidate_id = report.get("selected_candidate_id")
    if selected_candidate_id:
        selected = next(
            (row for row in rows if row.get("candidate_id") == selected_candidate_id),
            None,
        )
        if selected is not None:
            return selected

    preferred_ids = (
        "m1_3_combined_candidate",
        "m1_3_rerank_guard_candidate",
        "m1_3_recall_only_candidate",
        "m1_2_guarded_rerank_v1",
        "m1_2_baseline",
    )
    for candidate_id in preferred_ids:
        candidate = next(
            (row for row in rows if row.get("candidate_id") == candidate_id),
            None,
        )
        if candidate is not None:
            return candidate
    return rows[0]


def build_m13_regression_gate_summary(
    *,
    top10_hit_rate: float | int | None,
    evaluated_query_count: int | None = None,
    before_vs_after_label_distribution: dict[str, Any] | None = None,
    after_vs_baseline_label_distribution: dict[str, Any] | None = None,
    metric_regression_count: int | None = None,
    recall_miss_count: int | None = None,
    top10_miss_count: int | None = None,
    blocked_items: list[str] | None = None,
) -> dict[str, Any]:
    """Build the M1.3 hard-gate fields with conservative missing-data handling."""

    blocked_items = list(blocked_items or [])
    missing_inputs: list[str] = []

    if before_vs_after_label_distribution is None:
        missing_inputs.append("beforeVsAfterRegressedCount")
    if after_vs_baseline_label_distribution is None:
        missing_inputs.append("afterVsBaselineRegressedCount")
    if metric_regression_count is None and after_vs_baseline_label_distribution is None:
        missing_inputs.append("metricRegressionCount")
    if top10_hit_rate is None:
        missing_inputs.append("top10HitRate")

    before_vs_after_regressed_count = _count_distribution_label(
        before_vs_after_label_distribution,
        "REGRESSED",
    )
    after_vs_baseline_regressed_count = _count_distribution_label(
        after_vs_baseline_label_distribution,
        "REGRESSED",
    )
    if metric_regression_count is None:
        metric_regression_count = after_vs_baseline_regressed_count
    if recall_miss_count is None:
        recall_miss_count = 0
    if top10_miss_count is None:
        top10_miss_count = _top10_miss_count_from_rate(
            evaluated_query_count,
            _as_float(top10_hit_rate),
        )

    normalized_top10_hit_rate = _as_float(top10_hit_rate)
    hard_gate_failed_reasons: list[str] = []
    if missing_inputs:
        hard_gate_failed_reasons.append("MISSING_HARD_GATE_INPUTS")
    if blocked_items:
        hard_gate_failed_reasons.append("EVAL_BLOCKED")
    if normalized_top10_hit_rate is None or normalized_top10_hit_rate < M13_TOP10_HIT_RATE_THRESHOLD:
        hard_gate_failed_reasons.append("TOP10_HIT_RATE_BELOW_0_60")
    if before_vs_after_regressed_count > 0:
        hard_gate_failed_reasons.append("BEFORE_VS_AFTER_REGRESSED_GT_0")
    if after_vs_baseline_regressed_count > 0:
        hard_gate_failed_reasons.append("AFTER_VS_BASELINE_REGRESSED_GT_0")
    if int(metric_regression_count) > 0:
        hard_gate_failed_reasons.append("METRIC_REGRESSION_GT_0")

    passed = not hard_gate_failed_reasons
    return {
        "version": M13_REGRESSION_GATE_VERSION,
        "top10HitRateThreshold": M13_TOP10_HIT_RATE_THRESHOLD,
        "top10HitRate": _round_or_none(normalized_top10_hit_rate),
        "evaluatedQueryCount": evaluated_query_count,
        "beforeVsAfterRegressedCount": int(before_vs_after_regressed_count),
        "afterVsBaselineRegressedCount": int(after_vs_baseline_regressed_count),
        "top10MissCount": int(top10_miss_count),
        "metricRegressionCount": int(metric_regression_count),
        "recallMissCount": int(recall_miss_count),
        "grayCandidateHardGatePassed": passed,
        "weightedRerankGrayCandidate": passed,
        "hardGateDataComplete": not missing_inputs and not blocked_items,
        "missingInputs": missing_inputs,
        "blockedItems": blocked_items,
        "hardGateFailedReasons": hard_gate_failed_reasons,
        "hardGateFormula": (
            "top10HitRate >= 0.60 AND beforeVsAfterRegressedCount == 0 "
            "AND afterVsBaselineRegressedCount == 0 AND metricRegressionCount == 0"
        ),
    }


def build_m13_regression_gate_from_report(
    report: dict[str, Any],
    *,
    bad_case_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compatibility reader for old M1.2/M1.3 product, regression, and candidate artifacts."""

    candidate_row = _candidate_matrix_row_for_gate(report)
    if candidate_row is not None:
        return build_m13_regression_gate_summary(
            top10_hit_rate=candidate_row.get("Top10 hit rate"),
            evaluated_query_count=_as_int(candidate_row.get("evaluatedQueryCount"), default=0),
            before_vs_after_label_distribution=(
                candidate_row.get("labelDistribution")
                if isinstance(candidate_row.get("labelDistribution"), dict)
                else None
            ),
            after_vs_baseline_label_distribution=(
                candidate_row.get("afterVsBaselineLabelDistribution")
                if isinstance(candidate_row.get("afterVsBaselineLabelDistribution"), dict)
                else None
            ),
            metric_regression_count=_as_int(
                candidate_row.get(
                    "METRIC_REGRESSION count",
                    candidate_row.get("metricRegressionCount"),
                ),
                default=0,
            ),
            recall_miss_count=_as_int(
                candidate_row.get("RECALL_MISS count", candidate_row.get("recallMissCount")),
                default=0,
            ),
            top10_miss_count=_as_int(candidate_row.get("top10MissCount"), default=0),
            blocked_items=candidate_row.get("blockedItems") or [],
        )

    overall_metrics = report.get("overallMetrics") or {}
    current_after = overall_metrics.get("currentAfter") or {}
    product_current = report.get("current") or {}
    current_metrics = current_after or product_current
    top10_hit_rate = current_metrics.get("Top10 hit rate", current_metrics.get("top10_hit_rate"))
    evaluated_query_count = current_metrics.get(
        "evaluatedQueryCount",
        current_metrics.get("evaluated_query_count"),
    )

    per_query = report.get("perQuery")
    if not isinstance(per_query, list):
        per_query = report.get("per_query")
    if not isinstance(per_query, list):
        per_query = []

    reason_distribution = None
    if bad_case_report is not None:
        reason_distribution = bad_case_report.get("reason_distribution")
    if reason_distribution is None:
        reason_distribution = (report.get("bad_case_report") or {}).get("reason_distribution")
    if not isinstance(reason_distribution, dict):
        reason_distribution = {}

    before_distribution = report.get("labelDistribution")
    after_distribution = report.get("afterVsBaselineLabelDistribution")
    is_regression_report = isinstance(before_distribution, dict) or isinstance(after_distribution, dict)
    current_mode = "currentAfter" if is_regression_report else "current"

    metric_regression_count: int | None = None
    if "METRIC_REGRESSION" in reason_distribution:
        metric_regression_count = _count_distribution_label(reason_distribution, "METRIC_REGRESSION")
    recall_miss_count = (
        _count_distribution_label(reason_distribution, "RECALL_MISS")
        if "RECALL_MISS" in reason_distribution
        else count_recall_misses_from_per_query(per_query, current_mode=current_mode)
    )

    return build_m13_regression_gate_summary(
        top10_hit_rate=top10_hit_rate,
        evaluated_query_count=_as_int(evaluated_query_count, default=0),
        before_vs_after_label_distribution=before_distribution if isinstance(before_distribution, dict) else None,
        after_vs_baseline_label_distribution=after_distribution if isinstance(after_distribution, dict) else None,
        metric_regression_count=metric_regression_count,
        recall_miss_count=recall_miss_count,
        top10_miss_count=count_top10_misses_from_per_query(per_query, mode=current_mode),
        blocked_items=report.get("blockedItems") or report.get("blocked_items") or [],
    )
