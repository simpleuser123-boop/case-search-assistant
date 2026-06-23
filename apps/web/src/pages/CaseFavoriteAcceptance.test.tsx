import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SearchPage } from "./SearchPage";
import { MOCK_SEARCH_RESPONSE } from "../mocks/searchMockFixture";
import { CASE_FAVORITE_STORAGE_KEY, loadFavorites } from "../lib/caseFavorite";

// M4-3 case-favorite acceptance (jsdom). Drives the real SearchPage tree on the
// built-in mock path (host<->VM browser bridge is unreachable). Covers: favorite
// from a result card, the favorites list (metadata only), jump back to detail,
// unfavorite, clear-all, and the privacy boundary (no judgment/summary body text
// in the favorite storage blob, no raw case body to /api/events, zero console
// errors). The feature flag is ON here; default-off behavior is asserted by
// SearchPage.test.tsx / CitationCopyAcceptance / CaseCompareAcceptance.

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
  vi.stubEnv("VITE_ENABLE_CASE_FAVORITE", "true");
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

describe("M4-3 case favorite acceptance (mock path)", () => {
  it("favorites a case from a result card and persists metadata only", async () => {
    renderPage();
    await loadMockResults();

    const favButtons = await screen.findAllByRole("button", { name: /^收藏：/ });
    fireEvent.click(favButtons[0]);

    await waitFor(() => expect(loadFavorites(window.localStorage).length).toBe(1));
    const stored = loadFavorites(window.localStorage)[0];
    expect(stored.case_id).toBe("mock-case-001-non-real");
    expect(stored.case_number).toBe("TEST-2026-MOCK-001（非真实案号）");

    // Whitelist / no-body guard: the storage blob carries metadata + anchors +
    // user fields, never summary / matched_text / judgment body.
    const blob = window.localStorage.getItem(CASE_FAVORITE_STORAGE_KEY) ?? "";
    expect(blob).toContain("TEST-2026-MOCK-001");
    expect(blob).not.toContain("本院查明");
    expect(blob).not.toContain("裁判要旨");
    expect(blob).not.toContain("摘要");
  });

  it("shows the favorites list with metadata and toggles favorited state", async () => {
    renderPage();
    await loadMockResults();
    fireEvent.click((await screen.findAllByRole("button", { name: /^收藏：/ }))[0]);

    const panel = await screen.findByLabelText("案例收藏");
    expect(within(panel).getByText(/案例收藏（1）/)).toBeInTheDocument();
    expect(within(panel).getByText("TEST-2026-MOCK-001（非真实案号）")).toBeInTheDocument();

    // The same card button now reflects the favorited state ("取消收藏：...").
    expect(
      (await screen.findAllByRole("button", { name: /^取消收藏：/ })).length
    ).toBeGreaterThan(0);
  });

  it("jumps back to case detail from the favorites list", async () => {
    renderPage();
    await loadMockResults();
    fireEvent.click((await screen.findAllByRole("button", { name: /^收藏：/ }))[0]);

    const panel = await screen.findByLabelText("案例收藏");
    fireEvent.click(within(panel).getByRole("button", { name: "查看详情" }));

    // The case-detail drawer opens for the favorited case.
    expect(await screen.findByRole("dialog")).toBeInTheDocument();
  });

  it("unfavorites from the list and supports clear-all", async () => {
    renderPage();
    await loadMockResults();

    // Favorite two cases.
    const favButtons = await screen.findAllByRole("button", { name: /^收藏：/ });
    fireEvent.click(favButtons[0]);
    fireEvent.click(
      (await screen.findAllByRole("button", { name: /^收藏：/ }))[0]
    );
    await waitFor(() => expect(loadFavorites(window.localStorage).length).toBe(2));

    // Remove one via the list.
    const panel = await screen.findByLabelText("案例收藏");
    fireEvent.click(within(panel).getAllByRole("button", { name: /^取消收藏：/ })[0]);
    await waitFor(() => expect(loadFavorites(window.localStorage).length).toBe(1));

    // Clear all (with confirm).
    fireEvent.click(within(panel).getByRole("button", { name: "清空收藏" }));
    fireEvent.click(await within(panel).findByRole("button", { name: "清空全部" }));
    await waitFor(() => expect(loadFavorites(window.localStorage).length).toBe(0));
  });

  it("never sends the raw case body to the analytics endpoint while favoriting", async () => {
    renderPage();
    await loadMockResults();
    fireEvent.click((await screen.findAllByRole("button", { name: /^收藏：/ }))[0]);
    await waitFor(() => expect(loadFavorites(window.localStorage).length).toBe(1));

    await waitFor(() => expect(analyticsBodies().length).toBeGreaterThan(0));
    analyticsBodies().forEach((body) => {
      expect(body).not.toContain(validQuery);
      expect(body).not.toContain("TEST-2026-MOCK-001");
    });
  });

  it("records zero React console errors across the favorite flow", async () => {
    renderPage();
    await loadMockResults();
    fireEvent.click((await screen.findAllByRole("button", { name: /^收藏：/ }))[0]);
    await screen.findByLabelText("案例收藏");
    expect(errorSpy).not.toHaveBeenCalled();
  });
});
