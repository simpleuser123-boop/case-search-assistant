import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { shareToTeam, syncSediment, unshare } from "./sharingApi";
import { clearSession, setSession } from "../lib/sessionState";

function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as Response;
}

type FetchCall = [string, RequestInit];

beforeEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  clearSession();
  setSession({
    account: { user_id: "u_1", display_name: "A", account_status: "active", auth_provider: "local" },
    sessionToken: "tok-xyz",
    expiresAt: null,
  });
});

afterEach(() => {
  clearSession();
});

describe("sharingApi client", () => {
  it("syncSediment sends only metadata/refs/anchors — never body/credentials", async () => {
    const fetchMock = vi.fn(async () => jsonResponse({ ok: true, object_id: "o_1", visibility: "private" }));
    vi.stubGlobal("fetch", fetchMock);
    const result = await syncSediment({
      objectType: "report_template",
      caseId: "c_1",
      caseNumber: "(2021)京01民终123号",
      note: "我的备注",
      sourceAnchors: [{ case_id: "c_1", source_chunk_id: "chunk_7" }],
    });
    expect(result.ok).toBe(true);
    const call = fetchMock.mock.calls[0] as unknown as FetchCall;
    expect(call[0]).toContain("/api/sharing/sync");
    const headers = call[1].headers as Record<string, string>;
    expect(headers.Authorization).toBe("Bearer tok-xyz");
    const serialized = call[1].body as string;
    // 绝不出现任何正文 / 凭据键。
    for (const forbidden of [
      "raw_query",
      "query",
      "case_fact_body",
      "candidate_body",
      "chunk_body",
      "judgment_long_text",
      "summary_body",
      "password",
      "token",
      "session_token",
      "content",
    ]) {
      expect(serialized).not.toContain(forbidden);
    }
    // 同步请求体里不含 team_id / visibility（共享是另一个显式动作）。
    expect(serialized).not.toContain("team_id");
    expect(serialized).not.toContain("visibility");
  });

  it("syncSediment whitelists keys — extra arbitrary fields never reach the wire", async () => {
    const fetchMock = vi.fn(async () => jsonResponse({ ok: true, object_id: "o_1", visibility: "private" }));
    vi.stubGlobal("fetch", fetchMock);
    // 即便调用方误传额外字段，toSyncBody 只白名单已知键。
    await syncSediment({
      objectType: "case_favorite",
      caseId: "c_2",
      // @ts-expect-error 故意传入不存在的字段，验证不会进入请求体
      caseFactBody: "案情正文应当被丢弃",
    });
    const call = fetchMock.mock.calls[0] as unknown as FetchCall;
    const serialized = call[1].body as string;
    expect(serialized).not.toContain("案情正文");
    expect(serialized).not.toContain("caseFactBody");
  });

  it("share sends explicit object_id + team_id and reaches /share", async () => {
    const fetchMock = vi.fn(async () =>
      jsonResponse({ ok: true, share_id: "s_1", visibility: "team", anchor_count: 2 }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const result = await shareToTeam("o_1", "t_1");
    expect(result.ok).toBe(true);
    expect(result.ok && result.data.visibility).toBe("team");
    const call = fetchMock.mock.calls[0] as unknown as FetchCall;
    expect(call[0]).toContain("/api/sharing/share");
  });

  it("missing_source_anchor rejection is surfaced as reasonCode", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse({ ok: false, reason_code: "missing_source_anchor" })),
    );
    const result = await shareToTeam("o_1", "t_1");
    expect(result.ok).toBe(false);
    expect(result.ok === false && result.reasonCode).toBe("missing_source_anchor");
  });

  it("403 TEAM_SHARING_DISABLED maps to disabled (back to M4 end state)", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse({ error: { code: "TEAM_SHARING_DISABLED" } }, 403)),
    );
    const result = await unshare("o_1");
    expect(result.ok).toBe(false);
    expect(result.ok === false && result.reason).toBe("disabled");
  });
});
