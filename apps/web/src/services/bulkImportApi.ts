// M5-6 批量导入 API 客户端（前端）。
//
// 隔离 / 隐私红线：
//   - 导入只上送元数据 / 引用 / 来源锚点 / 用户自填短字段，**绝不上送正文或原始案情**
//     （案情 / 候选 / 摘要 / chunk 等一律不在请求体里）。导入对象默认 owner 私有。
//   - 缺锚点 / 含正文 / 缺 case_id 的项会被后端降级或拒绝（reason_code 标注），不伪造锚点。
//   - 所有接口都需登录：复用 sessionState 的 Authorization 头；不缓存、不持久化凭据。
//   - 关闭态（后端 403 BULK_IMPORT_DISABLED）时调用方回到 M5-5 / M4 末态。
//   - 响应里 owner / team 标识为脱敏哈希；逐项只回 case_id + 短 reason code，无正文。

import { getAuthHeader } from "../lib/sessionState";

export const BULK_IMPORT_API_BASE = "/api/bulk-import";

export type BulkImportApiResult<T> =
  | { ok: true; data: T }
  | {
      ok: false;
      reason: "disabled" | "rejected" | "network_error" | "http_error";
      status?: number;
      reasonCode?: string;
    };

export type ImportItemInput = {
  caseId: string;
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

export type BulkImportInput = {
  sourceType: "case_list_file" | "csv" | "existing_list";
  objectType: "case_favorite" | "case_list" | "report_template";
  items: ImportItemInput[];
  teamId?: string;
};

export type ItemOutcomeView = {
  case_id: string | null;
  ok: boolean;
  reason_code: string;
  object_id: string | null;
};

export type BulkImportResult = {
  ok: boolean;
  import_job_id: string | null;
  import_status: string;
  item_count: number;
  imported_count: number;
  rejected_count: number;
  duplicate_count: number;
  degrade_reason: string | null;
  outcomes: ItemOutcomeView[];
};

export type JobView = {
  import_job_id: string;
  source_type: string;
  item_count: number;
  imported_count: number;
  rejected_count: number;
  duplicate_count: number;
  import_status: string;
  degrade_reason: string | null;
  owner_user_id_hash: string;
  team_id_hash: string;
};

function classify(status: number): "disabled" | "rejected" | "http_error" {
  if (status === 403) return "disabled";
  if (status === 400 || status === 401 || status === 422) return "rejected";
  return "http_error";
}

async function request<T>(path: string, method: "GET" | "POST", body?: unknown): Promise<BulkImportApiResult<T>> {
  if (typeof fetch === "undefined") {
    return { ok: false, reason: "network_error" };
  }
  let resp: Response;
  try {
    resp = await fetch(`${BULK_IMPORT_API_BASE}${path}`, {
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
  return { ok: true, data: data as unknown as T };
}

// 把单个导入项收窄到白名单键：杜绝任何正文键意外进入请求体。
function toItemBody(item: ImportItemInput): Record<string, unknown> {
  const body: Record<string, unknown> = { case_id: item.caseId };
  if (item.caseNumber) body.case_number = item.caseNumber;
  if (item.court) body.court = item.court;
  if (item.trialLevel) body.trial_level = item.trialLevel;
  if (item.caseCause) body.case_cause = item.caseCause;
  if (item.judgmentDate) body.judgment_date = item.judgmentDate;
  if (item.sourceAnchors && item.sourceAnchors.length > 0) body.source_anchors = item.sourceAnchors;
  if (item.note) body.note = item.note;
  if (item.tag) body.tag = item.tag;
  if (item.label) body.label = item.label;
  if (item.listId) body.list_id = item.listId;
  if (item.listTitle) body.list_title = item.listTitle;
  if (item.reportId) body.report_id = item.reportId;
  return body;
}

// 运行一次批量导入。只上送白名单字段；导入对象默认 owner 私有。
export async function runBulkImport(input: BulkImportInput): Promise<BulkImportApiResult<BulkImportResult>> {
  const body: Record<string, unknown> = {
    source_type: input.sourceType,
    object_type: input.objectType,
    items: input.items.map(toItemBody),
  };
  if (input.teamId) body.team_id = input.teamId;
  return request("/run", "POST", body);
}

export async function listImportJobs(): Promise<BulkImportApiResult<{ items: JobView[] }>> {
  return request("/jobs", "GET");
}
