"""M5-5 来源锚点校验：共享对象的 AI / 案例侧内容必须可追溯。

红线（无锚点不进入共享）：
- 来源锚点是 [{case_id, source_chunk_id, anchor_type?}] 的列表（结构化引用，非正文）。
- 有效锚点必须同时带非空 case_id 与非空 source_chunk_id。
- 报告 / 清单等含案例侧 AI 内容的对象共享时，必须至少有一条有效锚点，
  否则拒绝共享（防止把无法溯源的 AI 内容暴露给团队）。
- 收藏（单纯引用一个案例）若带锚点也必须合法；不带锚点的纯元数据引用允许，
  但只要 object_type 属于「AI 内容承载型」（report_template / case_list）就强制要求锚点。

本模块为纯逻辑，不 import 检索 / rerank / 持久层，不触碰主排序。
"""
from __future__ import annotations

from app.kernel.identity.team.models import (
    OBJECT_TYPE_LIST,
    OBJECT_TYPE_REPORT,
)

# 承载案例侧 AI 内容的对象类型：共享时强制要求来源锚点（无锚点即拒绝）。
AI_CONTENT_OBJECT_TYPES = frozenset({OBJECT_TYPE_LIST, OBJECT_TYPE_REPORT})

REASON_OK = "ok"
REASON_NO_ANCHOR = "missing_source_anchor"
REASON_INVALID_ANCHOR = "invalid_source_anchor"


def is_valid_anchor(anchor: object) -> bool:
    """单条锚点是否合法：必须是 dict 且 case_id / source_chunk_id 均非空。"""
    if not isinstance(anchor, dict):
        return False
    case_id = anchor.get("case_id")
    chunk_id = anchor.get("source_chunk_id")
    return bool(case_id) and isinstance(case_id, str) and bool(chunk_id) and isinstance(chunk_id, str)


def validate_anchors_for_share(*, object_type: str, anchors: list | None) -> tuple[bool, str]:
    """共享前的锚点校验。

    返回 (ok, reason_code)：
    - 承载 AI 内容的类型（report_template / case_list）：必须至少有一条合法锚点，
      且所有出现的锚点都必须合法，否则拒绝。
    - 其余类型（如 case_favorite）：可不带锚点；若带锚点则每条都必须合法。
    """
    items = anchors or []
    # 任何出现的锚点都必须合法（防止半截 / 脏锚点混入共享）。
    for anchor in items:
        if not is_valid_anchor(anchor):
            return False, REASON_INVALID_ANCHOR
    if object_type in AI_CONTENT_OBJECT_TYPES:
        # AI 内容承载型：无锚点不进入共享。
        if not items:
            return False, REASON_NO_ANCHOR
    return True, REASON_OK
