import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  STATUTE_SEARCH_API_PATH,
  STATUTE_BY_CASE_API_PATH,
  STATUTE_CASES_BY_STATUTE_API_PATH,
  submitStatuteSearch,
  fetchStatutesByCase,
  fetchCasesByStatute,
  toStatuteSearchBody,
  toStatuteByCaseBody,
  toStatuteCasesBody,
} from "./statuteApi";
import type { SearchProfileDraft } from "../intake/sanitize";

// /search 请求体白名单（只允许这 7 个键）。
const SEARCH_ALLOWED_KEYS = [
  "case_cause",
  "region",
  "trial_level_preference",
  "dispute_focus_keywords",
  "query_text",
  "mode",
  "limit",
].sort();

// 互跳请求体白名单（by-case / cases-by-statute 各只允许这 3 个键）。
const BY_CASE_ALLOWED_KEYS = ["case_id", "mode", "limit"].sort();
const CASES_ALLOWED_KEYS = ["statute_id", "mode", "limit"].sort();

const PROFILE: SearchProfileDraft = {
  case_cause: "买卖合同纠纷",
  region: "北京",
  trial_level_preference: "二审",
  dispute_focus_keywords: ["货款", "违约责任"],
  query_text: "标的物质量不符合约定的违约责任如何认定",
};

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("toStatuteSearchBody", () => {
  it("emits only the whitelisted SearchProfile keys", () => {
    const body = toStatuteSearchBody(PROFILE);
    expect(Object.keys(body).sort()).toEqual(SEARCH_ALLOWED_KEYS);
  });

  it("never carries raw case / PII keys even if injected onto the profile object", () => {
    const polluted = {
      ...PROFILE,
      raw_case: "原告张三与被告李四，电话13800138000",
      raw_query: "当事人住址北京市朝阳区xx路1号",
      name: "张三",
      id_card: "11010119900101001X",
    } as unknown as SearchProfileDraft;
    const body = toStatuteSearchBody(polluted);
    expect(Object.keys(body).sort()).toEqual(SEARCH_ALLOWED_KEYS);
    const serialized = JSON.stringify(body);
    expect(serialized).not.toContain("raw_case");
    expect(serialized).not.toContain("raw_query");
    expect(serialized).not.toContain("张三");
    expect(serialized).not.toContain("13800138000");
    expect(serialized).not.toContain("11010119900101001X");
  });
});

describe("toStatuteByCaseBody / toStatuteCasesBody", () => {
  it("by-case body holds only case_id + mode + limit", () => {
    const body = toStatuteByCaseBody("case_001", { mode: "standard", limit: 5 });
    expect(Object.keys(body).sort()).toEqual(BY_CASE_ALLOWED_KEYS);
    expect(body.case_id).toBe("case_001");
  });

  it("cases-by-statute body holds only statute_id + mode + limit", () => {
    const body = toStatuteCasesBody("statute_刑法_133", { limit: 8 });
    expect(Object.keys(body).sort()).toEqual(CASES_ALLOWED_KEYS);
    expect(body.statute_id).toBe("statute_刑法_133");
  });
});

describe("submitStatuteSearch", () => {
  beforeEach(() => {
    vi.stubGlobal("AbortController", class {
      signal = {} as AbortSignal;
      abort() {}
    });
  });

  it("POSTs to the statute search endpoint with a whitelist-only JSON body", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        query_session_id: "qs_1",
        statute_refs: [],
        statute_count: 0,
        degraded: false,
        degraded_reasons: [],
        search_mode: "standard",
      }),
    });
    vi.stubGlobal("fetch", fetchMock);

    const result = await submitStatuteSearch(PROFILE, { mode: "standard", limit: 10 });
    expect(result.ok).toBe(true);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe(STATUTE_SEARCH_API_PATH);
    expect((init as RequestInit).method).toBe("POST");

    const sentBody = JSON.parse(String((init as RequestInit).body));
    expect(Object.keys(sentBody).sort()).toEqual(SEARCH_ALLOWED_KEYS);
    expect(sentBody.case_cause).toBe("买卖合同纠纷");
  });

  it("maps 403 to disabled with reason code", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: false,
      status: 403,
      json: async () => ({ error: { code: "STATUTE_SEARCH_DISABLED" } }),
    });
    vi.stubGlobal("fetch", fetchMock);

    const result = await submitStatuteSearch(PROFILE);
    expect(result).toMatchObject({
      ok: false,
      reason: "disabled",
      reasonCode: "STATUTE_SEARCH_DISABLED",
    });
  });
});

describe("fetchStatutesByCase / fetchCasesByStatute", () => {
  beforeEach(() => {
    vi.stubGlobal("AbortController", class {
      signal = {} as AbortSignal;
      abort() {}
    });
  });

  it("by-case POSTs only case_id whitelist body", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        query_session_id: "qs_2",
        statute_refs: [],
        statute_count: 0,
        degraded: false,
        degraded_reasons: [],
        search_mode: "standard",
      }),
    });
    vi.stubGlobal("fetch", fetchMock);

    await fetchStatutesByCase("case_001");
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe(STATUTE_BY_CASE_API_PATH);
    const sentBody = JSON.parse(String((init as RequestInit).body));
    expect(Object.keys(sentBody).sort()).toEqual(BY_CASE_ALLOWED_KEYS);
  });

  it("cases-by-statute POSTs only statute_id whitelist body and returns CandidateRef[]", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        query_session_id: "qs_3",
        candidate_refs: [
          {
            case_id: "case_001",
            case_number: "(2021)京01刑终123号",
            court: "北京一中院",
            trial_level: "二审",
            case_cause: "诈骗",
            judgment_date: "2021-06-01",
            source_anchors: [
              { case_id: "case_001", source_chunk_id: "chunk_7", anchor_type: "holding" },
            ],
          },
        ],
        candidate_count: 1,
        degraded: false,
        degraded_reasons: [],
        search_mode: "standard",
      }),
    });
    vi.stubGlobal("fetch", fetchMock);

    const result = await fetchCasesByStatute("statute_刑法_266");
    expect(result.ok).toBe(true);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe(STATUTE_CASES_BY_STATUTE_API_PATH);
    const sentBody = JSON.parse(String((init as RequestInit).body));
    expect(Object.keys(sentBody).sort()).toEqual(CASES_ALLOWED_KEYS);
    if (result.ok) {
      // CandidateRef 只含白名单七字段（无 summary / highlight / 正文键）。
      const ref = result.data.candidate_refs[0];
      expect(Object.keys(ref).sort()).toEqual(
        [
          "case_id",
          "case_number",
          "court",
          "trial_level",
          "case_cause",
          "judgment_date",
          "source_anchors",
        ].sort(),
      );
    }
  });
});
