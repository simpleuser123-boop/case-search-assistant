"""E5-4 statute 检索服务（产品包层，仅依赖 app.kernel 公开面）。

职责（无状态透传 + 法条检索 + 互跳）：
1. /search：接收**已脱敏** SearchProfile 白名单载荷（dict），过 E4-2 后端防御层
   `sanitize_intake_profile_payload`（键级红线 fail-closed + 值级脱敏）做第二道闸，
   再经 `app.kernel.rag.StatuteSearchService.search_statutes` 出 `StatuteRef[]`（带锚点、零正文）。
2. /by-case：凭 case_id 经 `StatuteSearchService.statutes_by_case` 出关联 `StatuteRef[]`（类案→法条）。
3. /cases-by-statute：凭 statute_id 经 `StatuteSearchService.cases_by_statute` 出 `CandidateRef[]`（法条→类案）。

红线：
- 不复制检索主路径、不深引内核内部、不直连 retrieval / rerank / summary / query_processing。
- 不持有持久层句柄；不写库 / 不写搜索历史 / 不落库 SearchProfile / StatuteRef / CandidateRef。
- 法条条文只来自语料（StatuteRef.article_text 由内核服务从语料透传）；service 不生成 / 改写条文。
- 不调用任何服务端 AI 增强抽取（不接任何业务 flag on 路径以外能力）。
- 异常消息只暴露键名、绝不回显键值（原始 PII / 正文 / 条文零进异常 / 日志）。
"""
from __future__ import annotations

from typing import Any, Mapping

# 仅依赖 app.kernel 公开面：法条检索经 rag 服务，脱敏防御经 guardrails 护栏面。
from app.kernel.guardrails import (
    ContractViolationError,
    sanitize_intake_profile_payload,
)
from app.kernel.rag import (
    SearchProfile,
    StatuteCaseRefResult,
    StatuteSearchResult,
    StatuteSearchService,
)


class StatuteQueryService:
    """statute 产品包检索服务：查询/类案 -> StatuteRef[]，法条 -> CandidateRef[]（无状态）。

    依赖经构造函数注入（默认用内核公开面的 StatuteSearchService），便于测试替换；
    本服务不持有任何持久层句柄，不写库 / 不写搜索历史。
    """

    def __init__(
        self,
        *,
        statute_search_service: StatuteSearchService | None = None,
    ) -> None:
        self._statute_search_service = statute_search_service or StatuteSearchService()

    def search_statutes(
        self,
        profile_payload: Mapping[str, Any],
        *,
        mode: str = "standard",
        limit: int = 10,
        query_session_id: str | None = None,
    ) -> StatuteSearchResult:
        """已脱敏 SearchProfile 载荷 -> StatuteRef[]（经内核法条检索服务）。

        步骤：
        1. E4-2 后端防御层二次校验 + 值级脱敏（fail-closed，正文 / PII 键即抛 ContractViolationError）。
        2. 构造 SearchProfile（仅结构化参数，零原始案情）。
        3. 经 StatuteSearchService.search_statutes 出 StatuteRef[]（带 text_id 锚点，零正文）。

        ContractViolationError 由调用方（router）转 400，异常消息只含键名。
        """
        # 第二道闸：键级红线（正文 / PII 键 fail-closed）+ 值级脱敏（残留 PII 占位）。
        sanitized = sanitize_intake_profile_payload(profile_payload)
        profile = SearchProfile(**sanitized)
        return self._statute_search_service.search_statutes(
            profile,
            limit=limit,
            query_session_id=query_session_id,
        )

    def statutes_by_case(
        self,
        case_id: str,
        *,
        limit: int = 10,
        query_session_id: str | None = None,
    ) -> StatuteSearchResult:
        """case_id -> 关联 StatuteRef[]（类案→法条互跳，经内核关联标注）。"""
        return self._statute_search_service.statutes_by_case(
            str(case_id).strip(),
            limit=limit,
            query_session_id=query_session_id,
        )

    def cases_by_statute(
        self,
        statute_id: str,
        *,
        limit: int = 10,
        query_session_id: str | None = None,
    ) -> StatuteCaseRefResult:
        """statute_id -> 关联 CandidateRef[]（法条→类案互跳，白名单七字段 + 锚点，零正文）。"""
        return self._statute_search_service.cases_by_statute(
            str(statute_id).strip(),
            limit=limit,
            query_session_id=query_session_id,
        )


__all__ = ["StatuteQueryService", "ContractViolationError"]
