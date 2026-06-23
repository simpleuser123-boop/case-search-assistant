import { afterEach, describe, expect, it, vi } from "vitest";

import {
  fetchMySubscription,
  fetchPlans,
  startTrial,
  submitRenewalIntent,
} from "./billingApi";

afterEach(() => {
  vi.unstubAllGlobals();
});

function mockJsonResponse(body: unknown) {
  const fetchMock = vi.fn().mockResolvedValue({
    ok: true,
    status: 200,
    json: async () => body,
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("billingApi response validation", () => {
  it("rejects malformed 200 plan responses as http errors", async () => {
    mockJsonResponse({ query_session_id: "search-response" });

    await expect(fetchPlans()).resolves.toEqual({
      ok: false,
      reason: "http_error",
      status: 200,
    });
  });

  it("rejects malformed 200 subscription responses as http errors", async () => {
    mockJsonResponse({ items: [] });

    await expect(fetchMySubscription("tok")).resolves.toEqual({
      ok: false,
      reason: "http_error",
      status: 200,
    });
    await expect(startTrial("plan_team_pro", null, "tok")).resolves.toEqual({
      ok: false,
      reason: "http_error",
      status: 200,
    });
    await expect(
      submitRenewalIntent("sub_abc", "will_renew", null, "tok")
    ).resolves.toEqual({
      ok: false,
      reason: "http_error",
      status: 200,
    });
  });
});
