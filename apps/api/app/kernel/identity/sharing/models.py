"""M5-5 共享账本模型：显式共享动作的留痕记录（m5_shared_object）。

设计要点：
- 真正的「可见性」仍由 M5-3 的 SedimentationObject.visibility + team_id 承载，
  并经唯一过滤点 tenant_visibility_clause 强制隔离；本表只做**显式共享动作的账本**
  （谁、把哪个对象、在何时、共享给哪个团队、当前是否有效），用于审计与撤销。
- 这样可见性判定不出现「第二条路径」：读取永远走 M5-3 的强制过滤；本账本不参与读取放权。

字段白名单（M5-1 合同：owner / visibility / shared_with_team_id 槽位 + 结构化关系 + 脱敏锚点摘要）：
- share_id / object_id / object_type / owner_user_id / shared_with_team_id /
  visibility / anchor_count / status / reason_code / created_at / updated_at。

绝不存：正文、原始案情、摘要 / 要旨长文本、密码 / 令牌等凭据、自由长文本。
anchor_count 只是「校验通过的锚点条数」整数，便于审计「共享内容是否有溯源」，不含锚点内容。

开关：所有写入只在 ENABLE_TEAM_SHARING=true 时由共享服务触发；
关闭时本模块不建表、不写入，行为回到 M4 本地沉淀末态。
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from sqlmodel import Field, SQLModel

SHARE_STATUS_ACTIVE = "active"
SHARE_STATUS_REVOKED = "revoked"
SHARE_STATUSES = (SHARE_STATUS_ACTIVE, SHARE_STATUS_REVOKED)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class SharedObject(SQLModel, table=True):
    """显式共享动作账本。仅结构化关系 + 脱敏摘要字段，无正文。"""

    __tablename__ = "m5_shared_object"

    share_id: str = Field(primary_key=True, max_length=64)
    # object_id：被共享的沉淀对象引用（指向 m5_sedimentation_object）。
    object_id: str = Field(index=True, max_length=64)
    object_type: str = Field(max_length=32)
    # owner_user_id：发起共享者（必须是对象 owner）。
    owner_user_id: str = Field(index=True, max_length=64)
    # shared_with_team_id：共享目标团队（M5-1 预留槽位）。撤销后保留以留痕。
    shared_with_team_id: str | None = Field(default=None, index=True, max_length=64)
    # visibility：动作产生的目标可见性（private=未共享 / team=已共享给团队）。
    visibility: str = Field(default="private", max_length=16)
    # anchor_count：共享时校验通过的来源锚点条数（仅整数，用于审计溯源，不含锚点内容）。
    anchor_count: int = Field(default=0)
    status: str = Field(default=SHARE_STATUS_ACTIVE, max_length=16)
    reason_code: str | None = Field(default=None, max_length=64)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


def hash_object_id(object_id: str | None) -> str:
    """日志 / 埋点用的 object_id 脱敏哈希（截断）。空对象返回固定标记。"""
    if not object_id:
        return "oidh_none"
    digest = hashlib.sha256(object_id.encode("utf-8")).hexdigest()
    return f"oidh_{digest[:16]}"
