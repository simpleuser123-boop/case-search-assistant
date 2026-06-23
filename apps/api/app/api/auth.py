"""M5-2 账号/认证 API 路由（flag-gated）。

ENABLE_ACCOUNT_SYSTEM=false（默认）时：所有端点返回 403 ACCOUNT_SYSTEM_DISABLED，
不建表、不读写、不暴露任何账号能力，行为与 M4 单用户私有末态一致。

日志红线：只记录 user_id_hash / status / reason_code；
绝不记录 login_name、password、session_token、display_name。
"""
from __future__ import annotations

from typing import Callable

from fastapi import APIRouter, Header, Request, status

from app.account.migration import evaluate_claim
from app.account.models import hash_user_id
from app.account.schemas import (
    AuthResponse,
    ClaimRequest,
    ClaimResponse,
    LoginRequest,
    LogoutResponse,
    PublicAccountModel,
    RegisterRequest,
    SessionResponse,
)
from app.account.service import AuthResult, AuthService
from app.account.store import AccountStore
from app.api.errors import api_error_response
from app.core.config import settings
from app.core.db import engine
from app.core.logging import logger
from app.schemas import ErrorResponse

router = APIRouter(prefix="/api/auth", tags=["auth"])

ACCOUNT_SYSTEM_DISABLED_CODE = "ACCOUNT_SYSTEM_DISABLED"

# 懒初始化：仅当 flag 打开时才建表 / 构造服务，关闭态零副作用。
_auth_service: AuthService | None = None


def _get_service() -> AuthService:
    global _auth_service
    if _auth_service is None:
        store = AccountStore(engine)
        store.init_schema()
        _auth_service = AuthService(store)
    return _auth_service


# 测试可注入服务（临时 sqlite），避免依赖 postgres。
def set_auth_service_for_test(service: AuthService | None) -> None:
    global _auth_service
    _auth_service = service


def _enabled() -> bool:
    return bool(getattr(settings, "ENABLE_ACCOUNT_SYSTEM", False))


def _disabled_response(request: Request):
    query_session_id = getattr(request.state, "query_session_id", None)
    # 关闭态只记录脱敏事件，不含任何凭据/正文。
    logger.info(
        "account_system_disabled path=%s reason_code=%s",
        request.url.path,
        "ENABLE_ACCOUNT_SYSTEM_false",
    )
    return api_error_response(
        status_code=status.HTTP_403_FORBIDDEN,
        code=ACCOUNT_SYSTEM_DISABLED_CODE,
        message="账号体系未启用（ENABLE_ACCOUNT_SYSTEM=false），当前为 M4 单用户私有模式。",
        query_session_id=query_session_id,
    )


def _log_auth(event: str, result: AuthResult) -> None:
    # 只记录脱敏字段；绝不记录 login_name / password / session_token。
    logger.info(
        "%s ok=%s user_id_hash=%s reason_code=%s",
        event,
        result.ok,
        result.user_id_hash or "-",
        result.reason_code or "-",
    )


def _public_model(result: AuthResult) -> PublicAccountModel | None:
    if result.account is None:
        return None
    return PublicAccountModel(
        user_id=result.account.user_id,
        display_name=result.account.display_name,
        account_status=result.account.account_status,
        auth_provider=result.account.auth_provider,
    )


@router.post(
    "/register",
    response_model=AuthResponse,
    responses={status.HTTP_403_FORBIDDEN: {"model": ErrorResponse}},
)
def register(payload: RegisterRequest, request: Request):
    if not _enabled():
        return _disabled_response(request)
    result = _get_service().register(
        login_name=payload.login_name,
        password=payload.password,
        display_name=payload.display_name,
    )
    _log_auth("account_register", result)
    if not result.ok:
        return api_error_response(
            status_code=status.HTTP_400_BAD_REQUEST,
            code="REGISTER_REJECTED",
            message="注册未通过（详见 reason_code）。",
            query_session_id=getattr(request.state, "query_session_id", None),
        )
    # 注册不下发 session_token；需登录获取。
    return AuthResponse(ok=True, account=_public_model(result), reason_code=result.reason_code)


@router.post(
    "/login",
    response_model=AuthResponse,
    responses={status.HTTP_403_FORBIDDEN: {"model": ErrorResponse}},
)
def login(payload: LoginRequest, request: Request):
    if not _enabled():
        return _disabled_response(request)
    result = _get_service().login(login_name=payload.login_name, password=payload.password)
    _log_auth("account_login", result)
    if not result.ok:
        return api_error_response(
            status_code=status.HTTP_401_UNAUTHORIZED,
            code="LOGIN_REJECTED",
            message="登录未通过（详见 reason_code）。",
            query_session_id=getattr(request.state, "query_session_id", None),
        )
    # session_token 一次性下发给客户端；服务端只存其哈希，不入日志。
    return AuthResponse(
        ok=True,
        account=_public_model(result),
        session_token=result.session_token,
        expires_at=result.expires_at,
        reason_code=result.reason_code,
    )


def _bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    parts = authorization.split(" ", 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip()
    return None


@router.post(
    "/logout",
    response_model=LogoutResponse,
    responses={status.HTTP_403_FORBIDDEN: {"model": ErrorResponse}},
)
def logout(request: Request, authorization: str | None = Header(default=None)):
    if not _enabled():
        return _disabled_response(request)
    token = _bearer_token(authorization)
    if not token:
        return LogoutResponse(ok=False, reason_code="invalid_session")
    result = _get_service().logout(session_token=token)
    _log_auth("account_logout", result)
    return LogoutResponse(ok=result.ok, reason_code=result.reason_code)


@router.get(
    "/session",
    response_model=SessionResponse,
    responses={status.HTTP_403_FORBIDDEN: {"model": ErrorResponse}},
)
def session(request: Request, authorization: str | None = Header(default=None)):
    if not _enabled():
        return _disabled_response(request)
    token = _bearer_token(authorization)
    if not token:
        return SessionResponse(ok=False, reason_code="invalid_session")
    result = _get_service().resolve_session(session_token=token)
    _log_auth("account_session", result)
    return SessionResponse(
        ok=result.ok,
        account=_public_model(result),
        expires_at=result.expires_at,
        reason_code=result.reason_code,
    )


@router.post(
    "/claim",
    response_model=ClaimResponse,
    responses={status.HTTP_403_FORBIDDEN: {"model": ErrorResponse}},
)
def claim(payload: ClaimRequest, request: Request, authorization: str | None = Header(default=None)):
    """把匿名 localStorage 沉淀认领到当前登录账号。需显式 confirm=true。"""
    if not _enabled():
        return _disabled_response(request)
    token = _bearer_token(authorization)
    session_result = _get_service().resolve_session(session_token=token) if token else AuthResult(ok=False)
    if not session_result.ok or session_result.account is None:
        return api_error_response(
            status_code=status.HTTP_401_UNAUTHORIZED,
            code="CLAIM_REQUIRES_LOGIN",
            message="认领需先登录。",
            query_session_id=getattr(request.state, "query_session_id", None),
        )
    if not payload.confirm:
        # 迁移默认不自动执行。
        logger.info(
            "account_claim_skipped user_id_hash=%s reason_code=%s",
            session_result.user_id_hash or "-",
            "confirm_required",
        )
        return ClaimResponse(
            ok=False,
            owner_user_id_hash=session_result.user_id_hash,
            reason_code="confirm_required",
        )
    items = [item.model_dump(exclude_none=True) for item in payload.items]
    outcome = evaluate_claim(owner_user_id=session_result.account.user_id, items=items)
    logger.info(
        "account_claim user_id_hash=%s requested=%s claimed=%s degraded=%s rejected=%s",
        hash_user_id(outcome.owner_user_id),
        outcome.requested_count,
        outcome.claimed_count,
        outcome.degraded_count,
        outcome.rejected_count,
    )
    return ClaimResponse(
        ok=True,
        owner_user_id_hash=hash_user_id(outcome.owner_user_id),
        requested_count=outcome.requested_count,
        claimed_count=outcome.claimed_count,
        degraded_count=outcome.degraded_count,
        rejected_count=outcome.rejected_count,
        reason_codes=outcome.reason_codes,
        reason_code="claimed",
    )
