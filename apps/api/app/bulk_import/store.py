"""M5-6 批量导入持久层：导入作业账本读写 + 已存在 case_id 去重查询。

红线（运行时防御）：
- 本 store 只写 m5_bulk_import_job（聚合统计 / 短状态码），不写任何正文列。
- 实际导入对象的写入复用 M5-3 TeamStore.create_sediment（白名单清洗 + 租户一致性），
  本 store 不绕过该路径，也不提供无过滤读取沉淀对象的方法。
- 去重查询只读 owner 自己的沉淀对象 case_id 集合（owner 私有域），不跨用户/跨团队读取。
- 引擎可注入：生产用 app.core.db.engine（postgres）；测试注入临时 sqlite。
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, select

from app.bulk_import.models import BulkImportJob
from app.team.models import SedimentationObject


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class BulkImportStore:
    """导入作业账本 + 去重查询。不写正文，不放权读取沉淀对象。"""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def init_schema(self) -> None:
        """仅创建 M5-6 导入作业账本表。只有 ENABLE_BULK_IMPORT=true 时才会被调用。"""
        SQLModel.metadata.create_all(self._engine, tables=[BulkImportJob.__table__])

    def existing_case_ids_for_owner(self, *, owner_user_id: str) -> set[str]:
        """取 owner 自己私有域内已存在的 case_id 集合，用于跨批次去重。

        只读自己 owner_user_id 且 team_id 为空的私有行（与单用户私有隔离一致），
        不跨用户/跨团队读取。
        """
        with Session(self._engine) as session:
            rows = session.exec(
                select(SedimentationObject.case_id).where(
                    SedimentationObject.owner_user_id == owner_user_id,
                    SedimentationObject.team_id.is_(None),  # type: ignore[union-attr]
                    SedimentationObject.case_id.is_not(None),  # type: ignore[union-attr]
                )
            ).all()
        return {r for r in rows if r}

    def record_job(
        self,
        *,
        source_type: str,
        item_count: int,
        imported_count: int,
        rejected_count: int,
        duplicate_count: int,
        import_status: str,
        degrade_reason: str | None,
        owner_user_id: str,
        team_id: str | None,
    ) -> BulkImportJob:
        """写入一条导入作业账本（聚合统计 + 短状态码，无正文 / 无明细）。"""
        job = BulkImportJob(
            import_job_id=f"imp_{uuid.uuid4().hex[:24]}",
            source_type=source_type,
            item_count=item_count,
            imported_count=imported_count,
            rejected_count=rejected_count,
            duplicate_count=duplicate_count,
            import_status=import_status,
            degrade_reason=degrade_reason,
            owner_user_id=owner_user_id,
            team_id=team_id,
        )
        with Session(self._engine) as session:
            session.add(job)
            session.commit()
            session.refresh(job)
        return job

    def get_job(self, import_job_id: str) -> BulkImportJob | None:
        with Session(self._engine) as session:
            return session.get(BulkImportJob, import_job_id)

    def list_jobs_for_owner(self, *, owner_user_id: str) -> list[BulkImportJob]:
        with Session(self._engine) as session:
            return list(
                session.exec(
                    select(BulkImportJob)
                    .where(BulkImportJob.owner_user_id == owner_user_id)
                    .order_by(BulkImportJob.created_at.desc())  # type: ignore[union-attr]
                ).all()
            )
