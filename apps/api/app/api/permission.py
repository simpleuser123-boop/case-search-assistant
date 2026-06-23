"""M5-4 权限分级 API 路由（flag-gated）。

ENABLE_PERMISSION_TIERING=false（默认）时：所有端点返回 403 PERMISSION_TIERING_DISABLED，
不建表、不鉴权、不审计，行为与 M5-3 / M4 末态一致（owner 私有，无角色概念）。

红线：
- 每个对象读 / 写 / 删 / 授权动作都经 PermissionService 对象级鉴权；越权 403 + 审计。
- 默认最小权限：非 owner 未显式授权对 private 对象有效权限为 none。
- 所有端点需登录（复用 M5-2 会话；账号体系关则会话无效）。
- 日志只记录 user_id_hash / object_id_hash / action / result / reason_code；
  绝不记录正文 / 凭据。
"""
from __future__ import annotations

from fastapi import APIRouter, Header, Request, status

from app.account.service import AuthResult
from app.api.errors import api_error_response
from app.core.config import settings
from app.core.db import engine
from app.core.logging import logger
from app.permission.models import LEVEL_TO_ROLE
from app.permission.schemas import (
    AssignRoleRequest,
    AuditItem,
    AuditListResponse,
    GenericPermissionResponse,
    GrantRequest,
    ReadObjectRequest,
    ReadObjectResponse,
    RevokeRequest,
)
from app.permission.service import PermissionService
from app.permission.store import PermissionStore
from app.schemas import ErrorResponse
from app.team.store import TeamStore

router = APIRouter(prefix="/api/permission", tags=["permission"])

PERMISSION_TIERING_DISABLED_CODE = "PERMISSION_TIERING_DISABLED"

_permission_service: PermissionService | None = None


def _get_service() -> PermissionService:
    global _permission_service
    if _permission_service is None:
        perm_store = PermissionStore(engine)
        perm_store.init_schema()
        team_store = TeamStore(engine)
        team_store.init_schema()
        _permission_service = PermissionService(perm_store, team_store)
    return _permission_service


def set_permission_service_for_test(service: PermissionService | None) -> None:
    global _permission_service
    _permission_service = service


def _enabled() -> bool:
    return bool(getattr(settings, "ENABLE_PERMISSION_TIERING", False))


def _disabled_response(request: Request):
    logger.info(
        "permission_tiering_disabled path=%s reason_code=%s",
        request.url.path, "ENABLE_PERMISSION_TIERING_false",
    )
    return api_error_response(
        status_code=status.HTTP_403_FORBIDDEN,
        code=PERMISSION_TIERING_DISABLED_CODE,
        message="权限分级未启用（ENABLE_PERMISSION_TIERING=false），当前为 owner 私有模式。",
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
    except Exception:  # noqa: BLE001
        return AuthResult(ok=False)


def _require_login(authorization: str | None) -> AuthResult | None:
    result = _resolve_login(authorization)
    if not result.ok or result.account is None:
        return None
    return result


def _login_required_response(request: Request):
    return api_error_response(
        status_code=status.HTTP_401_UNAUTHORIZED,
        code="PERMISSION_REQUIRES_LOGIN",
        message="权限操作需先登录。",
        query_session_id=getattr(request.state, "query_session_id", None),
    )


def _forbidden(request: Request, reason_code: str):
    return api_error_response(
        status_code=status.HTTP_403_FORBIDDEN,
        code="PERMISSION_DENIED",
        message="越权访问被拒绝。",
        query_session_id=getattr(request.state, "query_session_id", None),
    )


@router.post("/role", response_model=GenericPermissionResponse,
             responses={status.HTTP_403_FORBIDDEN: {"model": ErrorResponse}})
def assign_role(payload: AssignRoleRequest, request: Request, authorization: str | None = Header(default=None)):
    if not _enabled():
        return _disabled_response(request)
    login = _require_login(authorization)
    if login is None:
        return _login_required_response(request)
    result = _get_service().assign_role(
        actor_user_id=login.account.user_id, team_id=payload.team_id,
        member_user_id=payload.member_user_id, role=payload.role,
    )
    logger.info(
        "permission_assign_role user_id_hash=%s ok=%s reason_code=%s",
        login.user_id_hash or "-", result["ok"], result["reason_code"],
    )
    if not result["ok"]:
        return _forbidden(request, result["reason_code"])
    return GenericPermissionResponse(ok=True, reason_code=result["reason_code"])


@router.post("/grant", response_model=GenericPermissionResponse,
             responses={status.HTTP_403_FORBIDDEN: {"model": ErrorResponse}})
def grant(payload: GrantRequest, request: Request, authorization: str | None = Header(default=None)):
    if not _enabled():
        return _disabled_response(request)
    login = _require_login(authorization)
    if login is None:
        return _login_required_response(request)
    result = _get_service().grant(
        actor_user_id=login.account.user_id, object_id=payload.object_id,
        grantee_user_id=payload.grantee_user_id, permission_level=payload.permission_level,
    )
    logger.info(
        "permission_grant user_id_hash=%s ok=%s reason_code=%s",
        login.user_id_hash or "-", result["ok"], result["reason_code"],
    )
    if not result["ok"]:
        return _forbidden(request, result["reason_code"])
    return GenericPermissionResponse(ok=True, reason_code=result["reason_code"])


@router.post("/revoke", response_model=GenericPermissionResponse,
             responses={status.HTTP_403_FORBIDDEN: {"model": ErrorResponse}})
def revoke(payload: RevokeRequest, request: Request, authorization: str | None = Header(default=None)):
    if not _enabled():
        return _disabled_response(request)
    login = _require_login(authorization)
    if login is None:
        return _login_required_response(request)
    result = _get_service().revoke(
        actor_user_id=login.account.user_id, object_id=payload.object_id,
        grantee_user_id=payload.grantee_user_id,
    )
    logger.info(
        "permission_revoke user_id_hash=%s ok=%s reason_code=%s",
        login.user_id_hash or "-", result["ok"], result["reason_code"],
    )
    if not result["ok"]:
        return _forbidden(request, result["reason_code"])
    return GenericPermissionResponse(ok=True, reason_code=result["reason_code"])


@router.post("/object/read", response_model=ReadObjectResponse,
             responses={status.HTTP_403_FORBIDDEN: {"model": ErrorResponse}})
def read_object(payload: ReadObjectRequest, request: Request, authorization: str | None = Header(default=None)):
    if not _enabled():
        return _disabled_response(request)
    login = _require_login(authorization)
    if login is None:
        return _login_required_response(request)
    result = _get_service().read_object(actor_user_id=login.account.user_id, object_id=payload.object_id)
    logger.info(
        "permission_read_object user_id_hash=%s allowed=%s reason_code=%s",
        login.user_id_hash or "-", result.allowed, result.reason_code,
    )
    if not result.allowed:
        return _forbidden(request, result.reason_code)
    view = result.object_view
    return ReadObjectResponse(
        ok=True, reason_code=result.reason_code,
        effective_level=LEVEL_TO_ROLE.get(result.effective_level),
        object=view.__dict__ if view is not None else None,
    )


@router.get("/audit", response_model=AuditListResponse,
            responses={status.HTTP_403_FORBIDDEN: {"model": ErrorResponse}})
def list_audit(request: Request, authorization: str | None = Header(default=None)):
    if not _enabled():
        return _disabled_response(request)
    login = _require_login(authorization)
    if login is None:
        return _login_required_response(request)
    rows = _get_service().list_audit(actor_user_id=login.account.user_id)
    return AuditListResponse(ok=True, items=[AuditItem(**r) for r in rows])
