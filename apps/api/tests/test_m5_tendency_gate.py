"""M5-7 F19 法院/法官倾向分析数据质量门禁 focused tests。

验证：
- 门禁维度阈值/实际值/pass-fail 清晰；
- 真实数据未达标 → F19 不上线、ENABLE_TENDENCY_ANALYSIS 保持 false；
- 仅当全部 critical 维度 pass 才建议开启（合成数据反证）；
- 门禁输出隐私护栏：无正文键、无具名法官预测、无胜负概率话术；
- 门禁不引入排序/检索副作用（不 import retrieval/rerank）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from app.core.config import Settings
from app.tendency_gate import (
    TENDENCY_GATE_VERSION,
    ForbiddenGateContentError,
    assert_gate_output_clean,
    build_tendency_gate,
    evaluate_tendency_gate,
)
from app.tendency_gate.gate import DIMENSIONS
from app.tendency_gate.metrics import (
    CASES_PATH,
    TENDENCY_CORPUS_PATH,
    compute_tendency_metrics,
)


# ---------- 合成 metrics 工具 ----------

def _passing_metrics() -> dict:
    """构造一份"全部 critical 维度达标"的合成统计，反证门禁会放行。"""
    return {
        "total_cases": 6000,
        "court_level_counts": {"基层": 3000, "中级": 1800, "高级": 900, "最高": 300},
        "trial_level_counts": {"一审": 3600, "二审": 1800, "再审": 600},
        "units_with_adequate_sample": 45,
        "min_sample_per_adequate_unit": 33,
        "per_unit_note": "synthetic",
        "judge_field_present": True,
        "judge_field_completeness": 0.92,
        "case_type_domains": ["criminal", "civil", "administrative"],
        "source_transparency": 1.0,
        "source_fields": {},
        "source_note": "synthetic",
        "region_missing_rate": 0.02,
        "distinct_case_cause": 60,
        "top_case_cause_share": 0.25,
        "date_anomaly_rate": 0.0,
        "year_min": 2010,
        "year_max": 2021,
        "date_note": "synthetic",
    }


# ---------- 门禁结构 ----------

def test_gate_version_stable():
    assert TENDENCY_GATE_VERSION == "m5-7-tendency-data-gate-v2"


def test_every_dimension_has_threshold_and_status():
    res = evaluate_tendency_gate(_passing_metrics())
    assert res.dimensions, "门禁必须至少有一个维度"
    for d in res.dimensions:
        assert d.threshold, f"{d.key} 缺阈值"
        assert d.actual, f"{d.key} 缺实际值"
        assert d.gate_status in {"pass", "fail"}
        assert d.data_source


def test_critical_dimensions_present():
    keys = {d.key for d in DIMENSIONS if d.critical}
    # F19 的硬前置：法院层级 / 法官字段 / 单元样本量 / 审级 / 领域 / 总样本 / 来源
    assert {
        "court_level_coverage",
        "per_unit_sample_adequacy",
        "trial_level_coverage",
        "case_type_coverage",
        "total_sample_size",
        "data_source_transparency",
    } <= keys
    # 法官维度已按路线B方案B移出门禁
    assert "judge_field_completeness" not in keys


# ---------- 合成达标 → 放行（反证门禁不是"永远 fail"）----------

def test_synthetic_full_pass_allows_go_live():
    res = evaluate_tendency_gate(_passing_metrics())
    assert res.overall_status == "pass"
    assert res.f19_can_go_live is True
    assert res.enable_tendency_analysis_recommended is True
    assert not res.failed_critical


# ---------- 任一 critical fail → 不上线 ----------

@pytest.mark.parametrize(
    "patch",
    [
        {"court_level_counts": {"基层": 6000}},
        {"units_with_adequate_sample": 3},
        {"trial_level_counts": {"一审": 6000}},
        {"case_type_domains": ["criminal"]},
        {"total_cases": 2505},
        {"source_transparency": 0.33},
    ],
)
def test_any_single_critical_fail_blocks_go_live(patch):
    m = _passing_metrics()
    m.update(patch)
    res = evaluate_tendency_gate(m)
    assert res.overall_status == "fail"
    assert res.f19_can_go_live is False
    assert res.enable_tendency_analysis_recommended is False
    assert res.failed_critical


def test_noncritical_fail_alone_does_not_block():
    m = _passing_metrics()
    m["region_missing_rate"] = 0.5  # 非关键维度 fail
    res = evaluate_tendency_gate(m)
    assert res.overall_status == "pass"
    assert res.f19_can_go_live is True
    assert any(d.key == "region_completeness" and d.gate_status == "fail"
               for d in res.dimensions)


# ---------- 真实数据：JuDGE 单一刑事语料仍 fail ----------

def test_judge_only_corpus_still_fails():
    """仅 JuDGE（刑事 2505 条）评估时门禁仍 fail：领域单一 + 样本不足。"""
    m = compute_tendency_metrics(CASES_PATH)
    res = evaluate_tendency_gate(m)
    assert res.overall_status == "fail"
    assert res.f19_can_go_live is False
    failed = {d.key for d in res.failed_critical}
    assert "case_type_coverage" in failed
    assert "total_sample_size" in failed
    assert "judge_field_completeness" not in failed  # 法官维度已移除
    assert res.gap_summary and res.remediation


def test_judge_only_metrics_criminal_only():
    m = compute_tendency_metrics(CASES_PATH)
    # 法官字段仍作信息项统计（缺失），但不再进入门禁判定
    assert m["judge_field_present"] is False
    assert m["case_type_domains"] == ["criminal"]


# ---------- 扩充语料（路线B）：达标则放行 ----------

@pytest.mark.skipif(not TENDENCY_CORPUS_PATH.is_file(),
                    reason="扩充语料未生成，跳过")
def test_expanded_corpus_passes_gate():
    """路线B 扩充语料（多领域 + 多层级 + 足量）应让 6 关键维度全 pass。"""
    res = build_tendency_gate()
    assert res.overall_status == "pass"
    assert res.f19_can_go_live is True
    assert not res.failed_critical
    # 关键维度逐项 pass
    by_key = {d.key: d.gate_status for d in res.dimensions}
    for k in ("court_level_coverage", "per_unit_sample_adequacy", "trial_level_coverage",
              "case_type_coverage", "total_sample_size", "data_source_transparency"):
        assert by_key[k] == "pass", f"{k} 应 pass，实际 {by_key[k]}"


@pytest.mark.skipif(not TENDENCY_CORPUS_PATH.is_file(),
                    reason="扩充语料未生成，跳过")
def test_expanded_corpus_multi_domain():
    m = compute_tendency_metrics(TENDENCY_CORPUS_PATH)
    # 至少含民事 + 刑事 + 行政
    assert {"civil", "criminal", "administrative"} <= set(m["case_type_domains"])
    assert m["total_cases"] >= 5000


# ---------- 隐私护栏 ----------

def test_gate_output_passes_privacy_guard():
    res = build_tendency_gate()
    assert_gate_output_clean(res.as_dict())  # 不得抛错


def test_privacy_guard_rejects_body_key():
    with pytest.raises(ForbiddenGateContentError):
        assert_gate_output_clean({"case_fact_body": "被告于2019年..."})


def test_privacy_guard_rejects_named_judge_prediction():
    with pytest.raises(ForbiddenGateContentError):
        assert_gate_output_clean({"note": "审判员张三会判有罪"})


def test_privacy_guard_rejects_win_rate_phrase():
    with pytest.raises(ForbiddenGateContentError):
        assert_gate_output_clean({"summary": "该法院胜诉率为 80%"})


def test_gate_does_not_emit_named_judges():
    # 门禁只统计字段存在性，不应在任何输出里携带具名法官姓名样本
    res = build_tendency_gate()
    blob = str(res.as_dict())
    for token in ("胜诉率", "败诉率", "胜诉概率", "查全率"):
        assert token not in blob


# ---------- flag 默认安全态 ----------

def test_flag_defaults_false():
    s = Settings(_env_file=None)
    assert s.ENABLE_TENDENCY_ANALYSIS is False


# ---------- 不引入排序/检索副作用 ----------

def test_gate_module_does_not_import_ranking():
    import app.tendency_gate.gate as g
    import app.tendency_gate.metrics as mt
    for mod in (g, mt):
        src = Path(mod.__file__).read_text(encoding="utf-8")
        assert "import app.rerank" not in src
        assert "import app.retrieval" not in src
        assert "from app.rerank" not in src
        assert "from app.retrieval" not in src
