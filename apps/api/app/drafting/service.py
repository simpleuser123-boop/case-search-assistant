"""E6-2 drafting 文书工作台服务（产品包层，仅依赖 app.kernel 公开面 + 既有持久层）。

职责（**组装而非起草** + 持久化元数据/引用/短字段）：
1. assemble_draft()：把 structure_skeleton(标题) + candidate_refs + 可选 statute_refs +
   note/tag 经 E6-1 ``sanitize_draft_descriptor`` 收敛为 DraftDescriptor（缺锚点引用丢弃、
   起草正文/裁判正文/PII/胜负结论键 fail-closed），**绝不生成任何段落正文 / 结论 / 胜负判断**。
2. create_draft / get_draft / list_drafts / update_draft：经 DraftStore 持久化 / 读取，
   强制租户隔离 + 对象级鉴权（owner 私有），只存元数据/引用/短字段。

红线：
- 不复制检索主路径、不深引内核内部、不直连 retrieval / rerank / summary / query_processing。
- 不调用任何文本生成 / AI 起草；service 内无任何 LLM / 模型调用入口。
- 不 import 其它产品包（intake / statute / casebook）。
- 行级强隔离：所有读取经 DraftStore 强制租户过滤；写 / 更新经 owner + 租户一致性校验。
- 异常只暴露键名 / reason code，绝不回显起草正文 / 裁判正文 / PII / note 全文。
"""
from __future__ import annotations

from typing import Any, Mapping

# 仅依赖 app.kernel 公开面：DraftDescriptor 收敛经 guardrails 护栏面；租户上下文经身份组公开面。
from app.kernel.guardrails import (
    ContractViolationError,
    DraftDescriptor,
    sanitize_draft_descriptor,
)
from app.kernel.identity import TenantContext

from app.drafting.models import DraftDescriptorRow, VISIBILITY_PRIVATE
from app.drafting.store import DraftStore

# 组装阶段的占位 id（store 落库时生成真实 draft_id；契约模型要求 id 非空，故用占位）。
_ASSEMBLE_PLACEHOLDER_ID = "d_pending"


class DraftingService:
    """文书工作台服务：组装 DraftDescriptor（不起草）+ 持久化（只存元数据/引用/短字段）。

    依赖经构造函数注入（持久层 DraftStore）；本服务不持有任何检索 / 文本生成句柄。
    """

    def __init__(self, *, store: DraftStore) -> None:
        self._store = store

    # --- 组装（纯函数式收敛，不起草、不生成文本）---
    @staticmethod
    def assemble_draft(payload: Mapping[str, Any]) -> DraftDescriptor:
        """把入参组装为已收敛的 DraftDescriptor（经 E6-1 sanitize_draft_descriptor）。

        - structure_skeleton 每项做标题校验（非空 + ≤ 长度上限），超限 fail-closed 抛错。
        - candidate_refs / statute_refs 逐项收敛，缺锚点引用 fail-closed **丢弃**（保留项 100% 有锚点）。
        - 起草正文 / 裁判正文 / PII / 胜负结论型键 fail-closed 抛 ContractViolationError。
        - **绝不生成任何段落正文 / 结论 / 胜负判断**（本函数只做白名单清洗与锚点收敛）。
        """
        merged: dict[str, Any] = dict(payload)
        merged.setdefault("draft_id", _ASSEMBLE_PLACEHOLDER_ID)
        return sanitize_draft_descriptor(merged)

    # --- 持久化（只存元数据/引用/短字段，强制租户隔离）---
    def create_draft(
        self, *, ctx: TenantContext, payload: Mapping[str, Any]
    ) -> DraftDescriptor:
        """组装 + 持久化一条 DraftDescriptor（默认 owner 私有），出已收敛的契约对象。"""
        descriptor = self.assemble_draft(payload)
        row = self._store.create(
            ctx=ctx,
            payload=self._descriptor_to_persist_payload(descriptor),
            visibility=VISIBILITY_PRIVATE,
        )
        return self._row_to_descriptor(row)

    def list_drafts(self, *, ctx: TenantContext) -> list[DraftDescriptor]:
        """列出当前租户上下文可见的 DraftDescriptor（强制租户过滤）。"""
        rows = self._store.list_visible(ctx=ctx)
        return [self._row_to_descriptor(r) for r in rows]

    def get_draft(self, *, ctx: TenantContext, draft_id: str) -> DraftDescriptor | None:
        """读取单个 DraftDescriptor（强制租户过滤，跨租户取不到返回 None）。"""
        row = self._store.get_visible(ctx=ctx, draft_id=str(draft_id).strip())
        if row is None:
            return None
        return self._row_to_descriptor(row)

    def update_draft(
        self, *, ctx: TenantContext, draft_id: str, payload: Mapping[str, Any]
    ) -> DraftDescriptor | None:
        """更新 owner 本人的 DraftDescriptor（仍只存元数据，经 sanitize）。

        非 owner / 不存在 -> 返回 None（调用方转 404）。
        """
        descriptor = self.assemble_draft(payload)
        row = self._store.update_owned(
            ctx=ctx,
            draft_id=str(draft_id).strip(),
            payload=self._descriptor_to_persist_payload(descriptor),
        )
        if row is None:
            return None
        return self._row_to_descriptor(row)

    # --- 内部：契约对象 <-> 持久层行 ---
    @staticmethod
    def _descriptor_to_persist_payload(descriptor: DraftDescriptor) -> dict[str, Any]:
        """把已收敛 DraftDescriptor 转为持久层白名单载荷（结构骨架/引用/短字段，零正文）。"""
        return {
            "structure_skeleton": list(descriptor.structure_skeleton),
            "candidate_refs": [
                ref.model_dump(exclude_none=True) for ref in descriptor.candidate_refs
            ],
            "statute_refs": [
                ref.model_dump(exclude_none=True) for ref in descriptor.statute_refs
            ],
            "note": descriptor.note,
            "tag": descriptor.tag,
        }

    @staticmethod
    def _row_to_descriptor(row: DraftDescriptorRow) -> DraftDescriptor:
        """把持久层行还原为 DraftDescriptor（经 sanitize 再兜一层，保证出库零正文）。"""
        import json

        payload: dict[str, Any] = {
            "draft_id": row.draft_id,
            "structure_skeleton": json.loads(row.structure_skeleton or "[]"),
            "candidate_refs": json.loads(row.candidate_refs or "[]"),
            "statute_refs": json.loads(row.statute_refs or "[]"),
            "note": row.note,
            "tag": row.tag,
            "owner_user_id": row.owner_user_id,
            "team_id": row.team_id,
            "visibility": row.visibility,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        }
        # 出库再经 sanitize：即便库里被异常写入也在出库时 fail-closed（双保险，零正文）。
        return sanitize_draft_descriptor(payload)


__all__ = ["DraftingService", "ContractViolationError"]
