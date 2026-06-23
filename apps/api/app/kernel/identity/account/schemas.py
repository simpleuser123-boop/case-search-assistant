"""M5-2 账号/认证 API schemas。

凭据红线：响应模型里**没有** password / token 字段名以外的明文凭据；
session_token 仅在登录/会话响应里作为一次性下发字段返回给客户端，
绝不进日志/报告/JSON 产物/测试快照（API 层负责不打印其值）。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class RegisterRequest(BaseModel):
    # login_name / password 由用户在平台侧输入；工具不代填。
    login_name: str = Field(..., min_length=3, max_length=80)
    password: str = Field(..., min_length=8, max_length=200)
    display_name: str = Field(default="", max_length=60)

    model_config = ConfigDict(extra="forbid")


class LoginRequest(BaseModel):
    login_name: str = Field(..., min_length=1, max_length=80)
    password: str = Field(..., min_length=1, max_length=200)

    model_config = ConfigDict(extra="forbid")


class PublicAccountModel(BaseModel):
    user_id: str
    display_name: str
    account_status: str
    auth_provider: str


class AuthResponse(BaseModel):
    ok: bool
    account: PublicAccountModel | None = None
    # session_token：仅 login 成功时存在，一次性下发；服务端只存其哈希。
    session_token: str | None = None
    expires_at: datetime | None = None
    reason_code: str | None = None


class SessionResponse(BaseModel):
    ok: bool
    account: PublicAccountModel | None = None
    expires_at: datetime | None = None
    reason_code: str | None = None


class LogoutResponse(BaseModel):
    ok: bool
    reason_code: str | None = None


# --- 单用户态迁移认领 ---
class ClaimItem(BaseModel):
    """单条匿名沉淀引用：仅元数据 / 锚点 / 用户自填短字段；禁止正文。"""

    # 用 extra="forbid" + 校验层双保险，未知键直接 422，不进认领逻辑。
    object_type: Literal["case_favorite", "case_list", "report_template"] | None = None
    object_ref_id: str | None = Field(default=None, max_length=120)
    case_id: str | None = Field(default=None, max_length=120)
    case_number: str | None = Field(default=None, max_length=120)
    court: str | None = Field(default=None, max_length=120)
    trial_level: str | None = Field(default=None, max_length=40)
    case_cause: str | None = Field(default=None, max_length=120)
    judgment_date: str | None = Field(default=None, max_length=40)
    source_anchors: list[dict[str, Any]] = Field(default_factory=list)
    note: str | None = Field(default=None, max_length=200)
    tag: str | None = Field(default=None, max_length=80)
    list_title: str | None = Field(default=None, max_length=120)

    model_config = ConfigDict(extra="forbid")


class ClaimRequest(BaseModel):
    # 迁移默认不自动执行：必须显式 confirm=true 才会评估。
    confirm: bool = False
    items: list[ClaimItem] = Field(default_factory=list, max_length=2000)

    model_config = ConfigDict(extra="forbid")


class ClaimResponse(BaseModel):
    ok: bool
    owner_user_id_hash: str | None = None
    requested_count: int = 0
    claimed_count: int = 0
    degraded_count: int = 0
    rejected_count: int = 0
    reason_codes: dict[str, int] = Field(default_factory=dict)
    reason_code: str | None = None
