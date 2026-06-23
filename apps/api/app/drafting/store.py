"""E6-2 drafting 持久层：DraftDescriptor 读写 + 强制租户隔离 + 对象级鉴权。

红线（运行时防御，沿用 M5-3 / M5-5 持久层纪律）：
- 写入前只接受 sanitize 后的白名单载荷（结构骨架/引用 JSON + 短字段）；
  绝不写起草正文 / 裁判正文 / 结论列（本表本就无正文列）。
- 读取只暴露 ``list_visible`` / ``get_visible`` 两个入口，二者都强制拼接
  ``_tenant_clause``（行级隔离）；没有「无过滤读取」的对外路径。
- 写入 / 更新前调用 ``_assert_write_within_tenant``，禁止把对象写进别的团队 / 越权可见性。
- 更新只允许 owner 本人（``get_owned`` 取原始行做 owner 校验，非 owner 取不到即 404 语义）。
- 引擎可注入：生产用 app.core.db.engine（postgres）；测试注入临时 sqlite。

本模块只承载持久化与隔离，不 import 检索 / rerank / summary，不 import 其它产品包；
租户上下文 ``TenantContext`` 复用 app.kernel 公开面（身份与租户组）。
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, and_, or_, select

# 仅依赖 app.kernel 公开面：租户上下文来自身份与租户组公开面。
from app.kernel.identity import TenantContext

from app.drafting.models import (
    DRAFT_STATUS_ACTIVE,
    VISIBILITY_PRIVATE,
    VISIBILITY_TEAM,
    VISIBILITIES,
    DraftDescriptorRow,
)

# 写入 / 更新允许的白名单键（结构骨架/引用 JSON + 短字段 + 结构化状态）。
# 任何不在此集合的键（含起草正文 / 裁判正文 / 结论 / 凭据）都会被拒绝，绝不入库。
DRAFT_WRITE_ALLOWED_KEYS = frozenset(
    {
        "structure_skeleton",
        "candidate_refs",
        "statute_refs",
        "note",
        "tag",
        "status",
        "reason_code",
    }
)

# 明确禁止的正文 / 结论 / 凭据键（即便将来白名单被误扩，这些键也一律拒绝）。
DRAFT_FORBIDDEN_PERSIST_KEYS = frozenset(
    {
        "draft_body",
        "draft_content",
        "draft_text",
        "generated_text",
        "paragraph_body",
        "paragraph_text",
        "section_body",
        "conclusion",
        "conclusion_text",
        "opinion_text",
        "legal_opinion",
        "full_text",
        "content",
        "body",
        "chunk_text",
        "judgment_text",
        "summary_text",
        "raw_case",
        "raw_query",
        "win_probability",
        "outcome_prediction",
        "verdict",
        "password",
        "token",
        "session_token",
    }
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _tenant_clause(ctx: TenantContext):
    """构造 DraftDescriptorRow 的强制可见性过滤条件（行级隔离，与 SedimentationObject 同口径）。

    - 单用户私有态（ctx.team_id is None）：
        owner_user_id == ctx.owner_user_id AND team_id IS NULL
      —— 只看自己的私有行，看不到任何团队行、任何他人行。
    - 团队态（ctx.team_id 给定）：
        (owner_user_id == ctx.owner_user_id AND team_id IS NULL)   # 自己的私有行
        OR (team_id == ctx.team_id AND visibility == 'team')       # 本团队共享行
      —— 跨团队（team_id != ctx.team_id）不可见；他人 private 行不可见。
    """
    own_private = and_(
        DraftDescriptorRow.owner_user_id == ctx.owner_user_id,
        DraftDescriptorRow.team_id.is_(None),  # type: ignore[union-attr]
    )
    if ctx.team_id is None:
        return own_private
    team_shared = and_(
        DraftDescriptorRow.team_id == ctx.team_id,
        DraftDescriptorRow.visibility == VISIBILITY_TEAM,
    )
    return or_(own_private, team_shared)


def _assert_write_within_tenant(ctx: TenantContext, *, team_id: str | None, visibility: str) -> None:
    """写入前的租户一致性校验（防止把对象写进别的团队 / 越权可见性）。"""
    if team_id != ctx.team_id:
        raise ValueError("write team_id must match tenant context team_id")
    if visibility not in VISIBILITIES:
        raise ValueError(f"unknown visibility: {visibility}")
    if visibility == VISIBILITY_TEAM and ctx.team_id is None:
        raise ValueError("team visibility requires an active team context")


class DraftStore:
    """DraftDescriptor 持久层。所有读取强制租户过滤；写 / 更新强制 owner + 租户一致性。"""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def init_schema(self) -> None:
        """仅创建 drafting 表。只有 ENABLE_DRAFTING=true 时才会被调用。"""
        SQLModel.metadata.create_all(
            self._engine, tables=[DraftDescriptorRow.__table__]
        )

    def _sanitize_persist_payload(self, payload: dict) -> dict:
        """只保留白名单键；任何禁用键 / 未知键直接拒绝（绝不静默把正文/结论入库）。

        结构骨架 / 引用若是 list/dict，统一 JSON 序列化为文本列。
        """
        keys = set(payload.keys())
        forbidden = keys & DRAFT_FORBIDDEN_PERSIST_KEYS
        if forbidden:
            raise ValueError(
                f"forbidden body/outcome/credential keys not allowed in draft: {sorted(forbidden)}"
            )
        unknown = keys - DRAFT_WRITE_ALLOWED_KEYS
        if unknown:
            raise ValueError(f"unknown keys not in draft whitelist: {sorted(unknown)}")
        clean = dict(payload)
        for json_key in ("structure_skeleton", "candidate_refs", "statute_refs"):
            val = clean.get(json_key)
            if val is not None and not isinstance(val, str):
                clean[json_key] = json.dumps(val, ensure_ascii=False)
        return clean

    def create(
        self,
        *,
        ctx: TenantContext,
        payload: dict,
        visibility: str = VISIBILITY_PRIVATE,
        reason_code: str | None = None,
    ) -> DraftDescriptorRow:
        """创建一行 DraftDescriptor。默认 owner 私有，强制租户一致性。"""
        _assert_write_within_tenant(ctx, team_id=ctx.team_id, visibility=visibility)
        clean = self._sanitize_persist_payload(payload)
        row = DraftDescriptorRow(
            draft_id=f"d_{uuid.uuid4().hex[:24]}",
            owner_user_id=ctx.owner_user_id,
            team_id=ctx.team_id,
            visibility=visibility,
            status=DRAFT_STATUS_ACTIVE,
            reason_code=reason_code,
            **clean,
        )
        with Session(self._engine) as session:
            session.add(row)
            session.commit()
            session.refresh(row)
        return row

    def list_visible(self, *, ctx: TenantContext) -> list[DraftDescriptorRow]:
        """列出当前租户上下文可见的 DraftDescriptor。强制租户过滤，跨租户不可见。"""
        clause = _tenant_clause(ctx)
        with Session(self._engine) as session:
            stmt = select(DraftDescriptorRow).where(clause)
            return list(session.exec(stmt).all())

    def get_visible(self, *, ctx: TenantContext, draft_id: str) -> DraftDescriptorRow | None:
        """按 id 取单个对象，但仍强制租户过滤：跨租户取不到（返回 None）。"""
        clause = _tenant_clause(ctx)
        with Session(self._engine) as session:
            return session.exec(
                select(DraftDescriptorRow).where(
                    DraftDescriptorRow.draft_id == draft_id,
                    clause,
                )
            ).first()

    def update_owned(
        self,
        *,
        ctx: TenantContext,
        draft_id: str,
        payload: dict,
        reason_code: str | None = None,
    ) -> DraftDescriptorRow | None:
        """更新 owner 本人的 DraftDescriptor（仍只存元数据/引用/短字段）。

        非 owner / 不存在 -> 返回 None（调用方转 404，绝不泄露他人草稿是否存在的差异）。
        只更新传入的白名单键；其余字段保持不变。
        """
        clean = self._sanitize_persist_payload(payload)
        with Session(self._engine) as session:
            row = session.exec(
                select(DraftDescriptorRow).where(
                    DraftDescriptorRow.draft_id == draft_id,
                    DraftDescriptorRow.owner_user_id == ctx.owner_user_id,
                )
            ).first()
            if row is None:
                return None
            # 团队态下仅 owner 可改；team_id / visibility 不在本步可变集合（默认私有，E6-2 不放权）。
            for key, value in clean.items():
                setattr(row, key, value)
            if reason_code is not None:
                row.reason_code = reason_code
            row.updated_at = _utcnow()
            session.add(row)
            session.commit()
            session.refresh(row)
        return row


__all__ = [
    "DraftStore",
    "DRAFT_WRITE_ALLOWED_KEYS",
    "DRAFT_FORBIDDEN_PERSIST_KEYS",
]
