"""M5-9 商业化闭环持久层：套餐目录 + 订阅/试用/续费意愿 + 支付回执引用读写。

红线（运行时防御）：
- 本 store 只写 m5_billing_plan / m5_subscription / m5_payment_receipt_ref 三张表，
  且每次写入前调用 assert_billing_output_clean 对入参做凭据扫描（fail-closed）。
- 支付回执只落 payment_ref 的单向哈希 + 状态 + 金额展示串；原始回执号 / 卡号 /
  令牌绝不入库（store 不接受原始 ref 写列，只接受已哈希值）。
- 不改变检索 / 排序：本 store 不 import 检索 / rerank / retrieval。
- 引擎可注入：生产用 app.core.db.engine（postgres）；测试注入临时 sqlite。
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, select

from app.billing.models import (
    PAYMENT_STATUS_PENDING,
    SUBSCRIPTION_STATUS_TRIALING,
    TRIAL_STATUS_NONE,
    BillingPlan,
    PaymentReceiptRef,
    Subscription,
    hash_payment_ref,
)
from app.billing.privacy import assert_billing_output_clean


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class BillingStore:
    """套餐 / 订阅 / 回执引用读写。写入前强制凭据扫描，绝不落凭据。"""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def init_schema(self) -> None:
        """仅创建 M5-9 三张表。只有 ENABLE_BILLING=true 时才会被调用。"""
        SQLModel.metadata.create_all(
            self._engine,
            tables=[
                BillingPlan.__table__,
                Subscription.__table__,
                PaymentReceiptRef.__table__,
            ],
        )

    # ---------------- 套餐目录 ----------------
    def upsert_plan(self, plan: BillingPlan) -> BillingPlan:
        assert_billing_output_clean(plan.model_dump())
        with Session(self._engine) as session:
            existing = session.get(BillingPlan, plan.billing_plan_id)
            if existing is None:
                session.add(plan)
            else:
                for field in (
                    "plan_name", "quota_label", "price_display", "billing_cycle",
                    "seat_quota", "trial_days", "entitled_features", "is_active",
                    "sort_order",
                ):
                    setattr(existing, field, getattr(plan, field))
                existing.updated_at = _utcnow()
                session.add(existing)
            session.commit()
            return session.get(BillingPlan, plan.billing_plan_id)  # type: ignore[return-value]

    def list_active_plans(self) -> list[BillingPlan]:
        with Session(self._engine) as session:
            return list(
                session.exec(
                    select(BillingPlan)
                    .where(BillingPlan.is_active == True)  # noqa: E712
                    .order_by(BillingPlan.sort_order.asc())  # type: ignore[union-attr]
                ).all()
            )

    def get_plan(self, billing_plan_id: str) -> BillingPlan | None:
        with Session(self._engine) as session:
            return session.get(BillingPlan, billing_plan_id)

    # ---------------- 订阅 / 试用 / 续费意愿 ----------------
    def create_subscription(
        self,
        *,
        billing_plan_id: str,
        owner_user_id: str,
        team_id: str | None,
        trial_status: str = TRIAL_STATUS_NONE,
        subscription_status: str = SUBSCRIPTION_STATUS_TRIALING,
        trial_started_at: datetime | None = None,
        trial_ends_at: datetime | None = None,
        current_period_end: datetime | None = None,
        reason_code: str | None = None,
    ) -> Subscription:
        sub = Subscription(
            subscription_id=f"sub_{uuid.uuid4().hex[:24]}",
            billing_plan_id=billing_plan_id,
            owner_user_id=owner_user_id,
            team_id=team_id,
            trial_status=trial_status,
            subscription_status=subscription_status,
            trial_started_at=trial_started_at,
            trial_ends_at=trial_ends_at,
            current_period_end=current_period_end,
            reason_code=reason_code,
        )
        assert_billing_output_clean(sub.model_dump(mode="json"))
        with Session(self._engine) as session:
            session.add(sub)
            session.commit()
            session.refresh(sub)
        return sub

    def get_subscription(self, subscription_id: str) -> Subscription | None:
        with Session(self._engine) as session:
            return session.get(Subscription, subscription_id)

    def get_subscription_for_owner(
        self, *, subscription_id: str, owner_user_id: str
    ) -> Subscription | None:
        """对象级隔离：只取归属该 owner 的订阅，跨用户读取返回 None。"""
        sub = self.get_subscription(subscription_id)
        if sub is None or sub.owner_user_id != owner_user_id:
            return None
        return sub

    def latest_subscription_for_owner(
        self, *, owner_user_id: str
    ) -> Subscription | None:
        with Session(self._engine) as session:
            return session.exec(
                select(Subscription)
                .where(Subscription.owner_user_id == owner_user_id)
                .order_by(Subscription.created_at.desc())  # type: ignore[union-attr]
            ).first()

    def update_subscription(self, sub: Subscription) -> Subscription:
        assert_billing_output_clean(sub.model_dump(mode="json"))
        sub.updated_at = _utcnow()
        with Session(self._engine) as session:
            session.add(sub)
            session.commit()
            session.refresh(sub)
        return sub

    # ---------------- 支付回执引用（脱敏）----------------
    def record_receipt_ref(
        self,
        *,
        subscription_id: str,
        raw_payment_ref: str,
        payment_status: str,
        amount_display: str,
        owner_user_id: str,
        reason_code: str | None = None,
    ) -> PaymentReceiptRef:
        """落一条脱敏回执引用：raw_payment_ref 立即哈希，原始值绝不入库。"""
        receipt = PaymentReceiptRef(
            receipt_id=f"rcpt_{uuid.uuid4().hex[:24]}",
            subscription_id=subscription_id,
            payment_ref_hash=hash_payment_ref(raw_payment_ref),
            payment_status=payment_status or PAYMENT_STATUS_PENDING,
            amount_display=amount_display or "",
            owner_user_id=owner_user_id,
            reason_code=reason_code,
        )
        # 落库前再扫一遍（amount_display 等展示串不得夹带卡号 / 令牌）。
        assert_billing_output_clean(receipt.model_dump(mode="json"))
        with Session(self._engine) as session:
            session.add(receipt)
            session.commit()
            session.refresh(receipt)
        return receipt
