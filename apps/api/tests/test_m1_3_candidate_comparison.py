from __future__ import annotations

import ast
import inspect
import json
from pathlib import Path

from app.core.config import Settings
from app.eval.result_format import (
    build_m13_regression_gate_from_report,
    build_m13_regression_gate_summary,
)
from scripts.m1_3_candidate_comparison import (
    CANDIDATES,
    M13X_GUARD_V2_BASELINE_ANCHOR_TOP_N,
    _baseline_protection_fields,
    _candidate_scope_failed_reasons,
    _legal_score_shape_router_rows,
    _m1_3x_legal_score_shape_router_rows,
    _m1_3x_legal_score_shape_router_v2_rows,
    _m1_3x_regression_zero_upper_bound_rows,
    _m1_3x_guard_v2_rows,
    _privacy_check,
    _upper_bound_query_audit,
    build_success_criteria_checklist,
    select_candidate,
)


PROJECT_ROOT = Path(__file__).resolve().parents[3]


def _candidate(candidate_id: str, *, decision: str = "NO_GO", regressions: int = 1) -> dict:
    return {
        "candidate_id": candidate_id,
        "candidate_name": candidate_id,
        "goNoGo": decision,
        "afterVsBaselineRegressedCount": regressions,
        "beforeVsAfterRegressedCount": regressions,
        "METRIC_REGRESSION count": regressions,
        "top10MissCount": 10,
    }


def test_select_candidate_returns_no_go_when_no_candidate_passes():
    status, selected, reason = select_candidate([
        _candidate("m1_2_baseline"),
        _candidate("m1_3_combined_candidate"),
    ])

    assert status == "NO_GO"
    assert selected is None
    assert "No candidate" in reason


def test_select_candidate_picks_single_go_candidate():
    go = _candidate("m1_3_combined_candidate", decision="GO", regressions=0)

    status, selected, reason = select_candidate([
        _candidate("m1_2_baseline"),
        go,
    ])

    assert status == "GO"
    assert selected == go
    assert reason == ""


def test_select_candidate_requires_exactly_one_go_candidate():
    first = _candidate("m1_3_guard_candidate", decision="GO", regressions=0)
    second = _candidate("m1_3_combined_candidate", decision="GO", regressions=0)

    status, selected, reason = select_candidate([first, second])

    assert status == "NO_GO"
    assert selected is None
    assert "Exactly one candidate" in reason


def test_candidate_gate_blocks_after_baseline_regression_despite_top10_gain():
    gate = build_m13_regression_gate_summary(
        top10_hit_rate=0.68,
        evaluated_query_count=25,
        before_vs_after_label_distribution={"STABLE": 25},
        after_vs_baseline_label_distribution={"IMPROVED": 24, "REGRESSED": 1},
        metric_regression_count=0,
        top10_miss_count=8,
        recall_miss_count=0,
        blocked_items=[],
    )

    assert gate["grayCandidateHardGatePassed"] is False
    assert gate["weightedRerankGrayCandidate"] is False
    assert "AFTER_VS_BASELINE_REGRESSED_GT_0" in gate["hardGateFailedReasons"]


def test_candidate_gate_blocks_metric_regression_despite_top10_gain():
    gate = build_m13_regression_gate_summary(
        top10_hit_rate=0.68,
        evaluated_query_count=25,
        before_vs_after_label_distribution={"STABLE": 25},
        after_vs_baseline_label_distribution={"STABLE": 25},
        metric_regression_count=1,
        top10_miss_count=8,
        recall_miss_count=0,
        blocked_items=[],
    )

    assert gate["grayCandidateHardGatePassed"] is False
    assert gate["weightedRerankGrayCandidate"] is False
    assert "METRIC_REGRESSION_GT_0" in gate["hardGateFailedReasons"]


def test_baseline_protection_fields_record_rank_delta_and_reason():
    fields = _baseline_protection_fields(
        baseline_rows=[{"rank": 1, "case_id": "case-good"}],
        candidate_rows=[{"rank": 3, "case_id": "case-good"}],
        rels={"case-good": 3},
        after_baseline_label="REGRESSED",
        after_baseline_tags=["ndcg_at_10_down"],
    )

    assert fields == {
        "baseline_best_relevant_rank": 1,
        "candidate_best_relevant_rank": 3,
        "rank_delta_vs_baseline": 2,
        "metric_regression_reason": "BASELINE_RELEVANT_RANK_WORSENED_NDCG_DOWN",
    }


def _guard_v2_row(
    case_id: str,
    *,
    rank: int,
    score: float,
    base: float,
    fact_supported: bool = False,
    multi_source: bool = False,
) -> dict:
    return {
        "rank": rank,
        "case_id": case_id,
        "score": score,
        "base_retrieval_score": base,
        "fusion_guards": ["no_fact_guard_relaxed_multi_source"] if multi_source else [],
        "effective_feature_scores": {
            "legal_element_overlap": 1.0 if fact_supported else 0.0,
            "case_cause_match": 0.0,
        },
    }


def _candidate_row(case_id: str, rank: int, *, score: float | None = None) -> dict:
    return {
        "rank": rank,
        "case_id": case_id,
        "score": 1.0 - (rank * 0.01) if score is None else score,
        "base_retrieval_score": 1.0 - (rank * 0.01),
        "fusion_guards": [],
        "effective_feature_scores": {},
    }


def _rows(case_ids: list[str]) -> list[dict]:
    return [_candidate_row(case_id, rank) for rank, case_id in enumerate(case_ids, 1)]


def test_guard_v2_candidate_is_offline_and_parameterized():
    guard_v2 = next(row for row in CANDIDATES if row.candidate_id == "m1_3x_guard_v2_candidate")

    assert guard_v2.rank_mode == "m1_3x_guard_v2"
    assert guard_v2.targeted_recall_repairs is True
    assert guard_v2.before_mode == "raw_weighted_with_recall"
    assert guard_v2.parameters["defaultPathChanged"] is False
    assert guard_v2.parameters["usesQrelsForRanking"] is False
    assert guard_v2.parameters["expandsRecallPool"] is False


def test_regression_zero_upper_bound_candidate_is_qrels_blocked():
    candidate = next(
        row for row in CANDIDATES
        if row.candidate_id == "m1_3x_regression_zero_upper_bound_candidate"
    )

    assert candidate.rank_mode == "m1_3x_regression_zero_upper_bound"
    assert candidate.parameters["usesQrelsForRanking"] is True
    assert candidate.parameters["offlineUpperBoundOnly"] is True
    assert candidate.parameters["allowedAsGrayCandidate"] is False
    assert _candidate_scope_failed_reasons(candidate) == [
        "USES_QRELS_FOR_RANKING_OFFLINE_ONLY",
        "OFFLINE_UPPER_BOUND_ONLY",
        "NOT_ALLOWED_AS_GRAY_CANDIDATE",
    ]


def test_legal_score_shape_router_candidates_are_gray_eligible_and_non_label():
    legal_candidates = [
        row for row in CANDIDATES
        if row.candidate_id in {
            "m1_3x_legal_score_shape_router_candidate",
            "m1_3x_legal_score_shape_router_v2_candidate",
        }
    ]

    assert {candidate.rank_mode for candidate in legal_candidates} == {
        "m1_3x_legal_score_shape_router",
        "m1_3x_legal_score_shape_router_v2",
    }
    for candidate in legal_candidates:
        assert candidate.parameters["usesQrelsForRanking"] is False
        assert candidate.parameters["usesQueryIdForRanking"] is False
        assert candidate.parameters["usesCaseIdHardcoding"] is False
        assert candidate.parameters["manualRankOverride"] is False
        assert candidate.parameters["allowedAsGrayCandidate"] is True
        assert _candidate_scope_failed_reasons(candidate) == []


def test_legal_score_shape_routers_have_no_label_or_id_decision_inputs():
    forbidden_names = {
        "qrels",
        "rels",
        "relevance",
        "label",
        "query_id",
        "case_id",
    }
    for router in (
        _m1_3x_legal_score_shape_router_rows,
        _m1_3x_legal_score_shape_router_v2_rows,
        _legal_score_shape_router_rows,
    ):
        source = inspect.getsource(router)
        tree = ast.parse(source)
        referenced_names = {
            node.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Name)
        }

        assert referenced_names.isdisjoint(forbidden_names)
    for router in (
        _m1_3x_legal_score_shape_router_rows,
        _m1_3x_legal_score_shape_router_v2_rows,
    ):
        assert set(inspect.signature(router).parameters) == {"rows_by_mode", "top_k"}


def test_legal_score_shape_router_selects_complete_source_order_without_rewriting_rank():
    rows_by_mode = {
        "baseline": [
            _candidate_row("base-1", 4, score=0.60),
            _candidate_row("base-2", 8, score=0.60),
        ],
        "m1_3_guarded_with_recall": [
            _candidate_row("current-1", 2, score=0.80),
            _candidate_row("current-2", 7, score=0.80),
        ],
        "m1_3x_guard_v2_with_recall": [
            _candidate_row("guard-1", 3, score=0.70),
            _candidate_row("guard-2", 6, score=0.70),
        ],
    }

    ranked = _m1_3x_legal_score_shape_router_rows(rows_by_mode, top_k=2)

    assert [row["case_id"] for row in ranked] == ["base-1", "base-2"]
    assert [row["rank"] for row in ranked] == [4, 8]
    assert ranked[0]["legalScoreShapeSourceMode"] == "baseline"
    assert ranked[0]["legalScoreShapeReasonCodes"][0] == "legal_score_shape_router_non_label_only"


def test_legal_score_shape_router_v2_routes_near_flat_boundary_to_guard_v2():
    rows_by_mode = {
        "baseline": [
            _candidate_row(f"base-{rank}", rank, score=score)
            for rank, score in enumerate(
                [1.0, 0.98, 0.96, 0.94, 0.92, 0.80, 0.70, 0.60, 0.50, 0.40],
                1,
            )
        ],
        "m1_3_guarded_with_recall": [
            _candidate_row(f"current-{rank}", rank, score=0.80 - (rank * 0.0005))
            for rank in range(1, 11)
        ],
        "m1_3x_guard_v2_with_recall": [
            _candidate_row(f"guard-{rank}", rank, score=0.80 - (rank * 0.001))
            for rank in range(1, 11)
        ],
    }

    v1 = _m1_3x_legal_score_shape_router_rows(rows_by_mode, top_k=10)
    v2 = _m1_3x_legal_score_shape_router_v2_rows(rows_by_mode, top_k=10)

    assert v1[0]["legalScoreShapeSourceMode"] == "m1_3_guarded_with_recall"
    assert v2[0]["legalScoreShapeSourceMode"] == "m1_3x_guard_v2_with_recall"
    assert [row["rank"] for row in v2] == list(range(1, 11))


def test_upper_bound_audit_contains_only_sanitized_id_rank_score_label_fields():
    audit = _upper_bound_query_audit(
        query_id="q-1",
        rels={"case-relevant": 3},
        baseline_rows=[_candidate_row("case-relevant", 1, score=0.77)],
        before_rows=[_candidate_row("case-relevant", 3, score=0.71)],
        selected_rows=[{
            **_candidate_row("case-relevant", 2, score=0.75),
            "regressionZeroSourceMode": "baseline",
            "regressionZeroReasonCodes": ["qrels_mode_selection"],
        }],
        before_label="STABLE",
        after_baseline_label="REGRESSED",
    )

    assert set(audit) == {
        "queryId",
        "selectedSourceMode",
        "beforeVsAfterLabel",
        "afterVsBaselineLabel",
        "labelCounts",
        "reasonCodes",
        "cases",
    }
    assert set(audit["cases"][0]) == {
        "caseId",
        "label",
        "baselineRank",
        "beforeRank",
        "selectedRank",
        "selectedScore",
        "selectedScoreBucket",
    }


def test_weighted_rerank_default_remains_false():
    assert Settings.model_fields["ENABLE_WEIGHTED_RERANK"].default is False


def test_regression_zero_upper_bound_selects_non_regressing_source_mode():
    rows_by_mode = {
        "baseline": _rows(["rel", "base-2", "base-3", "base-4", "base-5"]),
        "raw_weighted_with_recall": _rows(["weak-1", "rel", "weak-3", "weak-4", "weak-5"]),
        "m1_3_guarded_with_recall": _rows(["bad-1", "bad-2", "bad-3", "bad-4", "bad-5", "rel"]),
        "m1_3x_guard_v2_with_recall": _rows(["rel", "guard-2", "guard-3", "guard-4", "guard-5"]),
        "m1_2_guarded_with_recall": _rows(["bad-1", "bad-2", "rel", "bad-4", "bad-5"]),
    }

    ranked = _m1_3x_regression_zero_upper_bound_rows(
        rows_by_mode,
        rels={"rel": 3},
        top_k=5,
    )

    assert [row["case_id"] for row in ranked] == ["rel", "guard-2", "guard-3", "guard-4", "guard-5"]
    assert ranked[0]["final_score_source"] == "m1_3x_regression_zero_upper_bound_candidate"
    assert ranked[0]["regressionZeroSourceMode"] == "m1_3x_guard_v2_with_recall"
    assert "regression_zero_upper_bound_qrels_mode_selection" in ranked[0]["regressionZeroReasonCodes"]


def test_guard_v2_baseline_anchor_stays_inside_score_bucket():
    rows = [
        _guard_v2_row("higher-score-bucket", rank=1, score=0.812, base=0.90),
        _guard_v2_row("baseline-anchor", rank=3, score=0.771, base=0.70),
        _guard_v2_row("same-bucket-non-anchor", rank=2, score=0.779, base=0.72),
    ]
    baseline_rows = [
        {"rank": 1, "case_id": "baseline-anchor"},
        {"rank": M13X_GUARD_V2_BASELINE_ANCHOR_TOP_N + 1, "case_id": "same-bucket-non-anchor"},
    ]

    ranked = _m1_3x_guard_v2_rows(rows, baseline_rows=baseline_rows, top_k=3)

    assert [row["case_id"] for row in ranked] == [
        "higher-score-bucket",
        "baseline-anchor",
        "same-bucket-non-anchor",
    ]
    assert "guard_v2_baseline_top10_anchor" in ranked[1]["guardV2ReasonCodes"]
    assert "guard_v2_anchor_bonus_applied" in ranked[1]["guardV2ReasonCodes"]


def test_guard_v2_fact_support_precedes_baseline_anchor_inside_bucket():
    rows = [
        _guard_v2_row("baseline-anchor", rank=1, score=0.779, base=0.70),
        _guard_v2_row("fact-supported", rank=2, score=0.771, base=0.68, fact_supported=True),
    ]
    baseline_rows = [{"rank": 1, "case_id": "baseline-anchor"}]

    ranked = _m1_3x_guard_v2_rows(rows, baseline_rows=baseline_rows, top_k=2)

    assert [row["case_id"] for row in ranked] == ["fact-supported", "baseline-anchor"]
    assert "guard_v2_fact_supported_candidate_protected" in ranked[0]["guardV2ReasonCodes"]


def test_guard_v2_multi_source_bonus_cannot_outrank_baseline_anchor_in_same_bucket():
    rows = [
        _guard_v2_row("multi-source-non-anchor", rank=1, score=0.779, base=0.75, multi_source=True),
        _guard_v2_row("baseline-anchor", rank=2, score=0.771, base=0.70),
    ]
    baseline_rows = [{"rank": 1, "case_id": "baseline-anchor"}]

    ranked = _m1_3x_guard_v2_rows(rows, baseline_rows=baseline_rows, top_k=2)

    assert [row["case_id"] for row in ranked] == ["baseline-anchor", "multi-source-non-anchor"]
    assert "guard_v2_multi_source_bonus_bucket_limited" in ranked[1]["guardV2ReasonCodes"]


def test_old_m1_3_final_candidate_artifact_reads_without_new_fields():
    path = PROJECT_ROOT / "docs/development/m1.3-final-candidate-comparison-20260610-152812.json"
    report = json.loads(path.read_text(encoding="utf-8"))

    gate = build_m13_regression_gate_from_report(report)

    assert gate["afterVsBaselineRegressedCount"] == 7
    assert gate["metricRegressionCount"] == 7
    assert gate["recallMissCount"] == 7
    assert gate["grayCandidateHardGatePassed"] is False


def test_success_checklist_fails_when_regression_counts_are_nonzero():
    checklist = build_success_criteria_checklist(
        candidate_metrics={
            "Precision@5": 0.05,
            "NDCG@10": 0.17,
            "Top10 hit rate": 0.68,
            "evaluatedQueryCount": 25,
        },
        baseline_metrics={
            "Precision@5": 0.032,
            "NDCG@10": 0.134,
            "Top10 hit rate": 0.48,
            "evaluatedQueryCount": 25,
        },
        gate={
            "blockedItems": [],
            "beforeVsAfterRegressedCount": 0,
            "afterVsBaselineRegressedCount": 7,
            "metricRegressionCount": 7,
            "recallMissCount": 7,
        },
        performance_summary={
            "warmP95Under3s": True,
            "warmP95Ms": 500,
            "errorRate": 0.0,
            "degradedReasonCounts": {},
        },
        rollback_summary={
            "weightedRerankReturnsBaseRetrieval": True,
            "globalEnableWeightedRerankDefault": False,
        },
        recall_reference_miss_count=11,
    )

    assert checklist["fixedRegressionAfterBaselineRegressedZero"]["passed"] is False
    assert checklist["metricRegressionZero"]["passed"] is False
    assert checklist["top10HitRateGte060"]["passed"] is True


def test_privacy_check_rejects_raw_query_and_forbidden_fields():
    payload = {"per_query": [{"query_text": "raw secret query"}]}
    result = _privacy_check("raw secret query", payload, ["raw secret query"])

    assert result["passed"] is False
    assert result["rawQueryTextPresent"] is True
    assert result["forbiddenTextFieldsPresent"] is True


def test_privacy_check_accepts_ids_and_metrics_only():
    payload = {
        "per_query_regression_labels": [
            {"queryId": "product_q001", "candidateTop10Hit": False}
        ]
    }
    result = _privacy_check("product_q001", payload, ["unwritten raw query"])

    assert result["passed"] is True
