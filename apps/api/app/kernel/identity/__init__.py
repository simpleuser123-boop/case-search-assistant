"""共享内核 · 身份与租户组公开面（E-2a 逻辑边界，纯 re-export）。

内核成员（依据文档 17 §2.1）：account / team / permission / sharing。
本模块只把上述四包「现有可调用入口」收敛为稳定公开符号，**纯 re-export**：
不复制实现、不改签名、不改运行时语义、不新增逻辑。E-2a 阶段零文件移动。

flag 基调（沿用各包既有声明）：ENABLE_ACCOUNT_SYSTEM / ENABLE_TEAM_WORKSPACE /
ENABLE_PERMISSION_TIERING / ENABLE_TEAM_SHARING 默认全 false；关闭时回到单用户私有末态。
本公开面只收敛「引用入口」，不改变上述默认开关与隔离/鉴权判定。
"""
from __future__ import annotations

# --- account 包公开面 ---
from app.kernel.identity.account import (
    Account,
    AccountSession,
    hash_session_token,
    hash_user_id,
)
from app.kernel.identity.account.service import AuthResult, AuthService, PublicAccount

# --- team 包公开面（含租户隔离调用面 isolation）---
from app.kernel.identity.team.service import SedimentView, TeamService, TenantResolution
from app.kernel.identity.team.isolation import (
    TenantContext,
    assert_write_within_tenant,
    tenant_visibility_clause,
)

# --- permission 包公开面（含对象级鉴权调用面 access）---
from app.kernel.identity.permission.service import AuthzResult, PermissionService
from app.kernel.identity.permission.access import (
    AccessDecision,
    ObjectAccessInput,
    authorize,
    resolve_effective_level,
)

# --- sharing 包公开面（含来源锚点校验 anchors）---
from app.kernel.identity.sharing.service import (
    ShareItemView,
    ShareResult,
    SharingService,
    SyncResult,
)
from app.kernel.identity.sharing.anchors import is_valid_anchor, validate_anchors_for_share

__all__ = [
    # account
    "Account", "AccountSession", "hash_session_token", "hash_user_id",
    "AuthResult", "AuthService", "PublicAccount",
    # team
    "SedimentView", "TeamService", "TenantResolution",
    "TenantContext", "assert_write_within_tenant", "tenant_visibility_clause",
    # permission
    "AuthzResult", "PermissionService",
    "AccessDecision", "ObjectAccessInput", "authorize", "resolve_effective_level",
    # sharing
    "ShareItemView", "ShareResult", "SharingService", "SyncResult",
    "is_valid_anchor", "validate_anchors_for_share",
]
