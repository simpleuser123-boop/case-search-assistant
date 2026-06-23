import {
  MOCK_EXPAND_SEARCH_RESPONSE,
  MOCK_SEARCH_RESPONSE,
} from "../mocks/searchMockFixture";
import { MOCK_CASE_DETAILS, buildMockFactAlignment } from "../mocks/caseDetailMockFixture";
import type {
  CaseDetailResponse,
  CaseDetailResult,
  FactAlignmentResponse,
  FactAlignmentResult,
  SearchApiErrorResponse,
  SearchCasesResult,
  SearchExpandRequest,
  SearchRequest,
  SearchResponse,
  SearchResultSource,
} from "../types/search";

const SEARCH_API_PATH = "/api/search";
const SEARCH_EXPAND_API_PATH = "/api/search/expand";
const CASE_DETAIL_API_PATH = "/api/cases";
const FACT_ALIGNMENT_API_SUFFIX = "fact-alignment";
const MOCK_DELAY_MS = 220;
const DEFAULT_SEARCH_API_TIMEOUT_MS = 20000;
const CLIENT_TIMEOUT_CODE = "CLIENT_TIMEOUT";
const CLIENT_NETWORK_ERROR_CODE = "CLIENT_NETWORK_ERROR";

export class SearchApiError extends Error {
  readonly code?: string;
  readonly querySessionId?: string | null;
  readonly status?: number;

  constructor({
    message,
    code,
    querySessionId,
    status,
  }: {
    message: string;
    code?: string;
    querySessionId?: string | null;
    status?: number;
  }) {
    super(message);
    this.name = "SearchApiError";
    this.code = code;
    this.querySessionId = querySessionId;
    this.status = status;
  }
}

export function getSearchClientMode(): SearchResultSource {
  return import.meta.env.VITE_SEARCH_API_MODE === "mock" ? "mock" : "api";
}

export async function searchCases(
  payload: SearchRequest,
  options: { useMock?: boolean; timeoutMs?: number } = {}
): Promise<SearchCasesResult> {
  const source = options.useMock ? "mock" : getSearchClientMode();

  if (source === "mock") {
    await delay(MOCK_DELAY_MS);
    return {
      response: cloneSearchResponse(MOCK_SEARCH_RESPONSE),
      source,
    };
  }

  let response: Response;
  try {
    response = await fetchWithTimeout(
      SEARCH_API_PATH,
      {
        method: "POST",
        headers: {
          Accept: "application/json",
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          mode: "standard",
          limit: 10,
          ...payload,
        }),
      },
      options.timeoutMs
    );
  } catch (error) {
    throw toClientRequestError(
      error,
      "检索请求超时，请稍后重试；若连续超时，可先关闭查询改写或摘要增强后再试。",
      "检索服务暂时不可用，请稍后重试或调整案情描述。"
    );
  }

  const body = (await safeJson(response)) as SearchResponse | SearchApiErrorResponse | null;

  if (!response.ok) {
    const errorBody = isSearchApiErrorResponse(body) ? body.error : undefined;
    throw new SearchApiError({
      status: response.status,
      code: errorBody?.code,
      querySessionId: errorBody?.query_session_id,
      message:
        errorBody?.message ||
        "检索服务暂时不可用，请稍后重试或调整案情描述。",
    });
  }

  return {
    response: body as SearchResponse,
    source,
  };
}

export async function expandSearchCases(
  payload: SearchExpandRequest,
  options: { useMock?: boolean; timeoutMs?: number } = {}
): Promise<SearchCasesResult> {
  const source = options.useMock ? "mock" : getSearchClientMode();

  if (source === "mock") {
    await delay(MOCK_DELAY_MS);
    return {
      response: cloneSearchResponse(MOCK_EXPAND_SEARCH_RESPONSE),
      source,
    };
  }

  let response: Response;
  try {
    response = await fetchWithTimeout(
      SEARCH_EXPAND_API_PATH,
      {
        method: "POST",
        headers: {
          Accept: "application/json",
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          mode: "expand",
          limit: 10,
          ...payload,
        }),
      },
      options.timeoutMs
    );
  } catch (error) {
    throw toClientRequestError(
      error,
      "补充候选请求超时，请保留当前结果并稍后重试。",
      "补充候选暂时不可用，请保留当前结果并稍后重试。"
    );
  }

  const body = (await safeJson(response)) as SearchResponse | SearchApiErrorResponse | null;

  if (!response.ok) {
    const errorBody = isSearchApiErrorResponse(body) ? body.error : undefined;
    throw new SearchApiError({
      status: response.status,
      code: errorBody?.code,
      querySessionId: errorBody?.query_session_id,
      message:
        errorBody?.message ||
        "补充候选暂时不可用，请保留当前结果并稍后重试。",
    });
  }

  return {
    response: body as SearchResponse,
    source,
  };
}

export async function fetchCaseDetail(
  caseId: string,
  options: { useMock?: boolean; timeoutMs?: number } = {}
): Promise<CaseDetailResult> {
  const source = options.useMock ? "mock" : getSearchClientMode();

  if (source === "mock") {
    await delay(MOCK_DELAY_MS);
    const detail = MOCK_CASE_DETAILS[caseId];

    if (!detail) {
      throw new SearchApiError({
        status: 404,
        code: "CASE_NOT_FOUND",
        message: "未找到指定案例详情。",
      });
    }

    return {
      response: cloneCaseDetailResponse(detail),
      source,
    };
  }

  let response: Response;
  try {
    response = await fetchWithTimeout(
      `${CASE_DETAIL_API_PATH}/${encodeURIComponent(caseId)}`,
      {
        method: "GET",
        headers: {
          Accept: "application/json",
        },
      },
      options.timeoutMs
    );
  } catch (error) {
    throw toClientRequestError(
      error,
      "案例详情请求超时，请稍后重试。",
      "案例详情暂时不可用，请稍后重试。"
    );
  }

  const body = (await safeJson(response)) as
    | CaseDetailResponse
    | SearchApiErrorResponse
    | null;

  if (!response.ok) {
    const errorBody = isSearchApiErrorResponse(body) ? body.error : undefined;
    throw new SearchApiError({
      status: response.status,
      code: errorBody?.code,
      querySessionId: errorBody?.query_session_id,
      message: errorBody?.message || "案例详情暂时不可用，请稍后重试。",
    });
  }

  return {
    response: body as CaseDetailResponse,
    source,
  };
}

export async function fetchFactAlignment(
  caseId: string,
  querySignal: string,
  options: { useMock?: boolean; timeoutMs?: number } = {}
): Promise<FactAlignmentResult> {
  const source = options.useMock ? "mock" : getSearchClientMode();

  if (source === "mock") {
    await delay(MOCK_DELAY_MS);
    const detail = MOCK_CASE_DETAILS[caseId];
    if (!detail) {
      throw new SearchApiError({
        status: 404,
        code: "CASE_NOT_FOUND",
        message: "未找到指定案例详情。",
      });
    }
    return {
      response: buildMockFactAlignment(detail, querySignal),
      source,
    };
  }

  let response: Response;
  try {
    response = await fetchWithTimeout(
      `${CASE_DETAIL_API_PATH}/${encodeURIComponent(caseId)}/${FACT_ALIGNMENT_API_SUFFIX}`,
      {
        method: "POST",
        headers: {
          Accept: "application/json",
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ query_signal: querySignal }),
      },
      options.timeoutMs
    );
  } catch (error) {
    throw toClientRequestError(
      error,
      "事实对比请求超时，请稍后重试；详情其他内容不受影响。",
      "事实对比暂时不可用，请稍后重试；详情其他内容不受影响。"
    );
  }

  const body = (await safeJson(response)) as
    | FactAlignmentResponse
    | SearchApiErrorResponse
    | null;

  if (!response.ok) {
    const errorBody = isSearchApiErrorResponse(body) ? body.error : undefined;
    throw new SearchApiError({
      status: response.status,
      code: errorBody?.code,
      querySessionId: errorBody?.query_session_id,
      message: errorBody?.message || "事实对比暂时不可用，请稍后重试。",
    });
  }

  return {
    response: body as FactAlignmentResponse,
    source,
  };
}

function isSearchApiErrorResponse(value: unknown): value is SearchApiErrorResponse {
  return (
    typeof value === "object" &&
    value !== null &&
    "error" in value &&
    typeof (value as SearchApiErrorResponse).error === "object"
  );
}

async function fetchWithTimeout(
  input: RequestInfo | URL,
  init: RequestInit,
  timeoutMs = getSearchApiTimeoutMs()
) {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(input, {
      ...init,
      signal: controller.signal,
    });
  } finally {
    window.clearTimeout(timer);
  }
}

function getSearchApiTimeoutMs() {
  const configured = Number(import.meta.env.VITE_SEARCH_API_TIMEOUT_MS);
  if (Number.isFinite(configured) && configured > 0) {
    return configured;
  }
  return DEFAULT_SEARCH_API_TIMEOUT_MS;
}

function toClientRequestError(
  error: unknown,
  timeoutMessage: string,
  networkMessage: string
) {
  if (isAbortError(error)) {
    return new SearchApiError({
      code: CLIENT_TIMEOUT_CODE,
      message: timeoutMessage,
    });
  }
  return new SearchApiError({
    code: CLIENT_NETWORK_ERROR_CODE,
    message: networkMessage,
  });
}

function isAbortError(error: unknown) {
  return (
    error instanceof DOMException && error.name === "AbortError"
  ) || (
    typeof error === "object" &&
    error !== null &&
    "name" in error &&
    (error as { name?: unknown }).name === "AbortError"
  );
}

async function safeJson(response: Response) {
  try {
    return await response.json();
  } catch {
    return null;
  }
}

function cloneSearchResponse(response: SearchResponse): SearchResponse {
  return JSON.parse(JSON.stringify(response)) as SearchResponse;
}

function cloneCaseDetailResponse(response: CaseDetailResponse): CaseDetailResponse {
  return JSON.parse(JSON.stringify(response)) as CaseDetailResponse;
}

function delay(ms: number) {
  return new Promise((resolve) => {
    window.setTimeout(resolve, ms);
  });
}
