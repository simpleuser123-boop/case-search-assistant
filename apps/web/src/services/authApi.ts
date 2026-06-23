// M5-2 账号/认证 API 客户端（前端）。
//
// 凭据红线：
//   - 本模块只把用户在表单里输入的 login_name / password 作为请求体一次性发送，
//     不缓存、不回显、不写日志、不持久化密码。
//   - 登录成功返回的 session_token 交给 sessionState（仅内存）保存，本模块不持久化。
//   - 工具不代填凭据：凭据值全部来自用户输入。
//   - 关闭态（后端 403 ACCOUNT_SYSTEM_DISABLED）时调用方应回到 M4 匿名态。

import {
  clearSession,
  getAuthHeader,
  setSession,
  type PublicAccount,
} from "../lib/sessionState";

export const AUTH_API_BASE = "/api/auth";

export type AuthApiResult<T> =
  | { ok: true; data: T }
  | { ok: false; reason: "disabled" | "rejected" | "network_error" | "http_error"; status?: number; reasonCode?: string };

type AuthResponseBody = {
  ok: boolean;
  account?: PublicAccount | null;
  session_token?: string | null;
  expires_at?: string | null;
  reason_code?: string | null;
};

async function postJson(path: string, body: unknown, withAuth = false): Promise<Response | null> {
  if (typeof fetch === "undefined") {
    return null;
  }
  return fetch(`${AUTH_API_BASE}${path}`, {
    method: "POST",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
      ...(withAuth ? getAuthHeader() : {}),
    },
    body: JSON.stringify(body),
  });
}

function classify(status: number): "disabled" | "rejected" | "http_error" {
  if (status === 403) return "disabled";
  if (status === 400 || status === 401) return "rejected";
  return "http_error";
}

export async function register(input: {
  loginName: string;
  password: string;
  displayName?: string;
}): Promise<AuthApiResult<PublicAccount>> {
  let resp: Response | null;
  try {
    resp = await postJson("/register", {
      login_name: input.loginName,
      password: input.password,
      display_name: input.displayName ?? "",
    });
  } catch {
    return { ok: false, reason: "network_error" };
  }
  if (!resp) return { ok: false, reason: "network_error" };
  if (!resp.ok) {
    const reason = classify(resp.status);
    return { ok: false, reason, status: resp.status };
  }
  const data = (await resp.json()) as AuthResponseBody;
  if (!data.ok || !data.account) {
    return { ok: false, reason: "rejected", reasonCode: data.reason_code ?? undefined };
  }
  return { ok: true, data: data.account };
}

export async function login(input: {
  loginName: string;
  password: string;
}): Promise<AuthApiResult<PublicAccount>> {
  let resp: Response | null;
  try {
    resp = await postJson("/login", {
      login_name: input.loginName,
      password: input.password,
    });
  } catch {
    return { ok: false, reason: "network_error" };
  }
  if (!resp) return { ok: false, reason: "network_error" };
  if (!resp.ok) {
    return { ok: false, reason: classify(resp.status), status: resp.status };
  }
  const data = (await resp.json()) as AuthResponseBody;
  if (!data.ok || !data.account || !data.session_token) {
    return { ok: false, reason: "rejected", reasonCode: data.reason_code ?? undefined };
  }
  // 令牌只交给内存运行态保存。
  setSession({
    account: data.account,
    sessionToken: data.session_token,
    expiresAt: data.expires_at ?? null,
  });
  return { ok: true, data: data.account };
}

export async function logout(): Promise<AuthApiResult<null>> {
  let resp: Response | null;
  try {
    resp = await postJson("/logout", {}, true);
  } catch {
    // 网络失败也要清掉本地内存态，避免悬挂登录。
    clearSession();
    return { ok: false, reason: "network_error" };
  }
  clearSession();
  if (!resp) return { ok: false, reason: "network_error" };
  if (!resp.ok) {
    return { ok: false, reason: classify(resp.status), status: resp.status };
  }
  return { ok: true, data: null };
}

// 认领：把匿名沉淀引用迁移到当前账号。需显式 confirm；仅元数据/锚点。
export async function claimAnonymousSediment(
  items: Array<Record<string, unknown>>,
  confirm: boolean
): Promise<AuthApiResult<{ claimed: number; degraded: number; rejected: number }>> {
  let resp: Response | null;
  try {
    resp = await postJson("/claim", { confirm, items }, true);
  } catch {
    return { ok: false, reason: "network_error" };
  }
  if (!resp) return { ok: false, reason: "network_error" };
  if (!resp.ok) {
    return { ok: false, reason: classify(resp.status), status: resp.status };
  }
  const data = (await resp.json()) as {
    ok: boolean;
    claimed_count?: number;
    degraded_count?: number;
    rejected_count?: number;
    reason_code?: string;
  };
  if (!data.ok) {
    return { ok: false, reason: "rejected", reasonCode: data.reason_code ?? undefined };
  }
  return {
    ok: true,
    data: {
      claimed: data.claimed_count ?? 0,
      degraded: data.degraded_count ?? 0,
      rejected: data.rejected_count ?? 0,
    },
  };
}
