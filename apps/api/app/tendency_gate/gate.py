"""M5-7 F19 数据质量门禁：纯判定逻辑（不读数据、不改排序）。

输入 = metrics.py 算出的只读统计字典；输出 = TendencyDataGateResult。
判定规则：任一 critical 维度 fail → overall=fail、f19_can_go_live=False、
不建议开启 ENABLE_TENDENCY_ANALYSIS（保持 false）。
"""
from __future__ import annotations

from typing import Any

from app.tendency_gate.models import (
    GateDimension,
    GateDimensionResult,
    TendencyDataGateResult,
)

TENDENCY_GATE_VERSION = "m5-7-tendency-data-gate-v2"

# --- 阈值常量（统计可解释性最小门槛，偏保守）---
# 倾向分析需要"跨层级 / 跨法官 / 足量样本"才有统计意义，故门槛高于检索数据门禁。
MIN_COURT_LEVELS = 3                 # 至少覆盖 基层/中级/高级 三个层级
MIN_SECONDARY_LEVEL_SHARE = 0.10     # 非主导层级合计占比 ≥10%（否则无法跨层级比较）
# 法官维度已按 M5-7 路线B方案B移出门禁（与隐私红线"不存具名法官/不预测具名法官"
# 直接对撞）：F19 收窄为法院层级/案由/审级的统计透明，不做法官维度。
MIN_TRIAL_LEVELS = 2                 # 至少覆盖两种审级
MIN_SECONDARY_TRIAL_SHARE = 0.10     # 非主导审级合计占比 ≥10%
MIN_CASE_TYPE_DOMAINS = 2            # 至少覆盖刑事 + 民事/行政之一（避免单一领域外推）
MIN_TOTAL_SAMPLE = 5000              # 总样本量 ≥5000 才谈统计意义上的倾向
MIN_UNITS_WITH_ADEQUATE_SAMPLE = 30  # 至少 30 个统计单元（法院/法官）样本量达标
MIN_SAMPLE_PER_UNIT = 30             # 单个统计单元样本量 ≥30
MAX_REGION_MISSING = 0.05            # 地域缺失率 ≤5%
MAX_DATE_ANOMALY = 0.01              # 裁判日期异常（未来/越界）率 ≤1%
MIN_SOURCE_TRANSPARENCY = 0.95       # 来源/更新时间/原文链接可声明度≥95%（与§8"缺失率≤5%"一致）


def _pct(x: float) -> str:
    return f"{round(100 * x, 2)}%"


# --- 维度定义（顺序即报告呈现顺序）---
DIMENSIONS: list[GateDimension] = [
    GateDimension(
        "court_level_coverage", "法院层级覆盖",
        "倾向分析需跨法院层级比较，单一层级无法支撑层级倾向结论。",
        f"覆盖 ≥{MIN_COURT_LEVELS} 个层级且非主导层级合计占比 ≥{_pct(MIN_SECONDARY_LEVEL_SHARE)}",
        critical=True,
    ),
    GateDimension(
        "per_unit_sample_adequacy", "统计单元样本量",
        "对单个法院/法官给出倾向，需该单元样本量达到最小可解释门槛。",
        f"≥{MIN_UNITS_WITH_ADEQUATE_SAMPLE} 个单元、每个 ≥{MIN_SAMPLE_PER_UNIT} 样本",
        critical=True,
    ),
    GateDimension(
        "trial_level_coverage", "审级覆盖",
        "审级倾向需要一审/二审/再审等多审级并存才能比较。",
        f"覆盖 ≥{MIN_TRIAL_LEVELS} 种审级且非主导审级占比 ≥{_pct(MIN_SECONDARY_TRIAL_SHARE)}",
        critical=True,
    ),
    GateDimension(
        "case_type_coverage", "案件领域覆盖",
        "单一领域（仅刑事）语料不能外推到民事/行政倾向，会误导用户。",
        f"覆盖 ≥{MIN_CASE_TYPE_DOMAINS} 个案件领域",
        critical=True,
    ),
    GateDimension(
        "total_sample_size", "总样本量",
        "倾向分析的统计稳定性要求足够大的总样本。",
        f"总案例数 ≥{MIN_TOTAL_SAMPLE}",
        critical=True,
    ),
    GateDimension(
        "data_source_transparency", "数据来源可声明",
        "倾向分析必须可追溯：数据来源、更新时间、原文链接需可声明。",
        f"来源/更新时间/原文链接可声明度 ≥{_pct(MIN_SOURCE_TRANSPARENCY)}",
        critical=True,
    ),
    GateDimension(
        "case_cause_distribution", "案由分布", 
        "案由分布过窄会使倾向结论被高频案由主导。",
        "案由数 ≥20 且最高频案由占比 ≤40%",
        critical=False,
    ),
    GateDimension(
        "region_completeness", "地域完整度",
        "地域是倾向分析常用切片，缺失过高会使地域切片失真。",
        f"地域缺失率 ≤{_pct(MAX_REGION_MISSING)}",
        critical=False,
    ),
    GateDimension(
        "temporal_consistency", "时间跨度一致性",
        "裁判日期异常（未来/越界）会污染时间趋势倾向。",
        f"日期异常率 ≤{_pct(MAX_DATE_ANOMALY)}",
        critical=False,
    ),
]

_DATA_SOURCE = "data/processed/cases.jsonl（JuDGE 刑事判决，只读元数据统计）"


def _mk(key: str, threshold: str, actual: str, status: str, note: str,
        metrics: dict[str, Any]) -> GateDimensionResult:
    dim = next(d for d in DIMENSIONS if d.key == key)
    return GateDimensionResult(
        key=key, name=dim.name, threshold=threshold, actual=actual,
        gate_status=status, critical=dim.critical,
        data_source=_DATA_SOURCE, coverage_note=note, metrics=metrics,
    )


def evaluate_tendency_gate(metrics: dict[str, Any]) -> TendencyDataGateResult:
    """根据只读统计字典判定门禁。metrics 由 metrics.py 计算或测试注入。"""

    results: list[GateDimensionResult] = []

    # 1. 法院层级覆盖
    levels = metrics.get("court_level_counts", {})
    total = max(int(metrics.get("total_cases", 0)), 1)
    _EMPTY = {"(empty)", "(空)", ""}
    n_levels = sum(1 for k, v in levels.items() if v > 0 and k not in _EMPTY)
    dominant = max(levels.values()) if levels else 0
    secondary_share = (total - dominant) / total if total else 0.0
    ok = n_levels >= MIN_COURT_LEVELS and secondary_share >= MIN_SECONDARY_LEVEL_SHARE
    results.append(_mk(
        "court_level_coverage",
        f"≥{MIN_COURT_LEVELS} 层级 & 非主导占比 ≥{_pct(MIN_SECONDARY_LEVEL_SHARE)}",
        f"{n_levels} 层级，非主导层级占比 {_pct(secondary_share)}",
        "pass" if ok else "fail",
        "层级分布：" + ", ".join(f"{k}={v}" for k, v in levels.items()),
        {"n_levels": n_levels, "secondary_share": round(secondary_share, 4),
         "level_counts": levels},
    ))

    # 3. 统计单元样本量
    units = int(metrics.get("units_with_adequate_sample", 0))
    per_min = int(metrics.get("min_sample_per_adequate_unit", 0))
    ok = units >= MIN_UNITS_WITH_ADEQUATE_SAMPLE
    results.append(_mk(
        "per_unit_sample_adequacy",
        f"≥{MIN_UNITS_WITH_ADEQUATE_SAMPLE} 单元且每单元 ≥{MIN_SAMPLE_PER_UNIT}",
        f"{units} 个达标单元（阈值/单元={MIN_SAMPLE_PER_UNIT}）",
        "pass" if ok else "fail",
        metrics.get("per_unit_note", ""),
        {"units_with_adequate_sample": units,
         "min_sample_per_unit_threshold": MIN_SAMPLE_PER_UNIT,
         "observed_min_per_adequate_unit": per_min},
    ))

    # 4. 审级覆盖
    trials = metrics.get("trial_level_counts", {})
    t_total = sum(trials.values()) or 1
    n_trials = sum(1 for k, v in trials.items() if v > 0 and k not in _EMPTY)
    t_dom = max(trials.values()) if trials else 0
    t_secondary = (t_total - t_dom) / t_total
    ok = n_trials >= MIN_TRIAL_LEVELS and t_secondary >= MIN_SECONDARY_TRIAL_SHARE
    results.append(_mk(
        "trial_level_coverage",
        f"≥{MIN_TRIAL_LEVELS} 审级 & 非主导占比 ≥{_pct(MIN_SECONDARY_TRIAL_SHARE)}",
        f"{n_trials} 审级，非主导占比 {_pct(t_secondary)}",
        "pass" if ok else "fail",
        "审级分布：" + ", ".join(f"{k}={v}" for k, v in trials.items()),
        {"n_trials": n_trials, "secondary_share": round(t_secondary, 4),
         "trial_counts": trials},
    ))

    # 5. 案件领域覆盖
    domains = metrics.get("case_type_domains", [])
    n_dom = len(domains)
    ok = n_dom >= MIN_CASE_TYPE_DOMAINS
    results.append(_mk(
        "case_type_coverage",
        f"≥{MIN_CASE_TYPE_DOMAINS} 领域",
        f"{n_dom} 领域：{', '.join(domains) if domains else '无'}",
        "pass" if ok else "fail",
        "JuDGE 为刑事专用语料，缺民事/行政" if n_dom < 2 else "",
        {"domains": list(domains)},
    ))

    # 6. 总样本量
    ok = total >= MIN_TOTAL_SAMPLE
    results.append(_mk(
        "total_sample_size",
        f"≥{MIN_TOTAL_SAMPLE}",
        str(total),
        "pass" if ok else "fail",
        f"当前总案例 {total}，门槛 {MIN_TOTAL_SAMPLE}",
        {"total_cases": total},
    ))

    # 7. 数据来源可声明
    tr = float(metrics.get("source_transparency", 0.0))
    ok = tr >= MIN_SOURCE_TRANSPARENCY
    results.append(_mk(
        "data_source_transparency",
        f"≥{_pct(MIN_SOURCE_TRANSPARENCY)}",
        _pct(tr),
        "pass" if ok else "fail",
        metrics.get("source_note", ""),
        {"source_transparency": round(tr, 4),
         "source_fields": metrics.get("source_fields", {})},
    ))

    # 8. 案由分布（非关键）
    n_cause = int(metrics.get("distinct_case_cause", 0))
    top_cause = float(metrics.get("top_case_cause_share", 1.0))
    ok = n_cause >= 20 and top_cause <= 0.40
    results.append(_mk(
        "case_cause_distribution",
        "案由数 ≥20 且最高频 ≤40%",
        f"案由数 {n_cause}，最高频占比 {_pct(top_cause)}",
        "pass" if ok else "fail", "",
        {"distinct_case_cause": n_cause, "top_share": round(top_cause, 4)},
    ))

    # 9. 地域完整度（非关键）
    rm = float(metrics.get("region_missing_rate", 1.0))
    ok = rm <= MAX_REGION_MISSING
    results.append(_mk(
        "region_completeness",
        f"缺失率 ≤{_pct(MAX_REGION_MISSING)}",
        _pct(rm),
        "pass" if ok else "fail", "",
        {"region_missing_rate": round(rm, 4)},
    ))

    # 10. 时间跨度一致性（非关键）
    da = float(metrics.get("date_anomaly_rate", 1.0))
    ok = da <= MAX_DATE_ANOMALY
    results.append(_mk(
        "temporal_consistency",
        f"日期异常率 ≤{_pct(MAX_DATE_ANOMALY)}",
        _pct(da),
        "pass" if ok else "fail",
        metrics.get("date_note", ""),
        {"date_anomaly_rate": round(da, 4),
         "year_min": metrics.get("year_min"), "year_max": metrics.get("year_max")},
    ))

    failed_critical = [d for d in results if d.critical and d.gate_status == "fail"]
    overall = "fail" if failed_critical else "pass"
    can_go_live = not failed_critical
    coverage_note = (
        f"数据来源：{_DATA_SOURCE}；总样本 {total}；"
        f"覆盖范围 = 刑事判决（JuDGE）；更新时间字段缺失。"
        " 倾向分析门禁为只读评估，不改变主排序、不输出个案正文、不预测具名法官结果。"
    )

    gap_summary = _build_gaps(results)
    remediation = _build_remediation(results)

    return TendencyDataGateResult(
        gate_version=TENDENCY_GATE_VERSION,
        overall_status=overall,
        f19_can_go_live=can_go_live,
        enable_tendency_analysis_recommended=can_go_live,
        dimensions=results,
        data_source=_DATA_SOURCE,
        coverage_note=coverage_note,
        gap_summary=gap_summary,
        remediation=remediation,
    )


def _build_gaps(results: list[GateDimensionResult]) -> list[str]:
    gaps: list[str] = []
    for d in results:
        if d.gate_status == "fail":
            tag = "关键" if d.critical else "提示"
            gaps.append(f"[{tag}] {d.name}：实际 {d.actual}（门槛 {d.threshold}）")
    return gaps


_REMEDIATION_MAP = {
    "court_level_coverage": "补充中级/高级/最高法院判决，使非基层层级占比达可比较规模。",
    "per_unit_sample_adequacy": "扩充语料使单个法院/法官样本量 ≥30，且达标单元 ≥30 个。",
    "trial_level_coverage": "补充二审/再审判决，使审级可比较。",
    "case_type_coverage": "引入民事/行政判决语料，避免仅凭刑事数据外推。",
    "total_sample_size": "将总样本扩充到 ≥5000，提升统计稳定性。",
    "data_source_transparency": "补齐 source_url / source_updated_at 等可追溯字段。",
    "case_cause_distribution": "扩充案由覆盖，降低高频案由主导度。",
    "region_completeness": "完善地域抽取规则，降低地域缺失率。",
    "temporal_consistency": "修复裁判日期解析异常（未来/越界日期）。",
}


def _build_remediation(results: list[GateDimensionResult]) -> list[str]:
    out: list[str] = []
    for d in results:
        if d.gate_status == "fail" and d.key in _REMEDIATION_MAP:
            out.append(f"{d.name}：{_REMEDIATION_MAP[d.key]}")
    return out


def build_tendency_gate() -> TendencyDataGateResult:
    """读取真实数据（只读）并判定门禁。延迟 import 避免无数据环境下副作用。"""

    from app.tendency_gate.metrics import (
        TENDENCY_CORPUS_PATH,
        compute_tendency_metrics,
    )

    # F19 倾向分析针对的是专建的只读元数据语料；存在则评估它，否则回落到 JuDGE。
    corpus = TENDENCY_CORPUS_PATH if TENDENCY_CORPUS_PATH.is_file() else None
    return evaluate_tendency_gate(compute_tendency_metrics(corpus))
