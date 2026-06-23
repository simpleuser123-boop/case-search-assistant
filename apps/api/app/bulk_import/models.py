"""M5-6 批量导入作业账本模型（m5_bulk_import_job）。

设计要点：
- 本表只记录**导入作业的元数据/统计/状态**（谁、来源类型、项数、成功/降级/拒绝计数、
  归属 owner/team、当前状态、降级原因短码），用于审计与进度展示。
- 真正的导入对象仍写入 M5-3 的 m5_sedimentation_object（默认 owner 私有），
  可见性由 tenant_visibility_clause 唯一承载；本账本不参与读取放权。
- 绝不存：裁判文书正文、摘要/要旨正文、chunk 正文、原始案情、任何自由长文本、凭据。
  导入项明细不在本表落库（避免正文经明细列混入）；只保留聚合计数与短状态码。

字段白名单（M5-1 合同：结构化关系 + 状态/计数 + 时间戳 + reason code）：
- import_job_id / source_type / item_count / imported_count / rejected_count /
  duplicate_count / import_status / degrade_reason / owner_user_id / team_id /
  created_at / updated_at。

开关：所有写入只在 ENABLE_BULK_IMPORT=true 时由导入服务触发；
关闭时本模块不建表、不写入，行为回到 M5-5/M4 末态。
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from sqlmodel import Field, SQLModel

# 导入作业状态短枚举（结构化字段，非正文）。
IMPORT_STATUS_COMPLETED = "completed"      # 全部项导入成功
IMPORT_STATUS_PARTIAL = "partial"          # 部分项导入成功（其余被降级/拒绝/去重）
IMPORT_STATUS_REJECTED = "rejected"        # 无任何项导入成功（全部被拒/降级）
IMPORT_STATUS_FAILED = "failed"            # 作业级失败（来源非法/空批等）
IMPORT_STATUSES = (
    IMPORT_STATUS_COMPLETED,
    IMPORT_STATUS_PARTIAL,
    IMPORT_STATUS_REJECTED,
    IMPORT_STATUS_FAILED,
)

# 来源类型短枚举：清单文件 / CSV / 既有清单（前端沉淀）。仅作分类标签，非正文。
SOURCE_TYPE_CASE_LIST = "case_list_file"
SOURCE_TYPE_CSV = "csv"
SOURCE_TYPE_EXISTING_LIST = "existing_list"
SOURCE_TYPES = (SOURCE_TYPE_CASE_LIST, SOURCE_TYPE_CSV, SOURCE_TYPE_EXISTING_LIST)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class BulkImportJob(SQLModel, table=True):
    """批量导入作业账本。仅结构化统计 + 短状态码，无正文、无导入项明细正文。"""

    __tablename__ = "m5_bulk_import_job"

    import_job_id: str = Field(primary_key=True, max_length=64)
    # source_type：清单文件 / CSV / 既有清单（仅分类标签）。
    source_type: str = Field(max_length=32)
    # item_count：本批提交的总项数。
    item_count: int = Field(default=0)
    # imported_count：成功导入（写入沉淀持久层）的项数。
    imported_count: int = Field(default=0)
    # rejected_count：因缺锚点/非法/含正文等被拒或降级的项数。
    rejected_count: int = Field(default=0)
    # duplicate_count：按 case_id 去重命中的项数（含批内 + 已有）。
    duplicate_count: int = Field(default=0)
    import_status: str = Field(default=IMPORT_STATUS_FAILED, max_length=16)
    degrade_reason: str | None = Field(default=None, max_length=64)
    # owner_user_id：导入发起者（导入对象默认归属此 owner）。
    owner_user_id: str = Field(index=True, max_length=64)
    # team_id：导入时的团队上下文（仅留痕；导入对象本身仍默认私有 team_id=None）。
    team_id: str | None = Field(default=None, index=True, max_length=64)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


def hash_job_id(job_id: str | None) -> str:
    """日志 / 埋点用的 import_job_id 脱敏哈希（截断）。空作业返回固定标记。"""
    if not job_id:
        return "jidh_none"
    digest = hashlib.sha256(job_id.encode("utf-8")).hexdigest()
    return f"jidh_{digest[:16]}"
