"""E7-2 casebook 端点请求 / 响应 schema（白名单 + extra=forbid，零裁判/起草正文）。

请求体均严格白名单 + extra="forbid"（第一道闸）：
- 创建 / 更新只接收 search_profile_summary(脱敏子集) + candidate_refs + draft_descriptors +
  title / note / tag（+ 更新可选 visibility）。任何非白名单键（含裁判正文 / 起草正文 /
  原始案情 / PII / 胜负结论型键）在 pydantic 层即 422；service 层再过 E7-1
  sanitize_case_folder 做第二道闸（缺锚点引用丢弃、原始案情 fail-closed）。

响应体为已收敛的 CaseFolder 视图（零正文）：
- search_profile_summary 仅 SearchProfile 脱敏白名单子集；candidate_refs / draft_descriptors
  仅白名单字段 + 锚点；title / note / tag 短字段；持久层元数据（case_folder_id / owner /
  visibility / 时间戳）。
- 绝不含裁判正文 / 起草正文 / 段落正文 / 结论 / 胜负判断 / 案件综述正文 / 原始案情。

引用字段在请求侧用「宽松 dict」承载，交由 service 层 sanitize_case_folder 逐项收敛
（缺锚点丢弃、禁止键 fail-closed），避免请求 schema 与契约模型口径漂移。
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


# --- 请求体（白名单 + extra=forbid，第一道闸）-----------------------------------

class CaseFolderCreateRequest(BaseModel):
    """创建 CaseFolder 请求：仅脱敏摘要 + 引用 + 短字段。

    红线：extra="forbid" —— 裁判正文 / 起草正文 / 原始案情 / PII / 胜负结论型 / 任何未知键
    在模型层即 422。引用用 list[dict] 承载，交由 service 层 sanitize_case_folder 逐项收敛。
    本步默认 visibility=private，不接收 visibility（创建恒私有；共享切换走 E7-4 / PUT）。
    """

    model_config = ConfigDict(extra="forbid")

    search_profile_summary: dict[str, Any] | None = None
    candidate_refs: list[dict[str, Any]] = Field(default_factory=list)
    draft_descriptors: list[dict[str, Any]] = Field(default_factory=list)
    title: str | None = None
    note: str | None = None
    tag: str | None = None


class CaseFolderUpdateRequest(BaseModel):
    """更新 CaseFolder 请求：仍只存元数据/引用/短字段。

    全量替换摘要/引用/短字段（仍经 sanitize）；本步允许 visibility 写入（E7-4 细化共享语义），
    其余正文型键仍在模型层即 422。
    """

    model_config = ConfigDict(extra="forbid")

    search_profile_summary: dict[str, Any] | None = None
    candidate_refs: list[dict[str, Any]] = Field(default_factory=list)
    draft_descriptors: list[dict[str, Any]] = Field(default_factory=list)
    title: str | None = None
    note: str | None = None
    tag: str | None = None
    visibility: str | None = None


class CaseFolderShareRequest(BaseModel):
    """E7-4 共享切换请求：只改 visibility 元数据，绝不承载任何正文/引用/摘要。

    红线（沿用 M5 共享语义，只 private|team 两级）：
    - ``visibility`` 用 Literal 约束为 ``private`` / ``team``：``public`` 或任何非法值在
      pydantic 层即 422（杜绝公开级可见性）。
    - 共享到 team 时须显式给出 ``team_id``（owner 须为该 team 活跃成员，否则 404）；
      取消共享（team->private）时 ``team_id`` 可省略（store 端 team_id 一并清空）。
    - extra="forbid"：任何摘要/引用/正文/PII 键在模型层即 422——共享端点零正文承载。
    """

    model_config = ConfigDict(extra="forbid")

    visibility: Literal["private", "team"]
    team_id: str | None = None


# --- 响应体视图（契约对象视图，零正文）------------------------------------------

class CaseFolderSourceAnchorView(BaseModel):
    """类案来源锚点视图（结构化引用，非裁判正文）。最小合法 = case_id + source_chunk_id。"""

    model_config = ConfigDict(extra="forbid")

    case_id: str
    source_chunk_id: str
    anchor_type: str | None = None


class CaseFolderCandidateRefView(BaseModel):
    """协作夹归集的类案视图（= CandidateRef 白名单七字段 + 锚点，零裁判正文）。"""

    model_config = ConfigDict(extra="forbid")

    case_id: str
    case_number: str | None = None
    court: str | None = None
    trial_level: str | None = None
    case_cause: str | None = None
    judgment_date: str | None = None
    source_anchors: list[CaseFolderSourceAnchorView] = Field(min_length=1)


class CaseFolderStatuteAnchorView(BaseModel):
    """法条来源锚点视图（结构化引用，非条文正文）。最小合法 = text_id 非空。"""

    model_config = ConfigDict(extra="forbid")

    text_id: str
    law_name: str | None = None
    article_no: str | None = None
    anchor_type: str | None = None


class CaseFolderStatuteRefView(BaseModel):
    """文书引用内的法条视图（白名单 + 锚点；article_text 若有只来自语料、不得模型生成）。"""

    model_config = ConfigDict(extra="forbid")

    statute_id: str
    law_name: str
    article_no: str | None = None
    statute_anchors: list[CaseFolderStatuteAnchorView] = Field(min_length=1)
    article_text: str | None = None
    source_corpus: str | None = None
    effective_status: str | None = None
    related_case_refs: list[CaseFolderCandidateRefView] = Field(default_factory=list)


class CaseFolderDraftDescriptorView(BaseModel):
    """协作夹归集的文书骨架视图（= DraftDescriptor 结构骨架 + 锚定引用 + 短字段，零起草正文）。"""

    model_config = ConfigDict(extra="forbid")

    draft_id: str | None = None
    structure_skeleton: list[str] = Field(default_factory=list)
    candidate_refs: list[CaseFolderCandidateRefView] = Field(default_factory=list)
    statute_refs: list[CaseFolderStatuteRefView] = Field(default_factory=list)
    note: str | None = None
    tag: str | None = None


class CaseFolderView(BaseModel):
    """已收敛的 CaseFolder 响应视图（只归集不起草，零正文）。"""

    model_config = ConfigDict(extra="forbid")

    case_folder_id: str
    owner_user_id: str
    team_id: str | None = None
    visibility: str = "private"
    search_profile_summary: dict[str, Any] | None = None
    candidate_refs: list[CaseFolderCandidateRefView] = Field(default_factory=list)
    draft_descriptors: list[CaseFolderDraftDescriptorView] = Field(default_factory=list)
    title: str | None = None
    note: str | None = None
    tag: str | None = None
    status: str = "active"
    created_at: str | None = None
    updated_at: str | None = None


class CaseFolderListResponse(BaseModel):
    """CaseFolder 列表响应：仅当前租户可见对象（零正文）。"""

    model_config = ConfigDict(extra="forbid")

    folders: list[CaseFolderView] = Field(default_factory=list)
    folder_count: int = 0


__all__ = [
    "CaseFolderCreateRequest",
    "CaseFolderUpdateRequest",
    "CaseFolderShareRequest",
    "CaseFolderSourceAnchorView",
    "CaseFolderCandidateRefView",
    "CaseFolderStatuteAnchorView",
    "CaseFolderStatuteRefView",
    "CaseFolderDraftDescriptorView",
    "CaseFolderView",
    "CaseFolderListResponse",
]
