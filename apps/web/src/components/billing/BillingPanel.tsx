import { useEffect, useState } from "react";

import { isBillingEnabled } from "../../config/featureFlags";
import {
  fetchMySubscription,
  fetchPlans,
  startTrial,
  submitRenewalIntent,
  type Plan,
  type RenewalIntent,
  type Subscription,
} from "../../services/billingApi";

// M5-9 商业化闭环（套餐/试用/计费/续费意愿）展示面板（flag-gated）。
//
// 默认 false（isBillingEnabled=false）时直接渲染 null：不显示任何套餐/计费/试用/
// 续费意愿入口，不调用任何接口，页面回到 M5-8 末态。
//
// 凭据红线（前端硬约束）：
//   - 本面板没有任何卡号 / 银行账户 / CVV / 支付令牌输入框；绝不代填、代管、代存凭据。
//   - 支付在平台侧 / 第三方完成；面板只展示套餐目录、开通试用、采集续费意愿、
//     展示脱敏订阅状态。需要支付时，引导用户到平台侧支付页（不在工具内输入凭据）。
//   - 续费意愿仅采集用户自填短码 + 短理由，非预测、非承诺。
//   - 计费状态不参与主排序 / 召回 / source selection（仅展示层）。

const RENEWAL_OPTIONS: { value: RenewalIntent; label: string }[] = [
  { value: "will_renew", label: "倾向续费" },
  { value: "undecided", label: "仍在考虑" },
  { value: "will_churn", label: "倾向不续费" },
];

function PlanCard({
  plan,
  onTrial,
  busy,
}: {
  plan: Plan;
  onTrial: (planId: string) => void;
  busy: boolean;
}) {
  return (
    <div className="rounded-[4px] border border-[var(--color-border)] px-3 py-2">
      <div className="flex items-center justify-between gap-2">
        <p className="font-medium text-[var(--color-text)]">{plan.plan_name}</p>
        <span className="text-sm text-[var(--color-text)]">{plan.price_display}</span>
      </div>
      <p className="mt-0.5 text-xs text-[var(--color-text-muted)]">{plan.quota_label}</p>
      {plan.entitled_features.length > 0 ? (
        <p className="mt-0.5 text-[11px] text-[var(--color-text-muted)]">
          含能力：{plan.entitled_features.join("、")}
        </p>
      ) : null}
      {plan.trial_days > 0 ? (
        <button
          type="button"
          disabled={busy}
          onClick={() => onTrial(plan.billing_plan_id)}
          className="mt-2 rounded-[4px] border border-[var(--color-border)] px-2 py-1 text-xs text-[var(--color-text)] disabled:opacity-50"
        >
          开通 {plan.trial_days} 天试用
        </button>
      ) : null}
    </div>
  );
}

function SubscriptionStatus({ sub }: { sub: Subscription }) {
  return (
    <div className="rounded-[4px] bg-[var(--color-bg)] px-3 py-2 text-xs text-[var(--color-text-muted)]">
      <p>当前套餐：{sub.billing_plan_id}</p>
      <p className="mt-0.5">
        订阅状态：{sub.subscription_status}；试用状态：{sub.trial_status}
      </p>
      {sub.trial_ends_at ? <p className="mt-0.5">试用到期：{sub.trial_ends_at}</p> : null}
      {sub.current_period_end ? (
        <p className="mt-0.5">当前周期到期：{sub.current_period_end}</p>
      ) : null}
      <p className="mt-0.5">续费意愿：{sub.renewal_intent}</p>
    </div>
  );
}

export function BillingPanel({ sessionToken }: { sessionToken?: string | null } = {}) {
  const enabled = isBillingEnabled();
  const [plans, setPlans] = useState<Plan[]>([]);
  const [sub, setSub] = useState<Subscription | null>(null);
  const [status, setStatus] = useState<"idle" | "loading" | "disabled" | "error">("idle");
  const [busy, setBusy] = useState(false);
  const [intent, setIntent] = useState<RenewalIntent>("will_renew");
  const [reason, setReason] = useState("");
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    setStatus("loading");
    fetchPlans().then((res) => {
      if (cancelled) return;
      if (res.ok) {
        setPlans(res.data.items);
        setStatus("idle");
      } else if (res.reason === "disabled") {
        setStatus("disabled");
      } else {
        setStatus("error");
      }
    });
    fetchMySubscription(sessionToken).then((res) => {
      if (cancelled) return;
      if (res.ok) setSub(res.data.subscription ?? null);
    });
    return () => {
      cancelled = true;
    };
  }, [enabled, sessionToken]);

  if (!enabled) {
    return null;
  }

  const onTrial = (planId: string) => {
    setBusy(true);
    setNotice(null);
    startTrial(planId, null, sessionToken).then((res) => {
      setBusy(false);
      if (res.ok) {
        if (res.data.subscription) {
          setSub(res.data.subscription);
          setNotice("试用已开通。");
        } else {
          setNotice("开通试用失败，请稍后重试。");
        }
      } else if (res.reason === "login_required") {
        setNotice("请先登录后再开通试用。");
      } else {
        setNotice("开通试用失败，请稍后重试。");
      }
    });
  };

  const onSubmitIntent = () => {
    if (!sub) return;
    setBusy(true);
    setNotice(null);
    submitRenewalIntent(sub.subscription_id, intent, reason.trim() || null, sessionToken).then(
      (res) => {
        setBusy(false);
        if (res.ok && res.data.subscription) {
          setSub(res.data.subscription);
          setNotice("续费意愿已记录，谢谢反馈。");
        } else {
          setNotice("提交失败，请稍后重试。");
        }
      }
    );
  };

  return (
    <section
      aria-label="套餐与订阅"
      className="rounded-[6px] border border-[var(--color-border)] bg-[var(--color-surface)] px-4 py-3 text-sm text-[var(--color-text)]"
    >
      <p className="font-medium">套餐与订阅</p>
      <p className="mt-0.5 text-xs text-[var(--color-text-muted)]">
        套餐与价格仅为展示，支付由平台侧 / 第三方完成。本页面不收集任何卡号 / 银行账户 /
        CVV / 支付凭据，请勿在此输入支付信息。
      </p>

      {status === "loading" ? (
        <p className="mt-3 text-xs text-[var(--color-text-muted)]">加载中…</p>
      ) : null}
      {status === "disabled" ? (
        <p className="mt-3 text-xs text-[var(--color-text-muted)]">
          计费能力暂未启用，当前不展示套餐 / 计费入口。
        </p>
      ) : null}
      {status === "error" ? (
        <p className="mt-3 text-xs text-[var(--color-text-muted)]">套餐加载失败，请稍后重试。</p>
      ) : null}

      {status === "idle" && plans.length > 0 ? (
        <div className="mt-3 flex flex-col gap-2">
          {plans.map((p) => (
            <PlanCard key={p.billing_plan_id} plan={p} onTrial={onTrial} busy={busy} />
          ))}
        </div>
      ) : null}

      {sub ? (
        <div className="mt-3 flex flex-col gap-2">
          <SubscriptionStatus sub={sub} />
          <div className="rounded-[4px] border border-[var(--color-border)] px-3 py-2">
            <p className="text-xs font-medium text-[var(--color-text)]">续费意愿</p>
            <div className="mt-1 flex flex-wrap gap-2">
              {RENEWAL_OPTIONS.map((opt) => (
                <label key={opt.value} className="flex items-center gap-1 text-xs">
                  <input
                    type="radio"
                    name="renewal-intent"
                    value={opt.value}
                    checked={intent === opt.value}
                    onChange={() => setIntent(opt.value)}
                  />
                  {opt.label}
                </label>
              ))}
            </div>
            <textarea
              aria-label="续费意愿补充说明"
              value={reason}
              onChange={(e) => setReason(e.target.value.slice(0, 200))}
              placeholder="可补充说明（选填，不要填写任何支付信息）"
              className="mt-2 w-full rounded-[4px] border border-[var(--color-border)] px-2 py-1 text-xs"
              rows={2}
            />
            <button
              type="button"
              disabled={busy}
              onClick={onSubmitIntent}
              className="mt-2 rounded-[4px] border border-[var(--color-border)] px-2 py-1 text-xs text-[var(--color-text)] disabled:opacity-50"
            >
              提交续费意愿
            </button>
          </div>
        </div>
      ) : null}

      {notice ? (
        <p className="mt-2 text-xs text-[var(--color-text-muted)]">{notice}</p>
      ) : null}
    </section>
  );
}
