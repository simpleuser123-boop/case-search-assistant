"""M5-8 法院/法官倾向分析（F19）展示层。

红线（承接 M5-1~M5-7，并按 F19 P3 复活条件加强）：
- **门禁前置**：仅当 M5-7 数据门禁 f19_can_go_live=True 且 ENABLE_TENDENCY_ANALYSIS=True
  时才输出分析；任一不满足 → 不展示，回到 M5-7 末态。
- **只读聚合**：只对既有只读元数据语料做聚合统计，绝不修改数据 / 向量库 /
  评测集，绝不改变主排序 / 召回 / source selection / rerank 默认行为。
- **不输出个案正文**：只产出聚合统计与可追溯的 case_id 引用，绝不回显案情 /
  裁判文书正文 / 当事人。
- **不预测个案 / 不输出胜负概率 / 不输出确定性法律结论**：聚合维度只有
  法院层级 / 审级 / 案件领域 / 案由的分布计数与占比；不针对具名法官输出预测。
- **强制样本量与覆盖范围标注**：每个聚合单元都带 sample_size；低于门槛的单元
  标注"样本不足"且不输出占比解读。强制免责说明。
"""

from app.tendency_analysis.models import (
    TendencyAggregation,
    TendencyAnalysisResult,
    TendencyBucket,
)
from app.tendency_analysis.privacy import (
    ForbiddenAnalysisContentError,
    assert_analysis_output_clean,
)
from app.tendency_analysis.service import (
    TENDENCY_ANALYSIS_DISCLAIMER,
    TENDENCY_ANALYSIS_VERSION,
    TendencyAnalysisService,
    TendencyUnavailable,
    build_tendency_analysis,
)

__all__ = [
    "TendencyAggregation",
    "TendencyAnalysisResult",
    "TendencyBucket",
    "ForbiddenAnalysisContentError",
    "assert_analysis_output_clean",
    "TENDENCY_ANALYSIS_DISCLAIMER",
    "TENDENCY_ANALYSIS_VERSION",
    "TendencyAnalysisService",
    "TendencyUnavailable",
    "build_tendency_analysis",
]
