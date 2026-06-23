import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { addMember, createTeam, listSediment, listTeams } from "./teamApi";
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

describe("teamApi client", () => {
  it("listTeams sends auth header and returns teams", async () => {
    const fetchMock = vi.fn(async () =>
      jsonResponse({ ok: true, teams: [{ team_id: "t1", team_name: "TA", team_id_hash: "tidh_x", status: "active" }] })
    );
    vi.stubGlobal("fetch", fetchMock);
    const result = await listTeams();
    expect(result.ok).toBe(true);
    expect(result.ok && result.data.teams[0].team_id).toBe("t1");
    const call = fetchMock.mock.calls[0] as unknown as FetchCall;
    const headers = call[1].headers as Record<string, string>;
    expect(headers.Authorization).toBe("Bearer tok-xyz");
  });

  it("403 maps to disabled (team workspace off)", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => jsonResponse({ error: { code: "TEAM_WORKSPACE_DISABLED" } }, 403)));
    const result = await createTeam("TA");
    expect(result.ok).toBe(false);
    expect(result.ok === false && result.reason).toBe("disabled");
  });

  it("listSediment sends team_id context and never sends body keys", async () => {
    const fetchMock = vi.fn(async () => jsonResponse({ ok: true, items: [], tenant_downgraded: false }));
    vi.stubGlobal("fetch", fetchMock);
    const result = await listSediment({ teamId: "t1", objectType: "case_favorite" });
    expect(result.ok).toBe(true);
    const call = fetchMock.mock.calls[0] as unknown as FetchCall;
    const body = JSON.parse(call[1].body as string);
    expect(body.team_id).toBe("t1");
    const serialized = JSON.stringify(body);
    for (const forbidden of ["raw_query", "password", "token", "case_fact_body", "chunk_body"]) {
      expect(serialized).not.toContain(forbidden);
    }
  });

  it("addMember posts structured relation only", async () => {
    const fetchMock = vi.fn(async () => jsonResponse({ ok: true, member_count: 2 }));
    vi.stubGlobal("fetch", fetchMock);
    const result = await addMember("t1", "u_2");
    expect(result.ok).toBe(true);
    const call = fetchMock.mock.calls[0] as unknown as FetchCall;
    const body = JSON.parse(call[1].body as string);
    expect(body.team_id).toBe("t1");
    expect(body.member_user_id).toBe("u_2");
  });
});
