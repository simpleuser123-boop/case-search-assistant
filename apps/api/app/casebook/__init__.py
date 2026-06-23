"""E7-2 案件协作工作台 casebook 产品能力包（E 系列第四个产品包）。

定位（文档 16 §4.1 第 4 契约对象 CaseFolder / 文档 17 §3.4 / 文档 22 §1~§3 / E7-2）：
- casebook 是 E 系列多产品生态的「案件协作工作台」后端落地：把录入端 SearchProfile 摘要(脱敏) +
  检索/清单沉淀的 CandidateRef（类案）+ 文书工作台 DraftDescriptor（文书骨架）**归集**为
  CaseFolder（脱敏摘要 + 锚定引用 + 用户短字段），并持久化（只存元数据/引用/短字段）。
- 本包是**产品能力包**，不是内核：只依赖 `app.kernel` 公开面
  （`app.kernel.guardrails` 的 CaseFolder / sanitize_case_folder / ContractViolationError +
  `app.kernel.identity` 的 TenantContext / AuthResult）与既有持久层范式
  （SQLModel + app.core.db.engine），**绝不**直连 retrieval / rerank / summary /
  query_processing，**绝不** import 其它产品包（intake / statute / drafting）。

第一性红线（E7-2）：
- **只归集不起草**：service 绝不生成任何案件综述正文 / 结论 / 胜负判断 / 诉讼结果预测；
  CaseFolder 只承载脱敏摘要 + 锚定引用 + 短字段。
- **引用必带锚点、无锚点不进交付物**：candidate_refs / draft_descriptors 经 sanitize 逐项收敛，
  缺锚点引用 fail-closed 丢弃。
- **持久层零正文**：case_folder 表只存元数据/多租户字段/脱敏摘要/引用/短字段/结构化关系，
  绝不含裁判正文 / 起草正文 / 候选 / chunk 正文 / 案件综述 / 原始案情 / 凭据列。
- **ENABLE_CASEBOOK 默认 false**：关闭时端点 403 安全降级（与 intake / statute / drafting 一致）。
- **多租户强隔离 + 对象级鉴权 + 默认 private**：所有读取强制租户过滤，写 / 更新经 owner +
  租户一致性校验；越权读写拒绝（404 / 不泄露他人协作夹）。
- **日志脱敏**：只写 user_id_hash / case_folder_id_hash / 计数 / note 元信息(长度+hash) /
  reason_code；绝不写裁判正文 / 起草正文 / 原始案情 / note 全文。
"""
from __future__ import annotations

from app.casebook.router import (
    CASEBOOK_DISABLED_CODE,
    CASEBOOK_REJECTED_CODE,
    CASEBOOK_REQUIRES_LOGIN_CODE,
    CASE_FOLDER_NOT_FOUND_CODE,
    router as casebook_router,
    set_casebook_service_for_test,
)
from app.casebook.schemas import (
    CaseFolderCandidateRefView,
    CaseFolderCreateRequest,
    CaseFolderDraftDescriptorView,
    CaseFolderListResponse,
    CaseFolderShareRequest,
    CaseFolderStatuteRefView,
    CaseFolderUpdateRequest,
    CaseFolderView,
)
from app.casebook.service import CasebookService
from app.casebook.store import CaseFolderStore

__all__ = [
    "casebook_router",
    "set_casebook_service_for_test",
    "CASEBOOK_DISABLED_CODE",
    "CASEBOOK_REJECTED_CODE",
    "CASEBOOK_REQUIRES_LOGIN_CODE",
    "CASE_FOLDER_NOT_FOUND_CODE",
    "CaseFolderCreateRequest",
    "CaseFolderUpdateRequest",
    "CaseFolderShareRequest",
    "CaseFolderView",
    "CaseFolderListResponse",
    "CaseFolderCandidateRefView",
    "CaseFolderStatuteRefView",
    "CaseFolderDraftDescriptorView",
    "CasebookService",
    "CaseFolderStore",
]
