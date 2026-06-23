"""M5-3 团队空间服务：建团 / 加成员 / 解析租户上下文 / 沉淀读写（强隔离）。

隔离红线：
- 解析租户上下文时，若请求带 team_id，必须先校验「该 user 是该 team 的活跃成员」，
  否则拒绝并降级为单用户私有态（team_id=None），绝不允许越权进入他团队隔离域。
- 所有沉淀读写都通过 store 的强制租户过滤入口；service 不暴露绕过路径。
- 返回值只含脱敏视图（计数 / 短字段 / 哈希），不回显正文。
- 日志只记录 user_id_hash / team_id_hash / count / status / reason_code。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from app.kernel.identity.account.models import hash_user_id
from app.kernel.identity.team.isolation import TenantContext
from app.kernel.identity.team.models import (
    VISIBILITY_PRIVATE,
    VISIBILITY_TEAM,
    SedimentationObject,
    hash_team_id,
)
from app.kernel.identity.team.store import TeamStore

REASON_NOT_A_MEMBER = "not_a_member"
REASON_TEAM_NOT_FOUND = "team_not_found"
REASON_OK = "ok"
REASON_FORBIDDEN_FIELD = "forbidden_field"


@dataclass
class TenantResolution:
    """租户上下文解析结果：拒绝越权时降级为单用户私有态。"""

    ctx: TenantContext
    downgraded: bool = False
    reason_code: str = REASON_OK


@dataclass
class SedimentView:
    """对外可见的沉淀视图：零正文、零凭据；team_id 以哈希呈现。"""

    object_id: str
    object_type: str
    visibility: str
    owner_user_id_hash: str
    team_id_hash: str
    case_id: str | None = None
    case_number: str | None = None
    court: str | None = None
    trial_level: str | None = None
    case_cause: str | None = None
    judgment_date: str | None = None
    note: str | None = None
    tag: str | None = None
    label: str | None = None
    list_id: str | None = None
    list_title: str | None = None
    report_id: str | None = None
    source_anchors: list = field(default_factory=list)


def _to_view(obj: SedimentationObject) -> SedimentView:
    anchors: list = []
    if obj.source_anchors:
        try:
            parsed = json.loads(obj.source_anchors)
            if isinstance(parsed, list):
                anchors = parsed
        except (ValueError, TypeError):
            anchors = []
    return SedimentView(
        object_id=obj.object_id,
        object_type=obj.object_type,
        visibility=obj.visibility,
        owner_user_id_hash=hash_user_id(obj.owner_user_id),
        team_id_hash=hash_team_id(obj.team_id),
        case_id=obj.case_id,
        case_number=obj.case_number,
        court=obj.court,
        trial_level=obj.trial_level,
        case_cause=obj.case_cause,
        judgment_date=obj.judgment_date,
        note=obj.note,
        tag=obj.tag,
        label=obj.label,
        list_id=obj.list_id,
        list_title=obj.list_title,
        report_id=obj.report_id,
        source_anchors=anchors,
    )


class TeamService:
    def __init__(self, store: TeamStore) -> None:
        self._store = store

    # --- 团队管理 ---
    def create_team(self, *, owner_user_id: str, team_name: str) -> dict:
        team = self._store.create_team(team_name=team_name, reason_code="create_team")
        # 建团者自动成为活跃成员。
        self._store.add_member(team_id=team.team_id, member_user_id=owner_user_id, reason_code="founder")
        return {
            "team_id": team.team_id,
            "team_name": team.team_name,
            "team_id_hash": hash_team_id(team.team_id),
            "status": team.status,
        }

    def add_member(self, *, team_id: str, member_user_id: str) -> dict:
        if self._store.get_team(team_id) is None:
            return {"ok": False, "reason_code": REASON_TEAM_NOT_FOUND}
        self._store.add_member(team_id=team_id, member_user_id=member_user_id, reason_code="add_member")
        return {"ok": True, "reason_code": REASON_OK}

    def list_teams(self, *, member_user_id: str) -> list[dict]:
        teams = self._store.list_teams_for_user(member_user_id=member_user_id)
        return [
            {
                "team_id": t.team_id,
                "team_name": t.team_name,
                "team_id_hash": hash_team_id(t.team_id),
                "status": t.status,
            }
            for t in teams
        ]

    def member_count(self, *, team_id: str) -> int:
        return len(self._store.list_member_user_ids(team_id=team_id))

    # --- 租户上下文解析（越权降级私有）---
    def resolve_tenant(
        self, *, owner_user_id: str, team_id: str | None = None, workspace_id: str | None = None
    ) -> TenantResolution:
        if team_id is None:
            # 单用户私有态：等同 M5-2 / M4 行为。
            return TenantResolution(ctx=TenantContext(owner_user_id=owner_user_id))
        if self._store.get_team(team_id) is None:
            return TenantResolution(
                ctx=TenantContext(owner_user_id=owner_user_id),
                downgraded=True,
                reason_code=REASON_TEAM_NOT_FOUND,
            )
        if not self._store.is_active_member(team_id=team_id, member_user_id=owner_user_id):
            # 越权：不是该团队成员 -> 拒绝进入团队域，降级为单用户私有。
            return TenantResolution(
                ctx=TenantContext(owner_user_id=owner_user_id),
                downgraded=True,
                reason_code=REASON_NOT_A_MEMBER,
            )
        return TenantResolution(
            ctx=TenantContext(
                owner_user_id=owner_user_id, team_id=team_id, workspace_id=workspace_id
            )
        )

    # --- 沉淀读写（强隔离）---
    def save_sediment(
        self, *, ctx: TenantContext, object_type: str, visibility: str, payload: dict
    ) -> dict:
        try:
            obj = self._store.create_sediment(
                ctx=ctx,
                object_type=object_type,
                visibility=visibility,
                payload=payload,
                reason_code="save_sediment",
            )
        except ValueError:
            # 含正文 / 未知键 / 越权可见性 -> 拒绝，不入库，不回显内容。
            return {"ok": False, "reason_code": REASON_FORBIDDEN_FIELD}
        return {"ok": True, "object_id": obj.object_id, "reason_code": REASON_OK}

    def list_sediment(self, *, ctx: TenantContext, object_type: str | None = None) -> list[SedimentView]:
        rows = self._store.list_visible(ctx=ctx, object_type=object_type)
        return [_to_view(o) for o in rows]

    def get_sediment(self, *, ctx: TenantContext, object_id: str) -> SedimentView | None:
        obj = self._store.get_visible(ctx=ctx, object_id=object_id)
        return _to_view(obj) if obj is not None else None
