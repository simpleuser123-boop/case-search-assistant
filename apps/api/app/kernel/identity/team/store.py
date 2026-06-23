"""M5-3 团队 / 沉淀对象持久层：仅白名单字段读写，所有沉淀读取强制租户过滤。

红线（运行时防御）：
- 写沉淀对象前，先用白名单过滤掉任何非白名单键（含正文 / 凭据键），
  非白名单键出现即 ValueError，绝不入库。
- 写入前调用 assert_write_within_tenant，禁止把对象写进别的团队 / 越权可见性。
- 读取沉淀对象只暴露 ``list_visible`` / ``get_visible`` 两个入口，
  二者都强制拼接 tenant_visibility_clause；没有「无过滤读取」的方法。
- 引擎可注入：生产用 app.core.db.engine（postgres）；测试注入临时 sqlite。
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, select

from app.kernel.identity.team.isolation import (
    TenantContext,
    assert_write_within_tenant,
    tenant_visibility_clause,
)
from app.kernel.identity.team.models import (
    MEMBERSHIP_STATUS_ACTIVE,
    VISIBILITY_PRIVATE,
    SedimentationObject,
    Team,
    TeamMembership,
    Workspace,
)

# 沉淀对象写入允许的白名单键（元数据 / 引用 / 锚点 / 用户自填短字段 / 结构化关系）。
# 任何不在此集合的键（含正文 / 凭据）都会被拒绝，绝不入库。
SEDIMENT_WRITE_ALLOWED_KEYS = frozenset(
    {
        "object_type",
        "case_id",
        "case_number",
        "court",
        "trial_level",
        "case_cause",
        "judgment_date",
        "source_anchors",
        "note",
        "tag",
        "label",
        "list_id",
        "list_title",
        "report_id",
    }
)

# 明确禁止的正文 / 凭据键（即便将来白名单被误扩，这些键也一律拒绝）。
SEDIMENT_FORBIDDEN_KEYS = frozenset(
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


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TeamStore:
    """团队 / 工作空间 / 成员 / 沉淀对象持久层。所有沉淀读取强制租户过滤。"""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def init_schema(self) -> None:
        """仅创建 M5-3 团队相关表。只有 ENABLE_TEAM_WORKSPACE=true 时才会被调用。"""
        SQLModel.metadata.create_all(
            self._engine,
            tables=[
                Team.__table__,
                Workspace.__table__,
                TeamMembership.__table__,
                SedimentationObject.__table__,
            ],
        )

    # --- team / workspace ---
    def create_team(self, *, team_name: str, reason_code: str | None = None) -> Team:
        team = Team(team_id=f"t_{uuid.uuid4().hex[:24]}", team_name=team_name[:60], reason_code=reason_code)
        with Session(self._engine) as session:
            session.add(team)
            session.commit()
            session.refresh(team)
        return team

    def create_workspace(self, *, team_id: str, workspace_name: str, reason_code: str | None = None) -> Workspace:
        ws = Workspace(
            workspace_id=f"w_{uuid.uuid4().hex[:24]}",
            team_id=team_id,
            workspace_name=workspace_name[:60],
            reason_code=reason_code,
        )
        with Session(self._engine) as session:
            session.add(ws)
            session.commit()
            session.refresh(ws)
        return ws

    def get_team(self, team_id: str) -> Team | None:
        with Session(self._engine) as session:
            return session.get(Team, team_id)

    # --- membership ---
    def add_member(self, *, team_id: str, member_user_id: str, workspace_id: str | None = None,
                   reason_code: str | None = None) -> TeamMembership:
        record = TeamMembership(
            membership_id=f"m_{uuid.uuid4().hex[:24]}",
            team_id=team_id,
            workspace_id=workspace_id,
            member_user_id=member_user_id,
            status=MEMBERSHIP_STATUS_ACTIVE,
            reason_code=reason_code,
        )
        with Session(self._engine) as session:
            session.add(record)
            session.commit()
            session.refresh(record)
        return record

    def is_active_member(self, *, team_id: str, member_user_id: str) -> bool:
        with Session(self._engine) as session:
            record = session.exec(
                select(TeamMembership).where(
                    TeamMembership.team_id == team_id,
                    TeamMembership.member_user_id == member_user_id,
                    TeamMembership.status == MEMBERSHIP_STATUS_ACTIVE,
                )
            ).first()
        return record is not None

    def list_member_user_ids(self, *, team_id: str) -> list[str]:
        with Session(self._engine) as session:
            rows = session.exec(
                select(TeamMembership.member_user_id).where(
                    TeamMembership.team_id == team_id,
                    TeamMembership.status == MEMBERSHIP_STATUS_ACTIVE,
                )
            ).all()
        return list(rows)

    def list_teams_for_user(self, *, member_user_id: str) -> list[Team]:
        with Session(self._engine) as session:
            team_ids = session.exec(
                select(TeamMembership.team_id).where(
                    TeamMembership.member_user_id == member_user_id,
                    TeamMembership.status == MEMBERSHIP_STATUS_ACTIVE,
                )
            ).all()
            if not team_ids:
                return []
            return list(session.exec(select(Team).where(Team.team_id.in_(team_ids))).all())  # type: ignore[union-attr]

    # --- sedimentation object (tenant-isolated) ---
    def _sanitize_payload(self, payload: dict) -> dict:
        """只保留白名单键；任何禁用键 / 未知键直接拒绝（绝不静默丢弃正文入库）。"""
        keys = set(payload.keys())
        forbidden = keys & SEDIMENT_FORBIDDEN_KEYS
        if forbidden:
            raise ValueError(f"forbidden body/credential keys not allowed in sediment: {sorted(forbidden)}")
        unknown = keys - SEDIMENT_WRITE_ALLOWED_KEYS
        if unknown:
            raise ValueError(f"unknown keys not in sediment whitelist: {sorted(unknown)}")
        clean = dict(payload)
        anchors = clean.get("source_anchors")
        if anchors is not None and not isinstance(anchors, str):
            clean["source_anchors"] = json.dumps(anchors, ensure_ascii=False)
        return clean

    def create_sediment(
        self,
        *,
        ctx: TenantContext,
        object_type: str,
        visibility: str = VISIBILITY_PRIVATE,
        payload: dict | None = None,
        reason_code: str | None = None,
    ) -> SedimentationObject:
        # 租户一致性：不允许把对象写进别的团队 / 越权可见性。
        assert_write_within_tenant(ctx, team_id=ctx.team_id, visibility=visibility)
        clean = self._sanitize_payload({"object_type": object_type, **(payload or {})})
        clean.pop("object_type", None)
        obj = SedimentationObject(
            object_id=f"o_{uuid.uuid4().hex[:24]}",
            object_type=object_type,
            owner_user_id=ctx.owner_user_id,
            team_id=ctx.team_id,
            workspace_id=ctx.workspace_id,
            visibility=visibility,
            reason_code=reason_code,
            **clean,
        )
        with Session(self._engine) as session:
            session.add(obj)
            session.commit()
            session.refresh(obj)
        return obj

    def list_visible(self, *, ctx: TenantContext, object_type: str | None = None) -> list[SedimentationObject]:
        """列出当前租户上下文可见的沉淀对象。强制租户过滤，跨租户不可见。"""
        clause = tenant_visibility_clause(ctx)
        with Session(self._engine) as session:
            stmt = select(SedimentationObject).where(clause)
            if object_type is not None:
                stmt = stmt.where(SedimentationObject.object_type == object_type)
            if ctx.workspace_id is not None:
                stmt = stmt.where(
                    (SedimentationObject.workspace_id == ctx.workspace_id)
                    | (SedimentationObject.workspace_id.is_(None))  # type: ignore[union-attr]
                )
            return list(session.exec(stmt).all())

    def get_object_for_authorization(self, object_id: str) -> SedimentationObject | None:
        """按 id 取单个对象的原始行——**不做租户可见性过滤**。

        仅供 M5-4 对象级鉴权（app.kernel.identity.permission）在 authorize() 判定时装配事实使用：
        owner 可对 private 对象显式授权，该对象不在 tenant_visibility_clause 命中范围内，
        因此必须有一条「取原始行供鉴权」的路径。调用方（权限服务）必须在 authorize()
        通过后才可返回内容；本方法本身不暴露给普通沉淀读取入口。
        """
        with Session(self._engine) as session:
            return session.get(SedimentationObject, object_id)

    def get_visible(self, *, ctx: TenantContext, object_id: str) -> SedimentationObject | None:
        """按 id 取单个对象，但仍强制租户过滤：跨租户取不到（返回 None）。"""
        clause = tenant_visibility_clause(ctx)
        with Session(self._engine) as session:
            return session.exec(
                select(SedimentationObject).where(
                    SedimentationObject.object_id == object_id,
                    clause,
                )
            ).first()
