import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { listImportJobs, runBulkImport } from "./bulkImportApi";
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

describe("bulkImportApi client", () => {
  it("runBulkImport sends only whitelisted metadata/refs/anchors — never body/credentials", async () => {
    const fetchMock = vi.fn(async () =>
      jsonResponse({ ok: true, import_job_id: "imp_1", import_status: "completed", item_count: 1,
        imported_count: 1, rejected_count: 0, duplicate_count: 0, outcomes: [] }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const result = await runBulkImport({
      sourceType: "case_list_file",
      objectType: "case_list",
      items: [
        {
          caseId: "c_1",
          caseNumber: "(2021)京01民终123号",
          court: "北京一中院",
          listTitle: "我的类案清单",
          note: "我的备注",
          sourceAnchors: [{ case_id: "c_1", source_chunk_id: "chunk_7" }],
        },
      ],
    });
    expect(result.ok).toBe(true);
    const call = fetchMock.mock.calls[0] as unknown as FetchCall;
    expect(call[0]).toContain("/api/bulk-import/run");
    const headers = call[1].headers as Record<string, string>;
    expect(headers.Authorization).toBe("Bearer tok-xyz");
    const serialized = call[1].body as string;
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
  });

  it("runBulkImport whitelists item keys — extra arbitrary fields never reach the wire", async () => {
    const fetchMock = vi.fn(async () =>
      jsonResponse({ ok: true, import_job_id: "imp_1", import_status: "completed", outcomes: [] }),
    );
    vi.stubGlobal("fetch", fetchMock);
    await runBulkImport({
      sourceType: "csv",
      objectType: "case_favorite",
      items: [
        {
          caseId: "c_2",
          // @ts-expect-error 故意传入不存在的正文字段，验证不会进入请求体
          caseFactBody: "案情正文应当被丢弃",
        },
      ],
    });
    const call = fetchMock.mock.calls[0] as unknown as FetchCall;
    const serialized = call[1].body as string;
    expect(serialized).not.toContain("案情正文");
    expect(serialized).not.toContain("caseFactBody");
  });

  it("missing_source_anchor per-item rejection is surfaced in outcomes", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse({
          ok: true,
          import_job_id: "imp_1",
          import_status: "partial",
          item_count: 2,
          imported_count: 1,
          rejected_count: 1,
          duplicate_count: 0,
          degrade_reason: "partial_import",
          outcomes: [
            { case_id: "c_1", ok: true, reason_code: "ok", object_id: "o_1" },
            { case_id: "c_2", ok: false, reason_code: "missing_source_anchor", object_id: null },
          ],
        }),
      ),
    );
    const result = await runBulkImport({
      sourceType: "case_list_file",
      objectType: "case_list",
      items: [{ caseId: "c_1" }, { caseId: "c_2" }],
    });
    expect(result.ok).toBe(true);
    if (result.ok) {
      const c2 = result.data.outcomes.find((o) => o.case_id === "c_2");
      expect(c2?.reason_code).toBe("missing_source_anchor");
    }
  });

  it("403 BULK_IMPORT_DISABLED maps to disabled (back to M5-5/M4 end state)", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse({ error: { code: "BULK_IMPORT_DISABLED" } }, 403)),
    );
    const result = await listImportJobs();
    expect(result.ok).toBe(false);
    expect(result.ok === false && result.reason).toBe("disabled");
  });
});
