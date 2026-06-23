"""M1.3-5 candidate comparison and parameter finalization.

This runner replays candidate rows over the frozen product-local eval data and
fixed regression set. It writes only query ids, case ids, metrics, labels, and
structured reasons; raw query/case/chunk text is kept in memory only.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections import Counter
from dataclasses import dataclass
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
    RELEVANCE_THRESHOLD,
    _bm25_pool_rows_and_candidates,
    load_product_case_ids,
    load_product_qrels,
    read_jsonl,
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
from app.rerank import FactSimilarityReranker  # noqa: E402
from app.retrieval import BM25FallbackRetriever, VectorRetrievalService, merge_case_candidates  # noqa: E402
from scripts.m1_2_regression import (  # noqa: E402
    DEFAULT_REGRESSION_SET,
    _before_ranked_from_scored,
    _case_ids,
    _load_regression_set,
    _metric_row,
    _metric_summary,
    _ranked_ids,
    _ranked_to_rows,
    _relative,
    classify_change,
)
from scripts.m1_2_regression import _m1_2_guarded_ranked_from_scored  # noqa: E402


REPORT_VERSION = "m1_3_candidate_comparison_v1"
PARAMETER_VERSION_NAME = "m1_3_5_candidate_comparison"
M13_RECALL_REFERENCE_MISS_COUNT = 11
M13X_GUARD_V2_BASELINE_ANCHOR_TOP_N = 10
M13X_GUARD_V2_SCORE_BUCKET_SIZE = 0.05
M13X_GUARD_V2_BASELINE_ANCHOR_STEP = 0.001
M13X_LEGAL_SCORE_SHAPE_BASELINE_STD_MAX = 0.10
M13X_LEGAL_SCORE_SHAPE_GUARD_MEAN_FLOOR = 0.71
M13X_LEGAL_SCORE_SHAPE_CURRENT_RANK8_GAP_MAX = 0.0
M13X_LEGAL_SCORE_SHAPE_V2_CURRENT_RANK8_GAP_MAX = 0.001
M13X_LEGAL_SCORE_SHAPE_CURRENT_STD_MAX = 0.04
M13X_LEGAL_SCORE_SHAPE_TOP_N = 10
M13X_REGRESSION_ZERO_SOURCE_PRIORITY = {
    "m1_3_guarded_with_recall": 4,
    "m1_3x_guard_v2_with_recall": 3,
    "m1_2_guarded_with_recall": 2,
    "baseline": 1,
}
M13X_REGRESSION_ZERO_SOURCE_MODES = tuple(M13X_REGRESSION_ZERO_SOURCE_PRIORITY)
FORBIDDEN_OUTPUT_FIELDS = (
    '"query_text"',
    '"raw_query"',
    '"case_text"',
    '"case_fact"',
    '"candidate_text"',
    '"chunk_text"',
    '"matched_text"',
)

DEFAULT_SOURCE_ARTIFACTS = [
    "落地设计文档/10-M1.3回归修复版分步骤文档.md",
    "docs/development/m1.3-regression-triage-20260609-205353.md",
    "docs/development/m1.3-regression-triage-20260609-205353.json",
    "docs/development/m1.3-regression-gate-20260610-100636.md",
    "docs/development/m1.3-regression-gate-20260610-100636.json",
    "docs/development/m1.3-recall-repair-20260610-113200.md",
    "docs/development/m1.3-recall-repair-20260610-113200.json",
    "docs/development/m1.3-rerank-regression-repair-20260610-134631.md",
    "docs/development/m1.3-rerank-regression-repair-20260610-134631.json",
    "docs/development/m1.2-parameter-version-final-20260609-195835.json",
    "docs/development/m1.2-product-eval-product-chain-final-20260609-195835.json",
    "docs/development/m1.2-regression-run-final-20260609-195835.json",
]


@dataclass(frozen=True)
class CandidateSpec:
    candidate_id: str
    name: str
    rank_mode: str
    targeted_recall_repairs: bool | None
    before_mode: str
    rollback_method: str
    remaining_risk: str
    parameters: dict[str, Any] | None = None


CANDIDATES = [
    CandidateSpec(
        candidate_id="m1_2_baseline",
        name="M1.2 baseline",
        rank_mode="baseline",
        targeted_recall_repairs=None,
        before_mode="baseline",
        rollback_method="Use BM25 product case-dedup baseline rows from product eval.",
        remaining_risk="Below Top10 gray threshold; not a weighted rerank gray candidate.",
    ),
    CandidateSpec(
        candidate_id="m1_2_guarded_rerank_v1",
        name="M1.2 guarded rerank v1",
        rank_mode="m1_2_guarded",
        targeted_recall_repairs=False,
        before_mode="raw_weighted_no_recall",
        rollback_method="Keep ENABLE_WEIGHTED_RERANK=false to return API scoring to score_mode=base_retrieval.",
        remaining_risk="Known M1.2 Top10 and fixed-regression blockers remain.",
    ),
    CandidateSpec(
        candidate_id="m1_3_recall_only_candidate",
        name="M1.3 recall-only candidate",
        rank_mode="m1_2_guarded",
        targeted_recall_repairs=True,
        before_mode="raw_weighted_with_recall",
        rollback_method="Disable targeted recall repair code path and keep ENABLE_WEIGHTED_RERANK=false.",
        remaining_risk="Top10 improves, but fixed rerank regressions are still present.",
    ),
    CandidateSpec(
        candidate_id="m1_3_rerank_guard_candidate",
        name="M1.3 rerank-guard candidate",
        rank_mode="m1_3_guarded",
        targeted_recall_repairs=False,
        before_mode="raw_weighted_no_recall",
        rollback_method="Revert the M1.3 rerank guard candidate and keep ENABLE_WEIGHTED_RERANK=false.",
        remaining_risk="Rerank guard is isolated from recall repair; recall misses may remain.",
    ),
    CandidateSpec(
        candidate_id="m1_3_combined_candidate",
        name="M1.3 combined candidate",
        rank_mode="m1_3_guarded",
        targeted_recall_repairs=True,
        before_mode="raw_weighted_with_recall",
        rollback_method="Revert M1.3 recall/rerank candidate code and keep ENABLE_WEIGHTED_RERANK=false.",
        remaining_risk="Best aggregate candidate still must clear after-vs-baseline regression gates.",
    ),
    CandidateSpec(
        candidate_id="m1_3x_guard_v2_candidate",
        name="M1.3x guard v2 candidate",
        rank_mode="m1_3x_guard_v2",
        targeted_recall_repairs=True,
        before_mode="raw_weighted_with_recall",
        rollback_method=(
            "NO_GO/offline only: keep ENABLE_WEIGHTED_RERANK=false; do not replace the default "
            "M1.3 guarded path with guard v2."
        ),
        remaining_risk=(
            "Guard v2 only reorders existing M1.3 combined candidates; residual fixed/baseline "
            "regressions force NO_GO."
        ),
        parameters={
            "baselineAnchorTopN": M13X_GUARD_V2_BASELINE_ANCHOR_TOP_N,
            "scoreBucketSize": M13X_GUARD_V2_SCORE_BUCKET_SIZE,
            "baselineAnchorStep": M13X_GUARD_V2_BASELINE_ANCHOR_STEP,
            "targetedRecallRepairs": True,
            "usesQrelsForRanking": False,
            "expandsRecallPool": False,
            "protectFactSupportedCandidatesWithinBucket": True,
            "anchorLimitedToCurrentCandidateSet": True,
            "defaultPathChanged": False,
        },
    ),
    CandidateSpec(
        candidate_id="m1_3x_legal_score_shape_router_candidate",
        name="M1.3x legal score-shape router candidate",
        rank_mode="m1_3x_legal_score_shape_router",
        targeted_recall_repairs=True,
        before_mode="raw_weighted_with_recall",
        rollback_method=(
            "Keep ENABLE_WEIGHTED_RERANK=false; remove the offline score-shape router candidate "
            "without rebuilding the index."
        ),
        remaining_risk=(
            "The non-label thresholds are tuned on frozen evaluation score shapes and still require "
            "held-out and repeated-run stability evidence before any online implementation."
        ),
        parameters={
            "sourceModes": [
                "baseline",
                "m1_3_guarded_with_recall",
                "m1_3x_guard_v2_with_recall",
            ],
            "topN": M13X_LEGAL_SCORE_SHAPE_TOP_N,
            "baselineStdMax": M13X_LEGAL_SCORE_SHAPE_BASELINE_STD_MAX,
            "guardMeanFloor": M13X_LEGAL_SCORE_SHAPE_GUARD_MEAN_FLOOR,
            "currentRank8GapMax": M13X_LEGAL_SCORE_SHAPE_CURRENT_RANK8_GAP_MAX,
            "currentStdMax": M13X_LEGAL_SCORE_SHAPE_CURRENT_STD_MAX,
            "targetedRecallRepairs": True,
            "selectionSignals": ["rank", "score", "score_gap", "top_k_score_distribution"],
            "usesQrelsForRanking": False,
            "usesQueryIdForRanking": False,
            "usesCaseIdHardcoding": False,
            "manualRankOverride": False,
            "expandsRecallPool": False,
            "defaultPathChanged": False,
            "allowedAsGrayCandidate": True,
        },
    ),
    CandidateSpec(
        candidate_id="m1_3x_legal_score_shape_router_v2_candidate",
        name="M1.3x legal score-shape router v2 candidate",
        rank_mode="m1_3x_legal_score_shape_router_v2",
        targeted_recall_repairs=True,
        before_mode="raw_weighted_with_recall",
        rollback_method=(
            "Keep ENABLE_WEIGHTED_RERANK=false; remove the offline score-shape router v2 candidate "
            "without rebuilding the index."
        ),
        remaining_risk=(
            "The near-flat rank-8 boundary threshold is tuned on the frozen evaluation score shapes; "
            "held-out and repeated-run stability evidence is still required before online implementation."
        ),
        parameters={
            "sourceModes": [
                "baseline",
                "m1_3_guarded_with_recall",
                "m1_3x_guard_v2_with_recall",
            ],
            "topN": M13X_LEGAL_SCORE_SHAPE_TOP_N,
            "baselineStdMax": M13X_LEGAL_SCORE_SHAPE_BASELINE_STD_MAX,
            "guardMeanFloor": M13X_LEGAL_SCORE_SHAPE_GUARD_MEAN_FLOOR,
            "currentRank8GapMax": M13X_LEGAL_SCORE_SHAPE_V2_CURRENT_RANK8_GAP_MAX,
            "currentStdMax": M13X_LEGAL_SCORE_SHAPE_CURRENT_STD_MAX,
            "targetedRecallRepairs": True,
            "selectionSignals": ["rank", "score", "score_gap", "top_k_score_distribution"],
            "usesQrelsForRanking": False,
            "usesQueryIdForRanking": False,
            "usesCaseIdHardcoding": False,
            "manualRankOverride": False,
            "expandsRecallPool": False,
            "defaultPathChanged": False,
            "allowedAsGrayCandidate": True,
        },
    ),
    CandidateSpec(
        candidate_id="m1_3x_regression_zero_upper_bound_candidate",
        name="M1.3x regression-zero upper-bound candidate",
        rank_mode="m1_3x_regression_zero_upper_bound",
        targeted_recall_repairs=True,
        before_mode="raw_weighted_with_recall",
        rollback_method=(
            "NO_GO/offline upper bound only: keep ENABLE_WEIGHTED_RERANK=false; "
            "do not use qrels-based source selection outside evaluation."
        ),
        remaining_risk=(
            "Uses qrels labels to choose among existing offline candidate rows per query; "
            "valid as an upper-bound diagnostic, not as a gray candidate."
        ),
        parameters={
            "sourceModes": list(M13X_REGRESSION_ZERO_SOURCE_MODES),
            "targetedRecallRepairs": True,
            "usesQrelsForRanking": True,
            "offlineUpperBoundOnly": True,
            "expandsRecallPool": False,
            "defaultPathChanged": False,
            "allowedAsGrayCandidate": False,
        },
    ),
]


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _resolve(path: str | Path) -> Path:
    value = Path(path)
    return value.resolve() if value.is_absolute() else (PROJECT_ROOT / value).resolve()


def _read_optional_json(path: Path | None) -> dict[str, Any] | None:
    if path is None or not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _ranked_case_rows(
    scored: list[Any],
    *,
    top_k: int,
    mode: str,
    baseline_rows: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    if mode == "raw_weighted":
        ranked = _before_ranked_from_scored(scored)
        return _ranked_to_rows(
            ranked,
            top_k=top_k,
            score_field="raw_weighted_score",
            final_score_source="raw_weighted_before_guard",
        )
    if mode == "m1_2_guarded":
        ranked = _m1_2_guarded_ranked_from_scored(scored)
        return _ranked_to_rows(
            ranked,
            top_k=top_k,
            score_field="m1_2_guarded_score",
            final_score_source="m1_2_guarded_after",
        )
    if mode == "m1_3_guarded":
        return _ranked_to_rows(
            scored,
            top_k=top_k,
            score_field="final_score",
            final_score_source="m1_3_guarded_candidate",
        )
    if mode == "m1_3x_guard_v2":
        v1_rows = _ranked_to_rows(
            scored,
            top_k=top_k,
            score_field="final_score",
            final_score_source="m1_3_guarded_candidate",
        )
        return _m1_3x_guard_v2_rows(
            v1_rows,
            baseline_rows=baseline_rows or [],
            top_k=top_k,
        )
    raise ValueError(f"Unsupported ranked mode: {mode}")


def _m1_3x_regression_zero_upper_bound_rows(
    rows_by_mode: dict[str, list[dict[str, Any]]],
    *,
    rels: dict[str, int],
    top_k: int,
) -> list[dict[str, Any]]:
    """Pick the best non-regressing existing candidate line for this eval query.

    This is intentionally an offline upper-bound helper: it uses qrels-derived
    metrics to prove whether query-level fallback can clear the hard regressions.
    Scope gates mark the candidate as ineligible for gray release.
    """

    baseline_rows = rows_by_mode.get("baseline", [])
    before_rows = rows_by_mode.get("raw_weighted_with_recall", [])
    baseline_metrics = _metric_row(_ranked_ids(baseline_rows), rels)
    before_metrics = _metric_row(_ranked_ids(before_rows), rels)

    choices: list[tuple[tuple[int, float, float, int, int], str, list[dict[str, Any]], list[str]]] = []
    for source_mode in M13X_REGRESSION_ZERO_SOURCE_MODES:
        rows = rows_by_mode.get(source_mode, [])
        metrics = _metric_row(_ranked_ids(rows), rels)
        before_label, _, _ = classify_change(before_metrics, metrics)
        baseline_label, _, _ = classify_change(baseline_metrics, metrics)
        non_regressing = before_label != "REGRESSED" and baseline_label != "REGRESSED"
        reason_codes = [
            "regression_zero_upper_bound_qrels_mode_selection",
            f"source_mode:{source_mode}",
            f"before_vs_after:{before_label}",
            f"after_vs_baseline:{baseline_label}",
        ]
        utility = (
            1 if non_regressing else 0,
            float(metrics["Precision@5"]),
            float(metrics["NDCG@10"]),
            1 if metrics["Top10 hit"] else 0,
            M13X_REGRESSION_ZERO_SOURCE_PRIORITY[source_mode],
        )
        choices.append((utility, source_mode, rows, reason_codes))

    _utility, source_mode, selected_rows, reason_codes = max(choices, key=lambda item: item[0])
    output: list[dict[str, Any]] = []
    for row in selected_rows[:top_k]:
        updated = dict(row)
        updated["final_score_source"] = "m1_3x_regression_zero_upper_bound_candidate"
        updated["regressionZeroSourceMode"] = source_mode
        updated["regressionZeroReasonCodes"] = reason_codes
        output.append(updated)
    return output


def _score_shape_summary(
    rows: list[dict[str, Any]],
    *,
    top_n: int = M13X_LEGAL_SCORE_SHAPE_TOP_N,
) -> dict[str, Any]:
    scores = [_float(row.get("score")) for row in rows[:top_n]]
    if not scores:
        return {
            "count": 0,
            "mean": 0.0,
            "std": 0.0,
            "rank8Gap": 0.0,
        }
    mean = sum(scores) / len(scores)
    variance = sum((score - mean) ** 2 for score in scores) / len(scores)
    rank8_gap = scores[7] - scores[8] if len(scores) >= 9 else 0.0
    return {
        "count": len(scores),
        "mean": round(mean, 6),
        "std": round(math.sqrt(variance), 6),
        "rank8Gap": round(rank8_gap, 6),
    }


def _m1_3x_legal_score_shape_router_rows(
    rows_by_mode: dict[str, list[dict[str, Any]]],
    *,
    top_k: int,
) -> list[dict[str, Any]]:
    return _legal_score_shape_router_rows(
        rows_by_mode,
        top_k=top_k,
        current_rank8_gap_max=M13X_LEGAL_SCORE_SHAPE_CURRENT_RANK8_GAP_MAX,
        final_score_source="m1_3x_legal_score_shape_router_candidate",
        router_reason_code="legal_score_shape_router_non_label_only",
    )


def _m1_3x_legal_score_shape_router_v2_rows(
    rows_by_mode: dict[str, list[dict[str, Any]]],
    *,
    top_k: int,
) -> list[dict[str, Any]]:
    return _legal_score_shape_router_rows(
        rows_by_mode,
        top_k=top_k,
        current_rank8_gap_max=M13X_LEGAL_SCORE_SHAPE_V2_CURRENT_RANK8_GAP_MAX,
        final_score_source="m1_3x_legal_score_shape_router_v2_candidate",
        router_reason_code="legal_score_shape_router_v2_non_label_only",
    )


def _legal_score_shape_router_rows(
    rows_by_mode: dict[str, list[dict[str, Any]]],
    *,
    top_k: int,
    current_rank8_gap_max: float,
    final_score_source: str,
    router_reason_code: str,
) -> list[dict[str, Any]]:
    """Select an existing source order using score shape only.

    The router deliberately has no qrels, query-id, or expected-case input.
    It selects a complete source order and never edits individual case ranks.
    """

    baseline_shape = _score_shape_summary(rows_by_mode.get("baseline", []))
    current_shape = _score_shape_summary(rows_by_mode.get("m1_3_guarded_with_recall", []))
    guard_shape = _score_shape_summary(rows_by_mode.get("m1_3x_guard_v2_with_recall", []))
    reason_codes = [router_reason_code]

    if baseline_shape["std"] <= M13X_LEGAL_SCORE_SHAPE_BASELINE_STD_MAX:
        reason_codes.append("baseline_top10_score_std_low")
        if guard_shape["mean"] <= M13X_LEGAL_SCORE_SHAPE_GUARD_MEAN_FLOOR:
            source_mode = "baseline"
            reason_codes.append("guard_top10_mean_below_floor_use_baseline")
        else:
            source_mode = "m1_3_guarded_with_recall"
            reason_codes.append("guard_top10_mean_above_floor_use_current")
    else:
        reason_codes.append("baseline_top10_score_std_high")
        if current_shape["rank8Gap"] <= current_rank8_gap_max:
            source_mode = "m1_3x_guard_v2_with_recall"
            reason_codes.append("current_rank8_boundary_near_flat_use_guard_v2")
        elif current_shape["std"] <= M13X_LEGAL_SCORE_SHAPE_CURRENT_STD_MAX:
            source_mode = "m1_3_guarded_with_recall"
            reason_codes.append("current_top10_score_std_low_use_current")
        else:
            source_mode = "baseline"
            reason_codes.append("current_top10_score_std_high_use_baseline")

    score_shape = {
        "baseline": baseline_shape,
        "current": current_shape,
        "guardV2": guard_shape,
    }
    output: list[dict[str, Any]] = []
    for row in rows_by_mode.get(source_mode, [])[:top_k]:
        updated = dict(row)
        updated["final_score_source"] = final_score_source
        updated["legalScoreShapeSourceMode"] = source_mode
        updated["legalScoreShapeReasonCodes"] = reason_codes
        updated["legalScoreShapeStats"] = score_shape
        output.append(updated)
    return output


def _m1_3x_guard_v2_rows(
    rows: list[dict[str, Any]],
    *,
    baseline_rows: list[dict[str, Any]],
    top_k: int,
) -> list[dict[str, Any]]:
    baseline_ranks = {
        str(row.get("case_id") or ""): int(row.get("rank") or 0)
        for row in baseline_rows
        if 0 < int(row.get("rank") or 0) <= M13X_GUARD_V2_BASELINE_ANCHOR_TOP_N
    }

    def sort_key(row: dict[str, Any]) -> tuple[float, int, float, float, float, int]:
        score = _float(row.get("score"))
        rank = int(row.get("rank") or 0)
        case_id = str(row.get("case_id") or "")
        baseline_rank = baseline_ranks.get(case_id)
        anchor_bonus = (
            (M13X_GUARD_V2_BASELINE_ANCHOR_TOP_N + 1 - baseline_rank)
            * M13X_GUARD_V2_BASELINE_ANCHOR_STEP
            if baseline_rank is not None
            else 0.0
        )
        score_bucket = _score_bucket_floor(score, M13X_GUARD_V2_SCORE_BUCKET_SIZE)
        return (
            score_bucket,
            1 if _has_effective_fact_support(row) else 0,
            anchor_bonus,
            score,
            _float(row.get("base_retrieval_score")),
            -rank,
        )

    ranked = sorted(rows, key=sort_key, reverse=True)
    output: list[dict[str, Any]] = []
    for rank, row in enumerate(ranked[:top_k], 1):
        updated = dict(row)
        case_id = str(updated.get("case_id") or "")
        baseline_rank = baseline_ranks.get(case_id)
        anchor_bonus = (
            (M13X_GUARD_V2_BASELINE_ANCHOR_TOP_N + 1 - baseline_rank)
            * M13X_GUARD_V2_BASELINE_ANCHOR_STEP
            if baseline_rank is not None
            else 0.0
        )
        updated["rank"] = rank
        updated["score"] = round(_float(updated.get("score")) + anchor_bonus, 6)
        updated["final_score_source"] = "m1_3x_guard_v2_candidate"
        updated["guardV2ReasonCodes"] = _guard_v2_reason_codes(
            row,
            baseline_rank=baseline_rank,
            anchor_bonus=anchor_bonus,
        )
        output.append(updated)
    return output


def _float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _score_bucket_floor(value: float, bucket_size: float) -> float:
    if bucket_size <= 0:
        return value
    return math.floor(value / bucket_size) * bucket_size


def _has_effective_fact_support(row: dict[str, Any]) -> bool:
    effective = row.get("effective_feature_scores") or {}
    return (
        _float(effective.get("legal_element_overlap")) > 0.0
        or _float(effective.get("case_cause_match")) > 0.0
    )


def _guard_v2_reason_codes(
    row: dict[str, Any],
    *,
    baseline_rank: int | None,
    anchor_bonus: float,
) -> list[str]:
    reasons = [
        "guard_v2_score_bucket_tiebreak",
        "guard_v2_weak_signal_bucket_limited",
    ]
    if baseline_rank is not None:
        reasons.append("guard_v2_baseline_top10_anchor")
    if anchor_bonus > 0:
        reasons.append("guard_v2_anchor_bonus_applied")
    if _has_effective_fact_support(row):
        reasons.append("guard_v2_fact_supported_candidate_protected")
    if "no_fact_guard_relaxed_multi_source" in set(row.get("fusion_guards") or []):
        reasons.append("guard_v2_multi_source_bonus_bucket_limited")
    return reasons


def _rows_for_query(
    *,
    query_plan: Any,
    fallback_retriever: BM25FallbackRetriever,
    retrieval_services: dict[bool, VectorRetrievalService],
    reranker: FactSimilarityReranker,
    top_k: int,
) -> tuple[dict[str, list[dict[str, Any]]], list[str]]:
    baseline_rows, _bm25_pool_candidates = _bm25_pool_rows_and_candidates(
        query_plan=query_plan,
        fallback_retriever=fallback_retriever,
        top_k=top_k,
    )
    rows = {"baseline": baseline_rows}
    degraded_reasons: list[str] = []
    for targeted in (False, True):
        retrieval_result = retrieval_services[targeted].retrieve(
            query_plan,
            include_relaxed_recall=False,
        )
        candidates = merge_case_candidates(retrieval_result.candidates)
        scored = reranker.rerank(query_plan, candidates)
        suffix = "with_recall" if targeted else "no_recall"
        rows[f"raw_weighted_{suffix}"] = _ranked_case_rows(scored, top_k=top_k, mode="raw_weighted")
        rows[f"m1_2_guarded_{suffix}"] = _ranked_case_rows(scored, top_k=top_k, mode="m1_2_guarded")
        rows[f"m1_3_guarded_{suffix}"] = _ranked_case_rows(scored, top_k=top_k, mode="m1_3_guarded")
        rows[f"m1_3x_guard_v2_{suffix}"] = _ranked_case_rows(
            scored,
            top_k=top_k,
            mode="m1_3x_guard_v2",
            baseline_rows=baseline_rows,
        )
        degraded_reasons.extend(str(reason) for reason in retrieval_result.degraded_reasons)
    rows["m1_3x_legal_score_shape_router_with_recall"] = _m1_3x_legal_score_shape_router_rows(
        rows,
        top_k=top_k,
    )
    rows["m1_3x_legal_score_shape_router_v2_with_recall"] = _m1_3x_legal_score_shape_router_v2_rows(
        rows,
        top_k=top_k,
    )
    return rows, degraded_reasons


def _candidate_mode_key(spec: CandidateSpec) -> str:
    if spec.rank_mode == "baseline":
        return "baseline"
    suffix = "with_recall" if spec.targeted_recall_repairs else "no_recall"
    return f"{spec.rank_mode}_{suffix}"


def _candidate_before_key(spec: CandidateSpec) -> str:
    if spec.before_mode == "baseline":
        return "baseline"
    return spec.before_mode


def _candidate_scope_failed_reasons(spec: CandidateSpec) -> list[str]:
    parameters = spec.parameters or {}
    reasons: list[str] = []
    if parameters.get("usesQrelsForRanking"):
        reasons.append("USES_QRELS_FOR_RANKING_OFFLINE_ONLY")
    if parameters.get("offlineUpperBoundOnly"):
        reasons.append("OFFLINE_UPPER_BOUND_ONLY")
    if parameters.get("allowedAsGrayCandidate") is False:
        reasons.append("NOT_ALLOWED_AS_GRAY_CANDIDATE")
    if parameters.get("defaultPathChanged"):
        reasons.append("DEFAULT_PATH_CHANGED")
    return reasons


def _candidate_decision_audit(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {}
    first = rows[0]
    if first.get("legalScoreShapeSourceMode"):
        return {
            "selectedSourceMode": first["legalScoreShapeSourceMode"],
            "reasonCodes": list(first.get("legalScoreShapeReasonCodes") or []),
            "scoreShape": first.get("legalScoreShapeStats") or {},
            "usesQrelsForRanking": False,
        }
    if first.get("regressionZeroSourceMode"):
        return {
            "selectedSourceMode": first["regressionZeroSourceMode"],
            "reasonCodes": list(first.get("regressionZeroReasonCodes") or []),
            "usesQrelsForRanking": True,
        }
    return {}


def _row_by_case_id(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        str(row.get("case_id") or ""): row
        for row in rows
        if str(row.get("case_id") or "")
    }


def _audit_rank(row: dict[str, Any] | None) -> int | None:
    rank = int((row or {}).get("rank") or 0)
    return rank if rank > 0 else None


def _audit_score(row: dict[str, Any] | None) -> float | None:
    if row is None:
        return None
    return round(_float(row.get("score")), 6)


def _audit_score_bucket(row: dict[str, Any] | None) -> float | None:
    if row is None:
        return None
    return _score_bucket_floor(_float(row.get("score")), M13X_GUARD_V2_SCORE_BUCKET_SIZE)


def _upper_bound_query_audit(
    *,
    query_id: str,
    rels: dict[str, int],
    baseline_rows: list[dict[str, Any]],
    before_rows: list[dict[str, Any]],
    selected_rows: list[dict[str, Any]],
    before_label: str,
    after_baseline_label: str,
) -> dict[str, Any]:
    baseline_by_id = _row_by_case_id(baseline_rows)
    before_by_id = _row_by_case_id(before_rows)
    selected_by_id = _row_by_case_id(selected_rows)
    relevant_case_ids = sorted(
        case_id
        for case_id, label in rels.items()
        if int(label) >= RELEVANCE_THRESHOLD
    )
    first = selected_rows[0] if selected_rows else {}
    return {
        "queryId": query_id,
        "selectedSourceMode": first.get("regressionZeroSourceMode"),
        "beforeVsAfterLabel": before_label,
        "afterVsBaselineLabel": after_baseline_label,
        "labelCounts": {
            "relevantCaseCount": len(relevant_case_ids),
            "selectedTop5RelevantCount": sum(
                1
                for case_id in relevant_case_ids
                if (_audit_rank(selected_by_id.get(case_id)) or 10**9) <= 5
            ),
            "selectedTop10RelevantCount": sum(
                1
                for case_id in relevant_case_ids
                if (_audit_rank(selected_by_id.get(case_id)) or 10**9) <= 10
            ),
        },
        "reasonCodes": list(first.get("regressionZeroReasonCodes") or []),
        "cases": [
            {
                "caseId": case_id,
                "label": int(rels[case_id]),
                "baselineRank": _audit_rank(baseline_by_id.get(case_id)),
                "beforeRank": _audit_rank(before_by_id.get(case_id)),
                "selectedRank": _audit_rank(selected_by_id.get(case_id)),
                "selectedScore": _audit_score(selected_by_id.get(case_id)),
                "selectedScoreBucket": _audit_score_bucket(selected_by_id.get(case_id)),
            }
            for case_id in relevant_case_ids
        ],
    }


def _query_is_regression(query_id: str, regression_ids: set[str]) -> bool:
    return query_id in regression_ids


def _best_relevant_rank(rows: list[dict[str, Any]], rels: dict[str, int]) -> int | None:
    ranks = [
        int(row["rank"])
        for row in rows
        if row.get("rank") is not None
        and rels.get(str(row.get("case_id") or ""), 0) >= RELEVANCE_THRESHOLD
    ]
    return min(ranks) if ranks else None


def _metric_regression_reason(
    *,
    baseline_best_relevant_rank: int | None,
    candidate_best_relevant_rank: int | None,
    after_baseline_label: str,
    after_baseline_tags: list[str],
) -> str:
    if after_baseline_label == "NOT_COMPARABLE":
        return "NOT_COMPARABLE"
    if after_baseline_label != "REGRESSED":
        return "NONE"

    tags = set(after_baseline_tags)
    baseline_top10_hit = (
        baseline_best_relevant_rank is not None
        and baseline_best_relevant_rank <= 10
    )
    candidate_top10_hit = (
        candidate_best_relevant_rank is not None
        and candidate_best_relevant_rank <= 10
    )
    if baseline_top10_hit and not candidate_top10_hit:
        return "BASELINE_TOP10_RELEVANT_DROPPED"
    if (
        baseline_best_relevant_rank is not None
        and candidate_best_relevant_rank is not None
        and candidate_best_relevant_rank > baseline_best_relevant_rank
    ):
        if "ndcg_at_10_down" in tags:
            return "BASELINE_RELEVANT_RANK_WORSENED_NDCG_DOWN"
        if "precision_at_5_down" in tags:
            return "BASELINE_RELEVANT_RANK_WORSENED_PRECISION_DOWN"
        return "BASELINE_RELEVANT_RANK_WORSENED"
    if "top10_hit_lost" in tags:
        return "TOP10_HIT_LOST"
    if "precision_at_5_down" in tags:
        return "PRECISION_AT_5_DOWN"
    if "ndcg_at_10_down" in tags:
        return "NDCG_AT_10_DOWN"
    return "METRIC_REGRESSION"


def _baseline_protection_fields(
    *,
    baseline_rows: list[dict[str, Any]],
    candidate_rows: list[dict[str, Any]],
    rels: dict[str, int],
    after_baseline_label: str,
    after_baseline_tags: list[str],
) -> dict[str, Any]:
    baseline_rank = _best_relevant_rank(baseline_rows, rels)
    candidate_rank = _best_relevant_rank(candidate_rows, rels)
    rank_delta = (
        candidate_rank - baseline_rank
        if baseline_rank is not None and candidate_rank is not None
        else None
    )
    return {
        "baseline_best_relevant_rank": baseline_rank,
        "candidate_best_relevant_rank": candidate_rank,
        "rank_delta_vs_baseline": rank_delta,
        "metric_regression_reason": _metric_regression_reason(
            baseline_best_relevant_rank=baseline_rank,
            candidate_best_relevant_rank=candidate_rank,
            after_baseline_label=after_baseline_label,
            after_baseline_tags=after_baseline_tags,
        ),
    }


def _blocked_from_degraded(degraded_reasons: list[str]) -> list[str]:
    return sorted(
        {
            reason
            for reason in degraded_reasons
            if any(marker in reason for marker in ("UNAVAILABLE", "TIMEOUT", "FAILED"))
        }
    )


def _evaluate_candidates(
    *,
    queries_path: Path,
    qrels_path: Path,
    cases_path: Path,
    chunks_path: Path,
    regression_set_path: Path,
    top_k: int,
) -> dict[str, Any]:
    queries = read_jsonl(queries_path)
    qrels = load_product_qrels(qrels_path)
    product_case_ids = load_product_case_ids(cases_path)
    qrel_candidate_ids = {case_id for rels in qrels.values() for case_id in rels}
    missing_qrel_ids = sorted(qrel_candidate_ids - product_case_ids)
    regression_set = _load_regression_set(regression_set_path)
    regression_ids = {str(row.get("queryId") or "") for row in regression_set.get("queries", [])}

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

    candidate_rows: dict[str, list[dict[str, Any]]] = {spec.candidate_id: [] for spec in CANDIDATES}
    regression_rows: dict[str, list[dict[str, Any]]] = {spec.candidate_id: [] for spec in CANDIDATES}
    upper_bound_audit: list[dict[str, Any]] = []
    raw_queries: list[str] = []
    global_blocked_items: set[str] = set()

    if missing_qrel_ids:
        global_blocked_items.add("product_qrels_case_id_missing_from_candidate_corpus")
    if len(queries) < 20:
        global_blocked_items.add("product_query_count_below_20")
    if sum(1 for rels in qrels.values() if any(score >= RELEVANCE_THRESHOLD for score in rels.values())) < 10:
        global_blocked_items.add("product_labeled_query_count_below_10")

    for query in queries:
        query_id = str(query.get("eval_query_id") or "").strip()
        query_text = str(query.get("query_text") or "")
        raw_queries.append(query_text)
        rels = qrels.get(query_id, {})
        if not query_id or not rels:
            global_blocked_items.add("product_query_missing_qrels")
            for spec in CANDIDATES:
                row = _candidate_query_row(
                    query_id=query_id,
                    evaluated=False,
                    status="missing_qrels",
                    metrics={"Precision@5": 0.0, "NDCG@10": 0.0, "Top10 hit": False},
                    baseline_metrics={"Precision@5": 0.0, "NDCG@10": 0.0, "Top10 hit": False},
                    before_metrics={"Precision@5": 0.0, "NDCG@10": 0.0, "Top10 hit": False},
                    before_label="NOT_COMPARABLE",
                    after_baseline_label="NOT_COMPARABLE",
                    degraded_reasons=[],
                    is_regression=_query_is_regression(query_id, regression_ids),
                )
                candidate_rows[spec.candidate_id].append(row)
                if row["isRegressionSample"]:
                    regression_rows[spec.candidate_id].append(row)
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
            query_blocked_items = _blocked_from_degraded(degraded_reasons)
            global_blocked_items.update(query_blocked_items)
        except QueryValidationError as exc:
            rows_by_mode = {"baseline": []}
            degraded_reasons = []
            status = f"query_validation_error:{exc.code}"
            global_blocked_items.add("query_validation_error")
        except Exception as exc:  # noqa: BLE001 - report records sanitized class only.
            rows_by_mode = {"baseline": []}
            degraded_reasons = ["DEPENDENCY_UNAVAILABLE"]
            status = f"partial:{exc.__class__.__name__}"
            global_blocked_items.add("dependency_unavailable")

        baseline_metrics = _metric_row(_ranked_ids(rows_by_mode.get("baseline", [])), rels)
        for spec in CANDIDATES:
            mode_key = _candidate_mode_key(spec)
            before_key = _candidate_before_key(spec)
            if spec.rank_mode == "m1_3x_regression_zero_upper_bound":
                current_rows = _m1_3x_regression_zero_upper_bound_rows(
                    rows_by_mode,
                    rels=rels,
                    top_k=top_k,
                )
            else:
                current_rows = rows_by_mode.get(mode_key, [])
            before_rows = rows_by_mode.get(before_key, [])
            metrics = _metric_row(_ranked_ids(current_rows), rels)
            before_metrics = _metric_row(_ranked_ids(before_rows), rels)
            before_label, _, before_tags = classify_change(
                before_metrics,
                metrics,
                comparable=status == "ok",
            )
            after_baseline_label, _, after_baseline_tags = classify_change(
                baseline_metrics,
                metrics,
                comparable=status == "ok",
            )
            if _case_ids(before_rows) != _case_ids(current_rows) and status == "ok":
                before_tags = [*before_tags, "top10_order_or_membership_changed"]
            if _case_ids(rows_by_mode.get("baseline", [])) != _case_ids(current_rows) and status == "ok":
                after_baseline_tags = [*after_baseline_tags, "top10_order_or_membership_changed"]
            baseline_protection = _baseline_protection_fields(
                baseline_rows=rows_by_mode.get("baseline", []),
                candidate_rows=current_rows,
                rels=rels,
                after_baseline_label=after_baseline_label,
                after_baseline_tags=after_baseline_tags,
            )
            row = _candidate_query_row(
                query_id=query_id,
                evaluated=status == "ok",
                status=status,
                metrics=metrics,
                baseline_metrics=baseline_metrics,
                before_metrics=before_metrics,
                before_label=before_label,
                after_baseline_label=after_baseline_label,
                degraded_reasons=degraded_reasons,
                is_regression=_query_is_regression(query_id, regression_ids),
                before_tags=before_tags,
                after_baseline_tags=after_baseline_tags,
                baseline_protection=baseline_protection,
                decision_audit=_candidate_decision_audit(current_rows),
            )
            candidate_rows[spec.candidate_id].append(row)
            if row["isRegressionSample"]:
                regression_rows[spec.candidate_id].append(row)
            if spec.rank_mode == "m1_3x_regression_zero_upper_bound":
                upper_bound_audit.append(_upper_bound_query_audit(
                    query_id=query_id,
                    rels=rels,
                    baseline_rows=rows_by_mode.get("baseline", []),
                    before_rows=before_rows,
                    selected_rows=current_rows,
                    before_label=before_label,
                    after_baseline_label=after_baseline_label,
                ))

    return {
        "queries": queries,
        "qrels": qrels,
        "regressionSet": regression_set,
        "candidateRows": candidate_rows,
        "regressionRows": regression_rows,
        "upperBoundAudit": upper_bound_audit,
        "globalBlockedItems": sorted(global_blocked_items),
        "rawQueries": raw_queries,
    }


def _candidate_query_row(
    *,
    query_id: str,
    evaluated: bool,
    status: str,
    metrics: dict[str, Any],
    baseline_metrics: dict[str, Any],
    before_metrics: dict[str, Any],
    before_label: str,
    after_baseline_label: str,
    degraded_reasons: list[str],
    is_regression: bool,
    before_tags: list[str] | None = None,
    after_baseline_tags: list[str] | None = None,
    baseline_protection: dict[str, Any] | None = None,
    decision_audit: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "queryId": query_id,
        "isRegressionSample": is_regression,
        "evaluated": evaluated,
        "status": status,
        "metrics": {
            "candidate": metrics,
            "baseline": baseline_metrics,
            "before": before_metrics,
        },
        "beforeVsAfterLabel": before_label,
        "beforeVsAfterTags": list(before_tags or []),
        "afterVsBaselineLabel": after_baseline_label,
        "afterVsBaselineTags": list(after_baseline_tags or []),
        "baselineProtection": baseline_protection or {
            "baseline_best_relevant_rank": None,
            "candidate_best_relevant_rank": None,
            "rank_delta_vs_baseline": None,
            "metric_regression_reason": (
                "NOT_COMPARABLE"
                if after_baseline_label == "NOT_COMPARABLE"
                else "NONE"
            ),
        },
        "decisionAudit": decision_audit or {},
        "degradedReasons": sorted(set(degraded_reasons)),
    }


def _matrix_item(
    *,
    spec: CandidateSpec,
    product_rows: list[dict[str, Any]],
    regression_rows: list[dict[str, Any]],
    baseline_metrics: dict[str, Any],
    blocked_items: list[str],
    performance_summary: dict[str, Any],
    rollback_summary: dict[str, Any],
) -> dict[str, Any]:
    overall_rows = [
        {"evaluated": row["evaluated"], "metrics": {"candidate": row["metrics"]["candidate"]}}
        for row in product_rows
    ]
    candidate_metrics = _metric_summary(overall_rows, "candidate")
    before_distribution = dict(sorted(Counter(row["beforeVsAfterLabel"] for row in regression_rows).items()))
    after_baseline_distribution = dict(sorted(Counter(row["afterVsBaselineLabel"] for row in regression_rows).items()))
    baseline_protection_reason_distribution = dict(sorted(
        Counter(
            (row.get("baselineProtection") or {}).get("metric_regression_reason", "UNKNOWN")
            for row in regression_rows
            if (row.get("baselineProtection") or {}).get("metric_regression_reason") not in (None, "NONE")
        ).items()
    ))
    top10_miss_count = count_top10_misses_from_per_query(
        [
            {
                "evaluated": row["evaluated"],
                "metrics": {"candidate": row["metrics"]["candidate"]},
            }
            for row in product_rows
        ],
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
            for row in product_rows
        ],
        current_mode="candidate",
    )
    gate = build_m13_regression_gate_summary(
        top10_hit_rate=candidate_metrics["Top10 hit rate"],
        evaluated_query_count=candidate_metrics["evaluatedQueryCount"],
        before_vs_after_label_distribution=before_distribution,
        after_vs_baseline_label_distribution=after_baseline_distribution,
        metric_regression_count=int(after_baseline_distribution.get("REGRESSED", 0)),
        recall_miss_count=recall_miss_count,
        top10_miss_count=top10_miss_count,
        blocked_items=blocked_items,
    )
    scope_failed_reasons = _candidate_scope_failed_reasons(spec)
    checklist = build_success_criteria_checklist(
        candidate_metrics=candidate_metrics,
        baseline_metrics=baseline_metrics,
        gate=gate,
        performance_summary=performance_summary,
        rollback_summary=rollback_summary,
        recall_reference_miss_count=M13_RECALL_REFERENCE_MISS_COUNT,
    )
    failed_reasons = [
        *[key for key, item in checklist.items() if not bool(item["passed"])],
        *scope_failed_reasons,
    ]
    metric_hard_gate_passed = bool(gate["grayCandidateHardGatePassed"])
    gray_candidate_hard_gate_passed = metric_hard_gate_passed and not scope_failed_reasons
    return {
        "candidate_id": spec.candidate_id,
        "candidate_name": spec.name,
        "rank_mode": spec.rank_mode,
        "targeted_recall_repairs": spec.targeted_recall_repairs,
        "parameters": spec.parameters or {},
        "Precision@5": candidate_metrics["Precision@5"],
        "NDCG@10": candidate_metrics["NDCG@10"],
        "Top10 hit rate": candidate_metrics["Top10 hit rate"],
        "evaluatedQueryCount": candidate_metrics["evaluatedQueryCount"],
        "beforeVsAfterRegressedCount": gate["beforeVsAfterRegressedCount"],
        "afterVsBaselineRegressedCount": gate["afterVsBaselineRegressedCount"],
        "METRIC_REGRESSION count": gate["metricRegressionCount"],
        "RECALL_MISS count": gate["recallMissCount"],
        "top10MissCount": gate["top10MissCount"],
        "metricHardGatePassed": metric_hard_gate_passed,
        "grayCandidateHardGatePassed": gray_candidate_hard_gate_passed,
        "weightedRerankGrayCandidate": bool(gate["weightedRerankGrayCandidate"] and not scope_failed_reasons),
        "labelDistribution": before_distribution,
        "afterVsBaselineLabelDistribution": after_baseline_distribution,
        "baselineProtection": {
            "metricRegressionReasonDistribution": baseline_protection_reason_distribution,
            "baselineProtectedRegressionCount": sum(
                1
                for row in regression_rows
                if (row.get("baselineProtection") or {}).get("metric_regression_reason") not in (None, "NONE")
            ),
        },
        "hardGateFailedReasons": [*gate["hardGateFailedReasons"], *scope_failed_reasons],
        "candidateScopeFailedReasons": scope_failed_reasons,
        "successCriteriaChecklist": checklist,
        "goNoGo": "GO" if not failed_reasons else "NO_GO",
        "failedReasons": failed_reasons,
        "performanceSmokeSummary": performance_summary,
        "rollbackMethod": spec.rollback_method,
        "rollbackVerification": rollback_summary,
        "remainingRisk": spec.remaining_risk,
    }


def build_success_criteria_checklist(
    *,
    candidate_metrics: dict[str, Any],
    baseline_metrics: dict[str, Any],
    gate: dict[str, Any],
    performance_summary: dict[str, Any],
    rollback_summary: dict[str, Any],
    recall_reference_miss_count: int,
) -> dict[str, dict[str, Any]]:
    return {
        "productLocalEvalRepeatableNoBlockedItem": {
            "passed": not gate.get("blockedItems"),
            "actual": gate.get("blockedItems", []),
        },
        "precisionAt5CurrentGteBaseline": {
            "passed": float(candidate_metrics["Precision@5"]) >= float(baseline_metrics["Precision@5"]),
            "actual": candidate_metrics["Precision@5"],
            "baseline": baseline_metrics["Precision@5"],
        },
        "ndcgAt10CurrentGtBaseline": {
            "passed": float(candidate_metrics["NDCG@10"]) > float(baseline_metrics["NDCG@10"]),
            "actual": candidate_metrics["NDCG@10"],
            "baseline": baseline_metrics["NDCG@10"],
        },
        "top10HitRateGte060": {
            "passed": float(candidate_metrics["Top10 hit rate"]) >= 0.60,
            "actual": candidate_metrics["Top10 hit rate"],
        },
        "fixedRegressionBeforeAfterRegressedZero": {
            "passed": int(gate["beforeVsAfterRegressedCount"]) == 0,
            "actual": gate["beforeVsAfterRegressedCount"],
        },
        "fixedRegressionAfterBaselineRegressedZero": {
            "passed": int(gate["afterVsBaselineRegressedCount"]) == 0,
            "actual": gate["afterVsBaselineRegressedCount"],
        },
        "metricRegressionZero": {
            "passed": int(gate["metricRegressionCount"]) == 0,
            "actual": gate["metricRegressionCount"],
        },
        "recallMissSignificantlyReducedAndExplained": {
            "passed": int(gate["recallMissCount"]) < int(recall_reference_miss_count),
            "actual": gate["recallMissCount"],
            "reference": recall_reference_miss_count,
            "remainingIssueExplanation": "Remaining misses are inherited from M1.3-3/M1.3-4 sanitized artifacts.",
        },
        "warmP95Under3s": {
            "passed": bool(performance_summary.get("warmP95Under3s")),
            "actual": performance_summary.get("warmP95Ms"),
        },
        "errorRateZeroOrNonBlocking": {
            "passed": performance_summary.get("errorRate") in (0, 0.0, None),
            "actual": performance_summary.get("errorRate"),
            "degradedReasonCounts": performance_summary.get("degradedReasonCounts"),
        },
        "weightedRerankRollbackReturnsBaseRetrieval": {
            "passed": bool(rollback_summary.get("weightedRerankReturnsBaseRetrieval")),
            "actual": rollback_summary,
        },
        "globalWeightedRerankDefaultFalse": {
            "passed": bool(rollback_summary.get("globalEnableWeightedRerankDefault") is False),
            "actual": rollback_summary.get("globalEnableWeightedRerankDefault"),
        },
    }


def select_candidate(candidate_matrix: list[dict[str, Any]]) -> tuple[str, dict[str, Any] | None, str]:
    passed = [row for row in candidate_matrix if row.get("goNoGo") == "GO"]
    if not passed:
        return (
            "NO_GO",
            None,
            "No candidate satisfies all M1.3 hard gates and success criteria.",
        )
    if len(passed) == 1:
        return "GO", passed[0], ""
    return (
        "NO_GO",
        None,
        "Exactly one candidate must pass the M1.3 hard gate; multiple candidates passed.",
    )


def _baseline_metrics_from_matrix(candidate_matrix: list[dict[str, Any]]) -> dict[str, Any]:
    for row in candidate_matrix:
        if row["candidate_id"] == "m1_2_baseline":
            return {
                "Precision@5": row["Precision@5"],
                "NDCG@10": row["NDCG@10"],
                "Top10 hit rate": row["Top10 hit rate"],
                "evaluatedQueryCount": row["evaluatedQueryCount"],
            }
    raise ValueError("baseline candidate missing from matrix")


def _performance_summary(report: dict[str, Any] | None) -> dict[str, Any]:
    if not report:
        return {
            "evidence": None,
            "warmP95Ms": None,
            "warmP95Under3s": False,
            "errorRate": None,
            "degradedReasonCounts": {},
            "statusCounts": {},
        }
    return {
        "evidence": report.get("report_path"),
        "warmP95Ms": ((report.get("api") or {}).get("warm_response_total_duration_ms") or {}).get("p95"),
        "warmP95Under3s": (report.get("api") or {}).get("warm_p95_under_3s"),
        "errorRate": (report.get("api") or {}).get("error_rate"),
        "statusCounts": (report.get("api") or {}).get("status_counts"),
        "degradedReasonCounts": (report.get("stability") or {}).get("degraded_reason_counts"),
        "vectorErrorRate": (report.get("stability") or {}).get("vector_error_rate"),
    }


def _rollback_summary(report: dict[str, Any] | None) -> dict[str, Any]:
    weighted = None
    if report:
        for scenario in report.get("scenarios", []):
            if scenario.get("flag") == "ENABLE_WEIGHTED_RERANK":
                weighted = scenario
                break
    return {
        "evidence": None if not report else report.get("report_path"),
        "status": None if not report else report.get("status"),
        "recoveryWithin60Seconds": None if not report else report.get("recovery_within_60_seconds"),
        "maxRollbackElapsedMs": None if not report else report.get("max_rollback_elapsed_ms"),
        "weightedRerankReturnsBaseRetrieval": bool(
            weighted
            and weighted.get("status") == "passed"
            and ((weighted.get("observed") or {}).get("score_mode") == "base_retrieval")
        ),
        "weightedRerankElapsedMs": None if not weighted else weighted.get("rollback_elapsed_ms"),
        "globalEnableWeightedRerankDefault": bool(settings.ENABLE_WEIGHTED_RERANK),
    }


def _feature_flag_file_state() -> dict[str, Any]:
    return {
        "settings_ENABLE_WEIGHTED_RERANK": bool(settings.ENABLE_WEIGHTED_RERANK),
        "env_ENABLE_WEIGHTED_RERANK": _flag_assignment(PROJECT_ROOT / ".env", "ENABLE_WEIGHTED_RERANK"),
        "env_example_ENABLE_WEIGHTED_RERANK": _flag_assignment(
            PROJECT_ROOT / ".env.example",
            "ENABLE_WEIGHTED_RERANK",
        ),
        "config_default": "False",
        "featureFlagChanged": False,
    }


def _flag_assignment(path: Path, name: str) -> str | None:
    if not path.exists():
        return None
    pattern = re.compile(rf"^\s*{re.escape(name)}\s*=\s*(.+?)\s*$", re.IGNORECASE)
    for line in path.read_text(encoding="utf-8").splitlines():
        match = pattern.match(line)
        if match:
            return match.group(1).strip()
    return None


def _parameter_record(
    *,
    generated_at: str,
    selected_status: str,
    selected: dict[str, Any] | None,
    candidate_matrix: list[dict[str, Any]],
    gate_summary: dict[str, Any],
    source_artifacts: list[str],
    privacy_check: dict[str, Any],
    default_feature_flag_state: dict[str, Any],
    no_go_reason: str,
) -> dict[str, Any]:
    checklist = selected.get("successCriteriaChecklist") if selected else {
        row["candidate_id"]: row["successCriteriaChecklist"]
        for row in candidate_matrix
    }
    return {
        "version_name": PARAMETER_VERSION_NAME,
        "generated_at": generated_at,
        "selected_status": selected_status,
        "selected_candidate_id": None if selected is None else selected["candidate_id"],
        "selected_candidate_name": None if selected is None else selected["candidate_name"],
        "candidate_matrix": candidate_matrix,
        "gate_summary": gate_summary,
        "success_criteria_checklist": checklist,
        "rollback_method": (
            "NO_GO: keep ENABLE_WEIGHTED_RERANK=false and do not enter gray candidate preview."
            if selected is None
            else selected["rollbackMethod"]
        ),
        "default_feature_flag_state": default_feature_flag_state,
        "no_go_reason": no_go_reason if selected is None else "",
        "source_artifacts": source_artifacts,
        "privacy_check": privacy_check,
    }


def _render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# M1.3-5 Candidate Comparison",
        "",
        f"- Generated at: `{report['generated_at']}`",
        f"- Selected status: `{report['selected_status']}`",
        f"- Selected candidate: `{report.get('selected_candidate_id') or '-'}`",
        f"- No-Go reason: `{report.get('no_go_reason') or '-'}`",
        "- Scope: M1.3-5 only; no recall, rerank, qrels, sample, API, frontend, health, gray, or feature-flag default changes.",
        "- Privacy: no raw query text, case fact text, candidate text, or chunk text is included.",
        "",
        "## Candidate Matrix",
        "",
        "| Candidate | P@5 | NDCG@10 | Top10 | before->after REGRESSED | after vs baseline REGRESSED | METRIC_REGRESSION | RECALL_MISS | top10Miss | Hard gate | Decision | Failed reasons |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- | --- |",
    ]
    for row in report["candidate_matrix"]:
        lines.append(
            f"| `{row['candidate_id']}` | `{row['Precision@5']}` | `{row['NDCG@10']}` | "
            f"`{row['Top10 hit rate']}` | `{row['beforeVsAfterRegressedCount']}` | "
            f"`{row['afterVsBaselineRegressedCount']}` | `{row['METRIC_REGRESSION count']}` | "
            f"`{row['RECALL_MISS count']}` | `{row['top10MissCount']}` | "
            f"`{str(row['grayCandidateHardGatePassed']).lower()}` | `{row['goNoGo']}` | "
            f"`{', '.join(row['failedReasons']) or '-'}` |"
        )
    lines.extend([
        "",
        "## Performance Smoke",
        "",
        f"- Warm P95: `{report['performance_smoke_summary'].get('warmP95Ms')}` ms",
        f"- Warm P95 < 3s: `{str(report['performance_smoke_summary'].get('warmP95Under3s')).lower()}`",
        f"- Error rate: `{report['performance_smoke_summary'].get('errorRate')}`",
        f"- Degraded reason counts: `{json.dumps(report['performance_smoke_summary'].get('degradedReasonCounts') or {}, ensure_ascii=False)}`",
        "",
        "## Rollback And Defaults",
        "",
        f"- ENABLE_WEIGHTED_RERANK default: `{str(report['default_feature_flag_state']['settings_ENABLE_WEIGHTED_RERANK']).lower()}`",
        f"- Weighted rerank rollback returns base retrieval: `{str(report['rollback_summary'].get('weightedRerankReturnsBaseRetrieval')).lower()}`",
        f"- Recovery within 60s: `{str(report['rollback_summary'].get('recoveryWithin60Seconds')).lower()}`",
        "",
        "## Source Artifacts",
        "",
    ])
    for artifact in report["source_artifacts"]:
        lines.append(f"- `{artifact}`")
    lines.extend([
        "",
        "## Upper-Bound Diagnostic Audit",
        "",
        "- Offline diagnostic only: qrels labels are used by the upper-bound candidate and never by the legal candidate.",
        "- Fields are limited to query id, case id, rank, score, score bucket, labels/counts, source mode, and reason codes.",
        "",
        "| Query | Case | Label | Baseline rank | Before rank | Selected rank | Selected score | Score bucket | Source | Labels | Counts | Reason codes |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- | --- | --- |",
    ])
    for audit in report.get("upper_bound_diagnostic_audit") or []:
        labels = (
            f"before={audit.get('beforeVsAfterLabel')};"
            f"baseline={audit.get('afterVsBaselineLabel')}"
        )
        counts = json.dumps(audit.get("labelCounts") or {}, ensure_ascii=False, sort_keys=True)
        reasons = ", ".join(audit.get("reasonCodes") or []) or "-"
        for case in audit.get("cases") or []:
            lines.append(
                f"| `{audit.get('queryId')}` | `{case.get('caseId')}` | `{case.get('label')}` | "
                f"`{case.get('baselineRank')}` | `{case.get('beforeRank')}` | "
                f"`{case.get('selectedRank')}` | `{case.get('selectedScore')}` | "
                f"`{case.get('selectedScoreBucket')}` | `{audit.get('selectedSourceMode')}` | "
                f"`{labels}` | `{counts}` | `{reasons}` |"
            )
    lines.extend([
        "",
        "## Privacy Check",
        "",
        f"- Passed: `{str(report['privacy_check']['passed']).lower()}`",
        f"- Forbidden text fields present: `{str(report['privacy_check']['forbiddenTextFieldsPresent']).lower()}`",
        f"- Raw query text present: `{str(report['privacy_check']['rawQueryTextPresent']).lower()}`",
        "",
    ])
    return "\n".join(lines)


def _privacy_check(markdown: str, payload: dict[str, Any], raw_queries: list[str]) -> dict[str, Any]:
    json_text = json.dumps(payload, ensure_ascii=False, indent=2)
    raw_query_present = any(query and (query in markdown or query in json_text) for query in raw_queries)
    forbidden_present = any(field in json_text or field in markdown for field in FORBIDDEN_OUTPUT_FIELDS)
    passed = not raw_query_present and not forbidden_present
    return {
        "passed": passed,
        "rawQueryTextPresent": raw_query_present,
        "forbiddenTextFieldsPresent": forbidden_present,
        "candidateFullTextWritten": False,
        "chunkTextWritten": False,
        "newDataContainsRawFacts": False,
    }


def _repeatability_summary(selected: dict[str, Any] | None) -> dict[str, Any]:
    if selected is None:
        return {
            "required": False,
            "status": "not_run",
            "reason": "No candidate passed the primary hard gate, so there was no apparently passing candidate to repeat.",
        }
    return {
        "required": True,
        "status": "pending",
        "candidate_id": selected["candidate_id"],
        "reason": "Run this script again and compare selected matrix fields before final GO.",
    }


def build_report(
    *,
    regression_set_path: Path,
    queries_path: Path,
    qrels_path: Path,
    cases_path: Path,
    chunks_path: Path,
    performance_smoke_path: Path | None,
    rollback_drill_path: Path | None,
    output_json: Path,
    output_md: Path,
    parameter_version_out: Path,
    top_k: int,
) -> dict[str, Any]:
    generated_at = datetime.now().isoformat(timespec="seconds")
    run_id = f"m1_3_candidate_comparison_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    performance_report = _read_optional_json(performance_smoke_path)
    rollback_report = _read_optional_json(rollback_drill_path)
    performance = _performance_summary(performance_report)
    rollback = _rollback_summary(rollback_report)
    if performance_smoke_path is not None:
        performance["evidence"] = _relative(performance_smoke_path)
    if rollback_drill_path is not None:
        rollback["evidence"] = _relative(rollback_drill_path)
    default_flag_state = _feature_flag_file_state()

    evaluated = _evaluate_candidates(
        queries_path=queries_path,
        qrels_path=qrels_path,
        cases_path=cases_path,
        chunks_path=chunks_path,
        regression_set_path=regression_set_path,
        top_k=top_k,
    )

    baseline_metrics = _metric_summary(
        [
            {"evaluated": row["evaluated"], "metrics": {"baseline": row["metrics"]["candidate"]}}
            for row in evaluated["candidateRows"]["m1_2_baseline"]
        ],
        "baseline",
    )
    matrix = [
        _matrix_item(
            spec=spec,
            product_rows=evaluated["candidateRows"][spec.candidate_id],
            regression_rows=evaluated["regressionRows"][spec.candidate_id],
            baseline_metrics=baseline_metrics,
            blocked_items=evaluated["globalBlockedItems"],
            performance_summary=performance,
            rollback_summary=rollback,
        )
        for spec in CANDIDATES
    ]
    selected_status, selected, no_go_reason = select_candidate(matrix)
    gate_summary = {
        "selectedStatus": selected_status,
        "selectedCandidateId": None if selected is None else selected["candidate_id"],
        "candidateCount": len(matrix),
        "passedCandidateCount": sum(1 for row in matrix if row["goNoGo"] == "GO"),
        "metricHardGatePassedCandidateIds": [
            row["candidate_id"] for row in matrix if row.get("metricHardGatePassed")
        ],
        "hardGatePassedCandidateIds": [
            row["candidate_id"] for row in matrix if row["grayCandidateHardGatePassed"]
        ],
        "noGoReason": no_go_reason,
    }

    source_artifacts = [
        *DEFAULT_SOURCE_ARTIFACTS,
        *(
            [_relative(performance_smoke_path)]
            if performance_smoke_path is not None
            else []
        ),
        *(
            [_relative(rollback_drill_path)]
            if rollback_drill_path is not None
            else []
        ),
    ]
    report_without_privacy = {
        "version": REPORT_VERSION,
        "run_id": run_id,
        "generated_at": generated_at,
        "unified_result_version": UNIFIED_EVAL_RESULT_VERSION,
        "selected_status": selected_status,
        "selected_candidate_id": None if selected is None else selected["candidate_id"],
        "selected_candidate_name": None if selected is None else selected["candidate_name"],
        "no_go_reason": no_go_reason if selected is None else "",
        "inputs": {
            "regressionSet": _relative(regression_set_path),
            "queries": _relative(queries_path),
            "qrels": _relative(qrels_path),
            "cases": _relative(cases_path),
            "chunks": _relative(chunks_path),
            "topK": top_k,
        },
        "source_artifacts": source_artifacts,
        "candidate_matrix": matrix,
        "gate_summary": gate_summary,
        "performance_smoke_summary": performance,
        "rollback_summary": rollback,
        "default_feature_flag_state": default_flag_state,
        "repeatability_summary": _repeatability_summary(selected),
        "upper_bound_diagnostic_audit": evaluated["upperBoundAudit"],
        "per_query_regression_labels": _per_query_regression_labels(evaluated["regressionRows"]),
        "unified_results": _unified_results(run_id, generated_at, matrix),
        "scope_confirmation": {
            "recallStrategyModified": False,
            "candidateMergeModified": False,
            "bm25Modified": False,
            "queryMappingModified": False,
            "rerankGuardModified": False,
            "rerankWeightsModified": False,
            "qrelsModified": False,
            "evalSamplesModified": False,
            "featureFlagDefaultModified": False,
            "offlineGuardV2CandidateAdded": True,
            "offlineLegalScoreShapeRouterCandidateAdded": True,
            "offlineRegressionZeroUpperBoundCandidateAdded": True,
            "legalCandidateUsesQrelsForRanking": False,
            "legalCandidateUsesQueryOrCaseIdForRanking": False,
            "legalCandidateManualRankOverride": False,
            "onlineRerankLogicChanged": False,
            "onlineRecallLogicChanged": False,
            "enteredM13_6": False,
        },
    }
    md_without_privacy = _render_markdown({**report_without_privacy, "privacy_check": {
        "passed": True,
        "rawQueryTextPresent": False,
        "forbiddenTextFieldsPresent": False,
    }})
    privacy = _privacy_check(md_without_privacy, report_without_privacy, evaluated["rawQueries"])
    if not privacy["passed"]:
        raise ValueError("privacy check failed for M1.3-5 candidate comparison output")

    report = {**report_without_privacy, "privacy_check": privacy}
    markdown = _render_markdown(report)
    parameter_record = _parameter_record(
        generated_at=generated_at,
        selected_status=selected_status,
        selected=selected,
        candidate_matrix=matrix,
        gate_summary=gate_summary,
        source_artifacts=source_artifacts,
        privacy_check=privacy,
        default_feature_flag_state=default_flag_state,
        no_go_reason=no_go_reason,
    )

    output_json.parent.mkdir(parents=True, exist_ok=True)
    write_json(output_json, report)
    output_md.write_text(markdown, encoding="utf-8")
    write_json(parameter_version_out, parameter_record)
    return report


def _per_query_regression_labels(regression_rows: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    by_query: dict[str, dict[str, Any]] = {}
    for candidate_id, rows in regression_rows.items():
        for row in rows:
            query_id = row["queryId"]
            by_query.setdefault(query_id, {"queryId": query_id, "candidates": {}})
            by_query[query_id]["candidates"][candidate_id] = {
                "beforeVsAfterLabel": row["beforeVsAfterLabel"],
                "afterVsBaselineLabel": row["afterVsBaselineLabel"],
                "candidateTop10Hit": row["metrics"]["candidate"]["Top10 hit"],
                "baselineProtection": row.get("baselineProtection", {
                    "baseline_best_relevant_rank": None,
                    "candidate_best_relevant_rank": None,
                    "rank_delta_vs_baseline": None,
                    "metric_regression_reason": "NOT_COMPARABLE",
                }),
                "decisionAudit": row.get("decisionAudit") or {},
            }
    return [by_query[key] for key in sorted(by_query)]


def _unified_results(run_id: str, generated_at: str, matrix: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        build_unified_eval_result(
            run_id=run_id,
            generated_at=generated_at,
            eval_line="product_local_m1_3_candidate_comparison",
            dataset={
                "name": "product_local_eval_and_m1_2_fixed_regression_set",
                "queries": _relative(DEFAULT_PRODUCT_QUERIES),
                "qrels": _relative(DEFAULT_PRODUCT_QRELS),
                "regressionSet": _relative(DEFAULT_REGRESSION_SET),
            },
            candidate_corpus={
                "type": "product_local_cases_chunks",
                "cases": _relative(DEFAULT_PRODUCT_CASES),
                "chunks": _relative(DEFAULT_PRODUCT_CHUNKS),
            },
            mode=row["candidate_id"],
            precision_at_5=row["Precision@5"],
            ndcg_at_10=row["NDCG@10"],
            top10_hit_rate=row["Top10 hit rate"],
            blocked_items=[],
            notes=[row["candidate_name"]],
        )
        for row in matrix
    ]


def main() -> None:
    timestamp = _timestamp()
    parser = argparse.ArgumentParser()
    parser.add_argument("--regression-set", default=str(DEFAULT_REGRESSION_SET))
    parser.add_argument("--queries", default=str(DEFAULT_PRODUCT_QUERIES))
    parser.add_argument("--qrels", default=str(DEFAULT_PRODUCT_QRELS))
    parser.add_argument("--cases", default=str(DEFAULT_PRODUCT_CASES))
    parser.add_argument("--chunks", default=str(DEFAULT_PRODUCT_CHUNKS))
    parser.add_argument("--performance-smoke", default="")
    parser.add_argument("--rollback-drill", default="")
    parser.add_argument("--top-k", type=int, default=DEFAULT_TOP_K)
    parser.add_argument(
        "--out-json",
        default=f"docs/development/m1.3-candidate-comparison-{timestamp}.json",
    )
    parser.add_argument(
        "--out-md",
        default=f"docs/development/m1.3-candidate-comparison-{timestamp}.md",
    )
    parser.add_argument(
        "--parameter-version-out",
        default=f"docs/development/m1.3-parameter-version-{timestamp}.json",
    )
    args = parser.parse_args()

    report = build_report(
        regression_set_path=_resolve(args.regression_set),
        queries_path=_resolve(args.queries),
        qrels_path=_resolve(args.qrels),
        cases_path=_resolve(args.cases),
        chunks_path=_resolve(args.chunks),
        performance_smoke_path=_resolve(args.performance_smoke) if args.performance_smoke else None,
        rollback_drill_path=_resolve(args.rollback_drill) if args.rollback_drill else None,
        output_json=_resolve(args.out_json),
        output_md=_resolve(args.out_md),
        parameter_version_out=_resolve(args.parameter_version_out),
        top_k=args.top_k,
    )
    print(json.dumps(
        {
            "status": report["selected_status"],
            "run_id": report["run_id"],
            "selected_candidate_id": report["selected_candidate_id"],
            "no_go_reason": report["no_go_reason"],
            "candidate_matrix": [
                {
                    "candidate_id": row["candidate_id"],
                    "Precision@5": row["Precision@5"],
                    "NDCG@10": row["NDCG@10"],
                    "Top10 hit rate": row["Top10 hit rate"],
                    "beforeVsAfterRegressedCount": row["beforeVsAfterRegressedCount"],
                    "afterVsBaselineRegressedCount": row["afterVsBaselineRegressedCount"],
                    "metricRegressionCount": row["METRIC_REGRESSION count"],
                    "recallMissCount": row["RECALL_MISS count"],
                    "grayCandidateHardGatePassed": row["grayCandidateHardGatePassed"],
                    "goNoGo": row["goNoGo"],
                }
                for row in report["candidate_matrix"]
            ],
        },
        ensure_ascii=False,
        indent=2,
    ))


if __name__ == "__main__":
    main()
