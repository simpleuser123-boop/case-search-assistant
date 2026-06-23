"""E5-4 法条检索端 statute 产品能力包（E 系列第二个产品包）。

定位（文档 16 §4.1 第 5 契约对象 / 文档 17 §3.5 / 文档 20 §1~§3 / E5-4）：
- statute 是 E 系列多产品生态的「法条检索端」后端落地：把查询 / 已脱敏 SearchProfile
  透传给内核法条检索服务，产出 StatuteRef[]（带 text_id 锚点、零裁判正文、条文只来自语料），
  并支持法条↔类案双向互跳（类案→法条出 StatuteRef[]，法条→类案出 CandidateRef[]）。
- 本包是**产品能力包**，不是内核：只依赖 `app.kernel` 公开面
  （`app.kernel.rag` 的 StatuteSearchService / 契约模型 + `app.kernel.guardrails`
  的 E4-2 脱敏防御层 / ContractViolationError），**绝不**直连
  retrieval / rerank / summary / query_processing，**绝不** import 其它产品包
  （intake / drafting / casebook）。

第一性红线（E5-4）：
- 原始案情零上送：端点只接收已脱敏 SearchProfile 白名单五字段 / 结构化锚点；raw_case /
  raw_query / PII / 裁判正文 / 模型生成条文型键一律在 schema(extra=forbid) + E4-2 防御层双闸拒绝。
- 法条条文必锚定语料、不杜撰：StatuteRef 经内核 sanitize_statute_ref 收敛，条文只来自语料、
  带 text_id 锚点；缺锚点 fail-closed 丢弃，绝不展示无来源条文。
- ENABLE_STATUTE_SEARCH 默认 false：关闭时端点 403 安全降级（与 E-1 / intake 关闭语义一致）。
- 互跳只走契约对象：两侧都不携带对侧正文（法条→类案出 CandidateRef 白名单七字段 + 锚点）。
- 无状态透传：不持久化 SearchProfile / StatuteRef / CandidateRef、不写搜索历史、不落库。
- 日志只写 query_session_id / 计数 / degraded_reasons；绝不写 query_text / 原始案情 /
  裁判正文 / 条文。
"""
from __future__ import annotations

from app.statute.router import (
    STATUTE_PROFILE_REJECTED_CODE,
    STATUTE_SEARCH_DISABLED_CODE,
    router,
    set_statute_query_service_for_test,
)
from app.statute.schemas import (
    StatuteByCaseRequest,
    StatuteCandidateRefView,
    StatuteCasesByStatuteRequest,
    StatuteCasesResponse,
    StatuteRefView,
    StatuteSearchRequest,
    StatuteSearchResponse,
)
from app.statute.service import StatuteQueryService

__all__ = [
    "router",
    "set_statute_query_service_for_test",
    "STATUTE_SEARCH_DISABLED_CODE",
    "STATUTE_PROFILE_REJECTED_CODE",
    "StatuteSearchRequest",
    "StatuteByCaseRequest",
    "StatuteCasesByStatuteRequest",
    "StatuteSearchResponse",
    "StatuteCasesResponse",
    "StatuteRefView",
    "StatuteCandidateRefView",
    "StatuteQueryService",
]
