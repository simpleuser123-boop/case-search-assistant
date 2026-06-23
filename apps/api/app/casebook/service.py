"""E7-2 casebook 案件协作工作台服务（产品包层，仅依赖 app.kernel 公开面 + 既有持久层）。

职责（**归集而非起草** + 持久化元数据/引用/短字段）：
1. assemble_case_folder()：把 search_profile_summary(脱敏子集) + candidate_refs +
   draft_descriptors + title/note/tag 经 E7-1 ``sanitize_case_folder`` 收敛为 CaseFolder
   （缺锚点引用丢弃、原始案情/PII/裁判正文/起草正文/胜负结论键 fail-closed），
   **绝不生成任何案件综述正文 / 结论 / 胜负判断 / 诉讼结果预测**。
2. create_case_folder / get_case_folder / list_case_folders / update_case_folder：
   经 CaseFolderStore 持久化 / 读取，强制租户隔离 + 对象级鉴权（owner 私有），
   只存元数据/引用/短字段。

红线：
- 不复制检索主路径、不深引内核内部、不直连检索召回 / 重排序 / 摘要 / 查询改写底层。
- 不调用任何文本生成 / AI 归纳 / AI 起草；service 内无任何大模型 / 模型调用入口。
- 不 import 其它产品包（intake / statute / drafting）。
- 行级强隔离：所有读取经 CaseFolderStore 强制租户过滤；写 / 更新经 owner + 租户一致性校验。
- 异常只暴露键名 / reason code，绝不回显裁判正文 / 起草正文 / PII / note 全文。
"""
from __future__ import annotations

import json
from typing import Any, Mapping

# 仅依赖 app.kernel 公开面：CaseFolder 收敛经 guardrails 护栏面；租户上下文经身份组公开面。
from app.kernel.guardrails import (
    CaseFolder,
    ContractViolationError,
    DEFAULT_VISIBILITY,
    sanitize_case_folder,
)
from app.kernel.identity import TenantContext

from app.casebook.models import CaseFolderRow, VISIBILITY_PRIVATE
from app.casebook.store import CaseFolderStore

# 组装阶段的占位 id（store 落库时生成真实 case_folder_id；契约模型要求 id 非空，故用占位）。
_ASSEMBLE_PLACEHOLDER_ID = "cf_pending"


class CasebookService:
    """案件协作工作台服务：归集 CaseFolder（不起草）+ 持久化（只存元数据/引用/短字段）。

    依赖经构造函数注入（持久层 CaseFolderStore）；本服务不持有任何检索 / 文本生成句柄。
    """

    def __init__(self, *, store: CaseFolderStore) -> None:
        self._store = store

    # --- 归集（纯函数式收敛，不起草、不生成文本）---
    @staticmethod
    def assemble_case_folder(
        payload: Mapping[str, Any], *, owner_user_id: str
    ) -> CaseFolder:
        """把入参组装为已收敛的 CaseFolder（经 E7-1 sanitize_case_folder）。

        - search_profile_summary 收敛为 SearchProfile 脱敏白名单子集（原始案情 fail-closed）。
        - candidate_refs / draft_descriptors 逐项收敛，缺锚点引用 fail-closed **丢弃**
          （保留项 100% 有锚点）。
        - 裁判正文 / 起草正文 / 原始案情 / PII / 胜负结论型键 fail-closed 抛 ContractViolationError。
        - visibility 缺省补 private。
        - **绝不生成任何案件综述正文 / 结论 / 胜负判断**（本函数只做白名单清洗与锚点收敛）。
        """
        merged: dict[str, Any] = dict(payload)
        # 契约模型要求 case_folder_id / owner_user_id 非空；组装阶段补占位与归属。
        merged.setdefault("case_folder_id", _ASSEMBLE_PLACEHOLDER_ID)
        merged["owner_user_id"] = owner_user_id
        return sanitize_case_folder(merged)

    # --- 持久化（只存元数据/引用/短字段，强制租户隔离）---
    def create_case_folder(
        self, *, ctx: TenantContext, payload: Mapping[str, Any]
    ) -> CaseFolder:
        """归集 + 持久化一条 CaseFolder（默认 owner 私有），出已收敛的契约对象。"""
        folder = self.assemble_case_folder(payload, owner_user_id=ctx.owner_user_id)
        row = self._store.create(
            ctx=ctx,
            payload=self._folder_to_persist_payload(folder),
            visibility=VISIBILITY_PRIVATE,
        )
        return self._row_to_folder(row)

    def list_case_folders(self, *, ctx: TenantContext) -> list[CaseFolder]:
        """列出当前租户上下文可见的 CaseFolder（强制租户过滤）。"""
        rows = self._store.list_visible(ctx=ctx)
        return [self._row_to_folder(r) for r in rows]

    def get_case_folder(
        self, *, ctx: TenantContext, case_folder_id: str
    ) -> CaseFolder | None:
        """读取单个 CaseFolder（强制租户过滤，跨租户取不到返回 None）。"""
        row = self._store.get_visible(
            ctx=ctx, case_folder_id=str(case_folder_id).strip()
        )
        if row is None:
            return None
        return self._row_to_folder(row)

    def update_case_folder(
        self,
        *,
        ctx: TenantContext,
        case_folder_id: str,
        payload: Mapping[str, Any],
    ) -> CaseFolder | None:
        """更新 owner 本人的 CaseFolder（仍只存元数据，经 sanitize）。

        非 owner / 不存在 -> 返回 None（调用方转 404）。
        本步允许 visibility 写入（E7-4 细化共享语义）；缺省时不改既有可见性。
        """
        folder = self.assemble_case_folder(payload, owner_user_id=ctx.owner_user_id)
        # 仅当请求显式给出合法 visibility 时才透传（缺省由 store 保持原值）。
        requested_visibility = payload.get("visibility") if isinstance(payload, Mapping) else None
        row = self._store.update_owned(
            ctx=ctx,
            case_folder_id=str(case_folder_id).strip(),
            payload=self._folder_to_persist_payload(folder),
            visibility=folder.visibility if requested_visibility else None,
        )
        if row is None:
            return None
        return self._row_to_folder(row)

    # --- 共享切换（E7-4，只改 visibility 元数据，零正文）---
    def share_case_folder(
        self,
        *,
        ctx: TenantContext,
        case_folder_id: str,
        visibility: str,
        team_id: str | None,
    ) -> CaseFolder | None:
        """E7-4 共享切换：把 owner 本人 CaseFolder 的可见性在 private<->team 间切换。

        - 复用持久层 set_sharing（owner-only + 原子改 visibility/team_id）；
          非 owner / 不存在 -> None（调用方转 404）。
        - 共享到 team 须先由 router 校验 owner 是该 team 活跃成员（不在本层放权）。
        - **绝不触碰摘要/引用/短字段**：共享只改可见性元数据，零正文、引用仍只带锚点。
        - 出库再经 _row_to_folder -> sanitize_case_folder 双保险（零正文）。
        """
        row = self._store.set_sharing(
            ctx=ctx,
            case_folder_id=str(case_folder_id).strip(),
            visibility=visibility,
            team_id=team_id,
        )
        if row is None:
            return None
        return self._row_to_folder(row)

    # --- 内部：契约对象 <-> 持久层行 ---
    @staticmethod
    def _folder_to_persist_payload(folder: CaseFolder) -> dict[str, Any]:
        """把已收敛 CaseFolder 转为持久层白名单载荷（脱敏摘要/引用/短字段，零正文）。"""
        return {
            "search_profile_summary": folder.search_profile_summary,
            "candidate_refs": [
                ref.model_dump(exclude_none=True) for ref in folder.candidate_refs
            ],
            "draft_descriptors": [
                d.model_dump(exclude_none=True) for d in folder.draft_descriptors
            ],
            "title": folder.title,
            "note": folder.note,
            "tag": folder.tag,
        }

    @staticmethod
    def _row_to_folder(row: CaseFolderRow) -> CaseFolder:
        """把持久层行还原为 CaseFolder（经 sanitize 再兜一层，保证出库零正文）。"""
        summary_raw = row.search_profile_summary
        summary = json.loads(summary_raw) if summary_raw else None
        payload: dict[str, Any] = {
            "case_folder_id": row.case_folder_id,
            "owner_user_id": row.owner_user_id,
            "team_id": row.team_id,
            "visibility": row.visibility,
            "search_profile_summary": summary,
            "candidate_refs": json.loads(row.candidate_refs or "[]"),
            "draft_descriptors": json.loads(row.draft_descriptors or "[]"),
            "title": row.title,
            "note": row.note,
            "tag": row.tag,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        }
        # 出库再经 sanitize：即便库里被异常写入也在出库时 fail-closed（双保险，零正文）。
        return sanitize_case_folder(payload)


__all__ = ["CasebookService", "ContractViolationError", "DEFAULT_VISIBILITY"]
