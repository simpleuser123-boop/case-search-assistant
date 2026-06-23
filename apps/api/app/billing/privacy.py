"""M5-9 商业化闭环（套餐/试用/计费/续费意愿）支付凭据护栏。

红线（本步最高优先级）：
- 工具内绝不代填、代管、代存任何支付凭据：卡号 / 银行账户 / CVV / 支付令牌明文。
- 套餐 / 订阅 / 续费意愿只存白名单结构化字段（计数 / 状态码 / 自填短文本），
  支付由平台侧 / 第三方完成，工具仅记录脱敏回执引用（payment_ref hash + status）。
- 任何将要落盘 / 返回 / 入日志 / 入埋点 / 入产物的结构，先过本护栏递归扫描；
  命中凭据型键或疑似明文凭据 / 卡号即抛错（fail-closed），绝不放行。

本护栏被 store / service / API / 埋点统一调用，是凭据不落库的最后一道防线。
"""
from __future__ import annotations

import re
from typing import Any

# 支付凭据型禁止键（卡号 / 银行账户 / CVV / 各类支付令牌明文）。命中即抛错。
FORBIDDEN_CREDENTIAL_KEYS = {
    "card_number", "cardnumber", "pan", "card_no", "cardno", "credit_card",
    "creditcard", "card", "cvv", "cvv2", "cvc", "cvc2", "csc", "card_cvv",
    "security_code", "expiry", "expiry_date", "exp_month", "exp_year",
    "bank_account", "bank_account_no", "account_number", "accountnumber",
    "iban", "routing_number", "sort_code", "swift", "bic",
    "payment_token", "pay_token", "card_token", "stripe_token", "alipay_token",
    "wechat_pay_token", "access_token", "refresh_token", "secret_key",
    "api_secret", "private_key", "password", "pwd", "cardholder_name",
    "cardholder", "billing_address",
    # 正文 / 原始 query 型（沿用既有里程碑口径，杜绝正文混入计费结构）
    "raw_query", "query", "case_fact_body", "fact_body", "chunk_body",
    "content", "body", "text_body",
}

# 疑似明文卡号：13~19 位连续数字（允许空格 / 连字符分隔）。
_CARD_LIKE = re.compile(r"(?<![0-9A-Za-z_])(?:\d[ -]?){13,19}(?![0-9A-Za-z_])")
# 第三方支付令牌常见前缀明文（Stripe / 通用 secret）。前缀后跟 6+ 位字母数字。
_TOKEN_LIKE = re.compile(
    r"(?:tok_live_|tok_test_|sk_live_|sk_test_|pk_live_|card_|pi_|seti_)"
    r"[A-Za-z0-9]{6,}"
)


class ForbiddenBillingCredentialError(RuntimeError):
    """计费结构命中支付凭据 / 卡号 / 令牌明文 / 正文护栏（fail-closed）。"""


def _digits_only(text: str) -> str:
    return re.sub(r"[ \-]", "", text)


def _looks_like_card_number(text: str) -> bool:
    """值级兜底：连续 13~19 位数字（允许空格 / 连字符）疑似卡号。

    脱敏回执引用是 hash（含字母）或带前缀短串，不会命中纯数字长串；
    案号 / case_id 含中文括号与年份，不构成 13~19 连续纯数字。
    """
    for m in _CARD_LIKE.finditer(text):
        if 13 <= len(_digits_only(m.group(0))) <= 19:
            return True
    return False


def _scan_text(text: str, path: str) -> None:
    if _TOKEN_LIKE.search(text):
        raise ForbiddenBillingCredentialError(f"payment token-like literal at {path}")
    if _looks_like_card_number(text):
        raise ForbiddenBillingCredentialError(f"card-number-like literal at {path}")


def assert_billing_output_clean(payload: Any, *, path: str = "$") -> None:
    """递归校验计费产物干净；命中凭据键 / 卡号 / 令牌明文 / 正文即抛错。"""
    if isinstance(payload, dict):
        for key, value in payload.items():
            if str(key).strip().lower() in FORBIDDEN_CREDENTIAL_KEYS:
                raise ForbiddenBillingCredentialError(
                    f"forbidden credential key '{key}' at {path}"
                )
            assert_billing_output_clean(value, path=f"{path}.{key}")
    elif isinstance(payload, (list, tuple)):
        for idx, item in enumerate(payload):
            assert_billing_output_clean(item, path=f"{path}[{idx}]")
    elif isinstance(payload, str):
        _scan_text(payload, path)
    # int/float/bool/None: no credential risk, pass.
