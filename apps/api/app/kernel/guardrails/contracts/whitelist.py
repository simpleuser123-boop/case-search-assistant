"""跨产品契约对象字段白名单 + sanitize 纯函数（E-1 冻结口径，权威来源：文档 17 §3）。

设计约束：
- 纯数据 + 纯函数，无任何 I/O、无任何运行时依赖、无任何业务行为。
- 字段集与 17-E系列分步骤文档.md §3 逐字段一致。
- sanitize 只保留白名单键，主动丢弃其余键；遇到正文型键立即拒绝（fail-closed）。
"""
from __future__ import annotations

from typing import Any, Mapping

# --- 4 个跨产品契约对象字段白名单（与文档 17 §3 逐字段一致）---

# 3.1 SearchProfile（录入端 → 检索助手）
# 红线：原始口语化案情不在内（仅浏览器本地）；只携带结构化白名单字段 + 已脱敏 query。
SEARCH_PROFILE_FIELDS: frozenset[str] = frozenset(
    {
        "case_cause",
        "region",
        "trial_level_preference",
        "dispute_focus_keywords",
        "query_text",  # 已脱敏的查询文本
    }
)

# 3.2 CandidateRef（检索助手 → 法条/文书/协作台）
# 红线：不含候选正文 / chunk 正文 / 裁判文书全文。
CANDIDATE_REF_FIELDS: frozenset[str] = frozenset(
    {
        "case_id",
        "case_number",
        "court",
        "trial_level",
        "case_cause",
        "judgment_date",
        "source_anchors",  # list[{case_id, source_chunk_id, anchor_type?}]
    }
)

# 3.3 DraftDescriptor（文书工作台 → 协作台）
# 红线：只存引用与骨架，不存起草正文。
DRAFT_DESCRIPTOR_FIELDS: frozenset[str] = frozenset(
    {
        "draft_id",
        "structure_skeleton",  # 段落标题，非正文
        "candidate_refs",
        "note",  # 用户自填短备注
        "tag",  # 用户自填短标签
    }
)

# 3.4 CaseFolder（协作台内归集）
# 红线：默认 visibility=private；对象级鉴权；持久层只存元数据/引用/短字段。
CASE_FOLDER_FIELDS: frozenset[str] = frozenset(
    {
        "case_folder_id",
        "owner_user_id",
        "team_id",
        "visibility",  # enum(private/team)，默认 private
        "search_profile_summary",  # 脱敏白名单子集
        "candidate_refs",
        "draft_descriptors",
        "created_at",
        "updated_at",
    }
)

# 契约名 → 字段白名单（E-2~E-9 比对基线）。
CONTRACT_FIELD_WHITELIST: dict[str, frozenset[str]] = {
    "SearchProfile": SEARCH_PROFILE_FIELDS,
    "CandidateRef": CANDIDATE_REF_FIELDS,
    "DraftDescriptor": DRAFT_DESCRIPTOR_FIELDS,
    "CaseFolder": CASE_FOLDER_FIELDS,
}

# 正文型键黑名单：任何契约对象出现这些键即视为正文泄露，sanitize 直接拒绝。
# 注意：这些键本就不在任何白名单里，黑名单是「显式拒绝 + fail-closed」的双保险，
# 防止下游误把正文塞进契约对象后被静默丢弃而无告警。
FORBIDDEN_BODY_KEYS: frozenset[str] = frozenset(
    {
        "raw_case",
        "raw_query",
        "raw_text",
        "full_text",
        "fulltext",
        "content",
        "chunk_text",
        "chunk_content",
        "judgment_full_text",
        "judgment_text",
        "case_body",
        "body",
        "document_text",
        "paragraph_text",
        "draft_body",
        "draft_content",
        "original_fact",
        "fact_text",
    }
)


class ContractViolationError(ValueError):
    """契约白名单违规：出现正文型键或未知契约名时抛出（fail-closed）。"""


def is_forbidden_body_key(key: str) -> bool:
    """判断某个键是否为正文型键（大小写不敏感）。"""
    return key.strip().lower() in FORBIDDEN_BODY_KEYS


def sanitize_contract(contract_name: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    """按白名单清洗契约对象 payload（纯函数，无副作用）。

    规则：
    1. contract_name 必须是 4 个已冻结契约之一，否则抛 ContractViolationError。
    2. payload 中出现任何正文型键（FORBIDDEN_BODY_KEYS）立即抛 ContractViolationError，
       不静默丢弃——正文出现在契约对象里是 NO_GO 级事件，必须显式失败。
    3. 仅保留白名单内的键，其余非白名单键被主动丢弃。
    """
    if contract_name not in CONTRACT_FIELD_WHITELIST:
        raise ContractViolationError(f"unknown contract object: {contract_name!r}")

    allowed = CONTRACT_FIELD_WHITELIST[contract_name]

    for key in payload:
        if is_forbidden_body_key(key):
            raise ContractViolationError(
                f"forbidden body-type key {key!r} not allowed in {contract_name}"
            )

    return {key: value for key, value in payload.items() if key in allowed}
