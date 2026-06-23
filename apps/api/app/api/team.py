"""M5-3 团队空间 API 路由（flag-gated）。

ENABLE_TEAM_WORKSPACE=false（默认）时：所有端点返回 403 TEAM_WORKSPACE_DISABLED，
不建表、不读写、不暴露任何团队能力，行为与 M5-2 / M4 单用户私有末态一致。

隔离红线：
- 所有沉淀读写都需登录（复用 M5-2 会话校验），并经 TeamService 解析租户上下文；
  请求带 team_id 但非该团队成员时降级为单用户私有态（绝不串读他团队）。
- 日志只记录 user_id_hash / team_id_hash / count / status / reason_code；
  绝不记录 login_name / 正文 / 凭据。
"""
from __future__ import annotations

from fastapi import APIRouter, Header, Request, status

from app.account.models import hash_user_id
from app.account.service import AuthResult
from app.api.errors import api_error_response
from app.core.config import settings
from app.core.db import engine
from app.core.logging import logger
from app.schemas import ErrorResponse
from app.team.models import hash_team_id
from app.team.schemas import (
    AddMemberRequest,
    CreateTeamRequest,
    CreateTeamResponse,
    GenericTeamResponse,
    ListSedimentRequest,
    ListSedimentResponse,
    SaveSedimentRequest,
    SaveSedimentResponse,
    SedimentItemView,
    TeamListResponse,
    TeamView,
)
from app.team.service import TeamService
from app.team.store import TeamStore

router = APIRouter(prefix="/api/team", tags=["team"])

TEAM_WORKSPACE_DISABLED_CODE = "TEAM_WORKSPACE_DISABLED"

# 懒初始化：仅当 flag 打开时才建表 / 构造服务，关闭态零副作用。
_team_service: TeamService | None = None


def _get_service() -> TeamService:
    global _team_service
    if _team_service is None:
        store = TeamStore(engine)
        store.init_schema()
        _team_service = TeamService(store)
    return _team_service


def set_team_service_for_test(service: TeamService | None) -> None:
    global _team_service
    _team_service = service


def _enabled() -> bool:
    return bool(getattr(settings, "ENABLE_TEAM_WORKSPACE", False))


def _disabled_response(request: Request):
    query_session_id = getattr(request.state, "query_session_id", None)
    logger.info(
        "team_workspace_disabled path=%s reason_code=%s",
        request.url.path,
        "ENABLE_TEAM_WORKSPACE_false",
    )
    return api_error_response(
        status_code=status.HTTP_403_FORBIDDEN,
        code=TEAM_WORKSPACE_DISABLED_CODE,
        message="团队空间未启用（ENABLE_TEAM_WORKSPACE=false），当前为单用户私有模式。",
        query_session_id=query_session_id,
    )


# 会话校验复用 M5-2 的 auth service（避免重复实现）。
def _resolve_login(authorization: str | None) -> AuthResult:
    from app.api import auth as auth_api

    token = None
    if authorization:
        parts = authorization.split(" ", 1)
        if len(parts) == 2 and parts[0].lower() == "bearer":
            token = parts[1].strip()
    if not token:
        return AuthResult(ok=False)
    # 仅当账号体系也启用时会话才有效；否则视为未登录。
    if not getattr(settings, "ENABLE_ACCOUNT_SYSTEM", False):
        return AuthResult(ok=False)
    try:
        return auth_api._get_service().resolve_session(session_token=token)  # noqa: SLF001
    except Exception:  # noqa: BLE001 - 校验失败一律视为未登录，不泄露细节
        return AuthResult(ok=False)


def _require_login(request: Request, authorization: str | None) -> AuthResult | None:
    result = _resolve_login(authorization)
    if not result.ok or result.account is None:
        return None
    return result


def _login_required_response(request: Request):
    return api_error_response(
        status_code=status.HTTP_401_UNAUTHORIZED,
        code="TEAM_REQUIRES_LOGIN",
        message="团队空间操作需先登录。",
        query_session_id=getattr(request.state, "query_session_id", None),
    )


@router.post("/create", response_model=CreateTeamResponse,
             responses={status.HTTP_403_FORBIDDEN: {"model": ErrorResponse}})
def create_team(payload: CreateTeamRequest, request: Request, authorization: str | None = Header(default=None)):
    if not _enabled():
        return _disabled_response(request)
    login = _require_login(request, authorization)
    if login is None:
        return _login_required_response(request)
    team = _get_service().create_team(owner_user_id=login.account.user_id, team_name=payload.team_name)
    logger.info(
        "team_create user_id_hash=%s team_id_hash=%s reason_code=%s",
        login.user_id_hash or "-", team["team_id_hash"], "create_team",
    )
    return CreateTeamResponse(ok=True, team=TeamView(**team), reason_code="create_team")


@router.post("/member", response_model=GenericTeamResponse,
             responses={status.HTTP_403_FORBIDDEN: {"model": ErrorResponse}})
def add_member(payload: AddMemberRequest, request: Request, authorization: str | None = Header(default=None)):
    if not _enabled():
        return _disabled_response(request)
    login = _require_login(request, authorization)
    if login is None:
        return _login_required_response(request)
    svc = _get_service()
    # 只有团队的活跃成员才能加人（最小特权；更细分级留待 M5-4）。
    if not svc._store.is_active_member(team_id=payload.team_id, member_user_id=login.account.user_id):  # noqa: SLF001
        return api_error_response(
            status_code=status.HTTP_403_FORBIDDEN,
            code="TEAM_NOT_MEMBER",
            message="非团队成员，无法管理成员。",
            query_session_id=getattr(request.state, "query_session_id", None),
        )
    result = svc.add_member(team_id=payload.team_id, member_user_id=payload.member_user_id)
    logger.info(
        "team_add_member user_id_hash=%s team_id_hash=%s ok=%s reason_code=%s",
        login.user_id_hash or "-", hash_team_id(payload.team_id), result["ok"], result["reason_code"],
    )
    member_count = svc.member_count(team_id=payload.team_id) if result["ok"] else None
    return GenericTeamResponse(ok=result["ok"], reason_code=result["reason_code"], member_count=member_count)


@router.get("/list", response_model=TeamListResponse,
            responses={status.HTTP_403_FORBIDDEN: {"model": ErrorResponse}})
def list_teams(request: Request, authorization: str | None = Header(default=None)):
    if not _enabled():
        return _disabled_response(request)
    login = _require_login(request, authorization)
    if login is None:
        return _login_required_response(request)
    teams = _get_service().list_teams(member_user_id=login.account.user_id)
    return TeamListResponse(ok=True, teams=[TeamView(**t) for t in teams], reason_code="ok")


@router.post("/sediment", response_model=SaveSedimentResponse,
             responses={status.HTTP_403_FORBIDDEN: {"model": ErrorResponse}})
def save_sediment(payload: SaveSedimentRequest, request: Request, authorization: str | None = Header(default=None)):
    if not _enabled():
        return _disabled_response(request)
    login = _require_login(request, authorization)
    if login is None:
        return _login_required_response(request)
    svc = _get_service()
    resolution = svc.resolve_tenant(
        owner_user_id=login.account.user_id, team_id=payload.team_id, workspace_id=payload.workspace_id
    )
    # 越权降级私有态时，强制 visibility=private（不允许把对象写进非成员团队）。
    visibility = payload.visibility if not resolution.downgraded else "private"
    fields = payload.model_dump(exclude_none=True, exclude={"team_id", "workspace_id", "visibility", "object_type"})
    result = svc.save_sediment(
        ctx=resolution.ctx, object_type=payload.object_type, visibility=visibility, payload=fields
    )
    logger.info(
        "team_save_sediment user_id_hash=%s team_id_hash=%s ok=%s downgraded=%s reason_code=%s",
        login.user_id_hash or "-", hash_team_id(resolution.ctx.team_id),
        result["ok"], resolution.downgraded, result["reason_code"],
    )
    return SaveSedimentResponse(
        ok=result["ok"], object_id=result.get("object_id"),
        tenant_downgraded=resolution.downgraded, reason_code=result["reason_code"],
    )


@router.post("/sediment/list", response_model=ListSedimentResponse,
             responses={status.HTTP_403_FORBIDDEN: {"model": ErrorResponse}})
def list_sediment(payload: ListSedimentRequest, request: Request, authorization: str | None = Header(default=None)):
    if not _enabled():
        return _disabled_response(request)
    login = _require_login(request, authorization)
    if login is None:
        return _login_required_response(request)
    svc = _get_service()
    resolution = svc.resolve_tenant(
        owner_user_id=login.account.user_id, team_id=payload.team_id, workspace_id=payload.workspace_id
    )
    views = svc.list_sediment(ctx=resolution.ctx, object_type=payload.object_type)
    items = [SedimentItemView(**view.__dict__) for view in views]
    logger.info(
        "team_list_sediment user_id_hash=%s team_id_hash=%s count=%s downgraded=%s",
        login.user_id_hash or "-", hash_team_id(resolution.ctx.team_id), len(items), resolution.downgraded,
    )
    return ListSedimentResponse(
        ok=True, items=items, tenant_downgraded=resolution.downgraded, reason_code="ok"
    )
