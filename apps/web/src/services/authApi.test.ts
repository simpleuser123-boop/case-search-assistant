import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { claimAnonymousSediment, login, logout, register } from "./authApi";
import { clearSession, getSession, isLoggedIn } from "../lib/sessionState";

const PLAINTEXT = "sup3rsecret-pw";

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
});

afterEach(() => {
  clearSession();
});

describe("authApi client", () => {
  it("login stores token in memory and never persists password/token", async () => {
    const fetchMock = vi.fn(async () =>
      jsonResponse({
        ok: true,
        account: { user_id: "u_1", display_name: "Alice", account_status: "active", auth_provider: "local" },
        session_token: "mem-only-token",
        expires_at: null,
      })
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await login({ loginName: "alice@x.io", password: PLAINTEXT });
    expect(result.ok).toBe(true);
    expect(isLoggedIn()).toBe(true);
    expect(getSession()?.sessionToken).toBe("mem-only-token");

    // request body carried password once; nothing persisted to storage
    const call = fetchMock.mock.calls[0] as unknown as FetchCall;
    const body = JSON.parse(call[1].body as string);
    expect(body.password).toBe(PLAINTEXT);
    expect(window.localStorage.length).toBe(0);
    expect(JSON.stringify({ ...window.localStorage })).not.toContain("mem-only-token");
  });

  it("login failure (401) does not create a session", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => jsonResponse({ error: { code: "LOGIN_REJECTED" } }, 401)));
    const result = await login({ loginName: "x", password: "y" });
    expect(result.ok).toBe(false);
    expect(result.ok === false && result.reason).toBe("rejected");
    expect(isLoggedIn()).toBe(false);
  });

  it("403 from backend maps to disabled (account system off)", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => jsonResponse({ error: { code: "ACCOUNT_SYSTEM_DISABLED" } }, 403)));
    const result = await register({ loginName: "a", password: "b" });
    expect(result.ok).toBe(false);
    expect(result.ok === false && result.reason).toBe("disabled");
  });

  it("logout clears the in-memory session and sends auth header", async () => {
    const fetchMock = vi.fn(async () =>
      jsonResponse({
        ok: true,
        account: { user_id: "u_1", display_name: "Alice", account_status: "active", auth_provider: "local" },
        session_token: "tok-xyz",
        expires_at: null,
      })
    );
    vi.stubGlobal("fetch", fetchMock);
    await login({ loginName: "alice@x.io", password: PLAINTEXT });

    const logoutMock = vi.fn(async () => jsonResponse({ ok: true }));
    vi.stubGlobal("fetch", logoutMock);
    const result = await logout();
    expect(result.ok).toBe(true);
    expect(isLoggedIn()).toBe(false);
    const call = logoutMock.mock.calls[0] as unknown as FetchCall;
    const headers = call[1].headers as Record<string, string>;
    expect(headers.Authorization).toBe("Bearer tok-xyz");
  });

  it("claim sends confirm flag and metadata-only items", async () => {
    const fetchMock = vi.fn(async () =>
      jsonResponse({ ok: true, claimed_count: 1, degraded_count: 1, rejected_count: 0 })
    );
    vi.stubGlobal("fetch", fetchMock);
    const result = await claimAnonymousSediment(
      [{ object_type: "case_favorite", case_id: "c1", source_anchors: [{ case_id: "c1", source_chunk_id: "c1-0" }] }],
      true
    );
    expect(result.ok).toBe(true);
    const call = fetchMock.mock.calls[0] as unknown as FetchCall;
    const body = JSON.parse(call[1].body as string);
    expect(body.confirm).toBe(true);
    expect(Array.isArray(body.items)).toBe(true);
    // no body/credential keys in the claim payload
    const serialized = JSON.stringify(body);
    for (const forbidden of ["raw_query", "password", "token", "case_fact_body"]) {
      expect(serialized).not.toContain(forbidden);
    }
  });
});
