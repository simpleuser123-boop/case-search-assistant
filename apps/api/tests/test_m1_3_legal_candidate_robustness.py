from __future__ import annotations

import ast
import inspect

from app.core.config import Settings
from scripts import m1_3_legal_candidate_robustness as robustness


def _candidate_row(case_id: str, rank: int, *, score: float) -> dict:
    return {
        "rank": rank,
        "case_id": case_id,
        "score": score,
        "base_retrieval_score": score,
        "fusion_guards": [],
        "effective_feature_scores": {},
    }


def _router_fixture(prefix: str) -> dict[str, list[dict]]:
    return {
        "baseline": [
            _candidate_row(f"{prefix}-base-{rank}", rank, score=score)
            for rank, score in enumerate(
                [1.0, 0.95, 0.90, 0.80, 0.70, 0.55, 0.40, 0.30, 0.20, 0.10],
                1,
            )
        ],
        "m1_3_guarded_with_recall": [
            _candidate_row(f"{prefix}-current-{rank}", rank, score=0.80 - (rank * 0.0001))
            for rank in range(1, 11)
        ],
        "m1_3x_guard_v2_with_recall": [
            _candidate_row(f"{prefix}-guard-{rank}", rank, score=0.80 - (rank * 0.0002))
            for rank in range(1, 11)
        ],
    }


def test_robustness_grid_is_predeclared_and_includes_v2_threshold():
    assert robustness.SENSITIVITY_GRID == [
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
    assert robustness.M13X_LEGAL_SCORE_SHAPE_V2_CURRENT_RANK8_GAP_MAX == 0.001


def test_robustness_router_does_not_accept_qrels_labels_or_ids(monkeypatch):
    forbidden = {
        "qrels",
        "rels",
        "relevance",
        "label",
        "query_id",
        "case_id",
    }
    signature_names = set(inspect.signature(robustness._router_rows_for_threshold).parameters)
    assert signature_names.isdisjoint(forbidden)

    tree = ast.parse(inspect.getsource(robustness._router_rows_for_threshold))
    referenced_names = {
        node.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Name)
    }
    assert referenced_names.isdisjoint(forbidden)

    captured: dict = {}

    def fake_router(rows_by_mode, **kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr(robustness, "_legal_score_shape_router_rows", fake_router)
    robustness._router_rows_for_threshold(
        _router_fixture("a"),
        current_rank8_gap_max=0.001,
        top_k=10,
    )

    assert set(captured).isdisjoint(forbidden)


def test_query_and_case_ids_do_not_change_router_source_selection():
    first = robustness._router_rows_for_threshold(
        _router_fixture("first"),
        current_rank8_gap_max=0.001,
        top_k=10,
    )
    second = robustness._router_rows_for_threshold(
        _router_fixture("second"),
        current_rank8_gap_max=0.001,
        top_k=10,
    )

    assert first[0]["legalScoreShapeSourceMode"] == "m1_3x_guard_v2_with_recall"
    assert second[0]["legalScoreShapeSourceMode"] == "m1_3x_guard_v2_with_recall"
    assert first[0]["legalScoreShapeReasonCodes"] == second[0]["legalScoreShapeReasonCodes"]


def test_upper_bound_remains_qrels_blocked_and_not_robust_candidate():
    blockers = robustness._candidate_scope_failed_reasons(robustness._upper_bound_candidate_spec())

    assert blockers == [
        "USES_QRELS_FOR_RANKING_OFFLINE_ONLY",
        "OFFLINE_UPPER_BOUND_ONLY",
        "NOT_ALLOWED_AS_GRAY_CANDIDATE",
    ]
    assert robustness.UPPER_BOUND_CANDIDATE_ID != robustness.SELECTED_CANDIDATE_ID


def test_enable_weighted_rerank_default_still_false():
    assert Settings.model_fields["ENABLE_WEIGHTED_RERANK"].default is False
    assert robustness._feature_flag_file_state()["settings_ENABLE_WEIGHTED_RERANK"] is False


def test_sensitivity_gate_rejects_single_point_pass():
    single_point = [
        {"threshold": threshold, "goNoGo": "GO" if threshold == 0.001 else "NO_GO"}
        for threshold in robustness.SENSITIVITY_GRID
    ]
    neighborhood = [
        {"threshold": threshold, "goNoGo": "GO" if threshold in {0.00075, 0.001} else "NO_GO"}
        for threshold in robustness.SENSITIVITY_GRID
    ]

    assert robustness._sensitivity_gate(single_point)["notSinglePointPass"] is False
    assert robustness._sensitivity_gate(neighborhood)["notSinglePointPass"] is True


def test_robustness_privacy_check_accepts_sanitized_report_fields():
    payload = {
        "upper_bound_difference_audit": [
            {
                "queryId": "product_q001",
                "v2SourceMode": "baseline",
                "upperBoundSourceMode": "baseline",
                "ndcgDeltaUpperMinusV2": 0.0,
            }
        ],
        "scope_confirmation": {
            "qrelsUsedForRouterRanking": False,
            "queryIdUsedForDeterministicFoldSplitOnly": True,
        },
    }

    result = robustness._privacy_check("product_q001", payload, ["raw secret query"])

    assert result["passed"] is True
    assert result["rawQueryTextPresent"] is False
    assert result["forbiddenTextFieldsPresent"] is False
