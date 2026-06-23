"""M5-4 权限持久层：成员角色 / 对象级授权 / 审计事件的读写。

红线：
- 只写白名单字段；审计只写脱敏哈希字段，绝不落正文 / 凭据 / 原始 object_id 明文。
- 角色 / 授权读取均按 active 状态过滤；撤销即标记 revoked，不物理删除（留痕）。
- 引擎可注入：生产用 app.core.db.engine（postgres）；测试注入临时 sqlite。
- 本层不做鉴权判定（判定在 access.py）；只提供事实装配所需的查询。
"""
from __future__ import annotations

import uuid

from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, select

from app.kernel.identity.permission.models import (
    GRANT_STATUS_ACTIVE,
    GRANT_STATUS_REVOKED,
    PERMISSION_NONE,
    ROLE_STATUS_ACTIVE,
    ROLE_STATUS_REVOKED,
    ROLE_TO_LEVEL,
    MembershipRole,
    ObjectGrant,
    PermissionAudit,
)


class PermissionStore:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def init_schema(self) -> None:
        """仅创建 M5-4 权限相关表。只有 ENABLE_PERMISSION_TIERING=true 时才会被调用。"""
        SQLModel.metadata.create_all(
            self._engine,
            tables=[
                MembershipRole.__table__,
                ObjectGrant.__table__,
                PermissionAudit.__table__,
            ],
        )

    # --- 成员角色 ---
    def assign_role(self, *, team_id: str, member_user_id: str, role: str,
                    reason_code: str | None = None) -> MembershipRole:
        """写入 / 更新成员角色（同一 (team, member) 只保留一条 active）。"""
        with Session(self._engine) as session:
            existing = session.exec(
                select(MembershipRole).where(
                    MembershipRole.team_id == team_id,
                    MembershipRole.member_user_id == member_user_id,
                    MembershipRole.status == ROLE_STATUS_ACTIVE,
                )
            ).first()
            if existing is not None:
                existing.role = role
                existing.reason_code = reason_code
                from app.kernel.identity.permission.models import utcnow

                existing.updated_at = utcnow()
                session.add(existing)
                session.commit()
                session.refresh(existing)
                return existing
            record = MembershipRole(
                role_id=f"r_{uuid.uuid4().hex[:24]}",
                team_id=team_id,
                member_user_id=member_user_id,
                role=role,
                reason_code=reason_code,
            )
            session.add(record)
            session.commit()
            session.refresh(record)
            return record

    def get_role_level(self, *, team_id: str | None, member_user_id: str) -> int:
        """返回成员在团队的 active 角色折算等级；无团队 / 无角色 -> PERMISSION_NONE。"""
        if not team_id:
            return PERMISSION_NONE
        with Session(self._engine) as session:
            record = session.exec(
                select(MembershipRole).where(
                    MembershipRole.team_id == team_id,
                    MembershipRole.member_user_id == member_user_id,
                    MembershipRole.status == ROLE_STATUS_ACTIVE,
                )
            ).first()
        if record is None:
            return PERMISSION_NONE
        return ROLE_TO_LEVEL.get(record.role, PERMISSION_NONE)

    def list_roles(self, *, team_id: str) -> list[MembershipRole]:
        with Session(self._engine) as session:
            return list(
                session.exec(
                    select(MembershipRole).where(
                        MembershipRole.team_id == team_id,
                        MembershipRole.status == ROLE_STATUS_ACTIVE,
                    )
                ).all()
            )

    # --- 对象级授权 ---
    def create_grant(self, *, object_id: str, grantee_user_id: str, permission_level: str,
                     granted_by_user_id: str, reason_code: str | None = None) -> ObjectGrant:
        """创建 / 刷新对象级授权（同一 (object, grantee) 只保留一条 active）。"""
        with Session(self._engine) as session:
            existing = session.exec(
                select(ObjectGrant).where(
                    ObjectGrant.object_id == object_id,
                    ObjectGrant.grantee_user_id == grantee_user_id,
                    ObjectGrant.status == GRANT_STATUS_ACTIVE,
                )
            ).first()
            if existing is not None:
                existing.permission_level = permission_level
                existing.granted_by_user_id = granted_by_user_id
                existing.reason_code = reason_code
                session.add(existing)
                session.commit()
                session.refresh(existing)
                return existing
            record = ObjectGrant(
                grant_id=f"g_{uuid.uuid4().hex[:24]}",
                object_id=object_id,
                grantee_user_id=grantee_user_id,
                permission_level=permission_level,
                granted_by_user_id=granted_by_user_id,
                reason_code=reason_code,
            )
            session.add(record)
            session.commit()
            session.refresh(record)
            return record

    def revoke_grant(self, *, object_id: str, grantee_user_id: str) -> bool:
        with Session(self._engine) as session:
            record = session.exec(
                select(ObjectGrant).where(
                    ObjectGrant.object_id == object_id,
                    ObjectGrant.grantee_user_id == grantee_user_id,
                    ObjectGrant.status == GRANT_STATUS_ACTIVE,
                )
            ).first()
            if record is None:
                return False
            record.status = GRANT_STATUS_REVOKED
            session.add(record)
            session.commit()
            return True

    def get_grant_level(self, *, object_id: str, grantee_user_id: str) -> int:
        """返回 actor 对该对象的 active 授权折算等级；无授权 -> PERMISSION_NONE。"""
        with Session(self._engine) as session:
            record = session.exec(
                select(ObjectGrant).where(
                    ObjectGrant.object_id == object_id,
                    ObjectGrant.grantee_user_id == grantee_user_id,
                    ObjectGrant.status == GRANT_STATUS_ACTIVE,
                )
            ).first()
        if record is None:
            return PERMISSION_NONE
        return ROLE_TO_LEVEL.get(record.permission_level, PERMISSION_NONE)

    def list_grants(self, *, object_id: str) -> list[ObjectGrant]:
        with Session(self._engine) as session:
            return list(
                session.exec(
                    select(ObjectGrant).where(
                        ObjectGrant.object_id == object_id,
                        ObjectGrant.status == GRANT_STATUS_ACTIVE,
                    )
                ).all()
            )

    # --- 审计（只写脱敏字段）---
    def write_audit(self, *, actor_user_id_hash: str, action: str, result: str,
                    reason_code: str, object_id_hash: str | None = None,
                    permission_level: str | None = None) -> None:
        record = PermissionAudit(
            audit_id=f"a_{uuid.uuid4().hex[:24]}",
            actor_user_id_hash=actor_user_id_hash,
            object_id_hash=object_id_hash,
            action=action,
            result=result,
            reason_code=reason_code,
            permission_level=permission_level,
        )
        with Session(self._engine) as session:
            session.add(record)
            session.commit()

    def list_audit(self, *, actor_user_id_hash: str | None = None, limit: int = 200) -> list[PermissionAudit]:
        with Session(self._engine) as session:
            stmt = select(PermissionAudit)
            if actor_user_id_hash is not None:
                stmt = stmt.where(PermissionAudit.actor_user_id_hash == actor_user_id_hash)
            stmt = stmt.order_by(PermissionAudit.created_at.desc()).limit(limit)  # type: ignore[union-attr]
            return list(session.exec(stmt).all())
