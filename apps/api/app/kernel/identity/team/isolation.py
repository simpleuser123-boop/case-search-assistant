"""M5-3 租户隔离核心：租户上下文 + 强制查询过滤。

设计红线（隔离不彻底即 NO_GO）：
- 任何对沉淀对象的读取都必须经过 ``tenant_visibility_clause`` 构造的过滤条件，
  store 层不提供「无租户过滤」的读取路径。
- 隔离语义：
  * team_id 为空（单用户私有态）：只能看到自己 owner_user_id 且 team_id 为空的行。
    等同当前单用户私有行为。
  * team_id 非空（团队态）：只能看到「自己私有行」+「同一 team_id 且 visibility=team 的行」。
    跨团队（不同 team_id）一律不可见；他人 private 行一律不可见。
- 上下文里的 team_id 必须经成员关系校验后才允许传入（由 service 层保证）；
  本模块只负责把上下文翻译成不可绕过的过滤条件。

本模块为纯逻辑，不 import 检索 / rerank，不触碰主排序。
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlmodel import and_, or_

from app.kernel.identity.team.models import (
    VISIBILITY_PRIVATE,
    VISIBILITY_TEAM,
    SedimentationObject,
)


@dataclass(frozen=True)
class TenantContext:
    """当前请求的租户上下文。

    - ``owner_user_id``：当前登录用户（具名身份）。必填。
    - ``team_id``：当前激活团队；None 表示单用户私有态（等同 M5-2 / M4 行为）。
    - ``workspace_id``：当前激活工作空间；仅做附加过滤，不放宽隔离。
    """

    owner_user_id: str
    team_id: str | None = None
    workspace_id: str | None = None

    def is_single_user_private(self) -> bool:
        return self.team_id is None


def tenant_visibility_clause(ctx: TenantContext):
    """构造 SedimentationObject 的强制可见性过滤条件（行级隔离）。

    返回的 SQLAlchemy 条件保证：
    - 单用户私有态（ctx.team_id is None）：
        owner_user_id == ctx.owner_user_id AND team_id IS NULL
      —— 只看自己的私有行，看不到任何团队行、任何他人行。
    - 团队态（ctx.team_id 给定）：
        (owner_user_id == ctx.owner_user_id AND team_id IS NULL)            # 自己的私有行
        OR (team_id == ctx.team_id AND visibility == 'team')               # 本团队共享行
      —— 跨团队（team_id != ctx.team_id）不可见；他人 private 行不可见。

    任何调用方都无法用本函数读到「其它租户」的行：跨团队串读在 SQL 层即被排除。
    """
    own_private = and_(
        SedimentationObject.owner_user_id == ctx.owner_user_id,
        SedimentationObject.team_id.is_(None),  # type: ignore[union-attr]
    )
    if ctx.team_id is None:
        # 单用户私有态：只允许自己的私有行。
        return own_private

    team_shared = and_(
        SedimentationObject.team_id == ctx.team_id,
        SedimentationObject.visibility == VISIBILITY_TEAM,
    )
    return or_(own_private, team_shared)


def assert_write_within_tenant(ctx: TenantContext, *, team_id: str | None, visibility: str) -> None:
    """写入前的租户一致性校验（防止把对象写进别的团队 / 越权可见性）。

    规则：
    - 写入的 team_id 必须与上下文 team_id 完全一致（包括都为 None）。
      单用户私有态不允许直接写出带 team_id 的行。
    - visibility=team 只能在团队态（ctx.team_id 非空）下使用；
      单用户私有态只能写 private 行。
    - visibility 必须是已知短枚举。
    """
    if team_id != ctx.team_id:
        raise ValueError("write team_id must match tenant context team_id")
    if visibility not in (VISIBILITY_PRIVATE, VISIBILITY_TEAM):
        raise ValueError(f"unknown visibility: {visibility}")
    if visibility == VISIBILITY_TEAM and ctx.team_id is None:
        raise ValueError("team visibility requires an active team context")
