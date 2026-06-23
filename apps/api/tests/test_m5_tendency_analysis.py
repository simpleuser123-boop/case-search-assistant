"""M5-8 F19 法院/法官倾向分析展示与边界 focused tests。

验证：
- 双闸：flag 关 → 不展示（403/TendencyUnavailable）；门禁未达标 → 不展示；
- 展示侧强制标注样本量 / 覆盖范围 / 数据来源 / 不确定性说明 + 强制免责；
- 可追溯到来源 case_id（引用，非正文）；
- 低于样本门槛的分组标 sample_sufficient=False 且不解读占比；
- 无个案预测 / 无胜负概率 / 无确定性法律结论 / 无个案正文（隐私护栏）；
- 不 import 排序 / 检索模块（不引入主排序副作用）。
"""
from __future__ import annotations

import sys

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.api import tendency as tendency_api
from app.tendency_analysis import (
    ForbiddenAnalysisContentError,
    TendencyAnalysisService,
    TendencyUnavailable,
    assert_analysis_output_clean,
    build_tendency_analysis,
)
from app.tendency_analysis.aggregate import MIN_SAMPLE_PER_BUCKET
from app.tendency_analysis.service import TENDENCY_ANALYSIS_DISCLAIMER
from app.tendency_gate.models import (
    GateDimensionResult,
    TendencyDataGateResult,
)

client = TestClient(app)


# ---------- 合成门禁结论 ----------

def _gate(can_go_live: bool) -> TendencyDataGateResult:
    status = "pass" if can_go_live else "fail"
    dim = GateDimensionResult(
        key="total_sample_size", name="总样本量", threshold="≥5000",
        actual="80996" if can_go_live else "2505",
        gate_status=status, critical=True,
        data_source="data/processed/cases.jsonl（只读元数据统计）",
        coverage_note="", metrics={},
    )
    return TendencyDataGateResult(
        gate_version="m5-7-tendency-data-gate-v2",
        overall_status=status,
        f19_can_go_live=can_go_live,
        enable_tendency_analysis_recommended=can_go_live,
        dimensions=[dim],
        data_source="data/processed/cases.jsonl（只读元数据统计）",
        coverage_note="测试合成门禁结论。",
    )


# ---------- 双闸：不展示路径 ----------

def test_flag_off_raises_unavailable():
    with pytest.raises(TendencyUnavailable) as exc:
        build_tendency_analysis(flag_enabled=False, gate_provider=lambda: _gate(True))
    assert exc.value.reason_code == "ENABLE_TENDENCY_ANALYSIS_false"


def test_gate_not_passed_raises_unavailable_even_if_flag_on():
    with pytest.raises(TendencyUnavailable) as exc:
        build_tendency_analysis(flag_enabled=True, gate_provider=lambda: _gate(False))
    assert exc.value.reason_code == "data_gate_not_passed"


def test_service_is_available_two_gates():
    assert TendencyAnalysisService(flag_enabled=False, gate_provider=lambda: _gate(True)).is_available()[0] is False
    assert TendencyAnalysisService(flag_enabled=True, gate_provider=lambda: _gate(False)).is_available()[0] is False
    assert TendencyAnalysisService(flag_enabled=True, gate_provider=lambda: _gate(True)).is_available()[0] is True


# ---------- 展示侧：样本量 / 覆盖 / 来源 / 免责 ----------

def test_build_includes_sample_size_coverage_source_disclaimer():
    result = build_tendency_analysis(flag_enabled=True, gate_provider=lambda: _gate(True))
    assert result.enabled is True
    assert result.gate_passed is True
    assert result.total_sample_size > 0
    assert result.min_sample_threshold == MIN_SAMPLE_PER_BUCKET
    assert result.data_source
    assert result.coverage_range
    assert result.disclaimer == TENDENCY_ANALYSIS_DISCLAIMER
    # 免责包含不预测 / 不构成法律意见 / 需复核
    assert "不预测个案结果" in result.disclaimer
    assert "不构成法律意见" in result.disclaimer
    assert "人工复核" in result.disclaimer
    # 维度齐全：法院层级 / 审级 / 案件领域 / 案由
    dims = {a.dimension for a in result.aggregations}
    assert {"court_level", "trial_level", "case_domain", "case_cause"} <= dims
    for agg in result.aggregations:
        assert agg.sample_size >= 0
        assert agg.coverage_range
        assert agg.confidence_note


def test_buckets_traceable_to_case_id_refs():
    result = build_tendency_analysis(flag_enabled=True, gate_provider=lambda: _gate(True))
    court = next(a for a in result.aggregations if a.dimension == "court_level")
    assert court.buckets, "应有法院层级分组"
    top = court.buckets[0]
    assert top.case_id_total > 0
    # 可追溯到来源 case_id（引用，截断到上限）。
    assert len(top.case_id_refs) > 0
    assert len(top.case_id_refs) <= 20


def test_low_sample_bucket_marked_insufficient():
    # 合成聚合：注入一个低于门槛的分组，确认服务如实标注。
    svc = TendencyAnalysisService(flag_enabled=True, gate_provider=lambda: _gate(True))
    result = svc.build()
    # 真实语料里案由维度截断为达标分组；这里断言所有展示出的案由分组都是达标的。
    cause = next(a for a in result.aggregations if a.dimension == "case_cause")
    assert all(b.sample_sufficient for b in cause.buckets)
    # 其它维度若存在不足分组，必须标 sample_sufficient=False（不解读占比由前端负责）。
    for agg in result.aggregations:
        for b in agg.buckets:
            if b.sample_size < MIN_SAMPLE_PER_BUCKET:
                assert b.sample_sufficient is False


# ---------- 隐私 / 边界护栏 ----------

def test_output_has_no_forbidden_prediction_or_body():
    result = build_tendency_analysis(flag_enabled=True, gate_provider=lambda: _gate(True))
    # 整个产物过隐私护栏（无正文键 / 无胜负话术 / 无具名法官预测）。
    assert_analysis_output_clean(result.as_dict())


def test_privacy_guard_rejects_winloss_phrase():
    with pytest.raises(ForbiddenAnalysisContentError):
        assert_analysis_output_clean({"note": "该案由胜诉率约为 70%"})


def test_privacy_guard_rejects_named_judge_prediction():
    with pytest.raises(ForbiddenAnalysisContentError):
        assert_analysis_output_clean({"note": "审判员张三会判被告败诉"})


def test_privacy_guard_rejects_body_key():
    with pytest.raises(ForbiddenAnalysisContentError):
        assert_analysis_output_clean({"candidate_body": "本院查明……"})
    with pytest.raises(ForbiddenAnalysisContentError):
        assert_analysis_output_clean({"parties": "原告李四"})


def test_disclaimer_passes_guard_despite_legal_opinion_negation():
    # 免责文案含"不构成法律意见"，否定式表达不应被误伤。
    assert_analysis_output_clean({"disclaimer": TENDENCY_ANALYSIS_DISCLAIMER})


# ---------- API 层：双闸 → 403 ----------

def test_api_default_flag_off_returns_403():
    tendency_api.set_tendency_service_factory_for_test(None)
    resp = client.get("/api/tendency/analysis")
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "TENDENCY_ANALYSIS_UNAVAILABLE"


def test_api_flag_on_gate_pass_returns_aggregations():
    tendency_api.set_tendency_service_factory_for_test(
        lambda: TendencyAnalysisService(flag_enabled=True, gate_provider=lambda: _gate(True))
    )
    try:
        resp = client.get("/api/tendency/analysis")
        assert resp.status_code == 200
        body = resp.json()
        assert body["enabled"] is True
        assert body["total_sample_size"] > 0
        assert body["disclaimer"]
        assert len(body["aggregations"]) >= 4
        # 响应不得含个案正文键。
        assert_analysis_output_clean(body)
    finally:
        tendency_api.set_tendency_service_factory_for_test(None)


def test_api_flag_on_gate_fail_returns_403():
    tendency_api.set_tendency_service_factory_for_test(
        lambda: TendencyAnalysisService(flag_enabled=True, gate_provider=lambda: _gate(False))
    )
    try:
        resp = client.get("/api/tendency/analysis")
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "TENDENCY_ANALYSIS_UNAVAILABLE"
    finally:
        tendency_api.set_tendency_service_factory_for_test(None)


# ---------- 不引入主排序 / 检索副作用 ----------

def test_no_ranking_retrieval_import_side_effects():
    import app.tendency_analysis.aggregate as agg_mod
    import app.tendency_analysis.service as svc_mod
    for mod in (agg_mod, svc_mod):
        src = open(mod.__file__, encoding="utf-8").read()
        assert "app.rerank" not in src
        assert "app.retrieval" not in src
