"""E7-2 casebook 持久层：CaseFolder 读写 + 强制租户隔离 + 对象级鉴权。

红线（运行时防御，沿用 M5-3 / M5-5 / E6-2 持久层纪律）：
- 写入前只接受 sanitize 后的白名单载荷（脱敏摘要/引用 JSON + 短字段）；
  绝不写裁判正文 / 起草正文 / 结论列（本表本就无正文列）。
- 读取只暴露 ``list_visible`` / ``get_visible`` 两个入口，二者都强制拼接
  ``_tenant_clause``（行级隔离）；没有「无过滤读取」的对外路径。
- 写入 / 更新前调用 ``_assert_write_within_tenant``，禁止把对象写进别的团队 / 越权可见性。
- 更新只允许 owner 本人（按 owner_user_id 取原始行做 owner 校验，非 owner 取不到即 404 语义）。
- 引擎可注入：生产用 app.core.db.engine（postgres）；测试注入临时 sqlite。

本模块只承载持久化与隔离，不 import 检索召回 / 重排序 / 摘要，不 import 其它产品包
（intake / statute / drafting）；租户上下文 ``TenantContext`` 复用 app.kernel 公开面。
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, and_, or_, select

# 仅依赖 app.kernel 公开面：租户上下文来自身份与租户组公开面。
from app.kernel.identity import TenantContext

from app.casebook.models import (
    CASE_FOLDER_STATUS_ACTIVE,
    VISIBILITY_PRIVATE,
    VISIBILITY_TEAM,
    VISIBILITIES,
    CaseFolderRow,
)

# 写入 / 更新允许的白名单键（脱敏摘要/引用 JSON + 短字段 + 结构化状态）。
# 任何不在此集合的键（含裁判正文 / 起草正文 / 结论 / 凭据）都会被拒绝，绝不入库。
CASE_FOLDER_WRITE_ALLOWED_KEYS = frozenset(
    {
        "search_profile_summary",
        "candidate_refs",
        "draft_descriptors",
        "title",
        "note",
        "tag",
        "status",
        "reason_code",
    }
)

# 明确禁止的正文 / 结论 / 原始案情 / 凭据键（即便将来白名单被误扩，这些键也一律拒绝）。
CASE_FOLDER_FORBIDDEN_PERSIST_KEYS = frozenset(
    {
        # 裁判 / 候选 / chunk 正文
        "chunk_text",
        "chunk_content",
        "judgment_text",
        "judgment_full_text",
        "summary_text",
        "highlight_text",
        "matched_text",
        "holding_summary",
        "case_body",
        "document_text",
        "full_text",
        "content",
        "body",
        # 起草 / 段落 / 结论正文
        "draft_body",
        "draft_content",
        "draft_text",
        "generated_text",
        "paragraph_body",
        "paragraph_text",
        "opinion_text",
        "legal_opinion",
        "conclusion",
        "conclusion_text",
        # 案件综述 / 胜负 / 预测
        "case_summary_text",
        "case_summary",
        "summary_conclusion",
        "win_probability",
        "outcome_prediction",
        "verdict",
        # 原始案情 / 凭据
        "raw_case",
        "raw_query",
        "password",
        "token",
        "session_token",
    }
)

# JSON 列键集合（list/dict 统一序列化为文本列）。
_JSON_COLUMN_KEYS = ("search_profile_summary", "candidate_refs", "draft_descriptors")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _tenant_clause(ctx: TenantContext):
    """构造 CaseFolderRow 的强制可见性过滤条件（行级隔离，与 DraftDescriptorRow 同口径）。

    - 单用户私有态（ctx.team_id is None）：
        owner_user_id == ctx.owner_user_id AND team_id IS NULL
      —— 只看自己的私有行，看不到任何团队行、任何他人行。
    - 团队态（ctx.team_id 给定）：
        (owner_user_id == ctx.owner_user_id AND team_id IS NULL)   # 自己的私有行
        OR (team_id == ctx.team_id AND visibility == 'team')       # 本团队共享行
      —— 跨团队（team_id != ctx.team_id）不可见；他人 private 行不可见。
    """
    own_private = and_(
        CaseFolderRow.owner_user_id == ctx.owner_user_id,
        CaseFolderRow.team_id.is_(None),  # type: ignore[union-attr]
    )
    if ctx.team_id is None:
        return own_private
    team_shared = and_(
        CaseFolderRow.team_id == ctx.team_id,
        CaseFolderRow.visibility == VISIBILITY_TEAM,
    )
    return or_(own_private, team_shared)


def _assert_write_within_tenant(
    ctx: TenantContext, *, team_id: str | None, visibility: str
) -> None:
    """写入前的租户一致性校验（防止把对象写进别的团队 / 越权可见性）。"""
    if team_id != ctx.team_id:
        raise ValueError("write team_id must match tenant context team_id")
    if visibility not in VISIBILITIES:
        raise ValueError(f"unknown visibility: {visibility}")
    if visibility == VISIBILITY_TEAM and ctx.team_id is None:
        raise ValueError("team visibility requires an active team context")


class CaseFolderStore:
    """CaseFolder 持久层。所有读取强制租户过滤；写 / 更新强制 owner + 租户一致性。"""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def init_schema(self) -> None:
        """仅创建 casebook 表。只有 ENABLE_CASEBOOK=true 时才会被调用。"""
        SQLModel.metadata.create_all(self._engine, tables=[CaseFolderRow.__table__])

    def _sanitize_persist_payload(self, payload: dict) -> dict:
        """只保留白名单键；任何禁用键 / 未知键直接拒绝（绝不静默把正文/结论入库）。

        脱敏摘要 / 引用若是 list/dict，统一 JSON 序列化为文本列。
        """
        keys = set(payload.keys())
        forbidden = keys & CASE_FOLDER_FORBIDDEN_PERSIST_KEYS
        if forbidden:
            raise ValueError(
                "forbidden body/outcome/credential keys not allowed in case_folder: "
                f"{sorted(forbidden)}"
            )
        unknown = keys - CASE_FOLDER_WRITE_ALLOWED_KEYS
        if unknown:
            raise ValueError(
                f"unknown keys not in case_folder whitelist: {sorted(unknown)}"
            )
        clean = dict(payload)
        for json_key in _JSON_COLUMN_KEYS:
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
    ) -> CaseFolderRow:
        """创建一行 CaseFolder。默认 owner 私有，强制租户一致性。"""
        _assert_write_within_tenant(ctx, team_id=ctx.team_id, visibility=visibility)
        clean = self._sanitize_persist_payload(payload)
        row = CaseFolderRow(
            case_folder_id=f"cf_{uuid.uuid4().hex[:24]}",
            owner_user_id=ctx.owner_user_id,
            team_id=ctx.team_id,
            visibility=visibility,
            status=CASE_FOLDER_STATUS_ACTIVE,
            reason_code=reason_code,
            **clean,
        )
        with Session(self._engine) as session:
            session.add(row)
            session.commit()
            session.refresh(row)
        return row

    def list_visible(self, *, ctx: TenantContext) -> list[CaseFolderRow]:
        """列出当前租户上下文可见的 CaseFolder。强制租户过滤，跨租户不可见。"""
        clause = _tenant_clause(ctx)
        with Session(self._engine) as session:
            stmt = select(CaseFolderRow).where(clause)
            return list(session.exec(stmt).all())

    def get_visible(
        self, *, ctx: TenantContext, case_folder_id: str
    ) -> CaseFolderRow | None:
        """按 id 取单个对象，但仍强制租户过滤：跨租户取不到（返回 None）。"""
        clause = _tenant_clause(ctx)
        with Session(self._engine) as session:
            return session.exec(
                select(CaseFolderRow).where(
                    CaseFolderRow.case_folder_id == case_folder_id,
                    clause,
                )
            ).first()

    def update_owned(
        self,
        *,
        ctx: TenantContext,
        case_folder_id: str,
        payload: dict,
        visibility: str | None = None,
        reason_code: str | None = None,
    ) -> CaseFolderRow | None:
        """更新 owner 本人的 CaseFolder（仍只存元数据/引用/短字段）。

        非 owner / 不存在 -> 返回 None（调用方转 404，绝不泄露他人协作夹是否存在的差异）。
        只更新传入的白名单键；其余字段保持不变。visibility 可选写入（E7-4 细化共享语义）。
        """
        clean = self._sanitize_persist_payload(payload)
        with Session(self._engine) as session:
            row = session.exec(
                select(CaseFolderRow).where(
                    CaseFolderRow.case_folder_id == case_folder_id,
                    CaseFolderRow.owner_user_id == ctx.owner_user_id,
                )
            ).first()
            if row is None:
                return None
            if visibility is not None:
                # 可见性写入须仍满足租户一致性（team 可见性要求有团队上下文）。
                _assert_write_within_tenant(
                    ctx, team_id=row.team_id, visibility=visibility
                )
                row.visibility = visibility
            for key, value in clean.items():
                setattr(row, key, value)
            if reason_code is not None:
                row.reason_code = reason_code
            row.updated_at = _utcnow()
            session.add(row)
            session.commit()
            session.refresh(row)
        return row


    def set_sharing(
        self,
        *,
        ctx: TenantContext,
        case_folder_id: str,
        visibility: str,
        team_id: str | None,
    ) -> CaseFolderRow | None:
        """E7-4 共享切换：原子地改 owner 本人 CaseFolder 的 ``visibility`` + ``team_id``。

        红线（复用 M5 多租户/对象级鉴权，不另起权限模型）：
        - 仅 owner 本人可改（按 owner_user_id 取原始行；非 owner / 不存在 -> None -> 调用方 404）。
        - 共享到 team（visibility=team）：要求 ``team_id`` 非空（owner 须为该 team 活跃成员，
          由 service/router 层先行校验）；原子写入 ``team_id=team_id`` + ``visibility=team``，
          满足「team 行必有 team_id 且 visibility=team」的隔离不变式。
        - 取消共享（visibility=private）：原子写入 ``team_id=None`` + ``visibility=private``，
          回到「private 行 team_id 必为 NULL」的隔离不变式（与 _tenant_clause own_private 一致）。
        - 只改可见性元数据，绝不触碰摘要/引用/短字段（零正文）。
        - visibility 只接受 private|team 短枚举；其它值（含 public）一律拒绝。
        """
        if visibility not in VISIBILITIES:
            raise ValueError(f"unknown visibility: {visibility}")
        target_team_id = team_id if visibility == VISIBILITY_TEAM else None
        if visibility == VISIBILITY_TEAM and not target_team_id:
            raise ValueError("team visibility requires a team_id")
        with Session(self._engine) as session:
            row = session.exec(
                select(CaseFolderRow).where(
                    CaseFolderRow.case_folder_id == case_folder_id,
                    CaseFolderRow.owner_user_id == ctx.owner_user_id,
                )
            ).first()
            if row is None:
                return None
            row.visibility = visibility
            row.team_id = target_team_id
            row.updated_at = _utcnow()
            session.add(row)
            session.commit()
            session.refresh(row)
        return row


__all__ = [
    "CaseFolderStore",
    "CASE_FOLDER_WRITE_ALLOWED_KEYS",
    "CASE_FOLDER_FORBIDDEN_PERSIST_KEYS",
]
