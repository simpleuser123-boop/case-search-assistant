"""E4-3 intake 端点请求 / 响应 schema（白名单 + extra=forbid，零正文）。

请求体 = 已脱敏 SearchProfile 白名单五字段（+ 仅结构化检索参数 mode/limit）。
- extra="forbid"：任何非白名单键（含 raw_case / raw_query / name / id_card / phone /
  address / email / full_text / content 等 PII / 正文型键）在 pydantic 层即 422 拒绝（第一道闸）。
- E4-2 后端防御层（sanitize_intake_profile_payload）在 service 层做第二道闸（键级红线 + 值级脱敏）。

响应体 = CandidateRef[] 的视图（白名单七字段 + source_anchors，零正文）+ 必要降级信息。
- 绝不含 summary / highlights / matched_text / chunk_text / full_text / content / body。
- 不回传 query_text / 原始案情 / timings 明细（避免承载或反射正文）。
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# 检索模式：standard（默认）/ expanded（放宽召回）。仅结构化透传，不改召回 / 排序策略。
IntakeSearchMode = Literal["standard", "expanded"]


class IntakeSearchRequest(BaseModel):
    """intake 检索请求：已脱敏 SearchProfile 白名单五字段 + 结构化检索参数。

    红线：extra="forbid" —— 任何非白名单 / PII / 正文型键在模型层即被拒绝（fail-closed）。
    本模型**只**承载已脱敏短查询与结构化要素，绝不接收原始口语化案情。
    """

    model_config = ConfigDict(extra="forbid")

    # --- SearchProfile 白名单五字段（与 E-1 / E3-1 / E4-1 逐字段一致）---
    case_cause: str | None = None
    region: str | None = None
    trial_level_preference: str | None = None
    dispute_focus_keywords: list[str] = Field(default_factory=list)
    query_text: str | None = None

    # --- 结构化检索参数（仅透传，不改默认召回 / 排序 / rerank）---
    mode: IntakeSearchMode = "standard"
    limit: int = Field(default=10, ge=1, le=50)


class IntakeSourceAnchorView(BaseModel):
    """来源锚点视图（结构化引用，非正文）。最小合法 = case_id + source_chunk_id。"""

    model_config = ConfigDict(extra="forbid")

    case_id: str
    source_chunk_id: str
    anchor_type: str | None = None


class IntakeCandidateRefView(BaseModel):
    """CandidateRef 视图（白名单七字段 + 锚点，零正文）。

    字段集与 E-1 CandidateRef 白名单逐字段一致；extra="forbid" 兜底拒绝正文型键。
    """

    model_config = ConfigDict(extra="forbid")

    case_id: str
    case_number: str | None = None
    court: str | None = None
    trial_level: str | None = None
    case_cause: str | None = None
    judgment_date: str | None = None
    source_anchors: list[IntakeSourceAnchorView] = Field(min_length=1)


class IntakeSearchResponse(BaseModel):
    """intake 检索响应：CandidateRef[]（零正文）+ 降级信息（不含正文）。"""

    model_config = ConfigDict(extra="forbid")

    query_session_id: str
    candidate_refs: list[IntakeCandidateRefView] = Field(default_factory=list)
    candidate_count: int = 0
    degraded: bool = False
    degraded_reasons: list[str] = Field(default_factory=list)
    search_mode: IntakeSearchMode = "standard"


__all__ = [
    "IntakeSearchMode",
    "IntakeSearchRequest",
    "IntakeSourceAnchorView",
    "IntakeCandidateRefView",
    "IntakeSearchResponse",
]
