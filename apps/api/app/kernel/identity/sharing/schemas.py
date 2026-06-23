"""M5-5 同步与共享 API schemas。

隐私红线：请求 / 响应里没有正文字段。同步写入只接受元数据 / 引用 / 锚点 /
用户自填短字段（与 M5-3 SaveSedimentRequest 同一白名单，extra=forbid 拦截正文）。
响应里的 owner / team 标识一律以哈希呈现。
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class SyncSedimentRequest(BaseModel):
    """本地沉淀同步：仅元数据 / 引用 / 锚点 / 短字段。extra=forbid 拦截正文。

    注意：同步永远写 owner 私有，因此**不接受 team_id / visibility**——
    共享是另一个显式动作（/share）。
    """

    object_type: Literal["case_favorite", "case_list", "report_template"]
    case_id: str | None = Field(default=None, max_length=120)
    case_number: str | None = Field(default=None, max_length=120)
    court: str | None = Field(default=None, max_length=120)
    trial_level: str | None = Field(default=None, max_length=40)
    case_cause: str | None = Field(default=None, max_length=120)
    judgment_date: str | None = Field(default=None, max_length=40)
    source_anchors: list[dict[str, Any]] = Field(default_factory=list)
    note: str | None = Field(default=None, max_length=200)
    tag: str | None = Field(default=None, max_length=80)
    label: str | None = Field(default=None, max_length=80)
    list_id: str | None = Field(default=None, max_length=120)
    list_title: str | None = Field(default=None, max_length=120)
    report_id: str | None = Field(default=None, max_length=120)

    model_config = ConfigDict(extra="forbid")


class SyncSedimentResponse(BaseModel):
    ok: bool
    object_id: str | None = None
    visibility: str = "private"
    reason_code: str | None = None


class ShareRequest(BaseModel):
    object_id: str = Field(..., max_length=64)
    team_id: str = Field(..., max_length=64)
    model_config = ConfigDict(extra="forbid")


class UnshareRequest(BaseModel):
    object_id: str = Field(..., max_length=64)
    model_config = ConfigDict(extra="forbid")


class ShareResponse(BaseModel):
    ok: bool
    share_id: str | None = None
    visibility: str = "private"
    anchor_count: int = 0
    reason_code: str | None = None


class ShareItemView(BaseModel):
    object_id: str
    object_type: str
    visibility: str
    owner_user_id_hash: str
    shared_with_team_id_hash: str
    anchor_count: int
    status: str


class ListTeamSharesRequest(BaseModel):
    team_id: str = Field(..., max_length=64)
    model_config = ConfigDict(extra="forbid")


class ShareListResponse(BaseModel):
    ok: bool
    items: list[ShareItemView] = Field(default_factory=list)
    reason_code: str | None = None
