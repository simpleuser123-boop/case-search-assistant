"""M5-8 F19 倾向分析展示数据模型（纯结构，无副作用、不读数据）。

每个聚合单元（bucket）只承载：分组键 + 样本量 + 占比 + 可追溯 case_id 引用，
绝不承载个案正文 / 预测 / 胜负概率。低于样本门槛的 bucket 标 sample_sufficient=False，
展示侧据此标注"样本不足"且不解读占比。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class TendencyBucket:
    """某个聚合维度下的一个分组单元（如"基层法院"或"一审"）。

    label: 分组键（如"基层"/"一审"/"civil"/某案由名）——仅结构化标签，无个案信息。
    sample_size: 落入该分组的案例数（聚合计数）。
    share: 占该维度总样本的比例（0~1）；sample_sufficient=False 时展示侧不解读。
    sample_sufficient: 样本量是否达到最小可解释门槛。
    case_id_refs: 可追溯到参与统计的 case_id 引用（截断到上限，仅引用非正文）。
    case_id_total: 该分组实际 case_id 总数（refs 可能被截断，此处给全量计数）。
    """

    label: str
    sample_size: int
    share: float
    sample_sufficient: bool
    case_id_refs: list[str] = field(default_factory=list)
    case_id_total: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "sample_size": self.sample_size,
            "share": round(self.share, 4),
            "sample_sufficient": self.sample_sufficient,
            "case_id_refs": list(self.case_id_refs),
            "case_id_total": self.case_id_total,
        }


@dataclass(frozen=True)
class TendencyAggregation:
    """一个聚合维度（如"法院层级分布"）的统计结果。

    dimension: 机器键（court_level / trial_level / case_domain / case_cause）。
    name: 中文展示名。
    sample_size: 该维度纳入统计的总样本量。
    coverage_range: 覆盖范围说明（数据来源 + 范围 + 时间跨度）。
    data_source: 数据来源声明。
    confidence_note: 不确定性 / 样本基础说明（非预测、非结论）。
    buckets: 各分组单元（已按样本量降序）。
    insufficient_dimension: 整个维度样本量是否不足（不足则展示侧整体标注）。
    """

    dimension: str
    name: str
    sample_size: int
    coverage_range: str
    data_source: str
    confidence_note: str
    buckets: list[TendencyBucket] = field(default_factory=list)
    insufficient_dimension: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "dimension": self.dimension,
            "name": self.name,
            "sample_size": self.sample_size,
            "coverage_range": self.coverage_range,
            "data_source": self.data_source,
            "confidence_note": self.confidence_note,
            "insufficient_dimension": self.insufficient_dimension,
            "buckets": [b.as_dict() for b in self.buckets],
        }


@dataclass(frozen=True)
class TendencyAnalysisResult:
    """F19 倾向分析展示总结果。

    enabled: 是否实际展示（门禁 + flag 同时满足才 True）。
    gate_passed: M5-7 数据门禁是否 pass。
    data_source / coverage_range: 顶层来源与覆盖范围声明。
    total_sample_size: 纳入分析的总样本量。
    min_sample_threshold: 单分组最小可解释样本门槛。
    disclaimer: 强制免责说明。
    aggregations: 各聚合维度。
    """

    version: str
    enabled: bool
    gate_passed: bool
    data_source: str
    coverage_range: str
    total_sample_size: int
    min_sample_threshold: int
    disclaimer: str
    aggregations: list[TendencyAggregation] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "enabled": self.enabled,
            "gate_passed": self.gate_passed,
            "data_source": self.data_source,
            "coverage_range": self.coverage_range,
            "total_sample_size": self.total_sample_size,
            "min_sample_threshold": self.min_sample_threshold,
            "disclaimer": self.disclaimer,
            "aggregations": [a.as_dict() for a in self.aggregations],
        }
