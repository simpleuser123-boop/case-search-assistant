"""E6-2 drafting 端点请求 / 响应 schema（白名单 + extra=forbid，零起草/裁判正文）。

请求体均严格白名单 + extra="forbid"（第一道闸）：
- 创建 / 更新只接收 structure_skeleton(标题列表) + candidate_refs + 可选 statute_refs +
  note / tag。任何非白名单键（含起草正文 / 裁判正文 / PII / 胜负结论型键）在 pydantic
  层即 422；service 层再过 E6-1 sanitize_draft_descriptor 做第二道闸（缺锚点引用丢弃）。

响应体为已收敛的 DraftDescriptor 视图（零正文）：
- structure_skeleton 仅标题清单；candidate_refs / statute_refs 仅白名单字段 + 锚点；
  note / tag 短字段；持久层元数据（draft_id / owner / visibility / 时间戳）。
- 绝不含起草正文 / 段落正文 / 结论 / 胜负判断 / 裁判正文。

引用字段在请求侧用「宽松 dict」承载，交由 service 层 sanitize_draft_descriptor 逐项
收敛（缺锚点丢弃、禁止键 fail-closed），避免请求 schema 与契约模型口径漂移。
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


# --- 请求体（白名单 + extra=forbid，第一道闸）-----------------------------------

class DraftCreateRequest(BaseModel):
    """创建 DraftDescriptor 请求：仅结构骨架(标题) + 引用 + 短字段。

    红线：extra="forbid" —— 起草正文 / 裁判正文 / PII / 胜负结论型 / 任何未知键在模型层即 422。
    引用用 list[dict] 承载，交由 service 层 sanitize_draft_descriptor 逐项收敛。
    """

    model_config = ConfigDict(extra="forbid")

    structure_skeleton: list[str] = Field(min_length=1)
    candidate_refs: list[dict[str, Any]] = Field(default_factory=list)
    statute_refs: list[dict[str, Any]] = Field(default_factory=list)
    note: str | None = None
    tag: str | None = None


class DraftUpdateRequest(BaseModel):
    """更新 DraftDescriptor 请求：仍只存元数据/引用/短字段。

    全量替换骨架/引用/短字段（仍经 sanitize）；不接受 visibility / team_id（E6-2 默认私有不放权）。
    """

    model_config = ConfigDict(extra="forbid")

    structure_skeleton: list[str] = Field(min_length=1)
    candidate_refs: list[dict[str, Any]] = Field(default_factory=list)
    statute_refs: list[dict[str, Any]] = Field(default_factory=list)
    note: str | None = None
    tag: str | None = None


# --- 响应体视图（契约对象视图，零正文）------------------------------------------

class DraftSourceAnchorView(BaseModel):
    """类案来源锚点视图（结构化引用，非裁判正文）。最小合法 = case_id + source_chunk_id。"""

    model_config = ConfigDict(extra="forbid")

    case_id: str
    source_chunk_id: str
    anchor_type: str | None = None


class DraftCandidateRefView(BaseModel):
    """文书引用的类案视图（= CandidateRef 白名单七字段 + 锚点，零裁判正文）。"""

    model_config = ConfigDict(extra="forbid")

    case_id: str
    case_number: str | None = None
    court: str | None = None
    trial_level: str | None = None
    case_cause: str | None = None
    judgment_date: str | None = None
    source_anchors: list[DraftSourceAnchorView] = Field(min_length=1)


class DraftStatuteAnchorView(BaseModel):
    """法条来源锚点视图（结构化引用，非条文正文）。最小合法 = text_id 非空。"""

    model_config = ConfigDict(extra="forbid")

    text_id: str
    law_name: str | None = None
    article_no: str | None = None
    anchor_type: str | None = None


class DraftStatuteRefView(BaseModel):
    """文书引用的法条视图（白名单 + 锚点；article_text 若有只来自语料、不得模型生成）。"""

    model_config = ConfigDict(extra="forbid")

    statute_id: str
    law_name: str
    article_no: str | None = None
    statute_anchors: list[DraftStatuteAnchorView] = Field(min_length=1)
    article_text: str | None = None
    source_corpus: str | None = None
    effective_status: str | None = None
    related_case_refs: list[DraftCandidateRefView] = Field(default_factory=list)


class DraftDescriptorView(BaseModel):
    """已收敛的 DraftDescriptor 响应视图（只组装不起草，零正文）。"""

    model_config = ConfigDict(extra="forbid")

    draft_id: str
    structure_skeleton: list[str]
    candidate_refs: list[DraftCandidateRefView] = Field(default_factory=list)
    statute_refs: list[DraftStatuteRefView] = Field(default_factory=list)
    note: str | None = None
    tag: str | None = None
    owner_user_id: str
    team_id: str | None = None
    visibility: str = "private"
    status: str = "active"
    created_at: str | None = None
    updated_at: str | None = None


class DraftListResponse(BaseModel):
    """DraftDescriptor 列表响应：仅当前租户可见对象（零正文）。"""

    model_config = ConfigDict(extra="forbid")

    drafts: list[DraftDescriptorView] = Field(default_factory=list)
    draft_count: int = 0


__all__ = [
    "DraftCreateRequest",
    "DraftUpdateRequest",
    "DraftSourceAnchorView",
    "DraftCandidateRefView",
    "DraftStatuteAnchorView",
    "DraftStatuteRefView",
    "DraftDescriptorView",
    "DraftListResponse",
]
