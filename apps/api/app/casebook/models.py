"""E7-2 案件协作工作台 CaseFolder 服务端持久层模型（casebook 产品包专属表）。

字段白名单（沿用 M4/M5 持久层隐私边界 + E7-1 CaseFolder 契约）：
- ``case_folder`` 表只存 **元数据 / 多租户字段 / 脱敏摘要 / 引用 / 用户自填短字段 /
  结构化关系字段**：
  case_folder_id / owner_user_id / team_id / visibility /
  search_profile_summary(JSON, SearchProfile 脱敏白名单子集) /
  candidate_refs(JSON, 带锚点引用) / draft_descriptors(JSON, 带锚点引用) /
  title / note / tag / status / reason_code / created_at / updated_at。

绝不存：裁判正文 / 候选 / chunk 正文 / 起草正文 / 段落正文 / 结论 / 胜负判断 /
案件综述正文 / 原始案情 / 任何自由长文本 / 凭据。

- search_profile_summary 列虽是 JSON 文本列，但只承载 **SearchProfile 脱敏白名单子集键**
  （由 E7-1 sanitize_case_folder -> sanitize_intake_search_profile 收敛），不承载原始案情。
- candidate_refs / draft_descriptors 列只承载已收敛的引用（CandidateRef / DraftDescriptor
  白名单字段 + 锚点；已由 sanitize_case_folder 逐项收敛，缺锚点丢弃），不含对侧裁判 / 起草正文。

行级强隔离：每行带 owner_user_id + team_id(可空) + visibility(默认 private)；
查询层强制按租户上下文过滤（见 store._tenant_clause），跨用户 / 跨团队默认不可见。

开关：所有写入只在 ENABLE_CASEBOOK=true 时由 casebook 端点触发；
关闭时本模块不建表、不写入，行为回到「无协作工作台」末态。
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from sqlmodel import Field, SQLModel

# 协作夹状态短枚举（结构化字段，非正文）。
CASE_FOLDER_STATUS_ACTIVE = "active"
CASE_FOLDER_STATUS_ARCHIVED = "archived"
CASE_FOLDER_STATUSES = (CASE_FOLDER_STATUS_ACTIVE, CASE_FOLDER_STATUS_ARCHIVED)

# 可见性：private（仅 owner）/ team（同团队成员）。默认 private（沿用 M5-3 / E6-2 口径）。
VISIBILITY_PRIVATE = "private"
VISIBILITY_TEAM = "team"
VISIBILITIES = (VISIBILITY_PRIVATE, VISIBILITY_TEAM)

# JSON 列长度上限（摘要/引用是有限清单，给足余量但有界，防自由长文本经 JSON 列混入）。
SUMMARY_JSON_MAX_LENGTH = 4000
REFS_JSON_MAX_LENGTH = 40000
TITLE_MAX_LENGTH = 120
NOTE_MAX_LENGTH = 200
TAG_MAX_LENGTH = 40


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class CaseFolderRow(SQLModel, table=True):
    """案件协作工作台沉淀对象（E7-2 首次引入的 casebook 产品包持久层）。

    只存元数据 / 多租户字段 / 脱敏摘要 / 引用 / 短字段；绝不含裁判 / 起草正文 / 结论。
    """

    __tablename__ = "case_folder"

    case_folder_id: str = Field(primary_key=True, max_length=64)
    # --- 租户隔离字段（强隔离核心，沿用 SedimentationObject / DraftDescriptorRow 口径）---
    owner_user_id: str = Field(index=True, max_length=64)
    team_id: str | None = Field(default=None, index=True, max_length=64)
    visibility: str = Field(default=VISIBILITY_PRIVATE, max_length=16)
    # --- 脱敏摘要（SearchProfile 白名单子集，JSON 序列化；零原始案情）---
    search_profile_summary: str | None = Field(
        default=None, max_length=SUMMARY_JSON_MAX_LENGTH
    )
    # --- 引用（带锚点，JSON 序列化；已由 sanitize 收敛，无对侧正文）---
    candidate_refs: str = Field(default="[]", max_length=REFS_JSON_MAX_LENGTH)
    draft_descriptors: str = Field(default="[]", max_length=REFS_JSON_MAX_LENGTH)
    # --- 用户自填短字段（非正文）---
    title: str | None = Field(default=None, max_length=TITLE_MAX_LENGTH)
    note: str | None = Field(default=None, max_length=NOTE_MAX_LENGTH)
    tag: str | None = Field(default=None, max_length=TAG_MAX_LENGTH)
    # --- 结构化状态字段 ---
    status: str = Field(default=CASE_FOLDER_STATUS_ACTIVE, max_length=16)
    reason_code: str | None = Field(default=None, max_length=64)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


def hash_case_folder_id(case_folder_id: str | None) -> str:
    """日志 / 埋点用的 case_folder_id 脱敏哈希（截断）。空夹返回固定标记。"""
    if not case_folder_id:
        return "cfh_none"
    digest = hashlib.sha256(case_folder_id.encode("utf-8")).hexdigest()
    return f"cfh_{digest[:16]}"


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


def input_hash(*parts: object) -> str:
    """归集入参的可入日志指纹（只暴露短 hash，绝不暴露原始内容）。"""
    raw = "|".join(str(p) for p in parts)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return f"inh_{digest[:16]}"


__all__ = [
    "CASE_FOLDER_STATUS_ACTIVE",
    "CASE_FOLDER_STATUS_ARCHIVED",
    "CASE_FOLDER_STATUSES",
    "VISIBILITY_PRIVATE",
    "VISIBILITY_TEAM",
    "VISIBILITIES",
    "TITLE_MAX_LENGTH",
    "NOTE_MAX_LENGTH",
    "TAG_MAX_LENGTH",
    "CaseFolderRow",
    "hash_case_folder_id",
    "hash_user_id_for_log",
    "note_log_meta",
    "input_hash",
    "utcnow",
]
