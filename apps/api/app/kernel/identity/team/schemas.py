"""M5-3 团队空间 API schemas。

隐私红线：请求 / 响应模型里没有正文字段；沉淀写入只接受元数据 / 引用 / 锚点 /
用户自填短字段。响应里的 owner / team 标识一律以哈希呈现，不回显具名 id 明文之外的内容。
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class CreateTeamRequest(BaseModel):
    team_name: str = Field(..., min_length=1, max_length=60)
    model_config = ConfigDict(extra="forbid")


class AddMemberRequest(BaseModel):
    team_id: str = Field(..., max_length=64)
    member_user_id: str = Field(..., max_length=64)
    model_config = ConfigDict(extra="forbid")


class TeamView(BaseModel):
    team_id: str
    team_name: str
    team_id_hash: str
    status: str


class TeamListResponse(BaseModel):
    ok: bool
    teams: list[TeamView] = Field(default_factory=list)
    reason_code: str | None = None


class CreateTeamResponse(BaseModel):
    ok: bool
    team: TeamView | None = None
    reason_code: str | None = None


class GenericTeamResponse(BaseModel):
    ok: bool
    reason_code: str | None = None
    member_count: int | None = None


class SaveSedimentRequest(BaseModel):
    """服务端沉淀写入：仅元数据 / 引用 / 锚点 / 短字段。extra=forbid 拦截正文。"""

    object_type: Literal["case_favorite", "case_list", "report_template"]
    # team_id 为空 -> 单用户私有；非空 -> 必须是活跃成员，否则降级私有。
    team_id: str | None = Field(default=None, max_length=64)
    workspace_id: str | None = Field(default=None, max_length=64)
    visibility: Literal["private", "team"] = "private"
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


class SaveSedimentResponse(BaseModel):
    ok: bool
    object_id: str | None = None
    tenant_downgraded: bool = False
    reason_code: str | None = None


class SedimentItemView(BaseModel):
    object_id: str
    object_type: str
    visibility: str
    owner_user_id_hash: str
    team_id_hash: str
    case_id: str | None = None
    case_number: str | None = None
    court: str | None = None
    trial_level: str | None = None
    case_cause: str | None = None
    judgment_date: str | None = None
    note: str | None = None
    tag: str | None = None
    label: str | None = None
    list_id: str | None = None
    list_title: str | None = None
    report_id: str | None = None
    source_anchors: list[dict[str, Any]] = Field(default_factory=list)


class ListSedimentRequest(BaseModel):
    team_id: str | None = Field(default=None, max_length=64)
    workspace_id: str | None = Field(default=None, max_length=64)
    object_type: Literal["case_favorite", "case_list", "report_template"] | None = None
    model_config = ConfigDict(extra="forbid")


class ListSedimentResponse(BaseModel):
    ok: bool
    items: list[SedimentItemView] = Field(default_factory=list)
    tenant_downgraded: bool = False
    reason_code: str | None = None
