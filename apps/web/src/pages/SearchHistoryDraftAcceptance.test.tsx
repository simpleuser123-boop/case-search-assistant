import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SearchPage } from "./SearchPage";
import { MOCK_SEARCH_RESPONSE } from "../mocks/searchMockFixture";
import {
  loadDraft,
  loadHistory,
  SEARCH_DRAFT_STORAGE_KEY,
  SEARCH_HISTORY_STORAGE_KEY,
} from "../lib/searchHistory";

// M4-2 acceptance (jsdom). Drives the real SearchPage tree on the built-in mock
// path (host<->VM browser bridge is unreachable). Covers: draft autosave +
// restore across remount, history display after search, re-search from history,
// clear history / clear draft, and the privacy boundary (no raw case body in any
// /api/events analytics request, no console errors). The feature flag is ON here;
// the default-off behavior is asserted by SearchPage.test.tsx / CitationCopy test.

function renderPage(state: Record<string, unknown> = {}) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[{ pathname: "/search", state }]}>
        <Routes>
          <Route path="/search" element={<SearchPage />} />
          <Route path="/" element={<div>home</div>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  );
}

const validQuery =
  "消费者购买电热水壶后出现漏电受伤，商家称系使用不当，双方争议产品缺陷与赔偿责任。";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
let errorSpy: any;
let fetchMock: ReturnType<typeof vi.fn>;

function analyticsBodies(): string[] {
  return fetchMock.mock.calls
    .filter(([input]) => String(input) === "/api/events")
    .map(([, init]) => String((init as RequestInit).body));
}

beforeEach(() => {
  vi.restoreAllMocks();
  window.localStorage.clear();
  vi.stubEnv("VITE_ENABLE_SEARCH_HISTORY", "true");
  fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL) => {
    const url = String(input);
    if (url === "/api/search") {
      return Promise.resolve({
        ok: true,
        status: 200,
        json: async () => MOCK_SEARCH_RESPONSE,
      });
    }
    return Promise.resolve({
      ok: true,
      status: 202,
      json: async () => ({ accepted: true }),
    });
  });
  vi.stubGlobal("fetch", fetchMock);
  errorSpy = vi.spyOn(console, "error").mockImplementation(() => {});
});

afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

async function loadMockResults() {
  fireEvent.change(screen.getByLabelText("案情描述"), {
    target: { value: validQuery },
  });
  fireEvent.click(screen.getByRole("button", { name: "测试数据" }));
  await screen.findByText("【测试数据】产品缺陷责任纠纷样例（非真实案例）");
}

describe("M4-2 search history & draft acceptance (mock path)", () => {
  it("autosaves an unsubmitted draft to local storage", async () => {
    renderPage();
    fireEvent.change(screen.getByLabelText("案情描述"), {
      target: { value: validQuery },
    });

    await waitFor(() => {
      const draft = loadDraft(window.localStorage);
      expect(draft?.draft_text).toBe(validQuery);
    });
    // The draft hint is visible in the side panel.
    expect(await screen.findByText("已保存当前输入草稿")).toBeInTheDocument();
  });

  it("restores the draft after a remount (simulated refresh)", async () => {
    const first = renderPage();
    fireEvent.change(screen.getByLabelText("案情描述"), {
      target: { value: validQuery },
    });
    await waitFor(() =>
      expect(loadDraft(window.localStorage)?.draft_text).toBe(validQuery)
    );
    first.unmount();

    renderPage();
    // Textarea is rehydrated from local draft, and a restore hint is shown.
    expect(screen.getByLabelText("案情描述")).toHaveValue(validQuery);
    expect(await screen.findByText("已恢复上次未提交的草稿")).toBeInTheDocument();
  });

  it("records a history entry after a search and clears the draft", async () => {
    renderPage();
    await loadMockResults();

    // History panel lists the search; draft is cleared after a successful search.
    const panel = await screen.findByLabelText("检索历史与草稿");
    await within(panel).findByText("重搜");
    await waitFor(() => expect(loadHistory(window.localStorage).length).toBe(1));
    expect(loadDraft(window.localStorage)).toBeNull();
  });

  it("re-searches from a history entry through the normal search flow", async () => {
    const first = renderPage();
    await loadMockResults();
    await waitFor(() => expect(loadHistory(window.localStorage).length).toBe(1));
    first.unmount();

    // Fresh mount: history is restored from local storage; re-search refills and runs.
    renderPage();
    const panel = await screen.findByLabelText("检索历史与草稿");
    fireEvent.click(within(panel).getByRole("button", { name: "重搜" }));

    // The query is refilled into the textarea (re-search goes through runSearch).
    expect(screen.getByLabelText("案情描述")).toHaveValue(validQuery);
    await screen.findByText("【测试数据】产品缺陷责任纠纷样例（非真实案例）");
  });

  it("clears history and clears draft via the panel controls", async () => {
    renderPage();
    // Create a draft first.
    fireEvent.change(screen.getByLabelText("案情描述"), {
      target: { value: validQuery },
    });
    await screen.findByText("已保存当前输入草稿");
    fireEvent.click(screen.getByRole("button", { name: "清除草稿" }));
    await waitFor(() => expect(loadDraft(window.localStorage)).toBeNull());
    expect(screen.getByLabelText("案情描述")).toHaveValue("");

    // Now create history and clear it.
    await loadMockResults();
    await waitFor(() => expect(loadHistory(window.localStorage).length).toBe(1));
    fireEvent.click(screen.getByRole("button", { name: "清除历史" }));
    fireEvent.click(await screen.findByRole("button", { name: "清空全部" }));
    await waitFor(() => expect(loadHistory(window.localStorage).length).toBe(0));
  });

  it("never sends the raw case body to the analytics endpoint", async () => {
    renderPage();
    await loadMockResults();

    await waitFor(() => expect(analyticsBodies().length).toBeGreaterThan(0));
    analyticsBodies().forEach((body) => {
      expect(body).not.toContain(validQuery);
    });
    // And the storage that DOES hold the body is local only — never fetched out.
    const localBlob =
      (window.localStorage.getItem(SEARCH_HISTORY_STORAGE_KEY) ?? "") +
      (window.localStorage.getItem(SEARCH_DRAFT_STORAGE_KEY) ?? "");
    // history holds the body locally (by design); the server bodies above do not.
    expect(localBlob.length).toBeGreaterThan(0);
  });

  it("records zero React console errors across the history flow", async () => {
    renderPage();
    await loadMockResults();
    const panel = await screen.findByLabelText("检索历史与草稿");
    fireEvent.click(within(panel).getByRole("button", { name: "重搜" }));
    await screen.findByText("【测试数据】产品缺陷责任纠纷样例（非真实案例）");
    expect(errorSpy).not.toHaveBeenCalled();
  });
});
