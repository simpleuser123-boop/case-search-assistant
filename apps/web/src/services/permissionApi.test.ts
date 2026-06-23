import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { assignRole, grant, listAudit, readObject, revoke } from "./permissionApi";
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

describe("permissionApi client", () => {
  it("grant sends auth header and structured fields only (no body/credentials)", async () => {
    const fetchMock = vi.fn(async () => jsonResponse({ ok: true, reason_code: "ok" }));
    vi.stubGlobal("fetch", fetchMock);
    const result = await grant("o_1", "u_2", "viewer");
    expect(result.ok).toBe(true);
    const call = fetchMock.mock.calls[0] as unknown as FetchCall;
    const headers = call[1].headers as Record<string, string>;
    expect(headers.Authorization).toBe("Bearer tok-xyz");
    const serialized = call[1].body as string;
    for (const forbidden of ["raw_query", "password", "token", "case_fact_body", "chunk_body"]) {
      expect(serialized).not.toContain(forbidden);
    }
  });

  it("403 PERMISSION_TIERING_DISABLED maps to disabled", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse({ error: { code: "PERMISSION_TIERING_DISABLED" } }, 403)),
    );
    const result = await assignRole("t1", "u2", "editor");
    expect(result.ok).toBe(false);
    expect(result.ok === false && result.reason).toBe("disabled");
  });

  it("403 PERMISSION_DENIED maps to denied (over-privilege rejected)", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse({ error: { code: "PERMISSION_DENIED" } }, 403)),
    );
    const result = await readObject("o_other");
    expect(result.ok).toBe(false);
    expect(result.ok === false && result.reason).toBe("denied");
  });

  it("readObject returns desensitized object view, never body", async () => {
    const fetchMock = vi.fn(async () =>
      jsonResponse({
        ok: true,
        effective_level: "owner",
        object: { object_id: "o_1", object_type: "case_favorite", visibility: "private", owner_user_id_hash: "uidh_x", team_id_hash: "tidh_none" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const result = await readObject("o_1");
    expect(result.ok).toBe(true);
    expect(result.ok && result.data.effective_level).toBe("owner");
    expect(result.ok && result.data.object?.owner_user_id_hash).toBe("uidh_x");
  });

  it("revoke and audit reach correct endpoints with auth header", async () => {
    const fetchMock = vi.fn(async () => jsonResponse({ ok: true, items: [] }));
    vi.stubGlobal("fetch", fetchMock);
    await revoke("o_1", "u_2");
    await listAudit();
    const revokeCall = fetchMock.mock.calls[0] as unknown as FetchCall;
    const auditCall = fetchMock.mock.calls[1] as unknown as FetchCall;
    expect(revokeCall[0]).toContain("/api/permission/revoke");
    expect(auditCall[0]).toContain("/api/permission/audit");
    expect((auditCall[1].headers as Record<string, string>).Authorization).toBe("Bearer tok-xyz");
  });
});
