// E4-4 案情录入端 API 客户端（前端）。
//
// 隐私 / 隔离红线（E4 生态红线①「原始案情零上送」的最后一道前端闸）：
//   - 只 POST 已脱敏的 SearchProfile 白名单五字段（+ 结构化检索参数 mode/limit）到
//     E4-3 的 /api/intake/search。请求体由白名单键**显式逐字段组装**，绝不 spread 任意对象，
//     杜绝原始案情 / raw_case / PII / 正文型键意外进入请求体。
//   - 原始口语化案情只存在于调用方的 React 内存态，绝不进入本模块、请求体、URL query。
//   - 不读写任何浏览器存储（localStorage / sessionStorage / IndexedDB / cookie）。
//   - 响应为 CandidateRef 视图（白名单七字段 + source_anchors，零正文）+ 降级信息。
//   - 关闭态（后端 403 INTAKE_DISABLED）时调用方回到入口不渲染的安全末态。
//   - 不接任何「服务端 AI 增强」路径（ENABLE_INTAKE_AI_EXTRACTION 默认 false、E4 无 on 路径）。

import type { SearchProfileDraft } from "../intake/sanitize";

export const INTAKE_SEARCH_API_PATH = "/api/intake/search";
const DEFAULT_INTAKE_API_TIMEOUT_MS = 20000;

// 请求体白名单（与后端 IntakeSearchRequest extra=forbid 逐字段一致，不得增删）。
export type IntakeSearchMode = "standard" | "expanded";

export interface IntakeSourceAnchorView {
  case_id: string;
  source_chunk_id: string;
  anchor_type?: string | null;
}

// CandidateRef 视图（白名单七字段 + 锚点，零正文）。
export interface IntakeCandidateRefView {
  case_id: string;
  case_number?: string | null;
  court?: string | null;
  trial_level?: string | null;
  case_cause?: string | null;
  judgment_date?: string | null;
  source_anchors: IntakeSourceAnchorView[];
}

export interface IntakeSearchResponse {
  query_session_id: string;
  candidate_refs: IntakeCandidateRefView[];
  candidate_count: number;
  degraded: boolean;
  degraded_reasons: string[];
  search_mode: IntakeSearchMode;
}

export type IntakeApiResult =
  | { ok: true; data: IntakeSearchResponse }
  | {
      ok: false;
      reason: "disabled" | "rejected" | "network_error" | "timeout" | "http_error";
      status?: number;
      reasonCode?: string;
      message?: string;
    };

// 已脱敏 SearchProfile -> intake 请求体（**只**逐字段取白名单键）。
// 这是「原始案情零上送」的可执行保证：无论入参对象上还挂了什么，出参只可能含这 7 个键。
export function toIntakeRequestBody(
  profile: SearchProfileDraft,
  options: { mode?: IntakeSearchMode; limit?: number } = {},
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

function classify(status: number): "disabled" | "rejected" | "http_error" {
  if (status === 403) return "disabled";
  if (status === 400 || status === 422) return "rejected";
  return "http_error";
}

function getIntakeApiTimeoutMs(): number {
  const configured = Number(import.meta.env.VITE_SEARCH_API_TIMEOUT_MS);
  if (Number.isFinite(configured) && configured > 0) {
    return configured;
  }
  return DEFAULT_INTAKE_API_TIMEOUT_MS;
}

// 提交一次录入端检索。入参为已脱敏 SearchProfile；请求体只含白名单字段。
export async function submitIntakeSearch(
  profile: SearchProfileDraft,
  options: { mode?: IntakeSearchMode; limit?: number; timeoutMs?: number } = {},
): Promise<IntakeApiResult> {
  if (typeof fetch === "undefined") {
    return { ok: false, reason: "network_error" };
  }

  const body = toIntakeRequestBody(profile, options);

  const controller =
    typeof AbortController !== "undefined" ? new AbortController() : undefined;
  const timer =
    controller && typeof setTimeout !== "undefined"
      ? setTimeout(() => controller.abort(), options.timeoutMs ?? getIntakeApiTimeoutMs())
      : undefined;

  let resp: Response;
  try {
    resp = await fetch(INTAKE_SEARCH_API_PATH, {
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

  let data: IntakeSearchResponse;
  try {
    data = (await resp.json()) as IntakeSearchResponse;
  } catch {
    return { ok: false, reason: "http_error", status: resp.status };
  }
  return { ok: true, data };
}
