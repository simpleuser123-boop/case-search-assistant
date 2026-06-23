"""M5-5 同步与团队共享 API 路由（flag-gated）。

ENABLE_TEAM_SHARING=false（默认）时：所有端点返回 403 TEAM_SHARING_DISABLED，
不建表、不同步、不共享，行为回到 M4 本地沉淀末态（单用户、纯前端、不上送服务端）。

红线：
- 同步只接受元数据 / 引用 / 锚点 / 用户自填短字段（schema extra=forbid + store 白名单双重拦截），
  绝不上送正文 / 原始案情。同步默认 owner 私有。
- 共享必须显式动作（/share），默认私有；只有对象 owner + 目标团队活跃成员可共享；
  AI 内容承载型无来源锚点不进入共享。
- 所有端点需登录（复用 M5-2 会话；账号体系关则会话无效）。
- 日志只记录 user_id_hash / object_id_hash / team_id_hash / ok / reason_code；
  绝不记录正文 / 凭据 / 锚点内容。
"""
from __future__ import annotations

from fastapi import APIRouter, Header, Request, status

from app.account.service import AuthResult
from app.api.errors import api_error_response
from app.core.config import settings
from app.core.db import engine
from app.core.logging import logger
from app.schemas import ErrorResponse
from app.sharing.models import hash_object_id
from app.sharing.schemas import (
    ListTeamSharesRequest,
    ShareItemView,
    ShareListResponse,
    ShareRequest,
    ShareResponse,
    SyncSedimentRequest,
    SyncSedimentResponse,
    UnshareRequest,
)
from app.sharing.service import SharingService
from app.sharing.store import SharingStore
from app.team.models import hash_team_id
from app.team.store import TeamStore

router = APIRouter(prefix="/api/sharing", tags=["sharing"])

TEAM_SHARING_DISABLED_CODE = "TEAM_SHARING_DISABLED"

_sharing_service: SharingService | None = None


def _get_service() -> SharingService:
    global _sharing_service
    if _sharing_service is None:
        sharing_store = SharingStore(engine)
        sharing_store.init_schema()
        team_store = TeamStore(engine)
        team_store.init_schema()
        _sharing_service = SharingService(sharing_store, team_store)
    return _sharing_service


def set_sharing_service_for_test(service: SharingService | None) -> None:
    global _sharing_service
    _sharing_service = service


def _enabled() -> bool:
    return bool(getattr(settings, "ENABLE_TEAM_SHARING", False))


def _disabled_response(request: Request):
    logger.info(
        "team_sharing_disabled path=%s reason_code=%s",
        request.url.path, "ENABLE_TEAM_SHARING_false",
    )
    return api_error_response(
        status_code=status.HTTP_403_FORBIDDEN,
        code=TEAM_SHARING_DISABLED_CODE,
        message="团队共享未启用（ENABLE_TEAM_SHARING=false），当前为本地沉淀 / owner 私有模式。",
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
        code="SHARING_REQUIRES_LOGIN",
        message="同步 / 共享操作需先登录。",
        query_session_id=getattr(request.state, "query_session_id", None),
    )


@router.post("/sync", response_model=SyncSedimentResponse,
             responses={status.HTTP_403_FORBIDDEN: {"model": ErrorResponse}})
def sync_sediment(payload: SyncSedimentRequest, request: Request, authorization: str | None = Header(default=None)):
    if not _enabled():
        return _disabled_response(request)
    login = _require_login(authorization)
    if login is None:
        return _login_required_response(request)
    fields = payload.model_dump(exclude_none=True, exclude={"object_type"})
    result = _get_service().sync_local(
        owner_user_id=login.account.user_id, object_type=payload.object_type, payload=fields,
    )
    logger.info(
        "sharing_sync user_id_hash=%s object_id_hash=%s ok=%s reason_code=%s",
        login.user_id_hash or "-", hash_object_id(result.object_id), result.ok, result.reason_code,
    )
    return SyncSedimentResponse(
        ok=result.ok, object_id=result.object_id, visibility="private", reason_code=result.reason_code,
    )


@router.post("/share", response_model=ShareResponse,
             responses={status.HTTP_403_FORBIDDEN: {"model": ErrorResponse}})
def share(payload: ShareRequest, request: Request, authorization: str | None = Header(default=None)):
    if not _enabled():
        return _disabled_response(request)
    login = _require_login(authorization)
    if login is None:
        return _login_required_response(request)
    result = _get_service().share_to_team(
        actor_user_id=login.account.user_id, object_id=payload.object_id, team_id=payload.team_id,
    )
    logger.info(
        "sharing_share user_id_hash=%s object_id_hash=%s team_id_hash=%s ok=%s anchors=%s reason_code=%s",
        login.user_id_hash or "-", hash_object_id(payload.object_id), hash_team_id(payload.team_id),
        result.ok, result.anchor_count, result.reason_code,
    )
    return ShareResponse(
        ok=result.ok, share_id=result.share_id, visibility=result.visibility,
        anchor_count=result.anchor_count, reason_code=result.reason_code,
    )


@router.post("/unshare", response_model=ShareResponse,
             responses={status.HTTP_403_FORBIDDEN: {"model": ErrorResponse}})
def unshare(payload: UnshareRequest, request: Request, authorization: str | None = Header(default=None)):
    if not _enabled():
        return _disabled_response(request)
    login = _require_login(authorization)
    if login is None:
        return _login_required_response(request)
    result = _get_service().unshare(actor_user_id=login.account.user_id, object_id=payload.object_id)
    logger.info(
        "sharing_unshare user_id_hash=%s object_id_hash=%s ok=%s reason_code=%s",
        login.user_id_hash or "-", hash_object_id(payload.object_id), result.ok, result.reason_code,
    )
    return ShareResponse(ok=result.ok, visibility="private", reason_code=result.reason_code)


@router.get("/mine", response_model=ShareListResponse,
            responses={status.HTTP_403_FORBIDDEN: {"model": ErrorResponse}})
def list_my_shares(request: Request, authorization: str | None = Header(default=None)):
    if not _enabled():
        return _disabled_response(request)
    login = _require_login(authorization)
    if login is None:
        return _login_required_response(request)
    views = _get_service().list_my_shares(owner_user_id=login.account.user_id)
    return ShareListResponse(ok=True, items=[ShareItemView(**v.__dict__) for v in views], reason_code="ok")


@router.post("/team", response_model=ShareListResponse,
             responses={status.HTTP_403_FORBIDDEN: {"model": ErrorResponse}})
def list_team_shares(payload: ListTeamSharesRequest, request: Request, authorization: str | None = Header(default=None)):
    if not _enabled():
        return _disabled_response(request)
    login = _require_login(authorization)
    if login is None:
        return _login_required_response(request)
    views = _get_service().list_team_shares(actor_user_id=login.account.user_id, team_id=payload.team_id)
    if views is None:
        # 非活跃成员：拒绝，绝不串读他团队共享账本。
        return api_error_response(
            status_code=status.HTTP_403_FORBIDDEN,
            code="SHARING_NOT_MEMBER",
            message="非团队成员，无法查看团队共享。",
            query_session_id=getattr(request.state, "query_session_id", None),
        )
    logger.info(
        "sharing_list_team user_id_hash=%s team_id_hash=%s count=%s",
        login.user_id_hash or "-", hash_team_id(payload.team_id), len(views),
    )
    return ShareListResponse(ok=True, items=[ShareItemView(**v.__dict__) for v in views], reason_code="ok")
