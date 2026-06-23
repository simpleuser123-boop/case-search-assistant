"""M5-4 权限分级 API schemas。

隐私红线：请求 / 响应模型里没有正文字段；审计响应里 actor / object 一律以哈希呈现。
角色 / 授权等级只接受短枚举（viewer/editor[/owner]），extra=forbid 拦截多余字段。
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class AssignRoleRequest(BaseModel):
    team_id: str = Field(..., max_length=64)
    member_user_id: str = Field(..., max_length=64)
    role: Literal["owner", "editor", "viewer"]
    model_config = ConfigDict(extra="forbid")


class GrantRequest(BaseModel):
    object_id: str = Field(..., max_length=64)
    grantee_user_id: str = Field(..., max_length=64)
    # 不允许直接授予 owner：owner 由对象归属决定。
    permission_level: Literal["editor", "viewer"]
    model_config = ConfigDict(extra="forbid")


class RevokeRequest(BaseModel):
    object_id: str = Field(..., max_length=64)
    grantee_user_id: str = Field(..., max_length=64)
    model_config = ConfigDict(extra="forbid")


class ReadObjectRequest(BaseModel):
    object_id: str = Field(..., max_length=64)
    model_config = ConfigDict(extra="forbid")


class GenericPermissionResponse(BaseModel):
    ok: bool
    reason_code: str | None = None


class ReadObjectResponse(BaseModel):
    ok: bool
    reason_code: str | None = None
    effective_level: str | None = None
    # 脱敏沉淀视图（零正文）；鉴权未通过时为 None。
    object: dict | None = None


class AuditItem(BaseModel):
    action: str
    result: str
    reason_code: str
    permission_level: str | None = None
    object_id_hash: str | None = None
    actor_user_id_hash: str


class AuditListResponse(BaseModel):
    ok: bool
    items: list[AuditItem] = Field(default_factory=list)
