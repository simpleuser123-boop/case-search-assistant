from __future__ import annotations

import json
from pathlib import Path

from app.eval.result_format import (
    build_m13_regression_gate_from_report,
    build_m13_regression_gate_summary,
)
from app.rerank.models import RankedCaseCandidate
from app.retrieval.models import CaseCandidate
from scripts.m1_2_regression import (
    _load_regression_set,
    _m1_2_guarded_ranked_from_scored,
    classify_change,
)


PROJECT_ROOT = Path(__file__).resolve().parents[3]


def _metrics(*, p5: float, ndcg: float, hit: bool) -> dict:
    return {"Precision@5": p5, "NDCG@10": ndcg, "Top10 hit": hit}


def _m13_gate(
    *,
    top10: float = 0.64,
    before_regressed: int = 0,
    after_regressed: int = 0,
    metric_regression: int = 0,
    top10_miss: int = 9,
    recall_miss: int = 3,
) -> dict:
    return build_m13_regression_gate_summary(
        top10_hit_rate=top10,
        evaluated_query_count=25,
        before_vs_after_label_distribution={"REGRESSED": before_regressed},
        after_vs_baseline_label_distribution={"REGRESSED": after_regressed},
        metric_regression_count=metric_regression,
        top10_miss_count=top10_miss,
        recall_miss_count=recall_miss,
        blocked_items=[],
    )


def test_classify_change_marks_improvement_when_top10_hit_recovers():
    label, label_zh, tags = classify_change(
        _metrics(p5=0.0, ndcg=0.0, hit=False),
        _metrics(p5=0.0, ndcg=0.2, hit=True),
    )

    assert label == "IMPROVED"
    assert label_zh == "改善"
    assert "top10_hit_recovered" in tags


def test_classify_change_marks_regression_before_stability():
    label, label_zh, tags = classify_change(
        _metrics(p5=0.2, ndcg=0.3, hit=True),
        _metrics(p5=0.2, ndcg=0.1, hit=True),
    )

    assert label == "REGRESSED"
    assert label_zh == "退化"
    assert tags == ["ndcg_at_10_down"]


def test_classify_change_marks_stable_when_metrics_match():
    label, label_zh, tags = classify_change(
        _metrics(p5=0.2, ndcg=0.3, hit=True),
        _metrics(p5=0.2, ndcg=0.3, hit=True),
    )

    assert label == "STABLE"
    assert label_zh == "持平"
    assert tags == ["metric_equal"]


def test_classify_change_marks_not_comparable():
    label, label_zh, tags = classify_change(
        _metrics(p5=0.0, ndcg=0.0, hit=False),
        _metrics(p5=0.0, ndcg=0.0, hit=False),
        comparable=False,
    )

    assert label == "NOT_COMPARABLE"
    assert label_zh == "不可比"
    assert tags == ["not_comparable"]


def test_fixed_regression_set_is_sanitized_and_covers_required_sample_types():
    path = PROJECT_ROOT / "data/eval/m1_2_regression_set_20260609.json"
    payload = _load_regression_set(path)
    raw_text = json.dumps(payload, ensure_ascii=False)
    sample_types = {row["sampleType"] for row in payload["queries"]}

    assert len(payload["queries"]) == 25
    assert len({row["queryId"] for row in payload["queries"]}) == 25
    assert {
        "high_value_bad_case",
        "typical_success",
        "weight_sensitive_boundary",
    }.issubset(sample_types)
    assert "query_text" not in raw_text
    assert "case_text" not in raw_text
    assert payload["privacy"]["containsRawQueryText"] is False


def test_m13_gate_fails_when_aggregate_improves_but_before_after_regresses():
    gate = _m13_gate(before_regressed=1)

    assert gate["grayCandidateHardGatePassed"] is False
    assert gate["weightedRerankGrayCandidate"] is False
    assert "BEFORE_VS_AFTER_REGRESSED_GT_0" in gate["hardGateFailedReasons"]


def test_m13_gate_fails_when_aggregate_improves_but_after_baseline_regresses():
    gate = _m13_gate(after_regressed=1)

    assert gate["grayCandidateHardGatePassed"] is False
    assert gate["weightedRerankGrayCandidate"] is False
    assert "AFTER_VS_BASELINE_REGRESSED_GT_0" in gate["hardGateFailedReasons"]


def test_m13_gate_fails_when_metric_regression_exists():
    gate = _m13_gate(metric_regression=1)

    assert gate["grayCandidateHardGatePassed"] is False
    assert gate["weightedRerankGrayCandidate"] is False
    assert "METRIC_REGRESSION_GT_0" in gate["hardGateFailedReasons"]


def test_m13_gate_fails_when_top10_hit_rate_below_threshold():
    gate = _m13_gate(top10=0.56)

    assert gate["grayCandidateHardGatePassed"] is False
    assert gate["weightedRerankGrayCandidate"] is False
    assert "TOP10_HIT_RATE_BELOW_0_60" in gate["hardGateFailedReasons"]


def test_m13_gate_passes_when_all_hard_gate_conditions_pass():
    gate = _m13_gate(top10=0.60)

    assert gate["grayCandidateHardGatePassed"] is True
    assert gate["weightedRerankGrayCandidate"] is True
    assert gate["hardGateFailedReasons"] == []


def test_m13_gate_reads_old_m1_2_regression_artifact_compatibly():
    path = PROJECT_ROOT / "docs/development/m1.2-regression-run-final-20260609-195835.json"
    report = json.loads(path.read_text(encoding="utf-8"))

    gate = build_m13_regression_gate_from_report(report)

    assert gate["beforeVsAfterRegressedCount"] == 4
    assert gate["afterVsBaselineRegressedCount"] == 9
    assert gate["top10MissCount"] == 12
    assert gate["metricRegressionCount"] == 9
    assert gate["recallMissCount"] == 11
    assert gate["grayCandidateHardGatePassed"] is False
    assert gate["weightedRerankGrayCandidate"] == gate["grayCandidateHardGatePassed"]


def test_m1_2_runner_replays_legacy_guarded_score_not_current_final_score():
    def ranked(case_id: str, final_score: float, guarded_score: float, input_rank: int) -> RankedCaseCandidate:
        return RankedCaseCandidate(
            candidate=CaseCandidate(
                case_id=case_id,
                top_chunk_id=f"{case_id}-chunk",
                source_chunk_ids=[f"{case_id}-chunk"],
                hit_chunk_ids=[f"{case_id}-chunk"],
                retrieval_source=["test_vector"],
                metadata={},
                matched_text="脱敏测试片段",
                source="fixture",
                retrieval_score=guarded_score,
            ),
            final_score=final_score,
            score_breakdown={
                "m1_2_guarded_score": guarded_score,
                "base_retrieval_score": guarded_score,
                "input_rank": input_rank,
            },
        )

    rows = _m1_2_guarded_ranked_from_scored([
        ranked("m1-3-candidate-top", final_score=0.9, guarded_score=0.3, input_rank=0),
        ranked("m1-2-guarded-top", final_score=0.4, guarded_score=0.8, input_rank=1),
    ])

    assert [row.candidate.case_id for row in rows] == [
        "m1-2-guarded-top",
        "m1-3-candidate-top",
    ]


def test_m1_2_regression_artifact_does_not_expose_raw_text_fields():
    path = PROJECT_ROOT / "docs/development/m1.2-regression-run-final-20260609-195835.json"
    report_text = path.read_text(encoding="utf-8")

    assert '"query_text"' not in report_text
    assert '"raw_query"' not in report_text
    assert '"case_text"' not in report_text
    assert '"candidate_text"' not in report_text
    assert '"chunk_text"' not in report_text
    assert '"matched_text"' not in report_text
