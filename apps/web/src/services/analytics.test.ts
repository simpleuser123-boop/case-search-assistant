import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  buildAnalyticsRequest,
  trackAnalyticsEvent,
  trackPageExit,
  trackResultCardClick,
} from "./analytics";

const rawQuery = "不得进入事件的原始案情文本XYZ，包含裁判文书长文本片段。";

beforeEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("analytics client", () => {
  it("builds only allowlisted metadata for safe search_submit", () => {
    const result = buildAnalyticsRequest({
      event_name: "search_submit",
      metadata: {
        input_length: 42,
        trigger: "button",
        has_draft_restored: true,
        ignored_field: "drop me",
      },
    });

    expect(result.request).toMatchObject({
      event_name: "search_submit",
      metadata: {
        input_length: 42,
        trigger: "button",
        has_draft_restored: true,
      },
    });
    expect(result.request?.metadata).not.toHaveProperty("ignored_field");
    expect(result.request?.query_session_id).toBeUndefined();
  });

  it("rejects sensitive metadata without sending or dispatching values", async () => {
    const fetchMock = vi.fn();
    const listener = vi.fn();
    const consoleLogSpy = vi.spyOn(console, "log").mockImplementation(() => undefined);
    vi.stubGlobal("fetch", fetchMock);
    window.addEventListener("case-search:analytics", listener);

    const result = await trackAnalyticsEvent({
      event_name: "search_result_render",
      query_session_id: "qs_privacy_001",
      metadata: {
        result_count: 1,
        query: rawQuery,
        nested: {
          raw_text: rawQuery,
        },
      },
    });

    expect(result).toEqual({ sent: false, reason: "sensitive_metadata" });
    expect(fetchMock).not.toHaveBeenCalled();
    expect(listener).not.toHaveBeenCalled();
    expect(consoleLogSpy).not.toHaveBeenCalled();
    expect(JSON.stringify(result)).not.toContain(rawQuery);

    window.removeEventListener("case-search:analytics", listener);
  });

  it("rejects query-like metadata keys including Chinese raw case text labels", () => {
    ["query", "raw_text", "content", "text", "案情全文"].forEach((key) => {
      const result = buildAnalyticsRequest({
        event_name: "search_result_render",
        query_session_id: "qs_sensitive_key",
        metadata: {
          result_count: 1,
          [key]: rawQuery,
        },
      });

      expect(result).toEqual({ reason: "sensitive_metadata" });
      expect(JSON.stringify(result)).not.toContain(rawQuery);
    });
  });

  it("hashes result card case ids and does not send raw case ids", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 202,
      json: async () => ({ accepted: true }),
    });
    const rawCaseId = "case-001-sensitive-id";
    vi.stubGlobal("fetch", fetchMock);

    await trackResultCardClick({
      query_session_id: "qs_click_001",
      case_id: rawCaseId,
      rank: 2,
      similarity_score: 0.81,
    });

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/events",
      expect.objectContaining({ method: "POST" })
    );
    const body = JSON.parse(String((fetchMock.mock.calls[0][1] as RequestInit).body));
    expect(body).toMatchObject({
      event_name: "result_card_click",
      query_session_id: "qs_click_001",
      metadata: {
        rank: 2,
        similarity_score: 0.81,
      },
    });
    expect(body.metadata.case_id_hash).toMatch(/^(sha256_[a-f0-9]{24}|fnv1a_[a-f0-9]{8})$/);
    expect(JSON.stringify(body)).not.toContain(rawCaseId);
  });

  it("uses sendBeacon for page_exit and falls back to keepalive fetch", async () => {
    const sendBeacon = vi.fn().mockReturnValue(false);
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 202,
      json: async () => ({ accepted: true }),
    });
    vi.stubGlobal("fetch", fetchMock);
    vi.stubGlobal("navigator", {
      ...navigator,
      sendBeacon,
    });

    await trackPageExit({
      query_session_id: "qs_exit_001",
      last_visible_result_count: 3,
      dwell_time_ms: 1200,
    });

    expect(sendBeacon).toHaveBeenCalledWith("/api/events", expect.any(Blob));
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/events",
      expect.objectContaining({
        method: "POST",
        keepalive: true,
      })
    );
  });
});
