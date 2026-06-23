// M5-9 商业化闭环（套餐/试用/计费/续费意愿）API 客户端（前端）。
//
// 边界 / 凭据红线：
//   - 绝不代填 / 代管 / 代存支付凭据：本客户端没有任何卡号 / 银行账户 / CVV /
//     支付令牌字段；支付在平台侧 / 第三方完成，前端只上送 / 接收脱敏引用
//     （payment_ref + status），且 payment_ref 由后端立即哈希。
//   - flag-gated：后端 ENABLE_BILLING=false 时返回 403 BILLING_DISABLED；
//     前端据此不展示，回到 M5-8 末态。
//   - 续费意愿仅采集用户自填短码 + 短理由，非预测、非承诺。
//   - 计费状态不参与主排序 / 召回 / source selection。

export const BILLING_API_BASE = "/api/billing";

export type BillingFailureReason =
  | "disabled"
  | "login_required"
  | "guard_block"
  | "network_error"
  | "http_error";

export type BillingApiResult<T> =
  | { ok: true; data: T }
  | {
      ok: false;
      reason: BillingFailureReason;
      status?: number;
      reasonCode?: string;
    };

export type Plan = {
  billing_plan_id: string;
  plan_name: string;
  quota_label: string;
  price_display: string;
  billing_cycle: string;
  seat_quota: number;
  trial_days: number;
  entitled_features: string[];
  sort_order: number;
};

export type PlanListResponse = { ok: boolean; items: Plan[]; reason_code?: string | null };

export type Subscription = {
  subscription_id: string;
  billing_plan_id: string;
  trial_status: string;
  subscription_status: string;
  renewal_intent: string;
  renewal_reason?: string | null;
  trial_ends_at?: string | null;
  current_period_end?: string | null;
  owner_user_id_hash: string;
  team_id_hash: string;
};

export type SubscriptionResponse = {
  ok: boolean;
  subscription?: Subscription | null;
  reason_code?: string | null;
};

export type RenewalIntent = "unknown" | "will_renew" | "undecided" | "will_churn";

function isStringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item) => typeof item === "string");
}

function isPlan(value: unknown): value is Plan {
  if (!value || typeof value !== "object") return false;
  const plan = value as Partial<Plan>;
  return (
    typeof plan.billing_plan_id === "string" &&
    typeof plan.plan_name === "string" &&
    typeof plan.quota_label === "string" &&
    typeof plan.price_display === "string" &&
    typeof plan.billing_cycle === "string" &&
    typeof plan.seat_quota === "number" &&
    typeof plan.trial_days === "number" &&
    isStringArray(plan.entitled_features) &&
    typeof plan.sort_order === "number"
  );
}

function isPlanListResponse(value: unknown): value is PlanListResponse {
  if (!value || typeof value !== "object") return false;
  const data = value as Partial<PlanListResponse>;
  return (
    typeof data.ok === "boolean" &&
    Array.isArray(data.items) &&
    data.items.every(isPlan) &&
    (data.reason_code === undefined ||
      data.reason_code === null ||
      typeof data.reason_code === "string")
  );
}

function isSubscription(value: unknown): value is Subscription {
  if (!value || typeof value !== "object") return false;
  const sub = value as Partial<Subscription>;
  return (
    typeof sub.subscription_id === "string" &&
    typeof sub.billing_plan_id === "string" &&
    typeof sub.trial_status === "string" &&
    typeof sub.subscription_status === "string" &&
    typeof sub.renewal_intent === "string" &&
    (sub.renewal_reason === undefined ||
      sub.renewal_reason === null ||
      typeof sub.renewal_reason === "string") &&
    (sub.trial_ends_at === undefined ||
      sub.trial_ends_at === null ||
      typeof sub.trial_ends_at === "string") &&
    (sub.current_period_end === undefined ||
      sub.current_period_end === null ||
      typeof sub.current_period_end === "string") &&
    typeof sub.owner_user_id_hash === "string" &&
    typeof sub.team_id_hash === "string"
  );
}

function isSubscriptionResponse(value: unknown): value is SubscriptionResponse {
  if (!value || typeof value !== "object") return false;
  const data = value as Partial<SubscriptionResponse>;
  return (
    typeof data.ok === "boolean" &&
    (data.subscription === undefined ||
      data.subscription === null ||
      isSubscription(data.subscription)) &&
    (data.reason_code === undefined ||
      data.reason_code === null ||
      typeof data.reason_code === "string")
  );
}

function authHeaders(token?: string | null): Record<string, string> {
  const headers: Record<string, string> = { Accept: "application/json" };
  if (token) headers.Authorization = `Bearer ${token}`;
  return headers;
}

function classify(status: number): BillingFailureReason {
  if (status === 403) return "disabled";
  if (status === 401) return "login_required";
  if (status === 400) return "guard_block";
  return "http_error";
}

async function parseError(resp: Response): Promise<string | undefined> {
  try {
    const body = (await resp.json()) as { error?: { code?: string } };
    return body?.error?.code;
  } catch {
    return undefined;
  }
}

async function request<T>(
  path: string,
  init: RequestInit,
  validate: (value: unknown) => value is T
): Promise<BillingApiResult<T>> {
  if (typeof fetch === "undefined") {
    return { ok: false, reason: "network_error" };
  }
  let resp: Response;
  try {
    resp = await fetch(`${BILLING_API_BASE}${path}`, init);
  } catch {
    return { ok: false, reason: "network_error" };
  }
  if (!resp.ok) {
    const reasonCode = await parseError(resp);
    return { ok: false, reason: classify(resp.status), status: resp.status, reasonCode };
  }
  let data: unknown;
  try {
    data = await resp.json();
  } catch {
    return { ok: false, reason: "http_error", status: resp.status };
  }
  if (!validate(data)) {
    return { ok: false, reason: "http_error", status: resp.status };
  }
  return { ok: true, data };
}

export async function fetchPlans(): Promise<BillingApiResult<PlanListResponse>> {
  return request<PlanListResponse>(
    "/plans",
    { method: "GET", headers: authHeaders() },
    isPlanListResponse
  );
}

export async function fetchMySubscription(
  token?: string | null
): Promise<BillingApiResult<SubscriptionResponse>> {
  return request<SubscriptionResponse>(
    "/subscription",
    {
      method: "GET",
      headers: authHeaders(token),
    },
    isSubscriptionResponse
  );
}

export async function startTrial(
  billingPlanId: string,
  teamId: string | null,
  token?: string | null
): Promise<BillingApiResult<SubscriptionResponse>> {
  return request<SubscriptionResponse>(
    "/trial",
    {
      method: "POST",
      headers: { ...authHeaders(token), "Content-Type": "application/json" },
      body: JSON.stringify({ billing_plan_id: billingPlanId, team_id: teamId }),
    },
    isSubscriptionResponse
  );
}

export async function submitRenewalIntent(
  subscriptionId: string,
  renewalIntent: RenewalIntent,
  renewalReason: string | null,
  token?: string | null
): Promise<BillingApiResult<SubscriptionResponse>> {
  // 注意：本请求体只含订阅引用 + 续费意愿短码 + 自填短理由，绝无任何支付凭据字段。
  return request<SubscriptionResponse>(
    "/renewal-intent",
    {
      method: "POST",
      headers: { ...authHeaders(token), "Content-Type": "application/json" },
      body: JSON.stringify({
        subscription_id: subscriptionId,
        renewal_intent: renewalIntent,
        renewal_reason: renewalReason,
      }),
    },
    isSubscriptionResponse
  );
}
