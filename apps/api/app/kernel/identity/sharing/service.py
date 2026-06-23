"""M5-5 同步与共享服务：本地沉淀同步（owner 私有）+ 显式团队共享（默认私有）。

职责：
- sync_local：把前端本地沉淀的**元数据 / 引用 / 锚点 / 用户自填短字段**同步到服务端，
  默认 owner 私有（team_id=None / visibility=private）。复用 M5-3 store 的白名单清洗，
  任何正文 / 凭据 / 未知键一律被拒，绝不入库。
- share_to_team：显式共享动作。校验 actor 是对象 owner + 是目标团队活跃成员；
  校验来源锚点（AI 内容承载型无锚点不进入共享）；通过后把对象升为 team 可见。
- unshare：owner 把对象降回私有。
- 共享后的可见性仍由 M5-3 tenant_visibility_clause 唯一承载，本服务不提供绕过读取。

红线：默认私有；共享必显式；无锚点 AI 内容不共享；正文绝不上送；
关闭 flag 回到 M4 本地沉淀末态（由 API 层 flag-gate 保证）。
"""
from __future__ import annotations

from dataclasses import dataclass

from app.kernel.identity.account.models import hash_user_id
from app.kernel.identity.sharing.anchors import validate_anchors_for_share
from app.kernel.identity.sharing.models import hash_object_id
from app.kernel.identity.sharing.store import SharingStore
from app.kernel.identity.team.isolation import TenantContext
from app.kernel.identity.team.models import VISIBILITY_PRIVATE, hash_team_id
from app.kernel.identity.team.store import TeamStore

REASON_OK = "ok"
REASON_FORBIDDEN_FIELD = "forbidden_field"
REASON_OBJECT_NOT_FOUND = "object_not_found"
REASON_NOT_OWNER = "not_owner"
REASON_NOT_A_MEMBER = "not_a_member"
REASON_TEAM_NOT_FOUND = "team_not_found"
REASON_NO_ANCHOR = "missing_source_anchor"
REASON_INVALID_ANCHOR = "invalid_source_anchor"


@dataclass
class SyncResult:
    ok: bool
    object_id: str | None = None
    reason_code: str = REASON_OK


@dataclass
class ShareResult:
    ok: bool
    share_id: str | None = None
    visibility: str = VISIBILITY_PRIVATE
    anchor_count: int = 0
    reason_code: str = REASON_OK


@dataclass
class ShareItemView:
    """对外可见的共享账本视图：零正文、零凭据；owner / team 以哈希呈现。"""

    object_id: str
    object_type: str
    visibility: str
    owner_user_id_hash: str
    shared_with_team_id_hash: str
    anchor_count: int
    status: str


class SharingService:
    def __init__(self, sharing_store: SharingStore, team_store: TeamStore) -> None:
        self._store = sharing_store
        self._team = team_store

    # --- 同步：本地沉淀 -> 服务端 owner 私有 ---
    def sync_local(self, *, owner_user_id: str, object_type: str, payload: dict) -> SyncResult:
        """同步一条本地沉淀到服务端，默认 owner 私有。

        复用 M5-3 store 的白名单清洗：payload 含正文 / 凭据 / 未知键即被拒，绝不入库。
        同步永远以单用户私有上下文写入（team_id=None / visibility=private）。
        """
        ctx = TenantContext(owner_user_id=owner_user_id)
        try:
            obj = self._team.create_sediment(
                ctx=ctx,
                object_type=object_type,
                visibility=VISIBILITY_PRIVATE,
                payload=payload,
                reason_code="sync_local",
            )
        except ValueError:
            return SyncResult(ok=False, reason_code=REASON_FORBIDDEN_FIELD)
        return SyncResult(ok=True, object_id=obj.object_id, reason_code=REASON_OK)

    # --- 共享：显式动作，默认私有，无锚点 AI 内容不共享 ---
    def share_to_team(self, *, actor_user_id: str, object_id: str, team_id: str) -> ShareResult:
        obj = self._store.get_object(object_id)
        if obj is None:
            return ShareResult(ok=False, reason_code=REASON_OBJECT_NOT_FOUND)
        if obj.owner_user_id != actor_user_id:
            return ShareResult(ok=False, reason_code=REASON_NOT_OWNER)
        if self._team.get_team(team_id) is None:
            return ShareResult(ok=False, reason_code=REASON_TEAM_NOT_FOUND)
        # 共享目标必须是 actor 自己所属的活跃团队（不能把对象推给非自己团队）。
        if not self._team.is_active_member(team_id=team_id, member_user_id=actor_user_id):
            return ShareResult(ok=False, reason_code=REASON_NOT_A_MEMBER)

        anchors = self._parse_anchors(obj.source_anchors)
        ok, reason = validate_anchors_for_share(object_type=obj.object_type, anchors=anchors)
        if not ok:
            return ShareResult(ok=False, reason_code=reason)

        try:
            record = self._store.promote_to_team(
                actor_user_id=actor_user_id, object_id=object_id,
                team_id=team_id, anchor_count=len(anchors),
            )
        except ValueError as exc:
            code = str(exc)
            return ShareResult(ok=False, reason_code=code if code in (REASON_OBJECT_NOT_FOUND, REASON_NOT_OWNER) else REASON_NOT_OWNER)
        return ShareResult(
            ok=True, share_id=record.share_id, visibility=record.visibility,
            anchor_count=record.anchor_count, reason_code=REASON_OK,
        )

    def unshare(self, *, actor_user_id: str, object_id: str) -> ShareResult:
        try:
            had_active = self._store.revert_to_private(actor_user_id=actor_user_id, object_id=object_id)
        except ValueError as exc:
            code = str(exc)
            return ShareResult(ok=False, reason_code=code if code in (REASON_OBJECT_NOT_FOUND, REASON_NOT_OWNER) else REASON_NOT_OWNER)
        return ShareResult(ok=had_active, visibility=VISIBILITY_PRIVATE, reason_code=REASON_OK)

    # --- 账本查询（脱敏视图）---
    def list_my_shares(self, *, owner_user_id: str) -> list[ShareItemView]:
        rows = self._store.list_shares_by_owner(owner_user_id=owner_user_id)
        return [self._to_view(r) for r in rows]

    def list_team_shares(self, *, actor_user_id: str, team_id: str) -> list[ShareItemView] | None:
        """列出团队内的共享账本。非活跃成员返回 None（调用方拒绝），绝不串读他团队。"""
        if not self._team.is_active_member(team_id=team_id, member_user_id=actor_user_id):
            return None
        rows = self._store.list_shares_for_team(team_id=team_id)
        return [self._to_view(r) for r in rows]

    @staticmethod
    def _parse_anchors(raw: str | None) -> list:
        import json

        if not raw:
            return []
        try:
            parsed = json.loads(raw)
        except (ValueError, TypeError):
            return []
        return parsed if isinstance(parsed, list) else []

    @staticmethod
    def _to_view(record) -> ShareItemView:
        return ShareItemView(
            object_id=record.object_id,
            object_type=record.object_type,
            visibility=record.visibility,
            owner_user_id_hash=hash_user_id(record.owner_user_id),
            shared_with_team_id_hash=hash_team_id(record.shared_with_team_id),
            anchor_count=record.anchor_count,
            status=record.status,
        )
