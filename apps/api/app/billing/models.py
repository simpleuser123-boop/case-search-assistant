"""M5-9 商业化闭环数据模型：套餐目录 + 订阅/试用/续费意愿账本 + 支付回执引用。

设计要点（落地基调：先合同 + 骨架，敏感项谨慎落地）：
- ``BillingPlan``：面向律所采购的**套餐目录**，只存展示字段（套餐名 / 额度 / 价格展示 /
  计费周期 / 联动 feature 列表 / 状态）。价格仅作**展示**，不在工具内发起任何扣款。
- ``Subscription``：某 team/owner 的**订阅 + 试用 + 续费意愿**账本，只存白名单结构化字段
  （套餐引用 / 试用状态 / 订阅状态 / 续费意愿码 + 用户自填短理由 / 归属 / 时间戳）。
- ``PaymentReceiptRef``：支付由平台侧 / 第三方完成后，工具**只记录脱敏回执引用**
  （payment_ref 的单向哈希 + 结算状态 + 金额展示串 + reason code），**绝不存**卡号 /
  银行账户 / CVV / 第三方支付令牌明文。

字段白名单（M5-1 合同：结构化关系 + 状态/计数/枚举 + 自填短文本 + 时间戳 + reason code）。
绝不含任何支付凭据列。所有写入只在 ENABLE_BILLING=true 时触发；关闭时不建表、不写入。
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from sqlmodel import Field, SQLModel

# --- 试用状态短枚举（结构化字段，非正文）---
TRIAL_STATUS_NONE = "none"          # 未开通试用
TRIAL_STATUS_ACTIVE = "active"      # 试用进行中
TRIAL_STATUS_EXPIRED = "expired"    # 试用已到期
TRIAL_STATUS_CONVERTED = "converted"  # 试用已转付费
TRIAL_STATUSES = (
    TRIAL_STATUS_NONE,
    TRIAL_STATUS_ACTIVE,
    TRIAL_STATUS_EXPIRED,
    TRIAL_STATUS_CONVERTED,
)

# --- 订阅状态短枚举 ---
SUBSCRIPTION_STATUS_TRIALING = "trialing"    # 处于试用期
SUBSCRIPTION_STATUS_ACTIVE = "active"        # 已付费生效
SUBSCRIPTION_STATUS_PAST_DUE = "past_due"    # 到期未续费（宽限）
SUBSCRIPTION_STATUS_CANCELED = "canceled"    # 已取消 / 退订
SUBSCRIPTION_STATUS_EXPIRED = "expired"      # 已过期失效
SUBSCRIPTION_STATUSES = (
    SUBSCRIPTION_STATUS_TRIALING,
    SUBSCRIPTION_STATUS_ACTIVE,
    SUBSCRIPTION_STATUS_PAST_DUE,
    SUBSCRIPTION_STATUS_CANCELED,
    SUBSCRIPTION_STATUS_EXPIRED,
)

# --- 续费意愿短枚举（用户自填采集，非预测、非承诺）---
RENEWAL_INTENT_UNKNOWN = "unknown"        # 未表态
RENEWAL_INTENT_WILL_RENEW = "will_renew"  # 倾向续费
RENEWAL_INTENT_UNDECIDED = "undecided"    # 仍在考虑
RENEWAL_INTENT_WILL_CHURN = "will_churn"  # 倾向不续费
RENEWAL_INTENTS = (
    RENEWAL_INTENT_UNKNOWN,
    RENEWAL_INTENT_WILL_RENEW,
    RENEWAL_INTENT_UNDECIDED,
    RENEWAL_INTENT_WILL_CHURN,
)

# --- 计费周期短枚举 ---
BILLING_CYCLE_MONTHLY = "monthly"
BILLING_CYCLE_YEARLY = "yearly"
BILLING_CYCLES = (BILLING_CYCLE_MONTHLY, BILLING_CYCLE_YEARLY)

# --- 支付结算状态短枚举（回执引用用，非凭据）---
PAYMENT_STATUS_PENDING = "pending"      # 平台侧 / 第三方处理中
PAYMENT_STATUS_SUCCEEDED = "succeeded"  # 结算成功（回执）
PAYMENT_STATUS_FAILED = "failed"        # 结算失败（回执）
PAYMENT_STATUS_REFUNDED = "refunded"    # 已退款（回执）
PAYMENT_STATUSES = (
    PAYMENT_STATUS_PENDING,
    PAYMENT_STATUS_SUCCEEDED,
    PAYMENT_STATUS_FAILED,
    PAYMENT_STATUS_REFUNDED,
)

# 自填短字段长度上限（防止自由长文本经备注混入持久层）。
RENEWAL_REASON_MAX_LENGTH = 200
PRICE_DISPLAY_MAX_LENGTH = 60


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class BillingPlan(SQLModel, table=True):
    """套餐目录。仅展示字段；价格只作展示，工具内不发起扣款。"""

    __tablename__ = "m5_billing_plan"

    # billing_plan_id：套餐稳定标识（如 plan_team_pro）。
    billing_plan_id: str = Field(primary_key=True, max_length=64)
    # plan_name：套餐展示名（如"团队专业版"）。
    plan_name: str = Field(max_length=80)
    # quota_label：额度展示串（如"5 个席位 / 月 2000 次检索"），仅展示，非计量逻辑。
    quota_label: str = Field(default="", max_length=120)
    # price_display：价格展示串（含币种，如"¥1980/年"）；仅展示，绝不据此扣款。
    price_display: str = Field(default="", max_length=PRICE_DISPLAY_MAX_LENGTH)
    # billing_cycle：monthly / yearly。
    billing_cycle: str = Field(default=BILLING_CYCLE_YEARLY, max_length=16)
    # seat_quota：席位数（结构化整数，用于功能门控展示，非扣款）。
    seat_quota: int = Field(default=0)
    # trial_days：试用天数（0 表示无试用）。
    trial_days: int = Field(default=0)
    # entitled_features：该套餐解锁的 feature flag 名清单（逗号分隔短码，非正文）。
    entitled_features: str = Field(default="", max_length=255)
    # is_active：套餐是否在售（下架后不展示，不影响已存在订阅状态）。
    is_active: bool = Field(default=True)
    # sort_order：展示排序。
    sort_order: int = Field(default=0)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class Subscription(SQLModel, table=True):
    """订阅 + 试用 + 续费意愿账本。仅白名单字段，无任何支付凭据。"""

    __tablename__ = "m5_subscription"

    # subscription_id：订阅稳定标识。
    subscription_id: str = Field(primary_key=True, max_length=64)
    # billing_plan_id：引用的套餐（外键语义，仅引用 id）。
    billing_plan_id: str = Field(index=True, max_length=64)
    # owner_user_id：订阅归属人（发起采购的账号）。
    owner_user_id: str = Field(index=True, max_length=64)
    # team_id：订阅归属团队（律所采购通常以 team 为单位）；为空表示个人订阅。
    team_id: str | None = Field(default=None, index=True, max_length=64)
    # trial_status：none / active / expired / converted。
    trial_status: str = Field(default=TRIAL_STATUS_NONE, max_length=16)
    # subscription_status：trialing / active / past_due / canceled / expired。
    subscription_status: str = Field(default=SUBSCRIPTION_STATUS_TRIALING, max_length=16)
    # renewal_intent：用户自填续费意愿短码（采集，非预测）。
    renewal_intent: str = Field(default=RENEWAL_INTENT_UNKNOWN, max_length=16)
    # renewal_reason：用户自填续费意愿短理由（短文本，非正文）。
    renewal_reason: str | None = Field(default=None, max_length=RENEWAL_REASON_MAX_LENGTH)
    # trial_started_at / trial_ends_at：试用窗口。
    trial_started_at: datetime | None = Field(default=None)
    trial_ends_at: datetime | None = Field(default=None)
    # current_period_end：当前计费周期结束时间（用于到期 / 续费提示）。
    current_period_end: datetime | None = Field(default=None)
    # reason_code：最近一次状态变更短码（脱敏审计），非正文。
    reason_code: str | None = Field(default=None, max_length=64)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class PaymentReceiptRef(SQLModel, table=True):
    """支付回执**脱敏引用**。绝不存卡号 / 银行账户 / CVV / 第三方令牌明文。

    支付在平台侧 / 第三方完成后，工具仅落一条回执引用：
    payment_ref 的单向哈希 + 结算状态 + 金额展示串 + reason code，用于对账与订阅联动。
    """

    __tablename__ = "m5_payment_receipt_ref"

    # receipt_id：回执引用稳定标识（工具侧生成，非第三方令牌）。
    receipt_id: str = Field(primary_key=True, max_length=64)
    # subscription_id：关联订阅。
    subscription_id: str = Field(index=True, max_length=64)
    # payment_ref_hash：第三方支付回执号的单向哈希（绝不存原始回执号 / 令牌 / 卡号）。
    payment_ref_hash: str = Field(max_length=80)
    # payment_status：pending / succeeded / failed / refunded（回执状态，非凭据）。
    payment_status: str = Field(default=PAYMENT_STATUS_PENDING, max_length=16)
    # amount_display：金额展示串（含币种，如"¥1980"），仅展示 / 对账，非扣款凭据。
    amount_display: str = Field(default="", max_length=PRICE_DISPLAY_MAX_LENGTH)
    # owner_user_id：发起支付的账号（脱敏留痕用）。
    owner_user_id: str = Field(index=True, max_length=64)
    # reason_code：回执处理短码（脱敏审计），非正文。
    reason_code: str | None = Field(default=None, max_length=64)
    created_at: datetime = Field(default_factory=utcnow)


def hash_payment_ref(raw_ref: str | None) -> str:
    """把第三方支付回执号 / 令牌转成不可逆存储哈希。原始值绝不入库 / 入日志。"""
    if not raw_ref:
        return "pref_none"
    digest = hashlib.sha256(raw_ref.encode("utf-8")).hexdigest()
    return f"pref_{digest[:24]}"


def hash_subscription_id(sub_id: str | None) -> str:
    """日志 / 埋点用的 subscription_id 脱敏哈希（截断）。"""
    if not sub_id:
        return "sidh_none"
    digest = hashlib.sha256(sub_id.encode("utf-8")).hexdigest()
    return f"sidh_{digest[:16]}"
