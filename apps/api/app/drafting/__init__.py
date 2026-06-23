"""E6-2 文书工作台 drafting 产品能力包（E 系列第三个产品包）。

定位（文档 16 §4.1 第 3 契约对象 DraftDescriptor / 文档 17 §3.3 / 文档 21 §1~§3 / E6-2）：
- drafting 是 E 系列多产品生态的「文书工作台」后端落地：把检索 / 清单沉淀的
  CandidateRef（类案）+ 可选 StatuteRef（法条，经 E5 互跳）**组装**为 DraftDescriptor
  （结构骨架=段落标题 + 锚定引用 + 用户短字段），并持久化（只存元数据/引用/短字段）。
- 本包是**产品能力包**，不是内核：只依赖 `app.kernel` 公开面
  （`app.kernel.guardrails` 的 DraftDescriptor / sanitize_draft_descriptor /
  ContractViolationError + `app.kernel.identity` 的 TenantContext / AuthResult）
  与既有持久层范式（SQLModel + app.core.db.engine），**绝不**直连
  retrieval / rerank / summary / query_processing，**绝不** import 其它产品包
  （intake / statute / casebook）。

第一性红线（E6-2）：
- **只组装不起草**：service 绝不生成任何段落正文 / 结论 / 胜负判断 / 诉讼结果预测；
  DraftDescriptor 只承载结构骨架(标题) + 锚定引用 + 短字段。
- **引用必带锚点、无锚点不进交付物**：candidate_refs / statute_refs 经 sanitize 逐项收敛，
  缺锚点引用 fail-closed 丢弃。
- **持久层零正文**：draft_descriptor 表只存元数据/引用/结构骨架(标题)/短字段/结构化关系，
  绝不含起草正文 / 裁判正文 / 候选 / chunk 正文 / 摘要 / 原始案情 / 凭据列。
- **ENABLE_DRAFTING 默认 false**：关闭时端点 403 安全降级（与 intake / statute 关闭语义一致）。
- **多租户强隔离 + 对象级鉴权 + 默认 private**：所有读取强制租户过滤，写 / 更新经 owner +
  租户一致性校验；越权读写拒绝（404 / 不泄露他人草稿）。
- **日志脱敏**：只写 user_id_hash / draft_id_hash / 计数 / note 元信息(长度+hash) / reason_code；
  绝不写起草正文 / 裁判正文 / 原始案情 / note 全文。
"""
from __future__ import annotations

from app.drafting.router import (
    DRAFTING_DISABLED_CODE,
    DRAFTING_REJECTED_CODE,
    DRAFTING_REQUIRES_LOGIN_CODE,
    DRAFT_NOT_FOUND_CODE,
    router as drafting_router,
    set_drafting_service_for_test,
)
from app.drafting.schemas import (
    DraftCandidateRefView,
    DraftCreateRequest,
    DraftDescriptorView,
    DraftListResponse,
    DraftStatuteRefView,
    DraftUpdateRequest,
)
from app.drafting.service import DraftingService
from app.drafting.store import DraftStore

__all__ = [
    "drafting_router",
    "set_drafting_service_for_test",
    "DRAFTING_DISABLED_CODE",
    "DRAFTING_REJECTED_CODE",
    "DRAFTING_REQUIRES_LOGIN_CODE",
    "DRAFT_NOT_FOUND_CODE",
    "DraftCreateRequest",
    "DraftUpdateRequest",
    "DraftDescriptorView",
    "DraftListResponse",
    "DraftCandidateRefView",
    "DraftStatuteRefView",
    "DraftingService",
    "DraftStore",
]
