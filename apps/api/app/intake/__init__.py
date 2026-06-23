"""E4-3 案情录入端 intake 产品能力包（E 系列第一个产品包）。

定位（文档 16 §3~§6 / 文档 17 §3 / 文档 19 §6）：
- intake 是 E 系列多产品生态的「案情录入端」后端落地：把已脱敏的 SearchProfile
  透传给 E3 内部检索服务，产出 CandidateRef[]（零正文），供录入端前端（E4-4）消费。
- 本包是**产品能力包**，不是内核：只依赖 `app.kernel` 公开面
  （`app.kernel.rag` 的 InternalSearchService / 契约模型 + `app.kernel.guardrails`
  的 E4-2 脱敏防御层），**绝不**直连 retrieval / rerank / summary / query_processing，
  **绝不** import 其它产品包（statute / drafting / casebook）。

第一性红线（E4-3）：
- 原始案情零上送：端点只接收已脱敏 SearchProfile 白名单五字段；raw_case / raw_query /
  PII / 正文型键一律在 schema(extra=forbid) + E4-2 后端防御层双闸拒绝 / 移除。
- ENABLE_INTAKE 默认 false：关闭时端点 403 安全降级（与 E-1 关闭语义一致）。
- ENABLE_INTAKE_AI_EXTRACTION 仍 off、不接线：端点绝不调用任何服务端 AI 增强抽取。
- 无状态透传：不持久化 SearchProfile / CandidateRef、不写搜索历史、不落库。
- 日志只写 query_session_id / input_hash / 计数 / degraded_reasons；绝不写 query_text /
  原始案情 / PII。
"""
from __future__ import annotations

from app.intake.router import (
    INTAKE_DISABLED_CODE,
    router,
    set_intake_search_service_for_test,
)
from app.intake.schemas import (
    IntakeCandidateRefView,
    IntakeSearchRequest,
    IntakeSearchResponse,
)
from app.intake.service import IntakeSearchService

__all__ = [
    "router",
    "set_intake_search_service_for_test",
    "INTAKE_DISABLED_CODE",
    "IntakeSearchRequest",
    "IntakeSearchResponse",
    "IntakeCandidateRefView",
    "IntakeSearchService",
]
