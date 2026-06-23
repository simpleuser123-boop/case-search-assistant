// M5-4 权限分级 API 客户端（前端）。
//
// 红线：
//   - 本模块只发送「角色 / 对象级授权 / 受控读取 / 审计查询」请求；绝不发送正文。
//   - 所有接口都需登录：复用 sessionState 的 Authorization 头；不缓存、不持久化凭据。
//   - 关闭态（后端 403 PERMISSION_TIERING_DISABLED）时调用方回到 M5-3 / M4 末态。
//   - 越权时后端返回 403 PERMISSION_DENIED；本模块只回显脱敏标识，绝不回显正文。

import { getAuthHeader } from "../lib/sessionState";

export const PERMISSION_API_BASE = "/api/permission";

export type PermissionRole = "owner" | "editor" | "viewer";
export type GrantLevel = "editor" | "viewer";

export type PermissionApiResult<T> =
  | { ok: true; data: T }
  | {
      ok: false;
      reason: "disabled" | "denied" | "rejected" | "network_error" | "http_error";
      status?: number;
      reasonCode?: string;
    };

export type AuditItem = {
  action: string;
  result: string;
  reason_code: string;
  permission_level?: string | null;
  object_id_hash?: string | null;
  actor_user_id_hash: string;
};

export type SedimentObjectView = {
  object_id: string;
  object_type: string;
  visibility: string;
  owner_user_id_hash: string;
  team_id_hash: string;
  case_id?: string | null;
  list_title?: string | null;
  [key: string]: unknown;
};

function classify(status: number): "disabled" | "denied" | "rejected" | "http_error" {
  if (status === 403) return "disabled";
  if (status === 400 || status === 401) return "rejected";
  return "http_error";
}

async function request<T>(
  path: string,
  method: "GET" | "POST",
  body?: unknown,
): Promise<PermissionApiResult<T>> {
  if (typeof fetch === "undefined") {
    return { ok: false, reason: "network_error" };
  }
  let resp: Response;
  try {
    resp = await fetch(`${PERMISSION_API_BASE}${path}`, {
      method,
      headers: {
        Accept: "application/json",
        ...(method === "POST" ? { "Content-Type": "application/json" } : {}),
        ...getAuthHeader(),
      },
      ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
    });
  } catch {
    return { ok: false, reason: "network_error" };
  }
  if (!resp.ok) {
    // 区分「未启用」与「越权被拒」：两者都是 403，靠错误码细分。
    let reasonCode: string | undefined;
    try {
      const errBody = (await resp.json()) as { error?: { code?: string } };
      reasonCode = errBody?.error?.code;
    } catch {
      reasonCode = undefined;
    }
    if (resp.status === 403 && reasonCode === "PERMISSION_DENIED") {
      return { ok: false, reason: "denied", status: 403, reasonCode };
    }
    return { ok: false, reason: classify(resp.status), status: resp.status, reasonCode };
  }
  const data = (await resp.json()) as { ok?: boolean; reason_code?: string } & Record<string, unknown>;
  if (data.ok === false) {
    return { ok: false, reason: "rejected", reasonCode: data.reason_code };
  }
  return { ok: true, data: data as unknown as T };
}

// owner 给团队成员分配角色（owner/editor/viewer）。仅团队 owner 可调用，否则后端 403。
export async function assignRole(
  teamId: string,
  memberUserId: string,
  role: PermissionRole,
): Promise<PermissionApiResult<{ reason_code: string }>> {
  return request("/role", "POST", { team_id: teamId, member_user_id: memberUserId, role });
}

// owner 把对象显式授予某用户某权限等级（viewer/editor）。这是「显式授权才扩大可见性」的入口。
export async function grant(
  objectId: string,
  granteeUserId: string,
  permissionLevel: GrantLevel,
): Promise<PermissionApiResult<{ reason_code: string }>> {
  return request("/grant", "POST", {
    object_id: objectId,
    grantee_user_id: granteeUserId,
    permission_level: permissionLevel,
  });
}

export async function revoke(
  objectId: string,
  granteeUserId: string,
): Promise<PermissionApiResult<{ reason_code: string }>> {
  return request("/revoke", "POST", { object_id: objectId, grantee_user_id: granteeUserId });
}

// 对象级受控读取：鉴权通过返回脱敏视图；越权时 reason==="denied"。
export async function readObject(
  objectId: string,
): Promise<PermissionApiResult<{ effective_level: string | null; object: SedimentObjectView | null }>> {
  return request("/object/read", "POST", { object_id: objectId });
}

export async function listAudit(): Promise<PermissionApiResult<{ items: AuditItem[] }>> {
  return request("/audit", "GET");
}
