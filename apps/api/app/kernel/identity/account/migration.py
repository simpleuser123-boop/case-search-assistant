"""M5-2 单用户态迁移：把匿名 localStorage 沉淀「认领」到具名 user_id。

设计要点（M5-1 合同 / anchor_inheritance + whitelist）：
- 仅元数据 / 引用 / 来源锚点 / 用户自填短字段进入认领请求；绝不引入正文。
- 锚点继承：带 case_id + source_chunk_id 的引用才被接收，缺锚点项降级丢弃，
  不伪造 source_chunk_id。
- 迁移**默认不自动执行**：必须由用户显式触发（API 层要求 confirm=true）。
- 本模块只做「校验 + 计数 + 归属标记（owner_user_id）」的纯函数骨架，
  M5-2 不真正把对象写进任何团队/服务端业务表（那是 M5-3 之后）。

本模块只产出脱敏的计数 / reason_code，不回显任何被认领项的明文内容。
"""
from __future__ import annotations

from dataclasses import dataclass, field

# 认领项只允许这些白名单键（元数据 / 引用 / 锚点 / 用户自填短字段）。
CLAIM_ITEM_ALLOWED_KEYS = frozenset(
    {
        "object_type",  # case_favorite / case_list / report_template（短枚举）
        "object_ref_id",  # 前端本地对象引用 id
        "case_id",
        "case_number",
        "court",
        "trial_level",
        "case_cause",
        "judgment_date",
        "source_anchors",  # [{case_id, source_chunk_id, anchor_type?}]
        "note",  # 用户自填短字段
        "tag",
        "list_title",
    }
)

# 任何疑似正文 / 凭据键出现即拒绝整项（防御 localStorage 被人为塞入正文）。
CLAIM_ITEM_FORBIDDEN_KEYS = frozenset(
    {
        "raw_query",
        "query",
        "case_fact_body",
        "candidate_body",
        "chunk_body",
        "judgment_long_text",
        "summary_body",
        "holding_body",
        "compare_body",
        "user_free_long_text",
        "text",
        "content",
        "password",
        "token",
        "session_token",
    }
)

CLAIM_REASON_OK = "claimed"
CLAIM_REASON_MISSING_ANCHOR = "missing_anchor"
CLAIM_REASON_FORBIDDEN_KEY = "forbidden_key"
CLAIM_REASON_UNKNOWN_KEY = "unknown_key"


@dataclass
class ClaimOutcome:
    """认领结果：只含脱敏计数与 reason_code 分布，无任何正文。"""

    owner_user_id: str
    requested_count: int = 0
    claimed_count: int = 0
    degraded_count: int = 0
    rejected_count: int = 0
    reason_codes: dict[str, int] = field(default_factory=dict)

    def _bump(self, code: str) -> None:
        self.reason_codes[code] = self.reason_codes.get(code, 0) + 1


def _has_valid_anchor(item: dict) -> bool:
    anchors = item.get("source_anchors")
    if not isinstance(anchors, list) or not anchors:
        # case 级引用也算有锚点：必须带 case_id + 至少一个 source_chunk_id 通道。
        return False
    for anchor in anchors:
        if not isinstance(anchor, dict):
            return False
        if not anchor.get("case_id") or not anchor.get("source_chunk_id"):
            return False
    return True


def evaluate_claim(*, owner_user_id: str, items: list[dict]) -> ClaimOutcome:
    """校验匿名沉淀项能否认领到 owner_user_id。纯函数：不落库、不回显正文。

    规则：
    - 含禁用键 -> rejected（forbidden_key）。
    - 含未知键 -> rejected（unknown_key）。
    - 缺 case_id + source_chunk_id 锚点 -> degraded（missing_anchor），不伪造锚点。
    - 通过 -> claimed，标记 owner_user_id。
    """
    outcome = ClaimOutcome(owner_user_id=owner_user_id, requested_count=len(items))
    for item in items:
        if not isinstance(item, dict):
            outcome.rejected_count += 1
            outcome._bump(CLAIM_REASON_FORBIDDEN_KEY)
            continue
        keys = set(item.keys())
        if keys & CLAIM_ITEM_FORBIDDEN_KEYS:
            outcome.rejected_count += 1
            outcome._bump(CLAIM_REASON_FORBIDDEN_KEY)
            continue
        if not keys <= CLAIM_ITEM_ALLOWED_KEYS:
            outcome.rejected_count += 1
            outcome._bump(CLAIM_REASON_UNKNOWN_KEY)
            continue
        if not _has_valid_anchor(item):
            outcome.degraded_count += 1
            outcome._bump(CLAIM_REASON_MISSING_ANCHOR)
            continue
        outcome.claimed_count += 1
        outcome._bump(CLAIM_REASON_OK)
    return outcome
