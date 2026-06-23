"""M5-8 F19 倾向分析服务：门禁 + flag 联动 + 只读聚合组装。

展示前置（两道闸，缺一即不展示）：
1. M5-7 数据门禁 f19_can_go_live=True；
2. ENABLE_TENDENCY_ANALYSIS=True。
任一不满足 → raise TendencyUnavailable，API 层回 403，回到 M5-7 末态。

组装时强制：标注样本量 / 覆盖范围 / 数据来源 / 不确定性说明 + 免责；
低于样本门槛的分组标 sample_sufficient=False；case_cause 维度只展示达标分组并截断。
绝不输出个案正文 / 个案预测 / 胜负概率 / 确定性结论。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from app.tendency_analysis.aggregate import (
    MIN_SAMPLE_PER_BUCKET,
    aggregate_tendency,
    resolve_corpus_path,
)
from app.tendency_analysis.models import (
    TendencyAggregation,
    TendencyAnalysisResult,
    TendencyBucket,
)
from app.tendency_analysis.privacy import assert_analysis_output_clean
from app.tendency_gate import build_tendency_gate
from app.tendency_gate.models import TendencyDataGateResult

TENDENCY_ANALYSIS_VERSION = "m5-8-tendency-analysis-v1"

TENDENCY_ANALYSIS_DISCLAIMER = (
    "本分析为基于现有数据覆盖的聚合统计参考，可能未覆盖全部案例，"
    "存在抽样与时间范围偏差；不构成法律意见，不预测个案结果，不代表任何具名法官/法院的裁判倾向，"
    "样本不足的维度不作解读，所有结论需结合个案与人工复核独立判断。"
)

# 维度展示名。
_DIM_NAMES = {
    "court_level": "法院层级分布",
    "trial_level": "审级分布",
    "case_domain": "案件领域分布",
    "case_cause": "案由分布（仅样本充足案由）",
}

# case_cause 维度分组极多（数百），仅保留达标分组并截断到前 N 个，避免长尾噪声。
_MAX_CASE_CAUSE_BUCKETS = 20


class TendencyUnavailable(RuntimeError):
    """F19 不可展示（门禁未达标或 flag 关闭）。携带原因码供 API 层映射。"""

    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code
        self.message = message


def _coverage_range(gate: TendencyDataGateResult, total: int) -> str:
    return (
        f"覆盖范围：{gate.data_source}；纳入聚合样本 {total} 条；"
        f"维度=法院层级/审级/案件领域/案由；数据门禁版本 {gate.gate_version}。"
        " 本分析仅为统计透明展示，不改变检索主排序，不输出个案正文与预测。"
    )


def _confidence_note(dim: str, sample_size: int) -> str:
    if sample_size < MIN_SAMPLE_PER_BUCKET:
        return (
            f"该维度纳入样本 {sample_size} 条，低于最小可解释门槛 "
            f"{MIN_SAMPLE_PER_BUCKET}，仅作存在性提示，不解读占比。"
        )
    return (
        f"该维度纳入样本 {sample_size} 条；占比为历史数据的统计分布，"
        "不代表未来个案结果，样本不足的分组已单独标注。"
    )


def _to_buckets(raw_buckets: list[dict[str, Any]]) -> list[TendencyBucket]:
    return [
        TendencyBucket(
            label=str(b["label"]),
            sample_size=int(b["sample_size"]),
            share=float(b["share"]),
            sample_sufficient=bool(b["sample_sufficient"]),
            case_id_refs=list(b.get("case_id_refs", [])),
            case_id_total=int(b.get("case_id_total", 0)),
        )
        for b in raw_buckets
    ]


class TendencyAnalysisService:
    """组装 F19 展示结果。门禁判定与 flag 读取均可注入，便于测试。"""

    def __init__(
        self,
        *,
        flag_enabled: bool,
        gate_provider: Callable[[], TendencyDataGateResult] | None = None,
        corpus_path: Path | None = None,
    ) -> None:
        self._flag_enabled = flag_enabled
        self._gate_provider = gate_provider or build_tendency_gate
        self._corpus_path = corpus_path

    def is_available(self) -> tuple[bool, str]:
        """返回 (是否可展示, 原因码)。两道闸都过才可展示。"""
        if not self._flag_enabled:
            return False, "ENABLE_TENDENCY_ANALYSIS_false"
        gate = self._gate_provider()
        if not gate.f19_can_go_live:
            return False, "data_gate_not_passed"
        return True, "ok"

    def build(self) -> TendencyAnalysisResult:
        ok, reason = self.is_available()
        if not ok:
            if reason == "ENABLE_TENDENCY_ANALYSIS_false":
                raise TendencyUnavailable(
                    reason, "倾向分析未启用（ENABLE_TENDENCY_ANALYSIS=false）。"
                )
            raise TendencyUnavailable(
                reason, "倾向分析数据门禁未达标，按边界要求不展示分析。"
            )

        gate = self._gate_provider()
        agg = aggregate_tendency(self._corpus_path)
        total = int(agg.get("total_cases", 0))
        coverage = _coverage_range(gate, total)

        aggregations: list[TendencyAggregation] = []
        for dim, name in _DIM_NAMES.items():
            block = agg["dimensions"].get(dim, {"total": 0, "buckets": []})
            dim_total = int(block.get("total", 0))
            buckets = _to_buckets(block.get("buckets", []))
            if dim == "case_cause":
                buckets = [b for b in buckets if b.sample_sufficient][:_MAX_CASE_CAUSE_BUCKETS]
            aggregations.append(
                TendencyAggregation(
                    dimension=dim,
                    name=name,
                    sample_size=dim_total,
                    coverage_range=coverage,
                    data_source=gate.data_source,
                    confidence_note=_confidence_note(dim, dim_total),
                    buckets=buckets,
                    insufficient_dimension=dim_total < MIN_SAMPLE_PER_BUCKET,
                )
            )

        result = TendencyAnalysisResult(
            version=TENDENCY_ANALYSIS_VERSION,
            enabled=True,
            gate_passed=True,
            data_source=gate.data_source,
            coverage_range=coverage,
            total_sample_size=total,
            min_sample_threshold=MIN_SAMPLE_PER_BUCKET,
            disclaimer=TENDENCY_ANALYSIS_DISCLAIMER,
            aggregations=aggregations,
        )
        # fail-closed：落盘/返回前强制隐私 + 边界扫描。
        assert_analysis_output_clean(result.as_dict())
        return result


def build_tendency_analysis(
    *,
    flag_enabled: bool,
    gate_provider: Callable[[], TendencyDataGateResult] | None = None,
    corpus_path: Path | None = None,
) -> TendencyAnalysisResult:
    return TendencyAnalysisService(
        flag_enabled=flag_enabled,
        gate_provider=gate_provider,
        corpus_path=corpus_path,
    ).build()
