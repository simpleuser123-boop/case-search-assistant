"""E6-2 文书工作台 DraftDescriptor 服务端持久层模型（drafting 产品包专属表）。

字段白名单（沿用 M4/M5 持久层隐私边界 + E6-1 DraftDescriptor 契约）：
- ``draft_descriptor`` 表只存 **元数据 / 引用 / 结构骨架(标题) / 用户自填短字段 /
  结构化关系字段**：
  draft_id / owner_user_id / team_id / visibility / structure_skeleton(JSON,标题清单) /
  candidate_refs(JSON,带锚点引用) / statute_refs(JSON,带锚点引用) / note / tag /
  status / reason_code / created_at / updated_at。

绝不存：起草正文 / 段落正文 / 结论 / 胜负判断 / 裁判正文 / 候选 / chunk 正文 /
摘要 / 要旨 / 原始案情 / 任何自由长文本 / 凭据。

- structure_skeleton 列虽是 JSON 文本列，但只承载**段落标题清单**（每项 ≤ 60 字，由
  E6-1 契约 sanitize_draft_descriptor 校验），不承载段落正文。
- candidate_refs / statute_refs 列只承载 **CandidateRef / StatuteRef 白名单字段 + 锚点**
  （已由 sanitize_draft_descriptor 逐项收敛，缺锚点丢弃），不含对侧裁判正文。

行级强隔离：每行带 owner_user_id + team_id(可空) + visibility(默认 private)；
查询层强制按租户上下文过滤（见 store.tenant_clause），跨用户 / 跨团队默认不可见。

开关：所有写入只在 ENABLE_DRAFTING=true 时由 drafting 端点触发；
关闭时本模块不建表、不写入，行为回到「无文书工作台」末态。
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from sqlmodel import Field, SQLModel

# 草稿状态短枚举（结构化字段，非正文）。
DRAFT_STATUS_ACTIVE = "active"
DRAFT_STATUS_ARCHIVED = "archived"
DRAFT_STATUSES = (DRAFT_STATUS_ACTIVE, DRAFT_STATUS_ARCHIVED)

# 可见性：private（仅 owner）/ team（同团队成员）。默认 private（沿用 M5-3 口径）。
VISIBILITY_PRIVATE = "private"
VISIBILITY_TEAM = "team"
VISIBILITIES = (VISIBILITY_PRIVATE, VISIBILITY_TEAM)

# JSON 列长度上限（结构骨架/引用是有限清单，给足余量但有界，防自由长文本经 JSON 列混入）。
SKELETON_JSON_MAX_LENGTH = 8000
REFS_JSON_MAX_LENGTH = 20000
NOTE_MAX_LENGTH = 200
TAG_MAX_LENGTH = 40


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class DraftDescriptorRow(SQLModel, table=True):
    """文书工作台沉淀对象（E6-2 首次引入的 drafting 产品包持久层）。

    只存元数据 / 引用 / 结构骨架(标题) / 短字段；绝不含起草正文 / 裁判正文 / 结论。
    """

    __tablename__ = "draft_descriptor"

    draft_id: str = Field(primary_key=True, max_length=64)
    # --- 租户隔离字段（强隔离核心，沿用 SedimentationObject 口径）---
    owner_user_id: str = Field(index=True, max_length=64)
    team_id: str | None = Field(default=None, index=True, max_length=64)
    visibility: str = Field(default=VISIBILITY_PRIVATE, max_length=16)
    # --- 结构骨架（标题清单，JSON 序列化）---
    structure_skeleton: str = Field(default="[]", max_length=SKELETON_JSON_MAX_LENGTH)
    # --- 引用（带锚点，JSON 序列化；已由 sanitize 收敛，无对侧正文）---
    candidate_refs: str = Field(default="[]", max_length=REFS_JSON_MAX_LENGTH)
    statute_refs: str = Field(default="[]", max_length=REFS_JSON_MAX_LENGTH)
    # --- 用户自填短字段（非正文）---
    note: str | None = Field(default=None, max_length=NOTE_MAX_LENGTH)
    tag: str | None = Field(default=None, max_length=TAG_MAX_LENGTH)
    # --- 结构化状态字段 ---
    status: str = Field(default=DRAFT_STATUS_ACTIVE, max_length=16)
    reason_code: str | None = Field(default=None, max_length=64)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


def hash_draft_id(draft_id: str | None) -> str:
    """日志 / 埋点用的 draft_id 脱敏哈希（截断）。空草稿返回固定标记。"""
    if not draft_id:
        return "didh_none"
    digest = hashlib.sha256(draft_id.encode("utf-8")).hexdigest()
    return f"didh_{digest[:16]}"


def hash_user_id_for_log(user_id: str | None) -> str:
    """日志用 user_id 脱敏哈希（截断），不暴露具名 id。"""
    if not user_id:
        return "uidh_none"
    digest = hashlib.sha256(user_id.encode("utf-8")).hexdigest()
    return f"uidh_{digest[:16]}"


def note_log_meta(note: str | None) -> str:
    """note 的可入日志元信息：只暴露长度 + 短 hash，绝不暴露 note 全文。"""
    if not note:
        return "len=0"
    digest = hashlib.sha256(note.encode("utf-8")).hexdigest()
    return f"len={len(note)} h={digest[:12]}"


__all__ = [
    "DRAFT_STATUS_ACTIVE",
    "DRAFT_STATUS_ARCHIVED",
    "DRAFT_STATUSES",
    "VISIBILITY_PRIVATE",
    "VISIBILITY_TEAM",
    "VISIBILITIES",
    "NOTE_MAX_LENGTH",
    "TAG_MAX_LENGTH",
    "DraftDescriptorRow",
    "hash_draft_id",
    "hash_user_id_for_log",
    "note_log_meta",
    "utcnow",
]
