"""M5-2 账号体系与认证骨架（flag-gated，默认关闭）。

ENABLE_ACCOUNT_SYSTEM=false 时不建表、不写入、不暴露任何账号入口，
行为与 M4 单用户私有末态完全一致。
"""
from __future__ import annotations

from app.kernel.identity.account.models import (
    Account,
    AccountSession,
    hash_session_token,
    hash_user_id,
)

__all__ = [
    "Account",
    "AccountSession",
    "hash_session_token",
    "hash_user_id",
]
