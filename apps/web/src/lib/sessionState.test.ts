import { afterEach, describe, expect, it } from "vitest";

import {
  clearSession,
  getAuthHeader,
  getSession,
  isLoggedIn,
  setSession,
  subscribe,
  type SessionState,
} from "./sessionState";

const sample: SessionState = {
  account: {
    user_id: "u_abc",
    display_name: "Alice",
    account_status: "active",
    auth_provider: "local",
  },
  sessionToken: "raw-token-must-stay-in-memory-only",
  expiresAt: null,
};

afterEach(() => {
  clearSession();
});

describe("sessionState (in-memory only)", () => {
  it("starts logged out", () => {
    expect(getSession()).toBeNull();
    expect(isLoggedIn()).toBe(false);
    expect(getAuthHeader()).toEqual({});
  });

  it("stores session in memory and exposes auth header", () => {
    setSession(sample);
    expect(isLoggedIn()).toBe(true);
    expect(getAuthHeader()).toEqual({
      Authorization: `Bearer ${sample.sessionToken}`,
    });
  });

  it("never writes the token to localStorage / sessionStorage", () => {
    // jsdom provides real storages; assert the module does not touch them.
    setSession(sample);
    const dumpLocal = JSON.stringify({ ...window.localStorage });
    const dumpSession = JSON.stringify({ ...window.sessionStorage });
    expect(dumpLocal).not.toContain(sample.sessionToken);
    expect(dumpSession).not.toContain(sample.sessionToken);
    expect(window.localStorage.length).toBe(0);
    expect(window.sessionStorage.length).toBe(0);
  });

  it("notifies subscribers on change and clears", () => {
    const seen: Array<SessionState | null> = [];
    const unsub = subscribe((s) => seen.push(s));
    setSession(sample);
    clearSession();
    unsub();
    expect(seen).toEqual([sample, null]);
    expect(isLoggedIn()).toBe(false);
  });
});
