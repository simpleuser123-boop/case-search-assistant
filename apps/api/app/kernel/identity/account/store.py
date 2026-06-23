"""M5-2 账号存储层：仅白名单字段读写，强制密码哈希、令牌哈希。

红线断言（运行时防御）：
- 写 account 前断言 password_hash 已是哈希格式（app.core.password.is_hashed），
  任何明文都会被 ValueError 拦截，绝不入库。
- 写 session 只接受 token_hash，不接受原始 token。
- 引擎可注入：生产用 app.core.db.engine（postgres）；测试注入临时 sqlite。
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, select

from app.kernel.identity.account.models import (
    ACCOUNT_STATUS_ACTIVE,
    Account,
    AccountSession,
    hash_session_token,
)
from app.core.password import is_hashed


class AccountStore:
    """账号 / 会话持久层。所有写入只落白名单字段。"""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def init_schema(self) -> None:
        """仅创建 M5 账号相关表。只有 ENABLE_ACCOUNT_SYSTEM=true 时才会被调用。"""
        SQLModel.metadata.create_all(
            self._engine,
            tables=[Account.__table__, AccountSession.__table__],
        )

    # --- account ---
    def create_account(
        self,
        *,
        user_id: str,
        login_name: str,
        display_name: str = "",
        password_hash: str | None = None,
        auth_provider: str = "local",
        auth_subject_ref: str | None = None,
        reason_code: str | None = None,
    ) -> Account:
        # 红线：password_hash 必须已是哈希格式（local 账号）。明文直接拒绝。
        if password_hash is not None and not is_hashed(password_hash):
            raise ValueError("password_hash must be a one-way hash, never plaintext")
        account = Account(
            user_id=user_id,
            login_name=login_name,
            display_name=display_name[:60],
            password_hash=password_hash,
            auth_provider=auth_provider,
            auth_subject_ref=auth_subject_ref,
            account_status=ACCOUNT_STATUS_ACTIVE,
            reason_code=reason_code,
        )
        with Session(self._engine) as session:
            session.add(account)
            session.commit()
            session.refresh(account)
        return account

    def get_by_login_name(self, login_name: str) -> Account | None:
        with Session(self._engine) as session:
            return session.exec(
                select(Account).where(Account.login_name == login_name)
            ).first()

    def get_by_user_id(self, user_id: str) -> Account | None:
        with Session(self._engine) as session:
            return session.get(Account, user_id)

    # --- session ---
    def create_session(
        self,
        *,
        session_id: str,
        user_id: str,
        raw_token: str,
        expires_at: datetime,
        reason_code: str | None = None,
    ) -> AccountSession:
        # 只存 token 哈希；原始 token 不入库。
        record = AccountSession(
            session_id=session_id,
            user_id=user_id,
            token_hash=hash_session_token(raw_token),
            expires_at=expires_at,
            session_status="active",
            reason_code=reason_code,
        )
        with Session(self._engine) as session:
            session.add(record)
            session.commit()
            session.refresh(record)
        return record

    def get_active_session_by_token(self, raw_token: str) -> AccountSession | None:
        token_hash = hash_session_token(raw_token)
        now = datetime.now(timezone.utc)
        with Session(self._engine) as session:
            record = session.exec(
                select(AccountSession).where(AccountSession.token_hash == token_hash)
            ).first()
        if record is None or record.session_status != "active":
            return None
        expires_at = record.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at <= now:
            return None
        return record

    def revoke_session_by_token(self, raw_token: str, *, reason_code: str = "logout") -> bool:
        token_hash = hash_session_token(raw_token)
        with Session(self._engine) as session:
            record = session.exec(
                select(AccountSession).where(AccountSession.token_hash == token_hash)
            ).first()
            if record is None:
                return False
            record.session_status = "revoked"
            record.reason_code = reason_code
            session.add(record)
            session.commit()
        return True
