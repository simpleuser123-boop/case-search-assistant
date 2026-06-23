"""M5-7 法院/法官倾向分析（F19）数据质量门禁包。

红线（与 M5-1~M5-6 一脉相承，并按 F19 P3 复活条件加强）：
- **只读评估**：本包只统计既有 data/processed 元数据的口径，绝不修改裁判文书数据、
  向量库或评测集，绝不改变主排序 / source selection / rerank 默认行为。
- **不输出个案正文**：门禁只产出统计口径与 pass/fail 结论；绝不回显原始案情、
  裁判文书正文或候选正文。
- **不预测具名法官**：门禁不输出针对具名法官/法院的结果预测或胜负概率；
  连"法官字段是否存在"也只统计存在性与完整度，不落具体姓名。
- **未达标即不上线**：任一关键维度（critical=True）fail → F19 不上线，
  ENABLE_TENDENCY_ANALYSIS 必须保持 false，f19_can_go_live=False。
- gate 判定逻辑（gate.py）是纯函数，不读数据；数据读取集中在 metrics.py（只读）。

M5-7 只做门禁，不做 M5-8 的分析展示。
"""

from app.tendency_gate.gate import (
    TENDENCY_GATE_VERSION,
    build_tendency_gate,
    evaluate_tendency_gate,
)
from app.tendency_gate.models import (
    GateDimension,
    GateDimensionResult,
    TendencyDataGateResult,
)
from app.tendency_gate.privacy import (
    ForbiddenGateContentError,
    assert_gate_output_clean,
)

__all__ = [
    "TENDENCY_GATE_VERSION",
    "build_tendency_gate",
    "evaluate_tendency_gate",
    "GateDimension",
    "GateDimensionResult",
    "TendencyDataGateResult",
    "ForbiddenGateContentError",
    "assert_gate_output_clean",
]
