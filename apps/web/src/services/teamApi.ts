// M5-3 团队空间 API 客户端（前端）。
//
// 隔离 / 隐私红线：
//   - 本模块只发送团队管理与「沉淀引用」请求；沉淀写入只带元数据 / 引用 / 锚点 /
//     用户自填短字段，绝不发送正文（raw_query / 案情 / 候选 / 摘要等）。
//   - 团队接口都需登录：复用 sessionState 的 Authorization 头；不缓存、不持久化凭据。
//   - 关闭态（后端 403 TEAM_WORKSPACE_DISABLED）时调用方回到 M5-2 / M4 单用户私有态。
//   - 响应里的 owner / team 标识为脱敏哈希；本模块不回显任何正文。

import { getAuthHeader } from "../lib/sessionState";

export const TEAM_API_BASE = "/api/team";

export type TeamApiResult<T> =
  | { ok: true; data: T }
  | { ok: false; reason: "disabled" | "rejected" | "network_error" | "http_error"; status?: number; reasonCode?: string };

export type TeamView = {
  team_id: string;
  team_name: string;
  team_id_hash: string;
  status: string;
};

export type SedimentItemView = {
  object_id: string;
  object_type: string;
  visibility: string;
  owner_user_id_hash: string;
  team_id_hash: string;
  case_id?: string | null;
  case_number?: string | null;
  court?: string | null;
  case_cause?: string | null;
  note?: string | null;
  list_title?: string | null;
  source_anchors?: Array<Record<string, unknown>>;
};

function classify(status: number): "disabled" | "rejected" | "http_error" {
  if (status === 403) return "disabled";
  if (status === 400 || status === 401) return "rejected";
  return "http_error";
}

async function request<T>(path: string, method: "GET" | "POST", body?: unknown): Promise<TeamApiResult<T>> {
  if (typeof fetch === "undefined") {
    return { ok: false, reason: "network_error" };
  }
  let resp: Response;
  try {
    resp = await fetch(`${TEAM_API_BASE}${path}`, {
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
    return { ok: false, reason: classify(resp.status), status: resp.status };
  }
  const data = (await resp.json()) as { ok?: boolean; reason_code?: string } & Record<string, unknown>;
  if (data.ok === false) {
    return { ok: false, reason: "rejected", reasonCode: data.reason_code };
  }
  return { ok: true, data: data as unknown as T };
}

export async function listTeams(): Promise<TeamApiResult<{ teams: TeamView[] }>> {
  return request<{ teams: TeamView[] }>("/list", "GET");
}

export async function createTeam(teamName: string): Promise<TeamApiResult<{ team: TeamView }>> {
  return request<{ team: TeamView }>("/create", "POST", { team_name: teamName });
}

export async function addMember(teamId: string, memberUserId: string): Promise<TeamApiResult<{ member_count: number }>> {
  return request<{ member_count: number }>("/member", "POST", {
    team_id: teamId,
    member_user_id: memberUserId,
  });
}

// 列出当前租户上下文（team_id 为空=单用户私有）可见的沉淀对象。跨团队不可见由后端强隔离保证。
export async function listSediment(input: {
  teamId?: string | null;
  objectType?: "case_favorite" | "case_list" | "report_template";
}): Promise<TeamApiResult<{ items: SedimentItemView[]; tenant_downgraded: boolean }>> {
  return request<{ items: SedimentItemView[]; tenant_downgraded: boolean }>("/sediment/list", "POST", {
    team_id: input.teamId ?? null,
    object_type: input.objectType ?? null,
  });
}
