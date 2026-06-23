import { afterEach, describe, expect, it, vi } from "vitest";

import { searchCases } from "./searchApi";

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("searchApi timeout handling", () => {
  it("turns a slow search request into a retryable timeout error without leaking the query", async () => {
    vi.useFakeTimers();
    const rawQuery = "不得出现在超时错误里的原始案情文本ABC123";
    let requestSignal: AbortSignal | undefined;
    const fetchMock = vi.fn().mockImplementation(
      (_input: RequestInfo | URL, init?: RequestInit) =>
        new Promise((_resolve, reject) => {
          requestSignal = init?.signal as AbortSignal | undefined;
          requestSignal?.addEventListener("abort", () => {
            reject(new DOMException("The request was aborted.", "AbortError"));
          });
        })
    );
    vi.stubGlobal("fetch", fetchMock);

    const request = searchCases(
      {
        query: rawQuery,
        mode: "standard",
        limit: 10,
      },
      { timeoutMs: 25 }
    );
    const timeoutExpectation = expect(request).rejects.toMatchObject({
      code: "CLIENT_TIMEOUT",
      message: expect.stringContaining("检索请求超时"),
    });

    await vi.advanceTimersByTimeAsync(30);

    await timeoutExpectation;
    expect(requestSignal?.aborted).toBe(true);
    expect(fetchMock).toHaveBeenCalledTimes(1);

    const error = await request.catch((caught: unknown) => caught);
    expect(String((error as Error).message)).not.toContain(rawQuery);
  });
});
