"""M5-4 对象级访问控制核心：有效权限解析（默认最小权限）。

这是「每一次读写都经过对象级鉴权」的判定中心。纯逻辑，不触碰检索 / rerank /
主排序，也不直接写库（审计由 service 负责）。

有效权限解析规则（取各来源的最大值；默认最小权限）：
1) 对象 owner（owner_user_id == actor）：PERMISSION_OWNER。
2) 该对象的 active 对象级授权（ObjectGrant）：授予的 permission_level。
3) 若对象 visibility==team 且 actor 是该 team 的 active 成员：
   按 actor 的团队角色（MembershipRole）折算等级；team 可见对象对团队成员
   至少给到角色对应等级（owner 角色=owner 级，editor=editor 级，viewer=viewer 级）。
   **注意**：private 对象不因团队成员身份获得任何权限——必须显式授权。
4) 其余一律 PERMISSION_NONE（默认最小权限）。

动作所需等级：
- read   -> PERMISSION_VIEWER
- write  -> PERMISSION_EDITOR
- delete -> PERMISSION_OWNER
- grant / revoke / assign_role -> PERMISSION_OWNER
"""
from __future__ import annotations

from dataclasses import dataclass

from app.kernel.identity.permission.models import (
    PERMISSION_EDITOR,
    PERMISSION_NONE,
    PERMISSION_OWNER,
    PERMISSION_VIEWER,
    ROLE_TO_LEVEL,
    VISIBILITY_TEAM_FALLBACK,
)

# 动作 -> 所需最小权限等级。
ACTION_READ = "read"
ACTION_WRITE = "write"
ACTION_DELETE = "delete"
ACTION_GRANT = "grant"
ACTION_REVOKE = "revoke"
ACTION_ASSIGN_ROLE = "assign_role"

ACTION_REQUIRED_LEVEL = {
    ACTION_READ: PERMISSION_VIEWER,
    ACTION_WRITE: PERMISSION_EDITOR,
    ACTION_DELETE: PERMISSION_OWNER,
    ACTION_GRANT: PERMISSION_OWNER,
    ACTION_REVOKE: PERMISSION_OWNER,
    ACTION_ASSIGN_ROLE: PERMISSION_OWNER,
}

# reason codes（结构化短码，进审计）。
REASON_OWNER = "owner"
REASON_OBJECT_GRANT = "object_grant"
REASON_TEAM_ROLE = "team_role"
REASON_NO_PERMISSION = "no_permission"
REASON_NOT_FOUND = "object_not_found"
REASON_UNKNOWN_ACTION = "unknown_action"


@dataclass(frozen=True)
class ObjectAccessInput:
    """鉴权所需的最小事实集合（由 service 从持久层装配，避免 access 直接访问库）。"""

    actor_user_id: str
    owner_user_id: str
    object_visibility: str  # "private" / "team"
    object_team_id: str | None
    # actor 对该对象的 active 对象级授权等级（无授权则 PERMISSION_NONE）。
    granted_level: int = PERMISSION_NONE
    # actor 在该对象所属 team 的 active 角色等级（非成员 / 无角色则 PERMISSION_NONE）。
    actor_team_role_level: int = PERMISSION_NONE


@dataclass(frozen=True)
class AccessDecision:
    allowed: bool
    effective_level: int
    reason_code: str


def resolve_effective_level(facts: ObjectAccessInput) -> tuple[int, str]:
    """解析 actor 对该对象的有效权限等级（取最大值，默认最小权限）。"""
    # 1) owner 归属：最高权限。
    if facts.actor_user_id == facts.owner_user_id:
        return PERMISSION_OWNER, REASON_OWNER

    best = PERMISSION_NONE
    reason = REASON_NO_PERMISSION

    # 2) 对象级显式授权。
    if facts.granted_level > best:
        best = facts.granted_level
        reason = REASON_OBJECT_GRANT

    # 3) 团队可见对象 + 团队角色（private 对象绝不因团队身份放权）。
    if facts.object_visibility == VISIBILITY_TEAM_FALLBACK and facts.object_team_id is not None:
        if facts.actor_team_role_level > best:
            best = facts.actor_team_role_level
            reason = REASON_TEAM_ROLE

    if best == PERMISSION_NONE:
        return PERMISSION_NONE, REASON_NO_PERMISSION
    return best, reason


def authorize(action: str, facts: ObjectAccessInput) -> AccessDecision:
    """对单个对象的单个动作做鉴权。越权返回 allowed=False（调用方据此 403 + 审计）。"""
    required = ACTION_REQUIRED_LEVEL.get(action)
    if required is None:
        return AccessDecision(allowed=False, effective_level=PERMISSION_NONE, reason_code=REASON_UNKNOWN_ACTION)
    effective, reason = resolve_effective_level(facts)
    if effective >= required:
        return AccessDecision(allowed=True, effective_level=effective, reason_code=reason)
    return AccessDecision(allowed=False, effective_level=effective, reason_code=REASON_NO_PERMISSION)
