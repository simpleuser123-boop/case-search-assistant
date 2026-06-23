"""M5-9 商业化闭环服务：套餐展示 + 试用开通/到期 + 续费意愿采集 + 支付回执联动。

职责与边界：
- 套餐目录：首次启用时按内置默认套餐种子化（仅展示字段），对外只读展示。
- 试用：按套餐 trial_days 开通试用窗口，到期由 refresh_lifecycle 推进状态（不扣款）。
- 续费意愿：记录用户自填续费意愿短码 + 短理由（采集，非预测、非承诺）。
- 支付：支付在平台侧 / 第三方完成；本服务只接收**脱敏回执引用**并联动订阅状态，
  绝不代填 / 代管 / 代存卡号 / 银行账户 / CVV / 令牌明文。
- 埋点：转化 / 续费意愿埋点只产出脱敏字段（plan_id / status / reason_code / count），
  绝不含正文 / 凭据 / 原始 query。

红线：本服务不 import 检索 / rerank / retrieval；计费状态绝不参与主排序或检索质量。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from app.billing.models import (
    BILLING_CYCLE_MONTHLY,
    BILLING_CYCLE_YEARLY,
    PAYMENT_STATUS_REFUNDED,
    PAYMENT_STATUS_SUCCEEDED,
    RENEWAL_INTENT_UNKNOWN,
    SUBSCRIPTION_STATUS_ACTIVE,
    SUBSCRIPTION_STATUS_CANCELED,
    SUBSCRIPTION_STATUS_EXPIRED,
    SUBSCRIPTION_STATUS_TRIALING,
    TRIAL_STATUS_ACTIVE,
    TRIAL_STATUS_CONVERTED,
    TRIAL_STATUS_EXPIRED,
    BillingPlan,
    PaymentReceiptRef,
    Subscription,
    hash_payment_ref,
    hash_subscription_id,
)
from app.billing.privacy import assert_billing_output_clean
from app.billing.store import BillingStore

BILLING_SERVICE_VERSION = "m5-9-billing-v1"

# 内置默认套餐种子（仅展示字段；价格为展示串，工具内不据此扣款）。
DEFAULT_PLANS: tuple[dict, ...] = (
    {
        "billing_plan_id": "plan_solo",
        "plan_name": "个人版",
        "quota_label": "1 席位 / 月 500 次检索",
        "price_display": "¥0（基础）",
        "billing_cycle": BILLING_CYCLE_MONTHLY,
        "seat_quota": 1,
        "trial_days": 0,
        "entitled_features": "",
        "sort_order": 0,
    },
    {
        "billing_plan_id": "plan_team_pro",
        "plan_name": "团队专业版",
        "quota_label": "5 席位 / 月 2000 次检索 / 团队共享",
        "price_display": "¥1980/年",
        "billing_cycle": BILLING_CYCLE_YEARLY,
        "seat_quota": 5,
        "trial_days": 14,
        "entitled_features": "ENABLE_TEAM_WORKSPACE,ENABLE_TEAM_SHARING,ENABLE_BULK_IMPORT",
        "sort_order": 1,
    },
    {
        "billing_plan_id": "plan_firm",
        "plan_name": "律所旗舰版",
        "quota_label": "20 席位 / 不限检索 / 全功能 + 数据治理",
        "price_display": "¥6800/年",
        "billing_cycle": BILLING_CYCLE_YEARLY,
        "seat_quota": 20,
        "trial_days": 30,
        "entitled_features": (
            "ENABLE_TEAM_WORKSPACE,ENABLE_TEAM_SHARING,ENABLE_BULK_IMPORT,"
            "ENABLE_PERMISSION_TIERING"
        ),
        "sort_order": 2,
    },
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def split_features(raw: str) -> list[str]:
    return [f.strip() for f in (raw or "").split(",") if f.strip()]


@dataclass
class BillingAnalyticsEvent:
    """脱敏埋点事件：只含枚举 / 计数 / 哈希引用，绝不含正文 / 凭据 / 原始 query。"""

    event_name: str
    plan_id: str | None = None
    subscription_id_hash: str | None = None
    status: str | None = None
    reason_code: str | None = None
    count: int = 1

    def as_dict(self) -> dict:
        payload = {
            "event_name": self.event_name,
            "plan_id": self.plan_id,
            "subscription_id_hash": self.subscription_id_hash,
            "status": self.status,
            "reason_code": self.reason_code,
            "count": self.count,
        }
        # 埋点产出前强制凭据扫描（fail-closed）。
        assert_billing_output_clean(payload)
        return payload


@dataclass
class BillingResult:
    ok: bool
    subscription: Subscription | None = None
    receipt: PaymentReceiptRef | None = None
    reason_code: str | None = None
    events: list[BillingAnalyticsEvent] = field(default_factory=list)


class BillingService:
    """商业化闭环服务。所有写入经 store（含凭据护栏），不接触主排序。"""

    def __init__(self, store: BillingStore, *, seed_defaults: bool = True) -> None:
        self._store = store
        if seed_defaults:
            self.seed_default_plans()

    def seed_default_plans(self) -> None:
        """种子化内置套餐（幂等 upsert）。仅展示字段，无扣款。"""
        for spec in DEFAULT_PLANS:
            self._store.upsert_plan(BillingPlan(**spec))

    # ---------------- 套餐展示 ----------------
    def list_plans(self) -> list[BillingPlan]:
        return self._store.list_active_plans()

    # ---------------- 试用开通 ----------------
    def start_trial(
        self, *, billing_plan_id: str, owner_user_id: str, team_id: str | None
    ) -> BillingResult:
        plan = self._store.get_plan(billing_plan_id)
        if plan is None or not plan.is_active:
            return BillingResult(ok=False, reason_code="plan_not_found")
        if plan.trial_days <= 0:
            return BillingResult(ok=False, reason_code="plan_no_trial")
        now = _utcnow()
        ends = now + timedelta(days=plan.trial_days)
        sub = self._store.create_subscription(
            billing_plan_id=billing_plan_id,
            owner_user_id=owner_user_id,
            team_id=team_id,
            trial_status=TRIAL_STATUS_ACTIVE,
            subscription_status=SUBSCRIPTION_STATUS_TRIALING,
            trial_started_at=now,
            trial_ends_at=ends,
            current_period_end=ends,
            reason_code="trial_started",
        )
        event = BillingAnalyticsEvent(
            event_name="trial_started",
            plan_id=billing_plan_id,
            subscription_id_hash=hash_subscription_id(sub.subscription_id),
            status=sub.subscription_status,
            reason_code="trial_started",
        )
        return BillingResult(ok=True, subscription=sub, events=[event])

    # ---------------- 续费意愿采集 ----------------
    def record_renewal_intent(
        self,
        *,
        subscription_id: str,
        owner_user_id: str,
        renewal_intent: str,
        renewal_reason: str | None,
    ) -> BillingResult:
        sub = self._store.get_subscription_for_owner(
            subscription_id=subscription_id, owner_user_id=owner_user_id
        )
        if sub is None:
            return BillingResult(ok=False, reason_code="subscription_not_found")
        sub.renewal_intent = renewal_intent or RENEWAL_INTENT_UNKNOWN
        sub.renewal_reason = renewal_reason
        sub.reason_code = "renewal_intent_recorded"
        sub = self._store.update_subscription(sub)
        event = BillingAnalyticsEvent(
            event_name="renewal_intent_recorded",
            plan_id=sub.billing_plan_id,
            subscription_id_hash=hash_subscription_id(sub.subscription_id),
            status=sub.subscription_status,
            reason_code=renewal_intent or RENEWAL_INTENT_UNKNOWN,
        )
        return BillingResult(ok=True, subscription=sub, events=[event])

    # ---------------- 支付回执联动（脱敏）----------------
    def apply_payment_receipt(
        self,
        *,
        subscription_id: str,
        owner_user_id: str,
        raw_payment_ref: str,
        payment_status: str,
        amount_display: str | None,
    ) -> BillingResult:
        """接收平台侧 / 第三方支付回执的脱敏引用，联动订阅状态。

        本方法**不接收也不要求**卡号 / 银行账户 / CVV / 令牌；raw_payment_ref 是
        第三方回执号，落库前在 store 内被哈希。工具不代填支付表单、不代输入凭据。
        """
        sub = self._store.get_subscription_for_owner(
            subscription_id=subscription_id, owner_user_id=owner_user_id
        )
        if sub is None:
            return BillingResult(ok=False, reason_code="subscription_not_found")

        receipt = self._store.record_receipt_ref(
            subscription_id=subscription_id,
            raw_payment_ref=raw_payment_ref,
            payment_status=payment_status,
            amount_display=amount_display or "",
            owner_user_id=owner_user_id,
            reason_code=f"receipt_{payment_status}",
        )

        # 回执状态联动订阅状态（仅状态机，不扣款）。
        if payment_status == PAYMENT_STATUS_SUCCEEDED:
            if sub.trial_status == TRIAL_STATUS_ACTIVE:
                sub.trial_status = TRIAL_STATUS_CONVERTED
            sub.subscription_status = SUBSCRIPTION_STATUS_ACTIVE
            sub.reason_code = "payment_succeeded"
        elif payment_status == PAYMENT_STATUS_REFUNDED:
            sub.subscription_status = SUBSCRIPTION_STATUS_CANCELED
            sub.reason_code = "payment_refunded"
        else:
            sub.reason_code = f"payment_{payment_status}"
        sub = self._store.update_subscription(sub)

        event = BillingAnalyticsEvent(
            event_name="payment_receipt_applied",
            plan_id=sub.billing_plan_id,
            subscription_id_hash=hash_subscription_id(sub.subscription_id),
            status=sub.subscription_status,
            reason_code=f"receipt_{payment_status}",
        )
        return BillingResult(ok=True, subscription=sub, receipt=receipt, events=[event])

    # ---------------- 试用 / 周期到期推进 ----------------
    def refresh_lifecycle(self, sub: Subscription) -> Subscription:
        """按当前时间推进试用 / 订阅到期状态（纯状态机，不扣款、不读检索）。"""
        now = _utcnow()
        changed = False
        if (
            sub.subscription_status == SUBSCRIPTION_STATUS_TRIALING
            and sub.trial_status == TRIAL_STATUS_ACTIVE
            and sub.trial_ends_at is not None
            and _aware(sub.trial_ends_at) <= now
        ):
            sub.trial_status = TRIAL_STATUS_EXPIRED
            sub.subscription_status = SUBSCRIPTION_STATUS_EXPIRED
            sub.reason_code = "trial_expired"
            changed = True
        elif (
            sub.subscription_status == SUBSCRIPTION_STATUS_ACTIVE
            and sub.current_period_end is not None
            and _aware(sub.current_period_end) <= now
        ):
            sub.subscription_status = SUBSCRIPTION_STATUS_EXPIRED
            sub.reason_code = "period_expired"
            changed = True
        if changed:
            sub = self._store.update_subscription(sub)
        return sub


def _aware(dt: datetime) -> datetime:
    """把可能的 naive datetime 视为 UTC，避免比较时报错。"""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt
