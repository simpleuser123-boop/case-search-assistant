"""M5-6 批量导入 API 路由（flag-gated）。

ENABLE_BULK_IMPORT=false（默认）时：所有端点返回 403 BULK_IMPORT_DISABLED，
不建表、不导入，行为回到 M5-5/M4 末态。

红线：
- 导入只接受元数据 / 引用 / 锚点 / 用户自填短字段（schema extra=forbid + 校验层白名单双重拦截），
  绝不上送正文 / 原始案情。导入对象默认归属当前 owner、默认私有。
- 缺锚点 / 含正文 / 缺 case_id 的项被降级或拒绝，绝不伪造锚点。
- 所有端点需登录（复用 M5-2 会话；账号体系关则会话无效）。
- 日志只记录 user_id_hash / job_id_hash / 计数 / 短 reason code；绝不记录正文 / 凭据 / 锚点内容。
"""
from __future__ import annotations

from fastapi import APIRouter, Header, Request, status

from app.account.models import hash_user_id
from app.account.service import AuthResult
from app.api.errors import api_error_response
from app.bulk_import.models import BulkImportJob, hash_job_id
from app.bulk_import.schemas import (
    BulkImportRequest,
    BulkImportResponse,
    ItemOutcomeView,
    JobListResponse,
    JobView,
)
from app.bulk_import.service import BulkImportService
from app.bulk_import.store import BulkImportStore
from app.core.config import settings
from app.core.db import engine
from app.core.logging import logger
from app.schemas import ErrorResponse
from app.team.models import hash_team_id
from app.team.store import TeamStore

router = APIRouter(prefix="/api/bulk-import", tags=["bulk-import"])

BULK_IMPORT_DISABLED_CODE = "BULK_IMPORT_DISABLED"

_bulk_import_service: BulkImportService | None = None


def _get_service() -> BulkImportService:
    global _bulk_import_service
    if _bulk_import_service is None:
        import_store = BulkImportStore(engine)
        import_store.init_schema()
        team_store = TeamStore(engine)
        team_store.init_schema()
        _bulk_import_service = BulkImportService(import_store, team_store)
    return _bulk_import_service


def set_bulk_import_service_for_test(service: BulkImportService | None) -> None:
    global _bulk_import_service
    _bulk_import_service = service


def _enabled() -> bool:
    return bool(getattr(settings, "ENABLE_BULK_IMPORT", False))


def _disabled_response(request: Request):
    logger.info(
        "bulk_import_disabled path=%s reason_code=%s",
        request.url.path, "ENABLE_BULK_IMPORT_false",
    )
    return api_error_response(
        status_code=status.HTTP_403_FORBIDDEN,
        code=BULK_IMPORT_DISABLED_CODE,
        message="批量导入未启用（ENABLE_BULK_IMPORT=false），当前为本地沉淀 / owner 私有模式。",
        query_session_id=getattr(request.state, "query_session_id", None),
    )


def _resolve_login(authorization: str | None) -> AuthResult:
    from app.api import auth as auth_api

    token = None
    if authorization:
        parts = authorization.split(" ", 1)
        if len(parts) == 2 and parts[0].lower() == "bearer":
            token = parts[1].strip()
    if not token:
        return AuthResult(ok=False)
    if not getattr(settings, "ENABLE_ACCOUNT_SYSTEM", False):
        return AuthResult(ok=False)
    try:
        return auth_api._get_service().resolve_session(session_token=token)  # noqa: SLF001
    except Exception:  # noqa: BLE001 - 校验失败一律视为未登录，不泄露细节
        return AuthResult(ok=False)


def _require_login(authorization: str | None) -> AuthResult | None:
    result = _resolve_login(authorization)
    if not result.ok or result.account is None:
        return None
    return result


def _login_required_response(request: Request):
    return api_error_response(
        status_code=status.HTTP_401_UNAUTHORIZED,
        code="BULK_IMPORT_REQUIRES_LOGIN",
        message="批量导入操作需先登录。",
        query_session_id=getattr(request.state, "query_session_id", None),
    )


def _job_to_view(job: BulkImportJob) -> JobView:
    return JobView(
        import_job_id=job.import_job_id,
        source_type=job.source_type,
        item_count=job.item_count,
        imported_count=job.imported_count,
        rejected_count=job.rejected_count,
        duplicate_count=job.duplicate_count,
        import_status=job.import_status,
        degrade_reason=job.degrade_reason,
        owner_user_id_hash=hash_user_id(job.owner_user_id),
        team_id_hash=hash_team_id(job.team_id),
    )


@router.post("/run", response_model=BulkImportResponse,
             responses={status.HTTP_403_FORBIDDEN: {"model": ErrorResponse}})
def run_import(payload: BulkImportRequest, request: Request, authorization: str | None = Header(default=None)):
    if not _enabled():
        return _disabled_response(request)
    login = _require_login(authorization)
    if login is None:
        return _login_required_response(request)
    # 把 schema 项收窄为 dict（exclude_none 杜绝把空值当正文键传下去）。
    items = [item.model_dump(exclude_none=True) for item in payload.items]
    result = _get_service().import_batch(
        owner_user_id=login.account.user_id,
        source_type=payload.source_type,
        object_type=payload.object_type,
        items=items,
        team_id=payload.team_id,
    )
    logger.info(
        "bulk_import_run user_id_hash=%s job_id_hash=%s status=%s items=%s imported=%s rejected=%s duplicate=%s reason_code=%s",
        login.user_id_hash or "-", hash_job_id(result.import_job_id), result.import_status,
        result.item_count, result.imported_count, result.rejected_count, result.duplicate_count,
        result.degrade_reason or "-",
    )
    return BulkImportResponse(
        ok=result.ok,
        import_job_id=result.import_job_id,
        import_status=result.import_status,
        item_count=result.item_count,
        imported_count=result.imported_count,
        rejected_count=result.rejected_count,
        duplicate_count=result.duplicate_count,
        degrade_reason=result.degrade_reason,
        outcomes=[
            ItemOutcomeView(case_id=o.case_id, ok=o.ok, reason_code=o.reason_code, object_id=o.object_id)
            for o in result.outcomes
        ],
    )


@router.get("/jobs", response_model=JobListResponse,
            responses={status.HTTP_403_FORBIDDEN: {"model": ErrorResponse}})
def list_jobs(request: Request, authorization: str | None = Header(default=None)):
    if not _enabled():
        return _disabled_response(request)
    login = _require_login(authorization)
    if login is None:
        return _login_required_response(request)
    jobs = _get_service().list_jobs(owner_user_id=login.account.user_id)
    return JobListResponse(ok=True, items=[_job_to_view(j) for j in jobs], reason_code="ok")
