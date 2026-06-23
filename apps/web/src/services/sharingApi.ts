// M5-5 沉淀同步与团队共享 API 客户端（前端）。
//
// 隔离 / 隐私红线：
//   - sync 只上送元数据 / 引用 / 来源锚点 / 用户自填短字段，**绝不上送正文或原始案情**
//     （raw_query / 案情 / 候选 / 摘要等一律不在请求体里）。同步默认 owner 私有。
//   - share 是显式动作，默认 private；只有对象 owner + 目标团队活跃成员可共享；
//     无来源锚点的 AI 内容承载型对象会被后端拒绝（reason_code=missing_source_anchor）。
//   - 所有接口都需登录：复用 sessionState 的 Authorization 头；不缓存、不持久化凭据。
//   - 关闭态（后端 403 TEAM_SHARING_DISABLED）时调用方回到 M4 本地沉淀末态。
//   - 响应里的 owner / team 标识为脱敏哈希；本模块不回显任何正文。

import { getAuthHeader } from "../lib/sessionState";

export const SHARING_API_BASE = "/api/sharing";

export type SharingApiResult<T> =
  | { ok: true; data: T }
  | {
      ok: false;
      reason: "disabled" | "rejected" | "network_error" | "http_error";
      status?: number;
      reasonCode?: string;
    };

// 同步只允许这些键：元数据 / 引用 / 来源锚点 / 用户自填短字段。无正文键。
export type SyncSedimentInput = {
  objectType: "case_favorite" | "case_list" | "report_template";
  caseId?: string;
  caseNumber?: string;
  court?: string;
  trialLevel?: string;
  caseCause?: string;
  judgmentDate?: string;
  sourceAnchors?: Array<{ case_id: string; source_chunk_id: string; anchor_type?: string }>;
  note?: string;
  tag?: string;
  label?: string;
  listId?: string;
  listTitle?: string;
  reportId?: string;
};

export type ShareItemView = {
  object_id: string;
  object_type: string;
  visibility: string;
  owner_user_id_hash: string;
  shared_with_team_id_hash: string;
  anchor_count: number;
  status: string;
};

function classify(status: number): "disabled" | "rejected" | "http_error" {
  if (status === 403) return "disabled";
  if (status === 400 || status === 401) return "rejected";
  return "http_error";
}

async function request<T>(path: string, method: "GET" | "POST", body?: unknown): Promise<SharingApiResult<T>> {
  if (typeof fetch === "undefined") {
    return { ok: false, reason: "network_error" };
  }
  let resp: Response;
  try {
    resp = await fetch(`${SHARING_API_BASE}${path}`, {
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
    let reasonCode: string | undefined;
    try {
      const errBody = (await resp.json()) as { error?: { code?: string } };
      reasonCode = errBody?.error?.code;
    } catch {
      reasonCode = undefined;
    }
    return { ok: false, reason: classify(resp.status), status: resp.status, reasonCode };
  }
  const data = (await resp.json()) as { ok?: boolean; reason_code?: string } & Record<string, unknown>;
  if (data.ok === false) {
    return { ok: false, reason: "rejected", reasonCode: data.reason_code };
  }
  return { ok: true, data: data as unknown as T };
}

// 把请求体收窄到白名单键：杜绝任何正文键意外进入请求体。
function toSyncBody(input: SyncSedimentInput): Record<string, unknown> {
  const body: Record<string, unknown> = { object_type: input.objectType };
  if (input.caseId) body.case_id = input.caseId;
  if (input.caseNumber) body.case_number = input.caseNumber;
  if (input.court) body.court = input.court;
  if (input.trialLevel) body.trial_level = input.trialLevel;
  if (input.caseCause) body.case_cause = input.caseCause;
  if (input.judgmentDate) body.judgment_date = input.judgmentDate;
  if (input.sourceAnchors && input.sourceAnchors.length > 0) body.source_anchors = input.sourceAnchors;
  if (input.note) body.note = input.note;
  if (input.tag) body.tag = input.tag;
  if (input.label) body.label = input.label;
  if (input.listId) body.list_id = input.listId;
  if (input.listTitle) body.list_title = input.listTitle;
  if (input.reportId) body.report_id = input.reportId;
  return body;
}

// 同步一条本地沉淀到服务端（默认 owner 私有）。只上送元数据 / 引用 / 锚点 / 短字段。
export async function syncSediment(
  input: SyncSedimentInput,
): Promise<SharingApiResult<{ object_id: string; visibility: string }>> {
  return request("/sync", "POST", toSyncBody(input));
}

// 显式把对象共享给团队。默认私有，必须显式调用本接口才共享；后端校验 owner + 成员 + 锚点。
export async function shareToTeam(
  objectId: string,
  teamId: string,
): Promise<SharingApiResult<{ share_id: string; visibility: string; anchor_count: number }>> {
  return request("/share", "POST", { object_id: objectId, team_id: teamId });
}

// 取消共享：把对象降回 owner 私有。
export async function unshare(
  objectId: string,
): Promise<SharingApiResult<{ visibility: string }>> {
  return request("/unshare", "POST", { object_id: objectId });
}

export async function listMyShares(): Promise<SharingApiResult<{ items: ShareItemView[] }>> {
  return request("/mine", "GET");
}

export async function listTeamShares(teamId: string): Promise<SharingApiResult<{ items: ShareItemView[] }>> {
  return request("/team", "POST", { team_id: teamId });
}
