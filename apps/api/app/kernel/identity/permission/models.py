"""M5-4 权限分级持久层模型：成员角色 / 对象级 ACL 授权 / 审计事件。

字段白名单（M5-1 合同 / server_multiuser_persistable_field_whitelist 的
role / permission_level 槽位 + 结构化关系字段）：
- ``membership_role`` 只存：role_id / team_id / member_user_id / role /
  status / created_at / updated_at / reason_code。
- ``object_grant``（对象级 ACL）只存：grant_id / object_id / grantee_user_id /
  permission_level / granted_by_user_id / status / created_at / reason_code。
- ``permission_audit``（审计事件）只存**脱敏字段**：audit_id / actor_user_id_hash /
  object_id_hash / action / result / reason_code / permission_level / created_at。

绝不存：正文、原始 object_id 明文（审计里只存哈希）、密码 / 令牌 / 任何凭据明文、
任何自由长文本。审计里 actor 与 object 均以单向哈希呈现。

开关：所有写入只在 ENABLE_PERMISSION_TIERING=true 时由权限服务触发；
关闭时本模块不建表、不写入，行为回到 M5-3 / M4 末态（owner 私有，无角色概念）。
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from sqlmodel import Field, SQLModel

# --- 角色短枚举（结构化字段，非正文）---
# owner：对象 / 团队的创建者与管理者，拥有全部权限（含授权 / 撤销）。
# editor：可读、可编辑对象，但不能管理授权。
# viewer：只读。
ROLE_OWNER = "owner"
ROLE_EDITOR = "editor"
ROLE_VIEWER = "viewer"
ROLES = (ROLE_OWNER, ROLE_EDITOR, ROLE_VIEWER)

# 权限等级（数值化，便于「最小权限」比较；数值越大权限越高）。
# none=0 表示无任何访问权限（默认最小权限的兜底）。
PERMISSION_NONE = 0
PERMISSION_VIEWER = 1
PERMISSION_EDITOR = 2
PERMISSION_OWNER = 3

ROLE_TO_LEVEL = {
    ROLE_VIEWER: PERMISSION_VIEWER,
    ROLE_EDITOR: PERMISSION_EDITOR,
    ROLE_OWNER: PERMISSION_OWNER,
}
LEVEL_TO_ROLE = {v: k for k, v in ROLE_TO_LEVEL.items()}

# 对象可见性短枚举回显（与 app.kernel.identity.team.models.VISIBILITY_TEAM 对齐，避免跨包硬依赖）。
# private 对象不因团队成员身份放权；仅 team 可见对象允许按团队角色折算等级。
VISIBILITY_TEAM_FALLBACK = "team"

ROLE_STATUS_ACTIVE = "active"
ROLE_STATUS_REVOKED = "revoked"
ROLE_STATUSES = (ROLE_STATUS_ACTIVE, ROLE_STATUS_REVOKED)

GRANT_STATUS_ACTIVE = "active"
GRANT_STATUS_REVOKED = "revoked"
GRANT_STATUSES = (GRANT_STATUS_ACTIVE, GRANT_STATUS_REVOKED)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class MembershipRole(SQLModel, table=True):
    """成员在团队中的角色。仅结构化关系字段，无正文。

    默认最小权限：未显式写入角色记录的成员，等级为 PERMISSION_NONE（团队对象也看不到）。
    建团者由权限服务显式写入 owner 角色。
    """

    __tablename__ = "m5_membership_role"

    role_id: str = Field(primary_key=True, max_length=64)
    team_id: str = Field(index=True, max_length=64)
    member_user_id: str = Field(index=True, max_length=64)
    # role：owner / editor / viewer。
    role: str = Field(default=ROLE_VIEWER, max_length=16)
    status: str = Field(default=ROLE_STATUS_ACTIVE, max_length=16)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    reason_code: str | None = Field(default=None, max_length=64)


class ObjectGrant(SQLModel, table=True):
    """对象级 ACL 授权：把某个沉淀对象显式授予某用户某权限等级。

    这是「显式授权才扩大可见性」的唯一载体。没有 active 授权记录时，
    非 owner 用户对 private 对象的有效权限为 PERMISSION_NONE。
    只有对象 owner 才能创建 / 撤销授权（由权限服务强制）。
    """

    __tablename__ = "m5_object_grant"

    grant_id: str = Field(primary_key=True, max_length=64)
    object_id: str = Field(index=True, max_length=64)
    grantee_user_id: str = Field(index=True, max_length=64)
    # permission_level：viewer / editor（不允许直接授予 owner；owner 由归属决定）。
    permission_level: str = Field(default=ROLE_VIEWER, max_length=16)
    granted_by_user_id: str = Field(max_length=64)
    status: str = Field(default=GRANT_STATUS_ACTIVE, max_length=16)
    created_at: datetime = Field(default_factory=utcnow)
    reason_code: str | None = Field(default=None, max_length=64)


class PermissionAudit(SQLModel, table=True):
    """权限审计事件。只存脱敏字段：actor / object 均为哈希，绝无正文 / 凭据。

    记录授权变更（grant / revoke / role 变更）与越权尝试（access_denied）。
    """

    __tablename__ = "m5_permission_audit"

    audit_id: str = Field(primary_key=True, max_length=64)
    # actor_user_id_hash：发起动作者的脱敏哈希（uidh_ 前缀）。
    actor_user_id_hash: str = Field(index=True, max_length=64)
    # object_id_hash：被作用对象的脱敏哈希（oidh_ 前缀）；非对象类动作可为 None。
    object_id_hash: str | None = Field(default=None, max_length=64)
    # action：read / write / delete / grant / revoke / assign_role。
    action: str = Field(max_length=32)
    # result：allow / deny。
    result: str = Field(max_length=16)
    reason_code: str = Field(max_length=64)
    # permission_level：动作要求 / 命中的等级（short enum 名），便于审计回溯。
    permission_level: str | None = Field(default=None, max_length=16)
    created_at: datetime = Field(default_factory=utcnow)


def hash_object_id(object_id: str | None) -> str:
    """审计 / 日志用的 object_id 脱敏哈希（截断）。空对象返回固定标记。"""
    if not object_id:
        return "oidh_none"
    digest = hashlib.sha256(object_id.encode("utf-8")).hexdigest()
    return f"oidh_{digest[:16]}"
