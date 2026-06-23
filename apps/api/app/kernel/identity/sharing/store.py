"""M5-5 共享 / 同步持久层：共享账本读写 + 沉淀对象可见性的受控变更。

红线（运行时防御）：
- 可见性变更只通过 ``promote_to_team`` / ``revert_to_private`` 两个受控入口，
  且必须先校验「actor == 对象 owner」；非 owner 一律拒绝（ValueError）。
- 共享只改 SedimentationObject 的 team_id / visibility（结构化字段），绝不触碰任何正文列
  （沉淀对象本来就无正文列）。读取放权仍由 M5-3 tenant_visibility_clause 唯一承载。
- 账本只写白名单字段；anchor_count 只存整数。
- 引擎可注入：生产用 app.core.db.engine（postgres）；测试注入临时 sqlite。
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, select

from app.kernel.identity.sharing.models import (
    SHARE_STATUS_ACTIVE,
    SHARE_STATUS_REVOKED,
    SharedObject,
)
from app.kernel.identity.team.models import (
    VISIBILITY_PRIVATE,
    VISIBILITY_TEAM,
    SedimentationObject,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class SharingStore:
    """共享账本 + 沉淀可见性受控变更。所有可见性变更强制 owner 校验。"""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def init_schema(self) -> None:
        """仅创建 M5-5 共享账本表。只有 ENABLE_TEAM_SHARING=true 时才会被调用。"""
        SQLModel.metadata.create_all(self._engine, tables=[SharedObject.__table__])

    # --- 取对象（供 owner 校验装配事实，不放权读取内容）---
    def get_object(self, object_id: str) -> SedimentationObject | None:
        with Session(self._engine) as session:
            return session.get(SedimentationObject, object_id)

    # --- 可见性受控变更（共享 / 取消共享）---
    def promote_to_team(
        self, *, actor_user_id: str, object_id: str, team_id: str, anchor_count: int,
    ) -> SharedObject:
        """把对象显式共享给团队：owner 校验通过后将 visibility 升为 team + 写入 team_id。

        失败抛 ValueError（对象不存在 / 非 owner）；调用方据此拒绝并安全提示。
        """
        with Session(self._engine) as session:
            obj = session.get(SedimentationObject, object_id)
            if obj is None:
                raise ValueError("object_not_found")
            if obj.owner_user_id != actor_user_id:
                # 仅对象 owner 可共享；非 owner 一律拒绝（绝不越权放权）。
                raise ValueError("not_owner")
            obj.team_id = team_id
            obj.visibility = VISIBILITY_TEAM
            obj.updated_at = _utcnow()
            session.add(obj)

            existing = session.exec(
                select(SharedObject).where(
                    SharedObject.object_id == object_id,
                    SharedObject.status == SHARE_STATUS_ACTIVE,
                )
            ).first()
            if existing is not None:
                existing.shared_with_team_id = team_id
                existing.visibility = VISIBILITY_TEAM
                existing.anchor_count = anchor_count
                existing.reason_code = "reshare"
                existing.updated_at = _utcnow()
                session.add(existing)
                session.commit()
                session.refresh(existing)
                return existing
            record = SharedObject(
                share_id=f"s_{uuid.uuid4().hex[:24]}",
                object_id=object_id,
                object_type=obj.object_type,
                owner_user_id=actor_user_id,
                shared_with_team_id=team_id,
                visibility=VISIBILITY_TEAM,
                anchor_count=anchor_count,
                status=SHARE_STATUS_ACTIVE,
                reason_code="share",
            )
            session.add(record)
            session.commit()
            session.refresh(record)
            return record

    def revert_to_private(self, *, actor_user_id: str, object_id: str) -> bool:
        """取消共享：owner 校验通过后把对象降回 owner 私有（visibility=private, team_id=None）。

        返回是否有 active 共享被撤销。非 owner / 对象不存在抛 ValueError。
        """
        with Session(self._engine) as session:
            obj = session.get(SedimentationObject, object_id)
            if obj is None:
                raise ValueError("object_not_found")
            if obj.owner_user_id != actor_user_id:
                raise ValueError("not_owner")
            obj.team_id = None
            obj.visibility = VISIBILITY_PRIVATE
            obj.updated_at = _utcnow()
            session.add(obj)

            record = session.exec(
                select(SharedObject).where(
                    SharedObject.object_id == object_id,
                    SharedObject.status == SHARE_STATUS_ACTIVE,
                )
            ).first()
            had_active = record is not None
            if record is not None:
                record.status = SHARE_STATUS_REVOKED
                record.visibility = VISIBILITY_PRIVATE
                record.reason_code = "unshare"
                record.updated_at = _utcnow()
                session.add(record)
            session.commit()
            return had_active

    # --- 账本查询（脱敏 / 计数）---
    def list_shares_by_owner(self, *, owner_user_id: str) -> list[SharedObject]:
        with Session(self._engine) as session:
            return list(
                session.exec(
                    select(SharedObject).where(
                        SharedObject.owner_user_id == owner_user_id,
                        SharedObject.status == SHARE_STATUS_ACTIVE,
                    )
                ).all()
            )

    def list_shares_for_team(self, *, team_id: str) -> list[SharedObject]:
        with Session(self._engine) as session:
            return list(
                session.exec(
                    select(SharedObject).where(
                        SharedObject.shared_with_team_id == team_id,
                        SharedObject.status == SHARE_STATUS_ACTIVE,
                    )
                ).all()
            )
