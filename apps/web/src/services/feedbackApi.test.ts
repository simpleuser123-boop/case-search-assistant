import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  buildFeedbackEventRequest,
  submitFeedbackEvent,
  type FeedbackEventRequest,
} from "./feedbackApi";

const rawQuery = "不得发送的原始案情文本XYZ，包含裁判文书正文片段。";
const rawSessionId = "qs_feedback_raw_session";
const rawCaseId = "case-raw-001";

beforeEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("feedback API client", () => {
  it("builds a feedback event request with hashes and no raw text", async () => {
    const result = await buildFeedbackEventRequest({
      querySessionId: rawSessionId,
      queryText: rawQuery,
      caseId: rawCaseId,
      rank: 3,
      feedbackValue: "not_relevant",
      searchMode: "standard",
      confidenceLevel: "medium",
    });

    expect(result.request).toMatchObject({
      event_type: "result_feedback",
      rank: 3,
      feedback_value: "not_relevant",
      search_mode: "standard",
      confidence_level: "medium",
    });
    assertFeedbackRequestOnlyContainsSanitizedFields(result.request);
    const serialized = JSON.stringify(result.request);
    expect(serialized).not.toContain(rawQuery);
    expect(serialized).not.toContain(rawSessionId);
    expect(serialized).not.toContain(rawCaseId);
  });

  it("does not send invalid feedback payloads", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const result = await submitFeedbackEvent({
      querySessionId: "",
      queryText: rawQuery,
      caseId: rawCaseId,
      rank: 1,
      feedbackValue: "relevant",
      searchMode: "standard",
      confidenceLevel: "high",
    });

    expect(result).toEqual({ sent: false, reason: "invalid_payload" });
    expect(fetchMock).not.toHaveBeenCalled();
    expect(JSON.stringify(result)).not.toContain(rawQuery);
  });

  it("posts only sanitized feedback fields", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 202,
      json: async () => ({ accepted: true, stored: true }),
    });
    vi.stubGlobal("fetch", fetchMock);

    const result = await submitFeedbackEvent({
      querySessionId: rawSessionId,
      queryText: rawQuery,
      caseId: rawCaseId,
      rank: 1,
      feedbackValue: "cleared",
      searchMode: "expanded",
      confidenceLevel: "low",
    });

    expect(result.sent).toBe(true);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/feedback",
      expect.objectContaining({ method: "POST" })
    );
    const body = JSON.parse(String((fetchMock.mock.calls[0][1] as RequestInit).body));
    assertFeedbackRequestOnlyContainsSanitizedFields(body);
    expect(body).toMatchObject({
      event_type: "result_feedback",
      rank: 1,
      feedback_value: "cleared",
      search_mode: "expanded",
      confidence_level: "low",
    });
    expect(JSON.stringify(body)).not.toContain(rawQuery);
    expect(JSON.stringify(body)).not.toContain(rawCaseId);
    expect(JSON.stringify(body)).not.toContain(rawSessionId);
    ["query", "raw_query", "case_text", "candidate_body", "chunk_body", "text", "reason"].forEach(
      (field) => expect(body).not.toHaveProperty(field)
    );
  });
});

function assertFeedbackRequestOnlyContainsSanitizedFields(
  request: FeedbackEventRequest | undefined
) {
  expect(request).toBeTruthy();
  expect(Object.keys(request || {})).toEqual([
    "event_type",
    "session_hash",
    "query_hash",
    "case_id_hash",
    "rank",
    "feedback_value",
    "search_mode",
    "confidence_level",
  ]);
  expect(request?.session_hash).toMatch(/^(sha256_[a-f0-9]{24}|fnv1a_[a-f0-9]{8})$/);
  expect(request?.query_hash).toMatch(/^(sha256_[a-f0-9]{24}|fnv1a_[a-f0-9]{8})$/);
  expect(request?.case_id_hash).toMatch(/^(sha256_[a-f0-9]{24}|fnv1a_[a-f0-9]{8})$/);
}
