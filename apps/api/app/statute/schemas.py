"""E5-4 statute 端点请求 / 响应 schema（白名单 + extra=forbid，零裁判正文 / 零杜撰条文）。

请求体（三个端点）均严格白名单 + extra="forbid"（第一道闸）：
- /search        = 已脱敏 SearchProfile 白名单五字段（+ 结构化检索参数 mode/limit）。
- /by-case       = 类案锚点（case_id 必填）+ limit；类案→法条互跳。
- /cases-by-statute = 法条锚点（statute_id 必填）+ limit；法条→类案互跳。
任何非白名单键（含 raw_case / raw_query / PII / 裁判正文 / 模型生成条文型键）在 pydantic
层即 422 拒绝；service 层再过 guardrails 防御层做第二道闸。

响应体均为契约对象视图（零正文）：
- StatuteRefView：statute_id / law_name / article_no / statute_anchors（必带 text_id）/
  article_text?（只来自语料、不得由模型生成）/ source_corpus? / effective_status? /
  related_case_refs?（CandidateRef 同款白名单七字段，无裁判正文）。
- CandidateRef 互跳视图：白名单七字段 + source_anchors，绝不含 summary / highlight / 正文。
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# 检索模式：standard（默认）/ expanded（放宽召回）。仅结构化透传，不改召回 / 排序策略。
StatuteSearchMode = Literal["standard", "expanded"]


# --- 请求体（白名单 + extra=forbid，第一道闸）-----------------------------------

class StatuteSearchRequest(BaseModel):
    """法条检索请求：已脱敏 SearchProfile 白名单五字段 + 结构化检索参数。

    红线：extra="forbid" —— 任何非白名单 / PII / 正文 / 模型生成条文型键在模型层即被拒绝。
    本模型只承载已脱敏短查询与结构化要素，绝不接收原始口语化案情 / 裁判正文。
    """

    model_config = ConfigDict(extra="forbid")

    # --- SearchProfile 白名单五字段（与 E-1 / E3-1 / E4-1 逐字段一致）---
    case_cause: str | None = None
    region: str | None = None
    trial_level_preference: str | None = None
    dispute_focus_keywords: list[str] = Field(default_factory=list)
    query_text: str | None = None

    # --- 结构化检索参数（仅透传，不改默认召回 / 排序 / rerank）---
    mode: StatuteSearchMode = "standard"
    limit: int = Field(default=10, ge=1, le=50)


class StatuteByCaseRequest(BaseModel):
    """类案→法条互跳请求：仅承载类案锚点（case_id 必填）+ 结构化参数。

    红线：extra="forbid" —— 不接收裁判正文 / summary / 原始案情；只凭 case_id 做关联标注互跳。
    """

    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(min_length=1)
    mode: StatuteSearchMode = "standard"
    limit: int = Field(default=10, ge=1, le=50)


class StatuteCasesByStatuteRequest(BaseModel):
    """法条→类案互跳请求：仅承载法条锚点（statute_id 必填）+ 结构化参数。

    红线：extra="forbid" —— 不接收法条条文正文 / 模型生成条文；只凭 statute_id 做关联标注互跳。
    """

    model_config = ConfigDict(extra="forbid")

    statute_id: str = Field(min_length=1)
    mode: StatuteSearchMode = "standard"
    limit: int = Field(default=10, ge=1, le=50)


# --- 响应体视图（契约对象视图，零正文）------------------------------------------

class StatuteAnchorView(BaseModel):
    """法条来源锚点视图（结构化引用，非条文正文）。最小合法 = text_id 非空。"""

    model_config = ConfigDict(extra="forbid")

    text_id: str
    law_name: str | None = None
    article_no: str | None = None
    anchor_type: str | None = None


class StatuteSourceAnchorView(BaseModel):
    """互跳类案来源锚点视图（结构化引用，非裁判正文）。最小合法 = case_id + source_chunk_id。"""

    model_config = ConfigDict(extra="forbid")

    case_id: str
    source_chunk_id: str
    anchor_type: str | None = None


class StatuteRelatedCaseView(BaseModel):
    """StatuteRef.related_case_refs 视图（= CandidateRef 白名单七字段，零裁判正文）。"""

    model_config = ConfigDict(extra="forbid")

    case_id: str
    case_number: str | None = None
    court: str | None = None
    trial_level: str | None = None
    case_cause: str | None = None
    judgment_date: str | None = None
    source_anchors: list[StatuteSourceAnchorView] = Field(min_length=1)


class StatuteRefView(BaseModel):
    """StatuteRef 视图（白名单 + 锚点，条文只来自语料、不得由模型生成）。

    statute_anchors 必带非空 text_id（无锚点不展示）；article_text 若有只来自法条语料。
    extra="forbid" 兜底拒绝裁判正文 / 模型生成条文型键。
    """

    model_config = ConfigDict(extra="forbid")

    statute_id: str
    law_name: str
    article_no: str | None = None
    statute_anchors: list[StatuteAnchorView] = Field(min_length=1)
    article_text: str | None = None
    source_corpus: str | None = None
    effective_status: str | None = None
    related_case_refs: list[StatuteRelatedCaseView] = Field(default_factory=list)


class StatuteCandidateRefView(BaseModel):
    """法条→类案互跳的 CandidateRef 视图（白名单七字段 + 锚点，零裁判正文）。"""

    model_config = ConfigDict(extra="forbid")

    case_id: str
    case_number: str | None = None
    court: str | None = None
    trial_level: str | None = None
    case_cause: str | None = None
    judgment_date: str | None = None
    source_anchors: list[StatuteSourceAnchorView] = Field(min_length=1)


class StatuteSearchResponse(BaseModel):
    """法条检索 / 类案→法条互跳响应：StatuteRef[]（零正文）+ 降级信息（不含正文）。"""

    model_config = ConfigDict(extra="forbid")

    query_session_id: str
    statute_refs: list[StatuteRefView] = Field(default_factory=list)
    statute_count: int = 0
    degraded: bool = False
    degraded_reasons: list[str] = Field(default_factory=list)
    search_mode: StatuteSearchMode = "standard"


class StatuteCasesResponse(BaseModel):
    """法条→类案互跳响应：CandidateRef[]（零正文）+ 降级信息（不含正文）。"""

    model_config = ConfigDict(extra="forbid")

    query_session_id: str
    candidate_refs: list[StatuteCandidateRefView] = Field(default_factory=list)
    candidate_count: int = 0
    degraded: bool = False
    degraded_reasons: list[str] = Field(default_factory=list)
    search_mode: StatuteSearchMode = "standard"


__all__ = [
    "StatuteSearchMode",
    "StatuteSearchRequest",
    "StatuteByCaseRequest",
    "StatuteCasesByStatuteRequest",
    "StatuteAnchorView",
    "StatuteSourceAnchorView",
    "StatuteRelatedCaseView",
    "StatuteRefView",
    "StatuteCandidateRefView",
    "StatuteSearchResponse",
    "StatuteCasesResponse",
]
