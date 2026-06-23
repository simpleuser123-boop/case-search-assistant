"""M5-6 批量导入 API schemas。

隐私红线：
- 导入项 schema（ImportItem）extra=forbid：请求体里出现任何非白名单键（含正文键）
  -> 422，绝不入库；这是 schema 层第一道拦截，service/validation 层为第二道。
- 响应里没有正文字段；owner / job 标识以哈希或引用呈现，逐项只回 case_id + 短 reason code。
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ImportItem(BaseModel):
    """单个导入项：只允许元数据 / 引用 / 来源锚点 / 用户自填短字段。

    extra=forbid 拦截任何正文 / 凭据 / 未知键。
    """

    case_id: str = Field(..., max_length=120)
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


class BulkImportRequest(BaseModel):
    source_type: Literal["case_list_file", "csv", "existing_list"]
    object_type: Literal["case_favorite", "case_list", "report_template"]
    items: list[ImportItem] = Field(default_factory=list, max_length=500)
    # team_id 仅用于作业账本留痕；导入对象本身仍默认 owner 私有（不据此放权）。
    team_id: str | None = Field(default=None, max_length=64)

    model_config = ConfigDict(extra="forbid")


class ItemOutcomeView(BaseModel):
    case_id: str | None = None
    ok: bool
    reason_code: str
    object_id: str | None = None


class BulkImportResponse(BaseModel):
    ok: bool
    import_job_id: str | None = None
    import_status: str = "failed"
    item_count: int = 0
    imported_count: int = 0
    rejected_count: int = 0
    duplicate_count: int = 0
    degrade_reason: str | None = None
    outcomes: list[ItemOutcomeView] = Field(default_factory=list)


class JobView(BaseModel):
    import_job_id: str
    source_type: str
    item_count: int
    imported_count: int
    rejected_count: int
    duplicate_count: int
    import_status: str
    degrade_reason: str | None = None
    owner_user_id_hash: str
    team_id_hash: str


class JobListResponse(BaseModel):
    ok: bool
    items: list[JobView] = Field(default_factory=list)
    reason_code: str | None = None
