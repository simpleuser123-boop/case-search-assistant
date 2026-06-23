"""M5-9 商业化闭环（套餐/试用/计费/续费意愿）。

红线（本步最高优先级，承接 M5-1~M5-8 并按商业化场景加强）：
- **绝不代填 / 代管 / 代存支付凭据**：卡号 / 银行账户 / CVV / 第三方支付令牌明文。
  支付由平台侧 / 第三方完成，工具仅记录脱敏回执引用（payment_ref hash + status）。
- **只存白名单字段**：套餐 / 订阅 / 试用 / 续费意愿只存结构化短码 / 计数 / 自填短文本 /
  时间戳 / reason code；任何写入前经 privacy 护栏递归扫描，命中凭据即 fail-closed。
- **计费状态绝不参与主排序 / 检索质量**：本包不 import 检索 / rerank / retrieval。
- **默认关闭**：ENABLE_BILLING 默认 false，关闭时不建表、不写入、不展示计费入口，
  所有功能回到 flag 默认态（M5-8 末态）。
- **埋点脱敏**：转化 / 续费意愿埋点只含 plan_id / status / reason_code / count，
  绝不含正文 / 凭据 / 原始 query。
"""
from app.billing.models import (
    BillingPlan,
    PaymentReceiptRef,
    Subscription,
    hash_payment_ref,
    hash_subscription_id,
)
from app.billing.privacy import (
    ForbiddenBillingCredentialError,
    assert_billing_output_clean,
)
from app.billing.service import (
    BILLING_SERVICE_VERSION,
    DEFAULT_PLANS,
    BillingAnalyticsEvent,
    BillingResult,
    BillingService,
    split_features,
)
from app.billing.store import BillingStore

__all__ = [
    "BillingPlan",
    "PaymentReceiptRef",
    "Subscription",
    "hash_payment_ref",
    "hash_subscription_id",
    "ForbiddenBillingCredentialError",
    "assert_billing_output_clean",
    "BILLING_SERVICE_VERSION",
    "DEFAULT_PLANS",
    "BillingAnalyticsEvent",
    "BillingResult",
    "BillingService",
    "split_features",
    "BillingStore",
]
