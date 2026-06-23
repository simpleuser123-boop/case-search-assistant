"""M5-8 F19 倾向分析输出隐私 / 边界护栏。

展示产物只允许出现"聚合统计 + case_id 引用 + 覆盖/样本/免责说明"，绝不允许：
- 个案正文 / 原始案情 / 裁判文书长文 / 当事人（正文型字段名或长文本片段）；
- 个案预测 / 胜负概率 / 确定性法律结论 / 具名法官预测话术。

本护栏对将要落盘 / 返回的分析结构做递归扫描，命中即抛错（fail-closed）。
复用 M5-7 门禁的禁止键 / 禁止话术口径，并补充倾向分析特有的禁用表达。
"""
from __future__ import annotations

import re
from typing import Any

from app.tendency_gate.privacy import FORBIDDEN_KEYS as _GATE_FORBIDDEN_KEYS
from app.tendency_gate.privacy import FORBIDDEN_PHRASES as _GATE_FORBIDDEN_PHRASES

# 正文型 / 凭据型 / 当事人型禁止键（在门禁基础上补当事人 / 全文列）。
FORBIDDEN_KEYS = set(_GATE_FORBIDDEN_KEYS) | {
    "parties",
    "party",
    "dangshiren",
    "quanwen",
    "content",
    "body",
    "text_body",
    "defendant",
    "plaintiff",
}

# 个案预测 / 胜负概率 / 确定性结论 话术（在门禁基础上补倾向分析常见误用表达）。
# 注意：免责文案会出现"不构成法律意见""不预测个案结果"等否定式表达，故这里只收录
# "肯定式断言"的措辞，避免误伤合规免责说明。
FORBIDDEN_PHRASES = list(_GATE_FORBIDDEN_PHRASES) + [
    "预测结果",
    "判决预测",
    "胜算",
    "败诉风险",
    "胜诉风险",
    "建议起诉",
    "建议上诉",
    "确定会判",
    "必然胜诉",
    "必然败诉",
    "一定胜诉",
    "一定败诉",
    "确定性法律结论",
    "出具法律意见",
    "提供法律意见",
]

# 具名法官 + 预测 的组合护栏（与门禁同口径）。
_NAMED_JUDGE_PREDICTION = re.compile(
    r"(审判长|审判员|法官)[^。；\n]{0,12}(会判|将判|倾向(?:于)?判|必判|预测)"
)


class ForbiddenAnalysisContentError(RuntimeError):
    """倾向分析输出命中正文 / 凭据 / 个案预测护栏。"""


def _scan_text(text: str, path: str) -> None:
    for phrase in FORBIDDEN_PHRASES:
        if phrase in text:
            raise ForbiddenAnalysisContentError(
                f"forbidden phrase '{phrase}' at {path}"
            )
    if _NAMED_JUDGE_PREDICTION.search(text):
        raise ForbiddenAnalysisContentError(
            f"named-judge prediction pattern at {path}"
        )


def assert_analysis_output_clean(payload: Any, *, path: str = "$") -> None:
    """递归校验分析产物干净；命中禁止键 / 禁止话术 / 具名法官预测即抛错。"""

    if isinstance(payload, dict):
        for key, value in payload.items():
            key_l = str(key).strip().lower()
            if key_l in FORBIDDEN_KEYS:
                raise ForbiddenAnalysisContentError(
                    f"forbidden key '{key}' at {path}"
                )
            assert_analysis_output_clean(value, path=f"{path}.{key}")
    elif isinstance(payload, (list, tuple)):
        for idx, item in enumerate(payload):
            assert_analysis_output_clean(item, path=f"{path}[{idx}]")
    elif isinstance(payload, str):
        _scan_text(payload, path)
    # int/float/bool/None：无正文风险，放行。
