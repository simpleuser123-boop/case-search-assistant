import type { SearchTrigger } from "../lib/searchValidation";

export const ANALYTICS_API_PATH = "/api/events";
export const ANALYTICS_EVENT_TARGET = "case-search:analytics";

export type AnalyticsEventName =
  | "search_submit"
  | "search_result_render"
  | "result_card_click"
  | "case_detail_view"
  | "search_refine"
  | "search_zero_result"
  | "extended_search_trigger"
  | "page_exit";

type AnalyticsMetadataValue = string | number | boolean | null;
type AnalyticsMetadata = Record<string, AnalyticsMetadataValue>;

export type AnalyticsEventRequest = {
  event_name: AnalyticsEventName;
  query_session_id?: string | null;
  timestamp: string;
  metadata: AnalyticsMetadata;
};

export type AnalyticsTrackResult = {
  sent: boolean;
  reason?:
    | "missing_query_session_id"
    | "sensitive_metadata"
    | "network_error"
    | "http_error"
    | "beacon_unavailable";
  status?: number;
  request?: AnalyticsEventRequest;
};

export type AnalyticsEventInput = {
  event_name: AnalyticsEventName;
  query_session_id?: string | null;
  metadata?: Record<string, unknown>;
  transport?: "fetch" | "beacon";
};

export type SearchSubmitAnalyticsPayload = {
  query_session_id?: string | null;
  input_length: number;
  trigger: SearchTrigger;
  has_draft_restored: boolean;
};

export type SearchResultRenderAnalyticsPayload = {
  query_session_id: string;
  result_count: number;
  degraded: boolean;
  total_duration_ms: number;
  degraded_reason_count: number;
};

export type ResultCardClickAnalyticsPayload = {
  query_session_id?: string | null;
  case_id: string;
  rank: number;
  similarity_score?: number | null;
};

export type CaseDetailViewAnalyticsPayload = {
  query_session_id?: string | null;
  case_id: string;
  rank: number;
};

export type SearchRefineAnalyticsPayload = {
  query_session_id: string;
  refine_count: number;
  previous_result_count: number;
  input_length: number;
};

export type SearchZeroResultAnalyticsPayload = {
  query_session_id: string;
  input_length: number;
  fallback_available: boolean;
};

export type ExtendedSearchTriggerAnalyticsPayload = {
  query_session_id: string;
  main_result_count: number;
};

export type PageExitAnalyticsPayload = {
  query_session_id?: string | null;
  last_visible_result_count: number;
  dwell_time_ms: number;
};

const SENSITIVE_METADATA_KEYS = new Set([
  "query",
  "raw_query",
  "raw_text",
  "content",
  "text",
  "case_text",
  "fact",
  "prompt",
  "api_key",
  "secret",
  "password",
  "token",
  "phone",
  "id_card",
  "identity_card",
  "deepseek_key",
  "案情全文",
]);

const QUERY_SESSION_REQUIRED_EVENTS = new Set<AnalyticsEventName>([
  "search_result_render",
  "result_card_click",
  "case_detail_view",
  "search_refine",
  "search_zero_result",
  "extended_search_trigger",
  "page_exit",
]);

const EVENT_METADATA_KEYS: Record<AnalyticsEventName, readonly string[]> = {
  search_submit: ["input_length", "trigger", "has_draft_restored"],
  search_result_render: [
    "result_count",
    "total_duration_ms",
    "degraded",
    "degraded_reason_count",
  ],
  result_card_click: ["case_id_hash", "rank", "similarity_score"],
  case_detail_view: ["case_id_hash", "rank"],
  search_refine: ["refine_count", "previous_result_count", "input_length"],
  search_zero_result: ["input_length", "fallback_available"],
  extended_search_trigger: ["main_result_count"],
  page_exit: ["last_visible_result_count", "dwell_time_ms"],
};

export function trackSearchSubmit(payload: SearchSubmitAnalyticsPayload) {
  const { query_session_id, ...metadata } = payload;
  return trackAnalyticsEvent({
    event_name: "search_submit",
    query_session_id,
    metadata,
  });
}

export function trackSearchResultRender(
  payload: SearchResultRenderAnalyticsPayload
) {
  const { query_session_id, ...metadata } = payload;
  return trackAnalyticsEvent({
    event_name: "search_result_render",
    query_session_id,
    metadata,
  });
}

export async function trackResultCardClick(
  payload: ResultCardClickAnalyticsPayload
) {
  if (!payload.case_id.trim()) {
    return {
      sent: false,
      reason: "sensitive_metadata",
    } satisfies AnalyticsTrackResult;
  }

  return trackAnalyticsEvent({
    event_name: "result_card_click",
    query_session_id: payload.query_session_id,
    metadata: {
      case_id_hash: await hashAnalyticsIdentifier(payload.case_id),
      rank: payload.rank,
      similarity_score: payload.similarity_score ?? null,
    },
  });
}

export async function trackCaseDetailView(
  payload: CaseDetailViewAnalyticsPayload
) {
  if (!payload.case_id.trim()) {
    return {
      sent: false,
      reason: "sensitive_metadata",
    } satisfies AnalyticsTrackResult;
  }

  return trackAnalyticsEvent({
    event_name: "case_detail_view",
    query_session_id: payload.query_session_id,
    metadata: {
      case_id_hash: await hashAnalyticsIdentifier(payload.case_id),
      rank: payload.rank,
    },
  });
}

export function trackSearchRefine(payload: SearchRefineAnalyticsPayload) {
  const { query_session_id, ...metadata } = payload;
  return trackAnalyticsEvent({
    event_name: "search_refine",
    query_session_id,
    metadata,
  });
}

export function trackSearchZeroResult(payload: SearchZeroResultAnalyticsPayload) {
  const { query_session_id, ...metadata } = payload;
  return trackAnalyticsEvent({
    event_name: "search_zero_result",
    query_session_id,
    metadata,
  });
}

export function trackExtendedSearchTrigger(
  payload: ExtendedSearchTriggerAnalyticsPayload
) {
  const { query_session_id, ...metadata } = payload;
  return trackAnalyticsEvent({
    event_name: "extended_search_trigger",
    query_session_id,
    metadata,
  });
}

export function trackPageExit(payload: PageExitAnalyticsPayload) {
  const { query_session_id, ...metadata } = payload;
  return trackAnalyticsEvent({
    event_name: "page_exit",
    query_session_id,
    metadata,
    transport: "beacon",
  });
}

export function trackAnalyticsEvent(
  input: AnalyticsEventInput
): Promise<AnalyticsTrackResult> {
  const requestResult = buildAnalyticsRequest(input);

  if (!requestResult.request) {
    return Promise.resolve({
      sent: false,
      reason: requestResult.reason,
    });
  }

  dispatchAnalyticsEvent(requestResult.request);

  if (input.transport === "beacon") {
    const beaconSent = sendBeaconRequest(requestResult.request);

    if (beaconSent) {
      return Promise.resolve({
        sent: true,
        request: requestResult.request,
      });
    }
  }

  return sendFetchRequest(requestResult.request);
}

export function buildAnalyticsRequest(input: AnalyticsEventInput):
  | {
      request: AnalyticsEventRequest;
      reason?: never;
    }
  | {
      request?: never;
      reason: AnalyticsTrackResult["reason"];
    } {
  const querySessionId = normalizeQuerySessionId(input.query_session_id);

  if (QUERY_SESSION_REQUIRED_EVENTS.has(input.event_name) && !querySessionId) {
    return { reason: "missing_query_session_id" };
  }

  const rawMetadata = input.metadata ?? {};

  if (findSensitiveMetadataKeys(rawMetadata).length > 0) {
    return { reason: "sensitive_metadata" };
  }

  return {
    request: {
      event_name: input.event_name,
      query_session_id: querySessionId,
      timestamp: new Date().toISOString(),
      metadata: sanitizeMetadata(input.event_name, rawMetadata),
    },
  };
}

export async function hashAnalyticsIdentifier(value: string) {
  const normalized = value.trim();

  if (!normalized) {
    return "missing_case_id";
  }

  if (globalThis.crypto?.subtle && typeof TextEncoder !== "undefined") {
    const digest = await globalThis.crypto.subtle.digest(
      "SHA-256",
      new TextEncoder().encode(normalized)
    );
    const hex = Array.from(new Uint8Array(digest))
      .map((byte) => byte.toString(16).padStart(2, "0"))
      .join("");

    return `sha256_${hex.slice(0, 24)}`;
  }

  return fallbackHashIdentifier(normalized);
}

function normalizeQuerySessionId(value: string | null | undefined) {
  const normalized = value?.trim();
  return normalized || undefined;
}

function sanitizeMetadata(
  eventName: AnalyticsEventName,
  metadata: Record<string, unknown>
) {
  const allowedKeys = EVENT_METADATA_KEYS[eventName];
  const safeMetadata: AnalyticsMetadata = {};

  allowedKeys.forEach((key) => {
    const safeValue = sanitizeMetadataValue(key, metadata[key]);

    if (safeValue !== undefined) {
      safeMetadata[key] = safeValue;
    }
  });

  return safeMetadata;
}

function sanitizeMetadataValue(
  key: string,
  value: unknown
): AnalyticsMetadataValue | undefined {
  if (value === null && key === "similarity_score") {
    return null;
  }

  if (key === "trigger") {
    return value === "button" || value === "keyboard" ? value : undefined;
  }

  if (key === "case_id_hash") {
    return typeof value === "string" && isSafeHashValue(value)
      ? value
      : undefined;
  }

  if (
    key === "degraded" ||
    key === "has_draft_restored" ||
    key === "fallback_available"
  ) {
    return typeof value === "boolean" ? value : undefined;
  }

  if (key === "similarity_score") {
    return toFiniteNumber(value);
  }

  return toNonNegativeInteger(value);
}

function toNonNegativeInteger(value: unknown) {
  const numericValue = typeof value === "number" ? value : Number(value);

  if (!Number.isFinite(numericValue) || numericValue < 0) {
    return undefined;
  }

  return Math.round(numericValue);
}

function toFiniteNumber(value: unknown) {
  const numericValue = typeof value === "number" ? value : Number(value);

  if (!Number.isFinite(numericValue)) {
    return undefined;
  }

  return numericValue;
}

function isSafeHashValue(value: string) {
  return /^(sha256_[a-f0-9]{24}|fnv1a_[a-f0-9]{8})$/.test(value);
}

function findSensitiveMetadataKeys(value: unknown): string[] {
  const found: string[] = [];

  if (Array.isArray(value)) {
    value.forEach((item) => {
      found.push(...findSensitiveMetadataKeys(item));
    });
    return found;
  }

  if (!value || typeof value !== "object") {
    return found;
  }

  Object.entries(value as Record<string, unknown>).forEach(([key, child]) => {
    if (SENSITIVE_METADATA_KEYS.has(key.toLowerCase())) {
      found.push(key);
    }

    found.push(...findSensitiveMetadataKeys(child));
  });

  return found;
}

function dispatchAnalyticsEvent(request: AnalyticsEventRequest) {
  if (typeof window === "undefined") {
    return;
  }

  window.dispatchEvent(
    new CustomEvent<AnalyticsEventRequest>(ANALYTICS_EVENT_TARGET, {
      detail: request,
    })
  );
}

function sendBeaconRequest(request: AnalyticsEventRequest) {
  if (typeof navigator === "undefined" || !navigator.sendBeacon) {
    return false;
  }

  const body = JSON.stringify(request);
  const blob = new Blob([body], { type: "application/json" });
  return navigator.sendBeacon(ANALYTICS_API_PATH, blob);
}

async function sendFetchRequest(
  request: AnalyticsEventRequest
): Promise<AnalyticsTrackResult> {
  if (typeof fetch === "undefined") {
    return {
      sent: false,
      reason: "network_error",
      request,
    };
  }

  try {
    const response = await fetch(ANALYTICS_API_PATH, {
      method: "POST",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
      },
      body: JSON.stringify(request),
      keepalive: request.event_name === "page_exit",
    });

    if (!response.ok) {
      return {
        sent: false,
        reason: "http_error",
        status: response.status,
        request,
      };
    }

    return {
      sent: true,
      status: response.status,
      request,
    };
  } catch {
    return {
      sent: false,
      reason: "network_error",
      request,
    };
  }
}

function fallbackHashIdentifier(value: string) {
  let hash = 0x811c9dc5;

  Array.from(value).forEach((char) => {
    hash ^= char.charCodeAt(0);
    hash = Math.imul(hash, 0x01000193);
  });

  return `fnv1a_${(hash >>> 0).toString(16).padStart(8, "0")}`;
}
