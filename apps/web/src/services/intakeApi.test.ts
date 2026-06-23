import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  INTAKE_SEARCH_API_PATH,
  submitIntakeSearch,
  toIntakeRequestBody,
} from "./intakeApi";
import type { SearchProfileDraft } from "../intake/sanitize";

// 白名单键（请求体只允许这 7 个）。
const ALLOWED_KEYS = [
  "case_cause",
  "region",
  "trial_level_preference",
  "dispute_focus_keywords",
  "query_text",
  "mode",
  "limit",
].sort();

const PROFILE: SearchProfileDraft = {
  case_cause: "买卖合同纠纷",
  region: "北京",
  trial_level_preference: "二审",
  dispute_focus_keywords: ["货款", "违约责任"],
  query_text: "原告主张被告拖欠货款，就付款义务存在争议",
};

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("toIntakeRequestBody", () => {
  it("emits only the whitelisted SearchProfile keys", () => {
    const body = toIntakeRequestBody(PROFILE);
    expect(Object.keys(body).sort()).toEqual(ALLOWED_KEYS);
  });

  it("never carries raw case / PII keys even if injected onto the profile object", () => {
    // 模拟「对象上意外挂了额外键」：白名单组装必须把它们全部丢弃。
    const polluted = {
      ...PROFILE,
      raw_case: "原告张三与被告李四，电话13800138000",
      raw_query: "当事人住址北京市朝阳区xx路1号",
      name: "张三",
      id_card: "11010119900101001X",
    } as unknown as SearchProfileDraft;
    const body = toIntakeRequestBody(polluted);
    expect(Object.keys(body).sort()).toEqual(ALLOWED_KEYS);
    const serialized = JSON.stringify(body);
    expect(serialized).not.toContain("raw_case");
    expect(serialized).not.toContain("raw_query");
    expect(serialized).not.toContain("张三");
    expect(serialized).not.toContain("13800138000");
    expect(serialized).not.toContain("11010119900101001X");
  });
});

describe("submitIntakeSearch", () => {
  beforeEach(() => {
    vi.stubGlobal("AbortController", class {
      signal = {} as AbortSignal;
      abort() {}
    });
  });

  it("POSTs to the intake endpoint with a whitelist-only JSON body", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        query_session_id: "qs_1",
        candidate_refs: [],
        candidate_count: 0,
        degraded: false,
        degraded_reasons: [],
        search_mode: "standard",
      }),
    });
    vi.stubGlobal("fetch", fetchMock);

    const result = await submitIntakeSearch(PROFILE, { mode: "standard", limit: 10 });
    expect(result.ok).toBe(true);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe(INTAKE_SEARCH_API_PATH);
    expect((init as RequestInit).method).toBe("POST");

    const sentBody = JSON.parse(String((init as RequestInit).body));
    expect(Object.keys(sentBody).sort()).toEqual(ALLOWED_KEYS);
    expect(sentBody.case_cause).toBe("买卖合同纠纷");
    expect(sentBody.query_text).toBe(
      "原告主张被告拖欠货款，就付款义务存在争议"
    );
  });

  it("maps 403 to disabled with reason code", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: false,
      status: 403,
      json: async () => ({ error: { code: "INTAKE_DISABLED" } }),
    });
    vi.stubGlobal("fetch", fetchMock);

    const result = await submitIntakeSearch(PROFILE);
    expect(result).toMatchObject({ ok: false, reason: "disabled", reasonCode: "INTAKE_DISABLED" });
  });

  it("maps network failure to network_error", async () => {
    const fetchMock = vi.fn().mockRejectedValue(new TypeError("network down"));
    vi.stubGlobal("fetch", fetchMock);

    const result = await submitIntakeSearch(PROFILE);
    expect(result).toMatchObject({ ok: false, reason: "network_error" });
  });
});
