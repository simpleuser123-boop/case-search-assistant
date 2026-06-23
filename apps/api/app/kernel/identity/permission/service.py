"""M5-4 权限服务：对象级鉴权 + 角色管理 + 授权变更，全程审计。

职责：
- 把「沉淀对象事实 + actor 角色 + actor 授权」装配成 access.authorize 的输入，
  对每一次读 / 写 / 删 / 授权动作做对象级鉴权；越权一律拒绝并写审计（result=deny）。
- 管理成员角色（owner/editor/viewer）与对象级授权（grant/revoke），仅 owner 可操作。
- 默认最小权限：未显式授权的非 owner 用户对 private 对象有效权限为 none。
- 审计只落脱敏字段（actor hash / object id hash / action / result / reason_code /
  permission_level），绝不落正文 / 凭据。

隔离衔接：跨租户隔离仍由 M5-3 的 tenant_visibility_clause 保证；本服务在其之上
叠加对象级 ACL。对 private 对象的显式授权读取走 get_object_for_authorization 原始行，
但必须 authorize() 通过后才返回，且对象 owner 才能创建授权——不破坏跨团队隔离。
"""
from __future__ import annotations

from dataclasses import dataclass

from app.kernel.identity.account.models import hash_user_id
from app.kernel.identity.permission.access import (
    ACTION_ASSIGN_ROLE,
    ACTION_DELETE,
    ACTION_GRANT,
    ACTION_READ,
    ACTION_REVOKE,
    ACTION_WRITE,
    ObjectAccessInput,
    authorize,
)
from app.kernel.identity.permission.models import (
    LEVEL_TO_ROLE,
    PERMISSION_NONE,
    ROLE_EDITOR,
    ROLE_OWNER,
    ROLE_VIEWER,
    ROLES,
    hash_object_id,
)
from app.kernel.identity.permission.store import PermissionStore
from app.kernel.identity.team.store import TeamStore

REASON_OK = "ok"
REASON_DISABLED = "permission_tiering_disabled"
REASON_OBJECT_NOT_FOUND = "object_not_found"
REASON_NOT_OWNER = "not_owner"
REASON_DENIED = "no_permission"
REASON_INVALID_ROLE = "invalid_role"
REASON_INVALID_LEVEL = "invalid_level"


@dataclass
class AuthzResult:
    allowed: bool
    effective_level: int
    reason_code: str
    object_view: object | None = None


class PermissionService:
    """对象级鉴权 + 角色 / 授权管理。审计全程开启。"""

    def __init__(self, perm_store: PermissionStore, team_store: TeamStore) -> None:
        self._perm = perm_store
        self._team = team_store

    # --- 事实装配 + 鉴权 ---
    def _assemble_facts(self, *, actor_user_id: str, obj) -> ObjectAccessInput:
        granted = self._perm.get_grant_level(object_id=obj.object_id, grantee_user_id=actor_user_id)
        role_level = self._perm.get_role_level(team_id=obj.team_id, member_user_id=actor_user_id)
        return ObjectAccessInput(
            actor_user_id=actor_user_id,
            owner_user_id=obj.owner_user_id,
            object_visibility=obj.visibility,
            object_team_id=obj.team_id,
            granted_level=granted,
            actor_team_role_level=role_level,
        )

    def authorize_action(self, *, actor_user_id: str, object_id: str, action: str) -> AuthzResult:
        """对单个对象的单个动作鉴权，并写审计（allow / deny 均记录）。"""
        actor_hash = hash_user_id(actor_user_id)
        obj_hash = hash_object_id(object_id)
        obj = self._team.get_object_for_authorization(object_id)
        if obj is None:
            self._perm.write_audit(
                actor_user_id_hash=actor_hash, object_id_hash=obj_hash,
                action=action, result="deny", reason_code=REASON_OBJECT_NOT_FOUND,
            )
            return AuthzResult(allowed=False, effective_level=PERMISSION_NONE, reason_code=REASON_OBJECT_NOT_FOUND)
        facts = self._assemble_facts(actor_user_id=actor_user_id, obj=obj)
        decision = authorize(action, facts)
        self._perm.write_audit(
            actor_user_id_hash=actor_hash, object_id_hash=obj_hash, action=action,
            result="allow" if decision.allowed else "deny",
            reason_code=decision.reason_code,
            permission_level=LEVEL_TO_ROLE.get(decision.effective_level),
        )
        return AuthzResult(
            allowed=decision.allowed,
            effective_level=decision.effective_level,
            reason_code=decision.reason_code if decision.allowed else REASON_DENIED,
        )

    def read_object(self, *, actor_user_id: str, object_id: str) -> AuthzResult:
        """对象级受控读取：鉴权通过才返回脱敏视图，否则拒绝（审计已在 authorize 内写）。"""
        result = self.authorize_action(actor_user_id=actor_user_id, object_id=object_id, action=ACTION_READ)
        if not result.allowed:
            return result
        # 通过鉴权后才取内容；复用 TeamService 的脱敏视图，避免回显正文。
        from app.kernel.identity.team.service import _to_view

        obj = self._team.get_object_for_authorization(object_id)
        result.object_view = _to_view(obj) if obj is not None else None
        return result

    # --- 角色管理（仅 owner 可分配）---
    def assign_role(self, *, actor_user_id: str, team_id: str, member_user_id: str, role: str) -> dict:
        actor_hash = hash_user_id(actor_user_id)
        if role not in ROLES:
            self._perm.write_audit(actor_user_id_hash=actor_hash, action=ACTION_ASSIGN_ROLE,
                                    result="deny", reason_code=REASON_INVALID_ROLE)
            return {"ok": False, "reason_code": REASON_INVALID_ROLE}
        # 只有团队 owner 角色可以分配角色（最小特权）。
        actor_level = self._perm.get_role_level(team_id=team_id, member_user_id=actor_user_id)
        from app.kernel.identity.permission.models import PERMISSION_OWNER

        if actor_level < PERMISSION_OWNER:
            self._perm.write_audit(actor_user_id_hash=actor_hash, action=ACTION_ASSIGN_ROLE,
                                   result="deny", reason_code=REASON_NOT_OWNER)
            return {"ok": False, "reason_code": REASON_NOT_OWNER}
        self._perm.assign_role(team_id=team_id, member_user_id=member_user_id, role=role,
                               reason_code="assign_role")
        self._perm.write_audit(actor_user_id_hash=actor_hash, action=ACTION_ASSIGN_ROLE,
                               result="allow", reason_code=REASON_OK, permission_level=role)
        return {"ok": True, "reason_code": REASON_OK}

    def bootstrap_owner(self, *, team_id: str, owner_user_id: str) -> None:
        """建团时由调用方触发：把创建者写成 owner 角色（默认最小权限的唯一例外）。"""
        self._perm.assign_role(team_id=team_id, member_user_id=owner_user_id, role=ROLE_OWNER,
                               reason_code="founder")

    # --- 对象级授权（仅对象 owner 可 grant/revoke）---
    def grant(self, *, actor_user_id: str, object_id: str, grantee_user_id: str, permission_level: str) -> dict:
        actor_hash = hash_user_id(actor_user_id)
        obj_hash = hash_object_id(object_id)
        if permission_level not in (ROLE_VIEWER, ROLE_EDITOR):
            self._perm.write_audit(actor_user_id_hash=actor_hash, object_id_hash=obj_hash,
                                   action=ACTION_GRANT, result="deny", reason_code=REASON_INVALID_LEVEL)
            return {"ok": False, "reason_code": REASON_INVALID_LEVEL}
        # 鉴权：只有对象 owner（PERMISSION_OWNER）可授权。
        decision = self.authorize_action(actor_user_id=actor_user_id, object_id=object_id, action=ACTION_GRANT)
        if not decision.allowed:
            return {"ok": False, "reason_code": decision.reason_code}
        self._perm.create_grant(object_id=object_id, grantee_user_id=grantee_user_id,
                                permission_level=permission_level, granted_by_user_id=actor_user_id,
                                reason_code="grant")
        self._perm.write_audit(actor_user_id_hash=actor_hash, object_id_hash=obj_hash,
                               action=ACTION_GRANT, result="allow", reason_code=REASON_OK,
                               permission_level=permission_level)
        return {"ok": True, "reason_code": REASON_OK}

    def revoke(self, *, actor_user_id: str, object_id: str, grantee_user_id: str) -> dict:
        actor_hash = hash_user_id(actor_user_id)
        obj_hash = hash_object_id(object_id)
        decision = self.authorize_action(actor_user_id=actor_user_id, object_id=object_id, action=ACTION_REVOKE)
        if not decision.allowed:
            return {"ok": False, "reason_code": decision.reason_code}
        removed = self._perm.revoke_grant(object_id=object_id, grantee_user_id=grantee_user_id)
        self._perm.write_audit(actor_user_id_hash=actor_hash, object_id_hash=obj_hash,
                               action=ACTION_REVOKE, result="allow" if removed else "deny",
                               reason_code=REASON_OK if removed else REASON_OBJECT_NOT_FOUND)
        return {"ok": removed, "reason_code": REASON_OK if removed else REASON_OBJECT_NOT_FOUND}

    def list_audit(self, *, actor_user_id: str, limit: int = 200) -> list[dict]:
        rows = self._perm.list_audit(actor_user_id_hash=hash_user_id(actor_user_id), limit=limit)
        return [
            {
                "action": r.action,
                "result": r.result,
                "reason_code": r.reason_code,
                "permission_level": r.permission_level,
                "object_id_hash": r.object_id_hash,
                "actor_user_id_hash": r.actor_user_id_hash,
            }
            for r in rows
        ]
