"""M5-7 门禁输出隐私护栏。

门禁产物只允许出现"统计口径 + pass/fail 结论"，绝不允许出现：
- 个案正文 / 原始案情 / 裁判文书长文（正文型字段名或长文本片段）；
- 针对具名法官 / 法院的"结果预测 / 胜负概率 / 确定性法律结论"话术。

本护栏对将要落盘 / 返回的门禁结构做递归扫描，命中即抛错（fail-closed）。
"""
from __future__ import annotations

import re
from typing import Any

# 正文型 / 凭据型禁止键（沿用 M4-1/M5-1 白名单口径的否定面）。
FORBIDDEN_KEYS = {
    "raw_query",
    "case_fact_body",
    "candidate_body",
    "chunk_body",
    "judgment_long_text",
    "full_document",
    "fact",
    "reasoning",
    "judgment",
    "password",
    "token",
    "api_key",
    "secret",
}

# 个案预测 / 胜负概率 / 确定性结论 话术（倾向分析绝不输出）。
FORBIDDEN_PHRASES = [
    "胜诉率",
    "败诉率",
    "胜诉概率",
    "败诉概率",
    "胜诉可能",
    "败诉可能",
    "必然判",
    "一定会判",
    "保证胜诉",
    "保证无遗漏",
    "已查全",
    "查全率",
    "预测该法官",
    "预测法官",
    "该法官将",
    "该法官会判",
    "法官倾向于判",
    "判决结果预测",
    "个案结果预测",
]

# 具名法官 + 预测 的组合护栏：审判员/法官 紧跟 会判/将判/倾向 等。
_NAMED_JUDGE_PREDICTION = re.compile(
    r"(审判长|审判员|法官)[^。；\n]{0,12}(会判|将判|倾向(?:于)?判|必判|预测)"
)


class ForbiddenGateContentError(RuntimeError):
    """门禁输出命中正文 / 凭据 / 个案预测护栏。"""


def _scan_text(text: str, path: str) -> None:
    for phrase in FORBIDDEN_PHRASES:
        if phrase in text:
            raise ForbiddenGateContentError(
                f"forbidden phrase '{phrase}' at {path}"
            )
    if _NAMED_JUDGE_PREDICTION.search(text):
        raise ForbiddenGateContentError(
            f"named-judge prediction pattern at {path}"
        )


def assert_gate_output_clean(payload: Any, *, path: str = "$") -> None:
    """递归校验门禁产物干净；命中禁止键 / 禁止话术 / 具名法官预测即抛错。"""

    if isinstance(payload, dict):
        for key, value in payload.items():
            key_l = str(key).strip().lower()
            if key_l in FORBIDDEN_KEYS:
                raise ForbiddenGateContentError(
                    f"forbidden key '{key}' at {path}"
                )
            assert_gate_output_clean(value, path=f"{path}.{key}")
    elif isinstance(payload, (list, tuple)):
        for idx, item in enumerate(payload):
            assert_gate_output_clean(item, path=f"{path}[{idx}]")
    elif isinstance(payload, str):
        _scan_text(payload, path)
    # int/float/bool/None：无正文风险，放行。
