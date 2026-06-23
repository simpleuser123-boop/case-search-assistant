"""M5-3 团队 / 工作空间 / 成员关系 / 沉淀对象的服务端持久层模型。

字段白名单（M5-1 合同 / server_multiuser_persistable_field_whitelist）：
- ``team`` 表只存：team_id / team_name（用户自填短字段）/ status / created_at /
  updated_at / reason_code。
- ``workspace`` 表只存：workspace_id / team_id / workspace_name（短字段）/ status /
  created_at / reason_code。
- ``team_membership`` 表只存结构化关系：team_id / workspace_id / member_user_id /
  status / created_at / reason_code。
- ``sedimentation_object`` 表（M5-3 首次引入的服务端沉淀持久层）只存 M4/M5 白名单内的
  **元数据 / 引用 / 来源锚点 / 结构化关系字段**：object_id / object_type /
  owner_user_id / team_id / workspace_id / visibility / case_id / case_number /
  court / trial_level / case_cause / judgment_date / source_anchors(JSON) /
  note / tag / label / list_id / list_title / report_id / status / reason_code /
  created_at / updated_at。

绝不存：raw_query、案情正文、候选 / chunk / 判决长文本、摘要 / 要旨 / 对比正文、
任何自由长文本、未脱敏个人信息、密码 / 令牌等凭据。

开关：所有写入只在 ENABLE_TEAM_WORKSPACE=true 时由团队服务触发；
关闭时本模块不建表、不写入，行为回到 M5-2 / M4 单用户私有末态。
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from sqlmodel import Field, SQLModel

# --- 短枚举（结构化字段，非正文）---
TEAM_STATUS_ACTIVE = "active"
TEAM_STATUS_DISABLED = "disabled"
TEAM_STATUSES = (TEAM_STATUS_ACTIVE, TEAM_STATUS_DISABLED)

MEMBERSHIP_STATUS_ACTIVE = "active"
MEMBERSHIP_STATUS_REMOVED = "removed"
MEMBERSHIP_STATUSES = (MEMBERSHIP_STATUS_ACTIVE, MEMBERSHIP_STATUS_REMOVED)

# 沉淀对象可见性：private=仅 owner 本人可见；team=同团队成员可见。
# M5-3 只引入这两档；更细的对象级 owner/editor/viewer 分级留待 M5-4 权限分级。
VISIBILITY_PRIVATE = "private"
VISIBILITY_TEAM = "team"
VISIBILITIES = (VISIBILITY_PRIVATE, VISIBILITY_TEAM)

# 沉淀对象类型短枚举（与 M4 前端沉淀对象对齐）。
OBJECT_TYPE_FAVORITE = "case_favorite"
OBJECT_TYPE_LIST = "case_list"
OBJECT_TYPE_REPORT = "report_template"
OBJECT_TYPES = (OBJECT_TYPE_FAVORITE, OBJECT_TYPE_LIST, OBJECT_TYPE_REPORT)

# 用户自填短字段长度上限（防止自由长文本经短字段混入持久层）。
TEAM_NAME_MAX_LENGTH = 60
WORKSPACE_NAME_MAX_LENGTH = 60
NOTE_MAX_LENGTH = 200
SHORT_FIELD_MAX_LENGTH = 120


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Team(SQLModel, table=True):
    """团队 / 组织实体。仅白名单短字段。"""

    __tablename__ = "m5_team"

    team_id: str = Field(primary_key=True, max_length=64)
    # team_name：用户自填短展示名。
    team_name: str = Field(default="", max_length=TEAM_NAME_MAX_LENGTH)
    status: str = Field(default=TEAM_STATUS_ACTIVE, max_length=16)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    reason_code: str | None = Field(default=None, max_length=64)


class Workspace(SQLModel, table=True):
    """团队下的工作空间。沉淀对象按 workspace 进一步分组（仍受 team 隔离约束）。"""

    __tablename__ = "m5_workspace"

    workspace_id: str = Field(primary_key=True, max_length=64)
    team_id: str = Field(index=True, max_length=64)
    workspace_name: str = Field(default="", max_length=WORKSPACE_NAME_MAX_LENGTH)
    status: str = Field(default=TEAM_STATUS_ACTIVE, max_length=16)
    created_at: datetime = Field(default_factory=utcnow)
    reason_code: str | None = Field(default=None, max_length=64)


class TeamMembership(SQLModel, table=True):
    """成员关系：user_id <-> team_id（+ 可选 workspace_id）。仅结构化关系字段。"""

    __tablename__ = "m5_team_membership"

    # 复合关系用代理主键，业务唯一性由 (team_id, member_user_id) 约束。
    membership_id: str = Field(primary_key=True, max_length=64)
    team_id: str = Field(index=True, max_length=64)
    workspace_id: str | None = Field(default=None, index=True, max_length=64)
    member_user_id: str = Field(index=True, max_length=64)
    status: str = Field(default=MEMBERSHIP_STATUS_ACTIVE, max_length=16)
    created_at: datetime = Field(default_factory=utcnow)
    reason_code: str | None = Field(default=None, max_length=64)


class SedimentationObject(SQLModel, table=True):
    """服务端沉淀对象（M5-3 首次引入的多租户持久层）。

    行级强隔离的载体：每行都带 owner_user_id + team_id（可空）+ visibility。
    查询层强制按租户上下文过滤，跨团队 / 跨用户默认不可见。
    只存元数据 / 引用 / 来源锚点 / 结构化关系字段，绝不存正文。
    """

    __tablename__ = "m5_sedimentation_object"

    object_id: str = Field(primary_key=True, max_length=64)
    # object_type：case_favorite / case_list / report_template。
    object_type: str = Field(index=True, max_length=32)
    # --- 租户隔离字段（强隔离核心）---
    # owner_user_id：对象归属用户。单用户私有态下隔离即按此字段。
    owner_user_id: str = Field(index=True, max_length=64)
    # team_id：为空（None）等同单用户私有；非空时进入团队隔离域。
    team_id: str | None = Field(default=None, index=True, max_length=64)
    workspace_id: str | None = Field(default=None, index=True, max_length=64)
    # visibility：private（仅 owner）/ team（同团队成员）。默认 private。
    visibility: str = Field(default=VISIBILITY_PRIVATE, max_length=16)
    # --- 元数据 / 引用（M4 白名单全量）---
    case_id: str | None = Field(default=None, max_length=SHORT_FIELD_MAX_LENGTH)
    case_number: str | None = Field(default=None, max_length=SHORT_FIELD_MAX_LENGTH)
    court: str | None = Field(default=None, max_length=SHORT_FIELD_MAX_LENGTH)
    trial_level: str | None = Field(default=None, max_length=40)
    case_cause: str | None = Field(default=None, max_length=SHORT_FIELD_MAX_LENGTH)
    judgment_date: str | None = Field(default=None, max_length=40)
    # source_anchors：JSON 序列化的来源锚点 [{case_id, source_chunk_id, anchor_type?}]。
    source_anchors: str | None = Field(default=None, max_length=4000)
    note: str | None = Field(default=None, max_length=NOTE_MAX_LENGTH)
    tag: str | None = Field(default=None, max_length=80)
    label: str | None = Field(default=None, max_length=80)
    list_id: str | None = Field(default=None, max_length=SHORT_FIELD_MAX_LENGTH)
    list_title: str | None = Field(default=None, max_length=SHORT_FIELD_MAX_LENGTH)
    report_id: str | None = Field(default=None, max_length=SHORT_FIELD_MAX_LENGTH)
    status: str = Field(default="active", max_length=16)
    reason_code: str | None = Field(default=None, max_length=64)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


def hash_team_id(team_id: str | None) -> str:
    """日志 / 埋点用的 team_id 脱敏哈希（截断），不暴露具名 id。空团队返回固定标记。"""
    if not team_id:
        return "tidh_none"
    digest = hashlib.sha256(team_id.encode("utf-8")).hexdigest()
    return f"tidh_{digest[:16]}"
