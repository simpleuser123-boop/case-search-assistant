// E7-3/E7-4 案件协作工作台 API 客户端（前端）。
//
// 隐私 / 隔离红线（E7「只归集锚定引用、不起草不下结论」+ 生态 3 红线的最后一道前端闸）：
//   - 端点（create / list / get / update / share）请求体均由白名单键**显式逐字段组装**，
//     绝不 spread 任意对象，杜绝裁判正文 / 起草正文 / 候选-chunk 正文 / 原始案情 / PII /
//     案件综述 / 胜负结论型键意外进入请求体。
//   - search_profile_summary 只发 SearchProfile **脱敏白名单子集**（case_cause / region /
//     trial_level_preference / dispute_focus_keywords / query_text），原始口语化案情绝不上送。
//   - candidate_refs / draft_descriptors 只发**引用 + 锚点**（白名单字段），逐字段取键，绝不携带
//     summary / highlight / chunk_text / judgment_text / draft_body 等正文键；无锚点引用在组装
//     阶段即被丢弃（与后端「无锚点丢弃」红线一致）。
//   - 协作夹态（摘要 / 引用 / 短字段）只来自调用方 React 内存态，绝不进入 URL query string、
//     绝不读写任何浏览器存储（localStorage / sessionStorage / IndexedDB / cookie）。
//   - 关闭态（后端 403 CASEBOOK_DISABLED）/ 未登录（401）时调用方回到入口不渲染的安全末态。
//   - **本模块零文本生成 / 零 AI 归纳 / 零结论预测调用**：只搬运用户归集好的脱敏摘要与带锚点引用。
//   - E7-4 共享：share 只携带 visibility (+ team_id)，绝不承载摘要/引用/正文；只 private|team 两级。

export const CASEBOOK_FOLDERS_API_PATH = "/api/casebook/folders";
const DEFAULT_CASEBOOK_API_TIMEOUT_MS = 20000;

// 与 E7-1/E7-2 契约一致的前端上限（呼应后端 sanitize_case_folder 校验）。
export const TITLE_MAX_LEN = 80;
export const NOTE_MAX_LEN = 200;
export const TAG_MAX_LEN = 40;

// search_profile_summary 脱敏白名单子集键（与后端 SEARCH_PROFILE_FIELDS 逐字段一致）。
// 原始案情/正文/PII 键绝不在内。
export const SEARCH_PROFILE_SUMMARY_KEYS = [
  "case_cause",
  "region",
  "trial_level_preference",
  "dispute_focus_keywords",
  "query_text",
] as const;

// --- 引用锚点视图（结构化引用，非正文）-----------------------------------------

export interface CasebookSourceAnchorView {
  case_id: string;
  source_chunk_id: string;
  anchor_type?: string | null;
}

export interface CasebookStatuteAnchorView {
  text_id: string;
  law_name?: string | null;
  article_no?: string | null;
  anchor_type?: string | null;
}

// 协作夹归集的类案视图（= CandidateRef 白名单七字段 + 锚点，零裁判正文）。
export interface CasebookCandidateRefView {
  case_id: string;
  case_number?: string | null;
  court?: string | null;
  trial_level?: string | null;
  case_cause?: string | null;
  judgment_date?: string | null;
  source_anchors: CasebookSourceAnchorView[];
}

// 协作夹归集（文书引用内）的法条视图（白名单 + 锚点；article_text 若有只来自语料、不得前端生成）。
export interface CasebookStatuteRefView {
  statute_id: string;
  law_name: string;
  article_no?: string | null;
  statute_anchors: CasebookStatuteAnchorView[];
  article_text?: string | null;
  source_corpus?: string | null;
  effective_status?: string | null;
  related_case_refs?: CasebookCandidateRefView[];
}

// 协作夹归集的文书骨架视图（= DraftDescriptor 结构骨架 + 锚定引用 + 短字段，零起草正文）。
export interface CasebookDraftDescriptorView {
  draft_id?: string | null;
  structure_skeleton: string[];
  candidate_refs: CasebookCandidateRefView[];
  statute_refs: CasebookStatuteRefView[];
  note?: string | null;
  tag?: string | null;
}

// 脱敏 SearchProfile 摘要（仅白名单子集；dispute_focus_keywords 为短关键词数组，非正文）。
export interface CasebookSearchProfileSummary {
  case_cause?: string | null;
  region?: string | null;
  trial_level_preference?: string | null;
  dispute_focus_keywords?: string[] | null;
  query_text?: string | null;
}

// 已收敛的 CaseFolder 响应视图（只归集不起草，零正文）。
export interface CaseFolderView {
  case_folder_id: string;
  owner_user_id: string;
  team_id?: string | null;
  visibility: string;
  search_profile_summary?: CasebookSearchProfileSummary | null;
  candidate_refs: CasebookCandidateRefView[];
  draft_descriptors: CasebookDraftDescriptorView[];
  title?: string | null;
  note?: string | null;
  tag?: string | null;
  status: string;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface CaseFolderListResponse {
  folders: CaseFolderView[];
  folder_count: number;
}

// 调用方归集好的协作夹输入（内存态）。引用允许带多余字段，组装阶段只取白名单键。
export interface CaseFolderInput {
  search_profile_summary?: CasebookSearchProfileSummary | null;
  candidate_refs: CasebookCandidateRefView[];
  draft_descriptors: CasebookDraftDescriptorView[];
  title?: string | null;
  note?: string | null;
  tag?: string | null;
  // 更新时可携带 visibility（共享切换的主路径是 share 端点；update 仅在调用方显式给出时透传）。
  visibility?: string | null;
}

export type CasebookApiFailure = {
  ok: false;
  reason: "disabled" | "login_required" | "rejected" | "network_error" | "timeout" | "http_error";
  status?: number;
  reasonCode?: string;
  message?: string;
};

export type CaseFolderMutationResult =
  | { ok: true; data: CaseFolderView }
  | CasebookApiFailure;

export type CaseFolderListResult =
  | { ok: true; data: CaseFolderListResponse }
  | CasebookApiFailure;

// --- 白名单逐字段组装（防止透传正文/多余字段）-----------------------------------

// 类案来源锚点 -> 只取 case_id + source_chunk_id (+ anchor_type)，逐字段不 spread。
function toSourceAnchorBody(anchor: CasebookSourceAnchorView): Record<string, unknown> {
  return {
    case_id: String(anchor.case_id ?? ""),
    source_chunk_id: String(anchor.source_chunk_id ?? ""),
    ...(anchor.anchor_type != null ? { anchor_type: String(anchor.anchor_type) } : {}),
  };
}

// 法条来源锚点 -> 只取 text_id (+ law_name/article_no/anchor_type)，逐字段不 spread。
function toStatuteAnchorBody(anchor: CasebookStatuteAnchorView): Record<string, unknown> {
  return {
    text_id: String(anchor.text_id ?? ""),
    ...(anchor.law_name != null ? { law_name: String(anchor.law_name) } : {}),
    ...(anchor.article_no != null ? { article_no: String(anchor.article_no) } : {}),
    ...(anchor.anchor_type != null ? { anchor_type: String(anchor.anchor_type) } : {}),
  };
}

// 类案锚点是否有效：至少一个带 case_id + source_chunk_id 的锚点。
function hasCandidateAnchor(ref: CasebookCandidateRefView): boolean {
  return (
    Array.isArray(ref.source_anchors) &&
    ref.source_anchors.some(
      (a) =>
        typeof a?.case_id === "string" &&
        a.case_id.trim().length > 0 &&
        typeof a?.source_chunk_id === "string" &&
        a.source_chunk_id.trim().length > 0,
    )
  );
}

// 法条锚点是否有效：至少一个带非空 text_id 的锚点。
function hasStatuteAnchor(ref: CasebookStatuteRefView): boolean {
  return (
    Array.isArray(ref.statute_anchors) &&
    ref.statute_anchors.some((a) => typeof a?.text_id === "string" && a.text_id.trim().length > 0)
  );
}

// 文书骨架是否可归集：至少一个非空段落标题（无标题则无意义，丢弃）。
function hasSkeleton(d: CasebookDraftDescriptorView): boolean {
  return (
    Array.isArray(d.structure_skeleton) &&
    d.structure_skeleton.some((s) => typeof s === "string" && s.trim().length > 0)
  );
}

function clampShort(value: string | null | undefined, maxLen: number): string | null {
  if (typeof value !== "string") {
    return null;
  }
  const trimmed = value.trim();
  if (trimmed.length === 0) {
    return null;
  }
  return trimmed.slice(0, maxLen);
}

// 类案引用 -> 请求体（**只**逐字段取 CandidateRef 白名单七字段；不 spread）。
// 无论入参对象上挂了什么（summary / chunk_text / judgment_text …），出参只可能含这 7 个键。
function toCandidateRefBody(ref: CasebookCandidateRefView): Record<string, unknown> {
  return {
    case_id: String(ref.case_id ?? ""),
    case_number: ref.case_number ?? null,
    court: ref.court ?? null,
    trial_level: ref.trial_level ?? null,
    case_cause: ref.case_cause ?? null,
    judgment_date: ref.judgment_date ?? null,
    source_anchors: (ref.source_anchors ?? []).map(toSourceAnchorBody),
  };
}

// 法条引用 -> 请求体（**只**逐字段取白名单键 + 锚点；article_text 原样透传不改写不生成）。
function toStatuteRefBody(ref: CasebookStatuteRefView): Record<string, unknown> {
  return {
    statute_id: String(ref.statute_id ?? ""),
    law_name: String(ref.law_name ?? ""),
    article_no: ref.article_no ?? null,
    statute_anchors: (ref.statute_anchors ?? []).map(toStatuteAnchorBody),
    article_text: ref.article_text ?? null,
    source_corpus: ref.source_corpus ?? null,
    effective_status: ref.effective_status ?? null,
    related_case_refs: (ref.related_case_refs ?? [])
      .filter(hasCandidateAnchor)
      .map(toCandidateRefBody),
  };
}

// 段落标题清单 -> 只发标题字符串数组：去空白、丢空项（呼应后端骨架仅标题非正文）。
function toSkeletonBody(skeleton: string[]): string[] {
  return (Array.isArray(skeleton) ? skeleton : [])
    .map((item) => String(item ?? "").trim())
    .filter((item) => item.length > 0);
}

// 文书骨架引用 -> 请求体（**只**逐字段取白名单键；不 spread；正文型键绝不进入）。
// 无骨架标题直接丢弃；内嵌的 candidate/statute 引用同样逐项过锚点闸。
function toDraftDescriptorBody(d: CasebookDraftDescriptorView): Record<string, unknown> {
  return {
    draft_id: d.draft_id ?? null,
    structure_skeleton: toSkeletonBody(d.structure_skeleton),
    candidate_refs: (d.candidate_refs ?? []).filter(hasCandidateAnchor).map(toCandidateRefBody),
    statute_refs: (d.statute_refs ?? []).filter(hasStatuteAnchor).map(toStatuteRefBody),
    note: clampShort(d.note, NOTE_MAX_LEN),
    tag: clampShort(d.tag, TAG_MAX_LEN),
  };
}

// 脱敏 SearchProfile 摘要 -> 请求体（**只**逐字段取脱敏白名单子集 5 键；不 spread）。
// 注入原始案情 / 正文 / PII 键时，出参只可能含这 5 个键；空对象/全空时归一为 null（不发空壳）。
function toSearchProfileSummaryBody(
  summary: CasebookSearchProfileSummary | null | undefined,
): Record<string, unknown> | null {
  if (summary == null || typeof summary !== "object") {
    return null;
  }
  const keywords = Array.isArray(summary.dispute_focus_keywords)
    ? summary.dispute_focus_keywords
        .map((k) => String(k ?? "").trim())
        .filter((k) => k.length > 0)
    : null;
  const body: Record<string, unknown> = {
    case_cause: clampShort(summary.case_cause, NOTE_MAX_LEN),
    region: clampShort(summary.region, NOTE_MAX_LEN),
    trial_level_preference: clampShort(summary.trial_level_preference, NOTE_MAX_LEN),
    dispute_focus_keywords: keywords && keywords.length > 0 ? keywords : null,
    query_text: clampShort(summary.query_text, NOTE_MAX_LEN),
  };
  // 全空摘要不发空壳（避免后端存空 dict）。
  const hasAny = Object.values(body).some((v) => v != null);
  return hasAny ? body : null;
}

// 协作夹输入 -> create 请求体（**只**逐字段取白名单键；不 spread；create 不发 visibility）。
// 这是「不落正文 / 引用必带锚点 / 摘要仅脱敏子集」的可执行保证。
export function toCaseFolderCreateBody(input: CaseFolderInput): Record<string, unknown> {
  return {
    search_profile_summary: toSearchProfileSummaryBody(input.search_profile_summary),
    candidate_refs: (input.candidate_refs ?? []).filter(hasCandidateAnchor).map(toCandidateRefBody),
    draft_descriptors: (input.draft_descriptors ?? [])
      .filter(hasSkeleton)
      .map(toDraftDescriptorBody),
    title: clampShort(input.title, TITLE_MAX_LEN),
    note: clampShort(input.note, NOTE_MAX_LEN),
    tag: clampShort(input.tag, TAG_MAX_LEN),
  };
}

// 协作夹输入 -> update 请求体（同 create 白名单 + 可选 visibility；仍逐字段不 spread）。
export function toCaseFolderUpdateBody(input: CaseFolderInput): Record<string, unknown> {
  const body = toCaseFolderCreateBody(input);
  if (input.visibility != null) {
    body.visibility = String(input.visibility);
  }
  return body;
}

// E7-4 共享可见性枚举：只 private|team 两级（无 public）。
export type CaseFolderVisibility = "private" | "team";

// 共享切换输入：只携带 visibility (+ 共享到 team 时的 team_id)；零摘要/引用/正文。
export interface CaseFolderShareInput {
  visibility: CaseFolderVisibility;
  team_id?: string | null;
}

// 共享切换 -> 请求体（**只**逐字段取 visibility + team_id；不 spread；零正文/零引用）。
// 取消共享（visibility=private）不发 team_id（后端一并清空 team 归属）。
export function toCaseFolderShareBody(input: CaseFolderShareInput): Record<string, unknown> {
  const body: Record<string, unknown> = { visibility: input.visibility };
  if (input.visibility === "team") {
    const teamId = typeof input.team_id === "string" ? input.team_id.trim() : "";
    if (teamId.length > 0) {
      body.team_id = teamId;
    }
  }
  return body;
}

function classify(status: number): "disabled" | "login_required" | "rejected" | "http_error" {
  if (status === 403) return "disabled";
  if (status === 401) return "login_required";
  if (status === 400 || status === 422) return "rejected";
  return "http_error";
}

function getCasebookApiTimeoutMs(): number {
  const configured = Number(import.meta.env.VITE_SEARCH_API_TIMEOUT_MS);
  if (Number.isFinite(configured) && configured > 0) {
    return configured;
  }
  return DEFAULT_CASEBOOK_API_TIMEOUT_MS;
}

// 通用请求：仅 JSON body（已是白名单组装结果），失败安全分类，绝不重试到非白名单路径。
async function requestJson(
  path: string,
  init: { method: string; body?: Record<string, unknown>; token?: string; teamId?: string },
  timeoutMs?: number,
): Promise<{ ok: true; data: unknown } | CasebookApiFailure> {
  if (typeof fetch === "undefined") {
    return { ok: false, reason: "network_error" };
  }

  const controller =
    typeof AbortController !== "undefined" ? new AbortController() : undefined;
  const timer =
    controller && typeof setTimeout !== "undefined"
      ? setTimeout(() => controller.abort(), timeoutMs ?? getCasebookApiTimeoutMs())
      : undefined;

  const headers: Record<string, string> = {
    Accept: "application/json",
  };
  if (init.body !== undefined) {
    headers["Content-Type"] = "application/json";
  }
  if (init.token) {
    headers.Authorization = `Bearer ${init.token}`;
  }
  // X-Team-Id：仅传团队 ID（短标识，非正文/非凭据），用于进入团队态读取 visibility=team 协作夹。
  if (init.teamId) {
    headers["X-Team-Id"] = init.teamId;
  }

  let resp: Response;
  try {
    resp = await fetch(path, {
      method: init.method,
      headers,
      ...(init.body !== undefined ? { body: JSON.stringify(init.body) } : {}),
      ...(controller ? { signal: controller.signal } : {}),
    });
  } catch (error) {
    if (
      typeof error === "object" &&
      error !== null &&
      "name" in error &&
      (error as { name?: unknown }).name === "AbortError"
    ) {
      return { ok: false, reason: "timeout" };
    }
    return { ok: false, reason: "network_error" };
  } finally {
    if (timer !== undefined) {
      clearTimeout(timer);
    }
  }

  if (!resp.ok) {
    let reasonCode: string | undefined;
    let message: string | undefined;
    try {
      const errBody = (await resp.json()) as {
        error?: { code?: string; message?: string };
      };
      reasonCode = errBody?.error?.code;
      message = errBody?.error?.message;
    } catch {
      reasonCode = undefined;
    }
    return {
      ok: false,
      reason: classify(resp.status),
      status: resp.status,
      reasonCode,
      message,
    };
  }

  try {
    const data = await resp.json();
    return { ok: true, data };
  } catch {
    return { ok: false, reason: "http_error", status: resp.status };
  }
}

// 创建 CaseFolder：归集好的脱敏摘要 + 带锚点引用 + 短字段 -> CaseFolder（默认 private，零正文）。
export async function createCaseFolder(
  input: CaseFolderInput,
  options: { token?: string; timeoutMs?: number } = {},
): Promise<CaseFolderMutationResult> {
  const body = toCaseFolderCreateBody(input);
  const result = await requestJson(
    CASEBOOK_FOLDERS_API_PATH,
    { method: "POST", body, token: options.token },
    options.timeoutMs,
  );
  if (!result.ok) {
    return result;
  }
  return { ok: true, data: result.data as CaseFolderView };
}

// 列出当前用户/团队可见 CaseFolder（仅本人/团队，零正文）。
// 带 teamId 时进入团队态（X-Team-Id 头），可见自己的私有夹 + 本团队 visibility=team 共享夹。
export async function listCaseFolders(
  options: { token?: string; teamId?: string; timeoutMs?: number } = {},
): Promise<CaseFolderListResult> {
  const result = await requestJson(
    CASEBOOK_FOLDERS_API_PATH,
    { method: "GET", token: options.token, teamId: options.teamId },
    options.timeoutMs,
  );
  if (!result.ok) {
    return result;
  }
  return { ok: true, data: result.data as CaseFolderListResponse };
}

// 读取单个 CaseFolder（越权 -> 404 映射为 http_error）。
// 带 teamId 时进入团队态（X-Team-Id 头）：同 team 成员可读 visibility=team 协作夹。
export async function getCaseFolder(
  caseFolderId: string,
  options: { token?: string; teamId?: string; timeoutMs?: number } = {},
): Promise<CaseFolderMutationResult> {
  const result = await requestJson(
    `${CASEBOOK_FOLDERS_API_PATH}/${encodeURIComponent(caseFolderId)}`,
    { method: "GET", token: options.token, teamId: options.teamId },
    options.timeoutMs,
  );
  if (!result.ok) {
    return result;
  }
  return { ok: true, data: result.data as CaseFolderView };
}

// 更新 CaseFolder：全量替换归集引用/短字段（仍只发白名单 + 锚点；id 走 path 非 query）。
export async function updateCaseFolder(
  caseFolderId: string,
  input: CaseFolderInput,
  options: { token?: string; timeoutMs?: number } = {},
): Promise<CaseFolderMutationResult> {
  const body = toCaseFolderUpdateBody(input);
  const result = await requestJson(
    `${CASEBOOK_FOLDERS_API_PATH}/${encodeURIComponent(caseFolderId)}`,
    { method: "PUT", body, token: options.token },
    options.timeoutMs,
  );
  if (!result.ok) {
    return result;
  }
  return { ok: true, data: result.data as CaseFolderView };
}

// E7-4 共享切换：把 owner 本人 CaseFolder 的可见性在 private<->team 间切换（仅 owner，零正文）。
// 共享到 team 须给 team_id（owner 须为该 team 活跃成员，否则后端 404）；id 走 path 非 query。
export async function shareCaseFolder(
  caseFolderId: string,
  input: CaseFolderShareInput,
  options: { token?: string; timeoutMs?: number } = {},
): Promise<CaseFolderMutationResult> {
  const body = toCaseFolderShareBody(input);
  const result = await requestJson(
    `${CASEBOOK_FOLDERS_API_PATH}/${encodeURIComponent(caseFolderId)}/share`,
    { method: "POST", body, token: options.token },
    options.timeoutMs,
  );
  if (!result.ok) {
    return result;
  }
  return { ok: true, data: result.data as CaseFolderView };
}

// 供调用方在选入引用前做前端拦截（无锚点引用不可加入协作夹）。
export { hasCandidateAnchor, hasStatuteAnchor };




