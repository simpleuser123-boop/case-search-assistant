"""M5-9 商业化闭环 API 路由（flag-gated + 需登录）。

ENABLE_BILLING=false（默认）时：所有端点返回 403 BILLING_DISABLED，
不建表、不写入、不展示套餐 / 计费入口，行为回到 M5-8 末态。

红线：
- 工具内**绝不代填 / 代管 / 代存支付凭据**：端点 schema extra=forbid 拦截卡号 / CVV /
  银行账户 / 支付令牌键（422）；service / store / privacy 护栏为后续防线。
- 支付由平台侧 / 第三方完成；回执上报端点只接收脱敏引用（payment_ref + status），
  payment_ref 在 service/store 内立即哈希，原始值绝不入库 / 入日志。
- 计费状态绝不参与主排序 / 检索质量（本模块不 import 检索 / rerank）。
- 所有写端点需登录（复用 M5-2 会话；账号体系关则会话无效）。
- 日志只记 user_id_hash / subscription_id_hash / plan_id / status / 短 reason code；
  绝不记录正文 / 凭据 / 原始回执号。
"""
from __future__ import annotations

from fastapi import APIRouter, Header, Request, status

from app.account.models import hash_user_id
from app.account.service import AuthResult
from app.api.errors import api_error_response
from app.billing.models import Subscription, hash_subscription_id
from app.billing.privacy import ForbiddenBillingCredentialError
from app.billing.schemas import (
    PaymentReceiptRequest,
    PaymentReceiptResponse,
    PlanListResponse,
    PlanView,
    RenewalIntentRequest,
    StartTrialRequest,
    SubscriptionResponse,
    SubscriptionView,
)
from app.billing.service import BillingResult, BillingService, split_features
from app.billing.store import BillingStore
from app.core.config import settings
from app.core.db import engine
from app.core.logging import logger
from app.schemas import ErrorResponse
from app.team.models import hash_team_id

router = APIRouter(prefix="/api/billing", tags=["billing"])

BILLING_DISABLED_CODE = "BILLING_DISABLED"
BILLING_REQUIRES_LOGIN_CODE = "BILLING_REQUIRES_LOGIN"
BILLING_GUARD_BLOCK_CODE = "BILLING_GUARD_BLOCK"

_billing_service: BillingService | None = None


def _get_service() -> BillingService:
    global _billing_service
    if _billing_service is None:
        store = BillingStore(engine)
        store.init_schema()
        _billing_service = BillingService(store)
    return _billing_service


def set_billing_service_for_test(service: BillingService | None) -> None:
    global _billing_service
    _billing_service = service


def _enabled() -> bool:
    return bool(getattr(settings, "ENABLE_BILLING", False))


def _disabled_response(request: Request):
    logger.info(
        "billing_disabled path=%s reason_code=%s",
        request.url.path, "ENABLE_BILLING_false",
    )
    return api_error_response(
        status_code=status.HTTP_403_FORBIDDEN,
        code=BILLING_DISABLED_CODE,
        message="计费未启用（ENABLE_BILLING=false），当前不展示套餐 / 计费入口。",
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
        code=BILLING_REQUIRES_LOGIN_CODE,
        message="计费操作需先登录。",
        query_session_id=getattr(request.state, "query_session_id", None),
    )


def _guard_block_response(request: Request):
    """凭据护栏命中：视为不可处理（fail-closed），不泄露细节。"""
    logger.warning(
        "billing_guard_block path=%s reason_code=%s",
        request.url.path, "billing_guard_block",
    )
    return api_error_response(
        status_code=status.HTTP_400_BAD_REQUEST,
        code=BILLING_GUARD_BLOCK_CODE,
        message="计费请求未通过凭据边界校验，已阻断。请勿提交任何支付凭据。",
        query_session_id=getattr(request.state, "query_session_id", None),
    )


def _emit_events(events) -> None:
    """脱敏埋点落日志（只记枚举 / 计数 / 哈希）。"""
    for ev in events:
        payload = ev.as_dict()
        logger.info(
            "billing_event name=%s plan_id=%s status=%s reason_code=%s count=%s",
            payload["event_name"], payload["plan_id"], payload["status"],
            payload["reason_code"], payload["count"],
        )


def _sub_to_view(sub: Subscription) -> SubscriptionView:
    return SubscriptionView(
        subscription_id=sub.subscription_id,
        billing_plan_id=sub.billing_plan_id,
        trial_status=sub.trial_status,
        subscription_status=sub.subscription_status,
        renewal_intent=sub.renewal_intent,
        renewal_reason=sub.renewal_reason,
        trial_ends_at=sub.trial_ends_at.isoformat() if sub.trial_ends_at else None,
        current_period_end=(
            sub.current_period_end.isoformat() if sub.current_period_end else None
        ),
        owner_user_id_hash=hash_user_id(sub.owner_user_id),
        team_id_hash=hash_team_id(sub.team_id),
    )


@router.get("/plans", response_model=PlanListResponse,
            responses={status.HTTP_403_FORBIDDEN: {"model": ErrorResponse}})
def list_plans(request: Request):
    if not _enabled():
        return _disabled_response(request)
    plans = _get_service().list_plans()
    items = [
        PlanView(
            billing_plan_id=p.billing_plan_id,
            plan_name=p.plan_name,
            quota_label=p.quota_label,
            price_display=p.price_display,
            billing_cycle=p.billing_cycle,
            seat_quota=p.seat_quota,
            trial_days=p.trial_days,
            entitled_features=split_features(p.entitled_features),
            sort_order=p.sort_order,
        )
        for p in plans
    ]
    logger.info("billing_plans_served path=%s count=%s", request.url.path, len(items))
    return PlanListResponse(ok=True, items=items)


def _handle(request: Request, result: BillingResult, *, ok_reason: str):
    if not result.ok:
        return SubscriptionResponse(ok=False, reason_code=result.reason_code)
    _emit_events(result.events)
    logger.info(
        "billing_op path=%s reason_code=%s subscription_id_hash=%s status=%s",
        request.url.path, ok_reason,
        hash_subscription_id(result.subscription.subscription_id if result.subscription else None),
        result.subscription.subscription_status if result.subscription else None,
    )
    return SubscriptionResponse(
        ok=True,
        subscription=_sub_to_view(result.subscription) if result.subscription else None,
        reason_code=ok_reason,
    )


@router.post("/trial", response_model=SubscriptionResponse,
             responses={status.HTTP_403_FORBIDDEN: {"model": ErrorResponse}})
def start_trial(payload: StartTrialRequest, request: Request,
                authorization: str | None = Header(default=None)):
    if not _enabled():
        return _disabled_response(request)
    login = _require_login(authorization)
    if login is None:
        return _login_required_response(request)
    try:
        result = _get_service().start_trial(
            billing_plan_id=payload.billing_plan_id,
            owner_user_id=login.account.user_id,
            team_id=payload.team_id,
        )
    except ForbiddenBillingCredentialError:
        return _guard_block_response(request)
    return _handle(request, result, ok_reason="trial_started")


@router.post("/renewal-intent", response_model=SubscriptionResponse,
             responses={status.HTTP_403_FORBIDDEN: {"model": ErrorResponse}})
def record_renewal_intent(payload: RenewalIntentRequest, request: Request,
                          authorization: str | None = Header(default=None)):
    if not _enabled():
        return _disabled_response(request)
    login = _require_login(authorization)
    if login is None:
        return _login_required_response(request)
    try:
        result = _get_service().record_renewal_intent(
            subscription_id=payload.subscription_id,
            owner_user_id=login.account.user_id,
            renewal_intent=payload.renewal_intent,
            renewal_reason=payload.renewal_reason,
        )
    except ForbiddenBillingCredentialError:
        return _guard_block_response(request)
    return _handle(request, result, ok_reason="renewal_intent_recorded")


@router.post("/payment-receipt", response_model=PaymentReceiptResponse,
             responses={status.HTTP_403_FORBIDDEN: {"model": ErrorResponse}})
def apply_payment_receipt(payload: PaymentReceiptRequest, request: Request,
                          authorization: str | None = Header(default=None)):
    """上报平台侧 / 第三方支付回执的**脱敏引用**，联动订阅状态。

    工具不代填支付表单、不代输入卡号 / 银行信息；本端点仅接收回执号（立即哈希）。
    schema extra=forbid 已拦截卡号 / CVV / 令牌键（422）。
    """
    if not _enabled():
        return _disabled_response(request)
    login = _require_login(authorization)
    if login is None:
        return _login_required_response(request)
    try:
        result = _get_service().apply_payment_receipt(
            subscription_id=payload.subscription_id,
            owner_user_id=login.account.user_id,
            raw_payment_ref=payload.payment_ref,
            payment_status=payload.payment_status,
            amount_display=payload.amount_display,
        )
    except ForbiddenBillingCredentialError:
        return _guard_block_response(request)
    if not result.ok:
        return PaymentReceiptResponse(ok=False, reason_code=result.reason_code)
    _emit_events(result.events)
    return PaymentReceiptResponse(
        ok=True,
        receipt_id=result.receipt.receipt_id if result.receipt else None,
        payment_ref_hash=result.receipt.payment_ref_hash if result.receipt else None,
        payment_status=result.receipt.payment_status if result.receipt else None,
        subscription_status=(
            result.subscription.subscription_status if result.subscription else None
        ),
        reason_code="payment_receipt_applied",
    )


@router.get("/subscription", response_model=SubscriptionResponse,
            responses={status.HTTP_403_FORBIDDEN: {"model": ErrorResponse}})
def get_my_subscription(request: Request,
                        authorization: str | None = Header(default=None)):
    if not _enabled():
        return _disabled_response(request)
    login = _require_login(authorization)
    if login is None:
        return _login_required_response(request)
    service = _get_service()
    sub = service._store.latest_subscription_for_owner(  # noqa: SLF001
        owner_user_id=login.account.user_id
    )
    if sub is None:
        return SubscriptionResponse(ok=True, subscription=None, reason_code="no_subscription")
    sub = service.refresh_lifecycle(sub)
    return SubscriptionResponse(ok=True, subscription=_sub_to_view(sub),
                                reason_code="ok")
