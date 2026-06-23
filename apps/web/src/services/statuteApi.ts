// E5-5 法条法规检索 API 客户端（前端）。
//
// 隐私 / 隔离红线（E5 法条特有红线 + 生态 3 红线的最后一道前端闸）：
//   - 三个端点（/search、/by-case、/cases-by-statute）请求体均由白名单键**显式逐字段组装**，
//     绝不 spread 任意对象，杜绝原始案情 / raw_case / PII / 裁判正文 / 模型生成条文型键意外进入请求体。
//   - 查询态（已脱敏 SearchProfile / case_id / statute_id）只来自调用方 React 内存态，
//     绝不进入 URL query string、绝不读写任何浏览器存储（localStorage / sessionStorage / IndexedDB / cookie）。
//   - /search 响应为 StatuteRef 视图：条文只来自法条语料、必带 text_id 锚点；前端不生成 / 不补全 / 不改写条文。
//   - /cases-by-statute 响应为 CandidateRef 视图（白名单七字段 + source_anchors，零裁判正文）。
//   - 关闭态（后端 403 STATUTE_SEARCH_DISABLED）时调用方回到入口不渲染的安全末态。

import type { SearchProfileDraft } from "../intake/sanitize";

export const STATUTE_SEARCH_API_PATH = "/api/statute/search";
export const STATUTE_BY_CASE_API_PATH = "/api/statute/by-case";
export const STATUTE_CASES_BY_STATUTE_API_PATH = "/api/statute/cases-by-statute";
const DEFAULT_STATUTE_API_TIMEOUT_MS = 20000;

export type StatuteSearchMode = "standard" | "expanded";

// 法条来源锚点（结构化引用，指向法条语料 text_id；非条文正文）。
export interface StatuteAnchorView {
  text_id: string;
  law_name?: string | null;
  article_no?: string | null;
  anchor_type?: string | null;
}

// 互跳类案来源锚点（结构化引用，非裁判正文）。
export interface StatuteSourceAnchorView {
  case_id: string;
  source_chunk_id: string;
  anchor_type?: string | null;
}

// StatuteRef.related_case_refs 视图（= CandidateRef 白名单七字段，零裁判正文）。
export interface StatuteRelatedCaseView {
  case_id: string;
  case_number?: string | null;
  court?: string | null;
  trial_level?: string | null;
  case_cause?: string | null;
  judgment_date?: string | null;
  source_anchors: StatuteSourceAnchorView[];
}

// StatuteRef 视图（白名单 + 锚点；article_text 只来自语料、不得由前端生成）。
export interface StatuteRefView {
  statute_id: string;
  law_name: string;
  article_no?: string | null;
  statute_anchors: StatuteAnchorView[];
  article_text?: string | null;
  source_corpus?: string | null;
  effective_status?: string | null;
  related_case_refs?: StatuteRelatedCaseView[];
}

// 法条→类案互跳 CandidateRef 视图（白名单七字段 + 锚点，零正文）。
export interface StatuteCandidateRefView {
  case_id: string;
  case_number?: string | null;
  court?: string | null;
  trial_level?: string | null;
  case_cause?: string | null;
  judgment_date?: string | null;
  source_anchors: StatuteSourceAnchorView[];
}

export interface StatuteSearchResponse {
  query_session_id: string;
  statute_refs: StatuteRefView[];
  statute_count: number;
  degraded: boolean;
  degraded_reasons: string[];
  search_mode: StatuteSearchMode;
}

export interface StatuteCasesResponse {
  query_session_id: string;
  candidate_refs: StatuteCandidateRefView[];
  candidate_count: number;
  degraded: boolean;
  degraded_reasons: string[];
  search_mode: StatuteSearchMode;
}

export type StatuteApiFailure = {
  ok: false;
  reason: "disabled" | "rejected" | "network_error" | "timeout" | "http_error";
  status?: number;
  reasonCode?: string;
  message?: string;
};

export type StatuteSearchResult =
  | { ok: true; data: StatuteSearchResponse }
  | StatuteApiFailure;

export type StatuteCasesResult =
  | { ok: true; data: StatuteCasesResponse }
  | StatuteApiFailure;

// 已脱敏 SearchProfile + 检索参数 -> /search 请求体（**只**逐字段取白名单键）。
// 「原始案情零上送」的可执行保证：无论入参对象上挂了什么，出参只可能含这 7 个键。
export function toStatuteSearchBody(
  profile: SearchProfileDraft,
  options: { mode?: StatuteSearchMode; limit?: number } = {},
): Record<string, unknown> {
  return {
    case_cause: profile.case_cause ?? null,
    region: profile.region ?? null,
    trial_level_preference: profile.trial_level_preference ?? null,
    dispute_focus_keywords: Array.isArray(profile.dispute_focus_keywords)
      ? [...profile.dispute_focus_keywords]
      : [],
    query_text: profile.query_text ?? "",
    mode: options.mode ?? "standard",
    limit: options.limit ?? 10,
  };
}

// 类案→法条互跳 -> /by-case 请求体（只逐字段取白名单键：case_id + mode/limit）。
export function toStatuteByCaseBody(
  caseId: string,
  options: { mode?: StatuteSearchMode; limit?: number } = {},
): Record<string, unknown> {
  return {
    case_id: String(caseId ?? ""),
    mode: options.mode ?? "standard",
    limit: options.limit ?? 10,
  };
}

// 法条→类案互跳 -> /cases-by-statute 请求体（只逐字段取白名单键：statute_id + mode/limit）。
export function toStatuteCasesBody(
  statuteId: string,
  options: { mode?: StatuteSearchMode; limit?: number } = {},
): Record<string, unknown> {
  return {
    statute_id: String(statuteId ?? ""),
    mode: options.mode ?? "standard",
    limit: options.limit ?? 10,
  };
}

function classify(status: number): "disabled" | "rejected" | "http_error" {
  if (status === 403) return "disabled";
  if (status === 400 || status === 422) return "rejected";
  return "http_error";
}

function getStatuteApiTimeoutMs(): number {
  const configured = Number(import.meta.env.VITE_SEARCH_API_TIMEOUT_MS);
  if (Number.isFinite(configured) && configured > 0) {
    return configured;
  }
  return DEFAULT_STATUTE_API_TIMEOUT_MS;
}

// 通用 POST：只发 JSON body（已是白名单组装结果），失败安全分类，绝不重试到非白名单路径。
async function postJson(
  path: string,
  body: Record<string, unknown>,
  timeoutMs?: number,
): Promise<
  | { ok: true; data: unknown }
  | StatuteApiFailure
> {
  if (typeof fetch === "undefined") {
    return { ok: false, reason: "network_error" };
  }

  const controller =
    typeof AbortController !== "undefined" ? new AbortController() : undefined;
  const timer =
    controller && typeof setTimeout !== "undefined"
      ? setTimeout(() => controller.abort(), timeoutMs ?? getStatuteApiTimeoutMs())
      : undefined;

  let resp: Response;
  try {
    resp = await fetch(path, {
      method: "POST",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
      },
      body: JSON.stringify(body),
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

// 法条检索：已脱敏 SearchProfile -> StatuteRef[]（条文带 text_id 锚点，零裁判正文）。
export async function submitStatuteSearch(
  profile: SearchProfileDraft,
  options: { mode?: StatuteSearchMode; limit?: number; timeoutMs?: number } = {},
): Promise<StatuteSearchResult> {
  const body = toStatuteSearchBody(profile, options);
  const result = await postJson(STATUTE_SEARCH_API_PATH, body, options.timeoutMs);
  if (!result.ok) {
    return result;
  }
  return { ok: true, data: result.data as StatuteSearchResponse };
}

// 类案→法条互跳：case_id -> 关联 StatuteRef[]（带锚点，零正文）。
export async function fetchStatutesByCase(
  caseId: string,
  options: { mode?: StatuteSearchMode; limit?: number; timeoutMs?: number } = {},
): Promise<StatuteSearchResult> {
  const body = toStatuteByCaseBody(caseId, options);
  const result = await postJson(STATUTE_BY_CASE_API_PATH, body, options.timeoutMs);
  if (!result.ok) {
    return result;
  }
  return { ok: true, data: result.data as StatuteSearchResponse };
}

// 法条→类案互跳：statute_id -> 关联 CandidateRef[]（白名单七字段 + 锚点，零正文）。
export async function fetchCasesByStatute(
  statuteId: string,
  options: { mode?: StatuteSearchMode; limit?: number; timeoutMs?: number } = {},
): Promise<StatuteCasesResult> {
  const body = toStatuteCasesBody(statuteId, options);
  const result = await postJson(
    STATUTE_CASES_BY_STATUTE_API_PATH,
    body,
    options.timeoutMs,
  );
  if (!result.ok) {
    return result;
  }
  return { ok: true, data: result.data as StatuteCasesResponse };
}
