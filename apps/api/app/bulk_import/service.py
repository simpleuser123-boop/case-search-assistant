"""M5-6 批量导入服务：把既有清单/案例批量导入团队空间（默认 owner 私有）。

职责：
- import_batch：逐项校验净化（白名单过滤 + 锚点完整性）→ 按 case_id 去重 →
  把通过的项写入 M5-3 沉淀持久层（默认 owner 私有：team_id=None / visibility=private）→
  写一条导入作业账本（聚合统计 + 短状态码）。
- 缺锚点 / 含正文 / 缺 case_id 的项被降级或拒绝，绝不伪造锚点、绝不让正文入库。
- 导入对象默认归属当前 owner、默认私有；可见性仍由 M5-3 tenant_visibility_clause 唯一承载。
- 导入失败（来源非法 / 空批）写 failed 作业并安全返回，不影响主链路。

红线：不 import 检索 / rerank / 排序；导入不改变主排序或既有默认行为。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.bulk_import.models import (
    IMPORT_STATUS_COMPLETED,
    IMPORT_STATUS_FAILED,
    IMPORT_STATUS_PARTIAL,
    IMPORT_STATUS_REJECTED,
    SOURCE_TYPES,
    BulkImportJob,
)
from app.bulk_import.store import BulkImportStore
from app.bulk_import.validation import (
    REASON_DUPLICATE,
    validate_and_clean_item,
)
from app.team.isolation import TenantContext
from app.team.models import VISIBILITY_PRIVATE
from app.team.store import TeamStore

# 单批导入项数上限（防止超大批拖垮请求；超出截断为 failed 安全态）。
MAX_ITEMS_PER_BATCH = 500

REASON_EMPTY_BATCH = "empty_batch"
REASON_INVALID_SOURCE = "invalid_source_type"
REASON_BATCH_TOO_LARGE = "batch_too_large"
REASON_ALL_REJECTED = "all_items_rejected"


@dataclass
class ItemOutcome:
    """单项导入结果（脱敏）：只含 case_id 引用与短 reason code，无正文。"""

    case_id: str | None
    ok: bool
    reason_code: str
    object_id: str | None = None


@dataclass
class ImportResult:
    ok: bool
    import_job_id: str | None = None
    import_status: str = IMPORT_STATUS_FAILED
    item_count: int = 0
    imported_count: int = 0
    rejected_count: int = 0
    duplicate_count: int = 0
    degrade_reason: str | None = None
    outcomes: list[ItemOutcome] = field(default_factory=list)


class BulkImportService:
    def __init__(self, import_store: BulkImportStore, team_store: TeamStore) -> None:
        self._store = import_store
        self._team = team_store

    def import_batch(
        self,
        *,
        owner_user_id: str,
        source_type: str,
        object_type: str,
        items: list[dict],
        team_id: str | None = None,
    ) -> ImportResult:
        """批量导入一组项。导入对象默认归属当前 owner、默认私有。

        team_id 仅用于作业账本留痕（导入时的团队上下文）；导入对象本身永远写
        owner 私有（team_id=None / visibility=private），共享是 M5-5 的另一显式动作。
        """
        # 作业级前置校验：来源类型 / 空批 / 超大批 -> failed 安全态。
        if source_type not in SOURCE_TYPES:
            return self._fail(source_type, owner_user_id, team_id, len(items or []), REASON_INVALID_SOURCE)
        if not items:
            return self._fail(source_type, owner_user_id, team_id, 0, REASON_EMPTY_BATCH)
        if len(items) > MAX_ITEMS_PER_BATCH:
            return self._fail(source_type, owner_user_id, team_id, len(items), REASON_BATCH_TOO_LARGE)

        # 导入对象永远写 owner 私有上下文（绝不默认跨用户 / 跨团队可见）。
        ctx = TenantContext(owner_user_id=owner_user_id)

        # 跨批次去重：owner 私有域内已存在的 case_id。
        seen_case_ids = self._store.existing_case_ids_for_owner(owner_user_id=owner_user_id)

        outcomes: list[ItemOutcome] = []
        imported = rejected = duplicate = 0

        for raw_item in items:
            res = validate_and_clean_item(object_type=object_type, raw_item=raw_item if isinstance(raw_item, dict) else {})
            if not res.ok:
                rejected += 1
                outcomes.append(ItemOutcome(case_id=res.case_id, ok=False, reason_code=res.reason_code))
                continue
            # 去重（批内 + 已存在）。
            if res.case_id in seen_case_ids:
                duplicate += 1
                outcomes.append(ItemOutcome(case_id=res.case_id, ok=False, reason_code=REASON_DUPLICATE))
                continue
            # 写入沉淀持久层：复用 M5-3 白名单清洗 + 租户一致性校验（默认 owner 私有）。
            try:
                obj = self._team.create_sediment(
                    ctx=ctx,
                    object_type=object_type,
                    visibility=VISIBILITY_PRIVATE,
                    payload=res.clean_payload,
                    reason_code="bulk_import",
                )
            except ValueError:
                # 二次防御：清洗后仍触发白名单拒绝（理论上不应发生），按拒绝计。
                rejected += 1
                outcomes.append(ItemOutcome(case_id=res.case_id, ok=False, reason_code="forbidden_body_field"))
                continue
            seen_case_ids.add(res.case_id)  # type: ignore[arg-type]
            imported += 1
            outcomes.append(ItemOutcome(case_id=res.case_id, ok=True, reason_code="ok", object_id=obj.object_id))

        status, degrade = self._derive_status(
            item_count=len(items), imported=imported, rejected=rejected, duplicate=duplicate
        )
        job = self._store.record_job(
            source_type=source_type,
            item_count=len(items),
            imported_count=imported,
            rejected_count=rejected,
            duplicate_count=duplicate,
            import_status=status,
            degrade_reason=degrade,
            owner_user_id=owner_user_id,
            team_id=team_id,
        )
        return ImportResult(
            ok=imported > 0,
            import_job_id=job.import_job_id,
            import_status=status,
            item_count=len(items),
            imported_count=imported,
            rejected_count=rejected,
            duplicate_count=duplicate,
            degrade_reason=degrade,
            outcomes=outcomes,
        )

    def list_jobs(self, *, owner_user_id: str) -> list[BulkImportJob]:
        return self._store.list_jobs_for_owner(owner_user_id=owner_user_id)

    @staticmethod
    def _derive_status(*, item_count: int, imported: int, rejected: int, duplicate: int) -> tuple[str, str | None]:
        if imported == item_count and item_count > 0:
            return IMPORT_STATUS_COMPLETED, None
        if imported == 0:
            return IMPORT_STATUS_REJECTED, REASON_ALL_REJECTED
        return IMPORT_STATUS_PARTIAL, "partial_import"

    def _fail(
        self, source_type: str, owner_user_id: str, team_id: str | None, item_count: int, reason: str
    ) -> ImportResult:
        # 作业级失败：仍写一条 failed 账本留痕（来源类型若非法则记原值供审计），安全返回。
        safe_source = source_type if source_type in SOURCE_TYPES else "unknown"
        job = self._store.record_job(
            source_type=safe_source,
            item_count=item_count,
            imported_count=0,
            rejected_count=0,
            duplicate_count=0,
            import_status=IMPORT_STATUS_FAILED,
            degrade_reason=reason,
            owner_user_id=owner_user_id,
            team_id=team_id,
        )
        return ImportResult(
            ok=False,
            import_job_id=job.import_job_id,
            import_status=IMPORT_STATUS_FAILED,
            item_count=item_count,
            degrade_reason=reason,
        )
