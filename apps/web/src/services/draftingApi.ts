// E6-3 文书工作台 API 客户端（前端）。
//
// 隐私 / 隔离红线（E6「只组装锚定来源、不起草结论」+ 生态 3 红线的最后一道前端闸）：
//   - 四个端点（create / list / get / update）请求体均由白名单键**显式逐字段组装**，
//     绝不 spread 任意对象，杜绝起草正文 / 裁判正文 / 候选-chunk 正文 / 原始案情 / PII /
//     胜负结论型键意外进入请求体。
//   - structure_skeleton 只发**段落标题字符串数组**（非正文）；逐项做长度上限裁剪呼应后端校验。
//   - candidate_refs / statute_refs 只发**引用 + 锚点**（白名单字段），逐字段取键，绝不携带
//     summary / highlight / chunk_text / judgment_text 等正文键。无锚点引用在组装阶段即被丢弃。
//   - 草稿态（骨架 / 引用 / 短字段）只来自调用方 React 内存态，绝不进入 URL query string、
//     绝不读写任何浏览器存储（localStorage / sessionStorage / IndexedDB / cookie）。
//   - 关闭态（后端 403 DRAFTING_DISABLED）/ 未登录（401）时调用方回到入口不渲染的安全末态。
//   - **本模块零文本生成 / 零 AI 起草调用**：只搬运用户编排好的标题与带锚点引用。

export const DRAFTING_DRAFTS_API_PATH = "/api/drafting/drafts";
const DEFAULT_DRAFTING_API_TIMEOUT_MS = 20000;

// 与 E6-1/E6-2 契约一致的前端上限（呼应后端 sanitize_draft_descriptor 校验）。
export const STRUCTURE_SKELETON_ITEM_MAX_LEN = 60;
export const STRUCTURE_SKELETON_MAX_ITEMS = 64;
export const NOTE_MAX_LEN = 200;
export const TAG_MAX_LEN = 40;

// --- 引用锚点视图（结构化引用，非正文）-----------------------------------------

export interface DraftSourceAnchorView {
  case_id: string;
  source_chunk_id: string;
  anchor_type?: string | null;
}

export interface DraftStatuteAnchorView {
  text_id: string;
  law_name?: string | null;
  article_no?: string | null;
  anchor_type?: string | null;
}

// 文书引用的类案视图（= CandidateRef 白名单七字段 + 锚点，零裁判正文）。
export interface DraftCandidateRefView {
  case_id: string;
  case_number?: string | null;
  court?: string | null;
  trial_level?: string | null;
  case_cause?: string | null;
  judgment_date?: string | null;
  source_anchors: DraftSourceAnchorView[];
}

// 文书引用的法条视图（白名单 + 锚点；article_text 若有只来自语料、不得前端生成）。
export interface DraftStatuteRefView {
  statute_id: string;
  law_name: string;
  article_no?: string | null;
  statute_anchors: DraftStatuteAnchorView[];
  article_text?: string | null;
  source_corpus?: string | null;
  effective_status?: string | null;
  related_case_refs?: DraftCandidateRefView[];
}

// 已收敛的 DraftDescriptor 响应视图（只组装不起草，零正文）。
export interface DraftDescriptorView {
  draft_id: string;
  structure_skeleton: string[];
  candidate_refs: DraftCandidateRefView[];
  statute_refs: DraftStatuteRefView[];
  note?: string | null;
  tag?: string | null;
  owner_user_id: string;
  team_id?: string | null;
  visibility: string;
  status: string;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface DraftListResponse {
  drafts: DraftDescriptorView[];
  draft_count: number;
}

// 调用方编排好的草稿输入（内存态）。引用允许带多余字段，组装阶段只取白名单键。
export interface DraftDraftInput {
  structure_skeleton: string[];
  candidate_refs: DraftCandidateRefView[];
  statute_refs: DraftStatuteRefView[];
  note?: string | null;
  tag?: string | null;
}

export type DraftingApiFailure = {
  ok: false;
  reason: "disabled" | "login_required" | "rejected" | "network_error" | "timeout" | "http_error";
  status?: number;
  reasonCode?: string;
  message?: string;
};

export type DraftMutationResult =
  | { ok: true; data: DraftDescriptorView }
  | DraftingApiFailure;

export type DraftListResult =
  | { ok: true; data: DraftListResponse }
  | DraftingApiFailure;

// --- 白名单逐字段组装（防止透传正文/多余字段）-----------------------------------

// 类案来源锚点 -> 只取 case_id + source_chunk_id (+ anchor_type)，逐字段不 spread。
function toSourceAnchorBody(anchor: DraftSourceAnchorView): Record<string, unknown> {
  return {
    case_id: String(anchor.case_id ?? ""),
    source_chunk_id: String(anchor.source_chunk_id ?? ""),
    ...(anchor.anchor_type != null ? { anchor_type: String(anchor.anchor_type) } : {}),
  };
}

// 法条来源锚点 -> 只取 text_id (+ law_name/article_no/anchor_type)，逐字段不 spread。
function toStatuteAnchorBody(anchor: DraftStatuteAnchorView): Record<string, unknown> {
  return {
    text_id: String(anchor.text_id ?? ""),
    ...(anchor.law_name != null ? { law_name: String(anchor.law_name) } : {}),
    ...(anchor.article_no != null ? { article_no: String(anchor.article_no) } : {}),
    ...(anchor.anchor_type != null ? { anchor_type: String(anchor.anchor_type) } : {}),
  };
}

// 类案锚点是否有效：至少一个带 case_id + source_chunk_id 的锚点。
function hasCandidateAnchor(ref: DraftCandidateRefView): boolean {
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
function hasStatuteAnchor(ref: DraftStatuteRefView): boolean {
  return (
    Array.isArray(ref.statute_anchors) &&
    ref.statute_anchors.some((a) => typeof a?.text_id === "string" && a.text_id.trim().length > 0)
  );
}

// 类案引用 -> 请求体（**只**逐字段取 CandidateRef 白名单七字段；不 spread）。
// 无论入参对象上挂了什么（summary / chunk_text / judgment_text …），出参只可能含这 7 个键。
function toCandidateRefBody(ref: DraftCandidateRefView): Record<string, unknown> {
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
function toStatuteRefBody(ref: DraftStatuteRefView): Record<string, unknown> {
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

// 段落标题清单 -> 只发标题字符串数组：去空白、丢空项、裁剪到上限项数、单项裁剪到上限长度。
function toSkeletonBody(skeleton: string[]): string[] {
  return (Array.isArray(skeleton) ? skeleton : [])
    .map((item) => String(item ?? "").trim())
    .filter((item) => item.length > 0)
    .slice(0, STRUCTURE_SKELETON_MAX_ITEMS)
    .map((item) => item.slice(0, STRUCTURE_SKELETON_ITEM_MAX_LEN));
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

// 草稿输入 -> create/update 请求体（**只**逐字段取白名单 5 键；不 spread）。
// 这是「不落正文 / 引用必带锚点」的可执行保证：无锚点引用被丢弃；引用只剩白名单字段 + 锚点。
export function toDraftRequestBody(input: DraftDraftInput): Record<string, unknown> {
  return {
    structure_skeleton: toSkeletonBody(input.structure_skeleton),
    candidate_refs: (input.candidate_refs ?? [])
      .filter(hasCandidateAnchor)
      .map(toCandidateRefBody),
    statute_refs: (input.statute_refs ?? []).filter(hasStatuteAnchor).map(toStatuteRefBody),
    note: clampShort(input.note, NOTE_MAX_LEN),
    tag: clampShort(input.tag, TAG_MAX_LEN),
  };
}

function classify(status: number): "disabled" | "login_required" | "rejected" | "http_error" {
  if (status === 403) return "disabled";
  if (status === 401) return "login_required";
  if (status === 400 || status === 422) return "rejected";
  return "http_error";
}

function getDraftingApiTimeoutMs(): number {
  const configured = Number(import.meta.env.VITE_SEARCH_API_TIMEOUT_MS);
  if (Number.isFinite(configured) && configured > 0) {
    return configured;
  }
  return DEFAULT_DRAFTING_API_TIMEOUT_MS;
}

// 通用请求：仅 JSON body（已是白名单组装结果），失败安全分类，绝不重试到非白名单路径。
async function requestJson(
  path: string,
  init: { method: string; body?: Record<string, unknown>; token?: string },
  timeoutMs?: number,
): Promise<{ ok: true; data: unknown } | DraftingApiFailure> {
  if (typeof fetch === "undefined") {
    return { ok: false, reason: "network_error" };
  }

  const controller =
    typeof AbortController !== "undefined" ? new AbortController() : undefined;
  const timer =
    controller && typeof setTimeout !== "undefined"
      ? setTimeout(() => controller.abort(), timeoutMs ?? getDraftingApiTimeoutMs())
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

// 创建草稿：编排好的骨架 + 带锚点引用 + 短字段 -> DraftDescriptor（零正文）。
export async function createDraft(
  input: DraftDraftInput,
  options: { token?: string; timeoutMs?: number } = {},
): Promise<DraftMutationResult> {
  const body = toDraftRequestBody(input);
  const result = await requestJson(
    DRAFTING_DRAFTS_API_PATH,
    { method: "POST", body, token: options.token },
    options.timeoutMs,
  );
  if (!result.ok) {
    return result;
  }
  return { ok: true, data: result.data as DraftDescriptorView };
}

// 列出当前用户/团队可见草稿（仅本人/团队，零正文）。
export async function listDrafts(
  options: { token?: string; timeoutMs?: number } = {},
): Promise<DraftListResult> {
  const result = await requestJson(
    DRAFTING_DRAFTS_API_PATH,
    { method: "GET", token: options.token },
    options.timeoutMs,
  );
  if (!result.ok) {
    return result;
  }
  return { ok: true, data: result.data as DraftListResponse };
}

// 读取单个草稿（越权 -> 404 映射为 http_error）。
export async function getDraft(
  draftId: string,
  options: { token?: string; timeoutMs?: number } = {},
): Promise<DraftMutationResult> {
  const result = await requestJson(
    `${DRAFTING_DRAFTS_API_PATH}/${encodeURIComponent(draftId)}`,
    { method: "GET", token: options.token },
    options.timeoutMs,
  );
  if (!result.ok) {
    return result;
  }
  return { ok: true, data: result.data as DraftDescriptorView };
}

// 更新草稿：全量替换骨架/引用/短字段（仍只发白名单 + 锚点）。
export async function updateDraft(
  draftId: string,
  input: DraftDraftInput,
  options: { token?: string; timeoutMs?: number } = {},
): Promise<DraftMutationResult> {
  const body = toDraftRequestBody(input);
  const result = await requestJson(
    `${DRAFTING_DRAFTS_API_PATH}/${encodeURIComponent(draftId)}`,
    { method: "PUT", body, token: options.token },
    options.timeoutMs,
  );
  if (!result.ok) {
    return result;
  }
  return { ok: true, data: result.data as DraftDescriptorView };
}

// 供调用方在选入引用前做前端拦截（无锚点不可加入草稿）。
export { hasCandidateAnchor, hasStatuteAnchor };
