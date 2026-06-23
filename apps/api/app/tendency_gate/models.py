"""M5-7 门禁数据模型（纯结构，无副作用、不读数据）。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class GateDimension:
    """一个门禁维度的定义（阈值口径，与实际值无关）。

    critical=True 表示该维度是 F19 上线的硬前置；任一 critical 维度 fail，
    F19 不得上线。critical=False 维度只记录为 warning，不单独阻断（但仍如实呈现）。
    """

    key: str
    name: str
    description: str
    threshold_desc: str
    critical: bool = True


@dataclass(frozen=True)
class GateDimensionResult:
    """某维度评估结果：阈值 / 实际值 / pass-fail。"""

    key: str
    name: str
    threshold: str
    actual: str
    gate_status: str  # "pass" | "fail"
    critical: bool
    data_source: str
    coverage_note: str
    metrics: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "dimension": self.key,
            "name": self.name,
            "threshold": self.threshold,
            "actual": self.actual,
            "gate_status": self.gate_status,
            "critical": self.critical,
            "data_source": self.data_source,
            "coverage_note": self.coverage_note,
            "metrics": dict(self.metrics),
        }


@dataclass(frozen=True)
class TendencyDataGateResult:
    """F19 数据质量门禁的总体结论。"""

    gate_version: str
    overall_status: str  # "pass" | "fail"
    f19_can_go_live: bool
    enable_tendency_analysis_recommended: bool  # 永远 False，除非全部 critical pass
    dimensions: list[GateDimensionResult]
    data_source: str
    coverage_note: str
    gap_summary: list[str] = field(default_factory=list)
    remediation: list[str] = field(default_factory=list)

    @property
    def failed_critical(self) -> list[GateDimensionResult]:
        return [d for d in self.dimensions if d.critical and d.gate_status == "fail"]

    @property
    def failed_noncritical(self) -> list[GateDimensionResult]:
        return [d for d in self.dimensions if not d.critical and d.gate_status == "fail"]

    def as_dict(self) -> dict[str, Any]:
        return {
            "gate_version": self.gate_version,
            "overall_status": self.overall_status,
            "f19_can_go_live": self.f19_can_go_live,
            "enable_tendency_analysis_recommended": self.enable_tendency_analysis_recommended,
            "data_source": self.data_source,
            "coverage_note": self.coverage_note,
            "dimensions": [d.as_dict() for d in self.dimensions],
            "failed_critical_dimensions": [d.key for d in self.failed_critical],
            "failed_noncritical_dimensions": [d.key for d in self.failed_noncritical],
            "gap_summary": list(self.gap_summary),
            "remediation": list(self.remediation),
        }
