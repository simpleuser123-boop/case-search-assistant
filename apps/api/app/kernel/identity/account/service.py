"""M5-2 认证服务：注册 / 登录 / 登出 / 会话校验骨架。

凭据红线：
- 明文密码只作为入参短暂存在，立即转哈希；绝不存储、绝不日志、绝不返回。
- 原始会话令牌只在登录结果里一次性返回给调用方（由 API 层放进响应/cookie），
  服务端只持久化它的哈希；不写日志、不进业务表明文、不进测试快照。
- 工具不代填凭据：本服务只接收调用方（用户/平台侧）已输入的明文，
  不主动生成、不替用户保管 SSO/密码明文。
- 日志只记录 user_id 哈希 + status + reason_code。

返回值用 AuthResult（成功）/ AuthError（失败 reason_code），均不含任何凭据。
"""
from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from app.kernel.identity.account.models import Account, hash_user_id
from app.kernel.identity.account.store import AccountStore
from app.core.password import hash_password, verify_password

# 会话有效期（骨架默认 12 小时；非凭据，可配置）。
SESSION_TTL_SECONDS = 12 * 60 * 60

# 失败 reason_code（脱敏短码，可安全入日志/响应）。
REASON_LOGIN_NAME_TAKEN = "login_name_taken"
REASON_INVALID_CREDENTIALS = "invalid_credentials"
REASON_ACCOUNT_DISABLED = "account_disabled"
REASON_INVALID_SESSION = "invalid_session"
REASON_WEAK_INPUT = "weak_input"


@dataclass
class PublicAccount:
    """对外可见的账号视图：零凭据、零正文。"""

    user_id: str
    display_name: str
    account_status: str
    auth_provider: str


@dataclass
class AuthResult:
    ok: bool
    account: PublicAccount | None = None
    # session_token 仅在 login 成功时存在，供 API 层一次性下发；不落日志/快照。
    session_token: str | None = None
    expires_at: datetime | None = None
    reason_code: str | None = None
    # user_id_hash 用于调用方安全打点。
    user_id_hash: str | None = field(default=None)


def _public(account: Account) -> PublicAccount:
    return PublicAccount(
        user_id=account.user_id,
        display_name=account.display_name,
        account_status=account.account_status,
        auth_provider=account.auth_provider,
    )


class AuthService:
    def __init__(self, store: AccountStore, *, ttl_seconds: int = SESSION_TTL_SECONDS) -> None:
        self._store = store
        self._ttl_seconds = ttl_seconds

    def register(self, *, login_name: str, password: str, display_name: str = "") -> AuthResult:
        login_name = (login_name or "").strip()
        if len(login_name) < 3 or len(password or "") < 8:
            return AuthResult(ok=False, reason_code=REASON_WEAK_INPUT)
        if self._store.get_by_login_name(login_name) is not None:
            return AuthResult(ok=False, reason_code=REASON_LOGIN_NAME_TAKEN)
        user_id = f"u_{uuid.uuid4().hex[:24]}"
        # 明文 -> 哈希，立即丢弃明文引用。
        password_hash = hash_password(password)
        account = self._store.create_account(
            user_id=user_id,
            login_name=login_name,
            display_name=(display_name or "").strip(),
            password_hash=password_hash,
            auth_provider="local",
            reason_code="register",
        )
        return AuthResult(
            ok=True,
            account=_public(account),
            user_id_hash=hash_user_id(account.user_id),
            reason_code="register",
        )

    def login(self, *, login_name: str, password: str) -> AuthResult:
        login_name = (login_name or "").strip()
        account = self._store.get_by_login_name(login_name)
        # 统一失败路径，避免账号枚举。
        if account is None or account.password_hash is None:
            return AuthResult(ok=False, reason_code=REASON_INVALID_CREDENTIALS)
        if account.account_status != "active":
            return AuthResult(ok=False, reason_code=REASON_ACCOUNT_DISABLED)
        if not verify_password(password, account.password_hash):
            return AuthResult(ok=False, reason_code=REASON_INVALID_CREDENTIALS)
        # 生成原始会话令牌（仅本次返回），服务端只存其哈希。
        raw_token = secrets.token_urlsafe(32)
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=self._ttl_seconds)
        self._store.create_session(
            session_id=f"s_{uuid.uuid4().hex[:24]}",
            user_id=account.user_id,
            raw_token=raw_token,
            expires_at=expires_at,
            reason_code="login",
        )
        return AuthResult(
            ok=True,
            account=_public(account),
            session_token=raw_token,
            expires_at=expires_at,
            user_id_hash=hash_user_id(account.user_id),
            reason_code="login",
        )

    def logout(self, *, session_token: str) -> AuthResult:
        revoked = self._store.revoke_session_by_token(session_token, reason_code="logout")
        if not revoked:
            return AuthResult(ok=False, reason_code=REASON_INVALID_SESSION)
        return AuthResult(ok=True, reason_code="logout")

    def resolve_session(self, *, session_token: str) -> AuthResult:
        record = self._store.get_active_session_by_token(session_token)
        if record is None:
            return AuthResult(ok=False, reason_code=REASON_INVALID_SESSION)
        account = self._store.get_by_user_id(record.user_id)
        if account is None or account.account_status != "active":
            return AuthResult(ok=False, reason_code=REASON_INVALID_SESSION)
        return AuthResult(
            ok=True,
            account=_public(account),
            expires_at=record.expires_at,
            user_id_hash=hash_user_id(account.user_id),
            reason_code="session_active",
        )
