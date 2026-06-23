"""M5-9 商业化闭环 API schemas。

隐私 / 凭据红线：
- 所有入参 schema ``extra=forbid``：请求体出现任何非白名单键（含卡号 / CVV /
  银行账户 / 支付令牌等凭据键）-> 422，绝不进入 service / 落库。这是第一道拦截，
  service / store / privacy 护栏为后续防线。
- 续费意愿 / 试用开通 / 订阅查询只收结构化短码 + 自填短理由；不收任何支付字段。
- 支付回执上报只收**脱敏引用**（payment_ref + status + amount_display），其中
  payment_ref 在 service 层立即哈希；schema 不接受 card_number / cvv / token 等键
  （extra=forbid 拦截），即使被塞入也 422。
- 响应里没有任何凭据字段；归属标识以哈希呈现。
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class PlanView(BaseModel):
    """套餐展示视图（只读目录）。仅展示字段，无扣款逻辑。"""

    billing_plan_id: str
    plan_name: str
    quota_label: str
    price_display: str
    billing_cycle: str
    seat_quota: int
    trial_days: int
    entitled_features: list[str] = Field(default_factory=list)
    sort_order: int = 0


class PlanListResponse(BaseModel):
    ok: bool
    items: list[PlanView] = Field(default_factory=list)
    reason_code: str | None = None


class StartTrialRequest(BaseModel):
    """开通试用：只引用套餐 + 可选团队上下文，无任何支付字段。"""

    billing_plan_id: str = Field(..., max_length=64)
    team_id: str | None = Field(default=None, max_length=64)

    model_config = ConfigDict(extra="forbid")


class RenewalIntentRequest(BaseModel):
    """续费意愿采集：用户自填短码 + 短理由，不预测、不承诺、不含支付字段。"""

    subscription_id: str = Field(..., max_length=64)
    renewal_intent: Literal["unknown", "will_renew", "undecided", "will_churn"]
    renewal_reason: str | None = Field(default=None, max_length=200)

    model_config = ConfigDict(extra="forbid")


class PaymentReceiptRequest(BaseModel):
    """支付回执上报：只收脱敏引用，绝不收凭据。

    payment_ref：第三方 / 平台侧支付完成后回传的回执号（service 层立即哈希存储）。
    extra=forbid：塞入 card_number / cvv / bank_account / *_token 等键直接 422。
    """

    subscription_id: str = Field(..., max_length=64)
    payment_ref: str = Field(..., max_length=190)
    payment_status: Literal["pending", "succeeded", "failed", "refunded"]
    amount_display: str | None = Field(default=None, max_length=60)

    model_config = ConfigDict(extra="forbid")


class SubscriptionView(BaseModel):
    """订阅视图（脱敏）。无凭据，归属以哈希呈现。"""

    subscription_id: str
    billing_plan_id: str
    trial_status: str
    subscription_status: str
    renewal_intent: str
    renewal_reason: str | None = None
    trial_ends_at: str | None = None
    current_period_end: str | None = None
    owner_user_id_hash: str
    team_id_hash: str


class SubscriptionResponse(BaseModel):
    ok: bool
    subscription: SubscriptionView | None = None
    reason_code: str | None = None


class PaymentReceiptResponse(BaseModel):
    """回执上报响应：只回脱敏引用哈希 + 状态，无凭据。"""

    ok: bool
    receipt_id: str | None = None
    payment_ref_hash: str | None = None
    payment_status: str | None = None
    subscription_status: str | None = None
    reason_code: str | None = None
