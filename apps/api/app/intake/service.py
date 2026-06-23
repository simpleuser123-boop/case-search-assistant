"""E4-3 intake 检索服务（产品包层，仅依赖 app.kernel 公开面）。

职责（无状态透传 + 检索）：
1. 接收**已脱敏**的 SearchProfile 白名单载荷（dict），过 E4-2 后端防御层
   `sanitize_intake_profile_payload`（键级红线 fail-closed + 值级脱敏）做第二道闸。
2. 构造 `InternalSearchRequest`，经 `app.kernel.rag.InternalSearchService.search_candidate_refs`
   出 `CandidateRef[]`（零正文）。
3. 不复制检索主路径、不深引内核内部、不直连 retrieval / rerank / summary / query_processing。

红线：
- 不持有持久层句柄；不写库 / 不写搜索历史 / 不落库 SearchProfile / CandidateRef。
- 不调用任何服务端 AI 增强抽取（ENABLE_INTAKE_AI_EXTRACTION 仍 off、不接线）。
- 异常消息只暴露键名、绝不回显键值（原始 PII / 正文零进异常 / 日志）。
"""
from __future__ import annotations

from typing import Any, Mapping

# 仅依赖 app.kernel 公开面：检索经 rag 服务，脱敏防御经 guardrails 护栏面。
from app.kernel.guardrails import (
    ContractViolationError,
    sanitize_intake_profile_payload,
)
from app.kernel.rag import (
    InternalSearchRequest,
    InternalSearchResult,
    InternalSearchService,
    SearchProfile,
)


class IntakeSearchService:
    """intake 产品包检索服务：已脱敏 SearchProfile -> CandidateRef[]（无状态）。

    依赖经构造函数注入（默认用内核公开面的 InternalSearchService），便于测试替换；
    本服务不持有任何持久层句柄，不写库 / 不写搜索历史。
    """

    def __init__(
        self,
        *,
        internal_search_service: InternalSearchService | None = None,
    ) -> None:
        self._internal_search_service = internal_search_service or InternalSearchService()

    def search_candidate_refs(
        self,
        profile_payload: Mapping[str, Any],
        *,
        mode: str = "standard",
        limit: int = 10,
        query_session_id: str | None = None,
    ) -> InternalSearchResult:
        """已脱敏 SearchProfile 载荷 -> CandidateRef[]（经 E3 内部检索服务）。

        步骤：
        1. E4-2 后端防御层二次校验 + 值级脱敏（fail-closed，正文 / PII 键即抛 ContractViolationError）。
        2. 构造 SearchProfile + InternalSearchRequest（仅结构化参数，零原始案情）。
        3. 经 InternalSearchService.search_candidate_refs 出 CandidateRef[]（零正文）。

        ContractViolationError 由调用方（router）转 400，异常消息只含键名。
        """
        # 第二道闸：键级红线（正文 / PII 键 fail-closed）+ 值级脱敏（残留 PII 占位）。
        sanitized = sanitize_intake_profile_payload(profile_payload)

        include_relaxed_recall = mode == "expanded"
        request = InternalSearchRequest(
            profile=SearchProfile(**sanitized),
            mode="expanded" if include_relaxed_recall else "standard",
            limit=limit,
            include_relaxed_recall=include_relaxed_recall,
        )
        return self._internal_search_service.search_candidate_refs(
            request,
            query_session_id=query_session_id,
        )


__all__ = ["IntakeSearchService", "ContractViolationError"]
