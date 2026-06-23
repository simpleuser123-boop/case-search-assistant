"""M5-2 账号体系：服务端持久层模型与存储。

字段白名单（M5-1 合同 / server_multiuser_persistable_field_whitelist）：
- ``account`` 表只存：user_id / display_name / account_status / auth_provider /
  auth_subject_ref / password_hash / created_at / updated_at / reason_code。
- ``password_hash`` 只存单向哈希串（见 app.core.password）；明文绝不入库。
- ``auth_subject_ref`` 只存 SSO/OAuth 的 provider+subject 引用（脱敏），
  绝不存 access/refresh/id token 明文。
- 会话令牌**不进业务表**：``account_session`` 只存 token 的单向哈希（token_hash），
  原始 token 仅在登录响应里一次性返回给客户端，服务端不可逆、不可反查。
- 绝不存：明文密码、SSO/会话令牌明文、raw_query、案情正文、任何自由长文本。

开关：所有写入只在 ENABLE_ACCOUNT_SYSTEM=true 时由认证服务触发；
关闭时本模块不建表、不写入，行为回到 M4 单用户私有末态。
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from sqlmodel import Field, SQLModel

# 账号状态短枚举（结构化字段，非正文）。
ACCOUNT_STATUS_ACTIVE = "active"
ACCOUNT_STATUS_DISABLED = "disabled"
ACCOUNT_STATUSES = (ACCOUNT_STATUS_ACTIVE, ACCOUNT_STATUS_DISABLED)

# 认证 provider 短枚举。local=用户名+密码哈希；其余为 SSO/OAuth 引用（不存令牌）。
AUTH_PROVIDER_LOCAL = "local"
AUTH_PROVIDER_SSO = "sso"
AUTH_PROVIDERS = (AUTH_PROVIDER_LOCAL, AUTH_PROVIDER_SSO)

# 自填短字段长度上限（防止自由长文本经 display_name 混入持久层）。
DISPLAY_NAME_MAX_LENGTH = 60
# 登录名（用于本地登录）也按短字段处理；只用于定位账号，非正文。
LOGIN_NAME_MAX_LENGTH = 80


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Account(SQLModel, table=True):
    """账号实体。仅白名单字段 + 密码单向哈希列。"""

    __tablename__ = "m5_account"

    # user_id：服务端稳定主键（具名身份）。匿名态没有该 id。
    user_id: str = Field(primary_key=True, max_length=64)
    # login_name：本地登录定位用的唯一短标识（邮箱/用户名等，由用户在平台侧输入）。
    login_name: str = Field(index=True, unique=True, max_length=LOGIN_NAME_MAX_LENGTH)
    # display_name：用户自填短展示名。
    display_name: str = Field(default="", max_length=DISPLAY_NAME_MAX_LENGTH)
    # account_status：active / disabled。
    account_status: str = Field(default=ACCOUNT_STATUS_ACTIVE, max_length=16)
    # auth_provider：local / sso。
    auth_provider: str = Field(default=AUTH_PROVIDER_LOCAL, max_length=16)
    # auth_subject_ref：SSO 的 "provider:subject_id" 引用，本地账号为空。绝不存令牌。
    auth_subject_ref: str | None = Field(default=None, max_length=190)
    # password_hash：单向哈希串（local 账号）；SSO 账号为空。绝不存明文。
    password_hash: str | None = Field(default=None, max_length=255)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    # reason_code：最近一次状态变更的短码（脱敏，用于审计），非正文。
    reason_code: str | None = Field(default=None, max_length=64)


class AccountSession(SQLModel, table=True):
    """会话记录。只存 token 的单向哈希，绝不存原始会话令牌。"""

    __tablename__ = "m5_account_session"

    # session_id：会话稳定标识（非令牌本身）。
    session_id: str = Field(primary_key=True, max_length=64)
    # user_id：该会话归属账号。
    user_id: str = Field(index=True, max_length=64)
    # token_hash：会话令牌的单向哈希（sha256 hex）。服务端据此校验，不可反推 token。
    token_hash: str = Field(index=True, max_length=64)
    created_at: datetime = Field(default_factory=utcnow)
    expires_at: datetime
    # session_status：active / revoked（登出即 revoked）。
    session_status: str = Field(default="active", max_length=16)
    reason_code: str | None = Field(default=None, max_length=64)


def hash_session_token(raw_token: str) -> str:
    """把原始会话令牌转成不可逆的存储哈希。原始 token 绝不入库 / 入日志。"""
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def hash_user_id(user_id: str) -> str:
    """日志 / 埋点用的 user_id 脱敏哈希（截断），不暴露具名 id。"""
    digest = hashlib.sha256(user_id.encode("utf-8")).hexdigest()
    return f"uidh_{digest[:16]}"
