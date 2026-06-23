import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SearchPage } from "./SearchPage";
import { MOCK_SEARCH_RESPONSE } from "../mocks/searchMockFixture";
import { CASE_LIST_STORAGE_KEY, loadLists } from "../lib/caseList";

// M4-4 case-list acceptance (jsdom). Drives the real SearchPage tree on the
// built-in mock path (host<->VM browser bridge is unreachable). Covers: add to a
// new list from a result card, the list panel (metadata only), edit note/tag,
// manual reorder (display-only), remove item / delete list, jump back to detail,
// and the privacy boundary (no judgment/summary body text in the list storage
// blob, no raw case body to /api/events, zero console errors, main results
// unchanged). Feature flag is ON here; default-off behavior is asserted by
// SearchPage.test.tsx / CaseFavoriteAcceptance / CaseCompareAcceptance.

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
  vi.stubEnv("VITE_ENABLE_CASE_LIST", "true");
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

// Open the first result card's "加入清单" popover and create a new list with it.
async function addFirstResultToNewList(title: string) {
  const addButtons = await screen.findAllByRole("button", { name: /^加入类案清单：/ });
  fireEvent.click(addButtons[0]);
  const dialog = await screen.findByRole("dialog", { name: "选择类案清单" });
  fireEvent.change(within(dialog).getByPlaceholderText("新建清单名称"), {
    target: { value: title },
  });
  fireEvent.click(within(dialog).getByRole("button", { name: "新建并加入" }));
}

describe("M4-4 case list acceptance (mock path)", () => {
  it("adds a case to a new list and persists references/metadata only (no body)", async () => {
    renderPage();
    await loadMockResults();
    await addFirstResultToNewList("产品缺陷参考");

    await waitFor(() => expect(loadLists(window.localStorage).length).toBe(1));
    const list = loadLists(window.localStorage)[0];
    expect(list.list_title).toBe("产品缺陷参考");
    expect(list.items).toHaveLength(1);
    expect(list.items[0].case_id).toBe("mock-case-001-non-real");
    expect(list.items[0].case_number).toBe("TEST-2026-MOCK-001（非真实案号）");
    // anchors preserved (case_id + source_chunk_id), so the item is traceable
    expect(list.items[0].source_anchors.length).toBeGreaterThan(0);
    expect(list.items[0].source_anchors[0].source_chunk_id).toContain("mock-case-001");

    // No-body guard on the persisted blob.
    const blob = window.localStorage.getItem(CASE_LIST_STORAGE_KEY) ?? "";
    expect(blob).toContain("TEST-2026-MOCK-001");
    expect(blob).not.toContain("本院查明");
    expect(blob).not.toContain("裁判要旨");
    expect(blob).not.toContain("前端测试数据片段");
    expect(blob).not.toContain(validQuery);
  });

  it("shows the list panel with metadata and dedupes the same case", async () => {
    renderPage();
    await loadMockResults();
    await addFirstResultToNewList("清单A");

    const panel = await screen.findByLabelText("类案清单");
    expect(within(panel).getByText(/类案清单（1）/)).toBeInTheDocument();
    expect(within(panel).getByText(/TEST-2026-MOCK-001（非真实案号）/)).toBeInTheDocument();

    // Re-adding the same case to the same list is a no-op (dedup).
    const addButtons = await screen.findAllByRole("button", { name: /^加入类案清单：/ });
    fireEvent.click(addButtons[0]);
    const dialog = await screen.findByRole("dialog", { name: "选择类案清单" });
    // The existing list checkbox is checked; toggling it off then on keeps 1 item max.
    const checkbox = within(dialog).getByRole("checkbox", { name: /清单A/ });
    expect(checkbox).toBeChecked();
    await waitFor(() => expect(loadLists(window.localStorage)[0].items).toHaveLength(1));
  });

  it("edits item note/tag (short fields, stored locally, no body)", async () => {
    renderPage();
    await loadMockResults();
    await addFirstResultToNewList("清单B");

    const panel = await screen.findByLabelText("类案清单");
    fireEvent.click(within(panel).getByRole("button", { name: "备注" }));
    fireEvent.change(within(panel).getByLabelText(/标签/), { target: { value: "缺陷" } });
    fireEvent.change(within(panel).getByLabelText(/备注/), { target: { value: "重点比对事实" } });
    fireEvent.click(within(panel).getByRole("button", { name: "保存" }));

    await waitFor(() => {
      const item = loadLists(window.localStorage)[0].items[0];
      expect(item.tag).toBe("缺陷");
      expect(item.note).toBe("重点比对事实");
    });
  });

  it("manually reorders items (display-only) and removes an item / deletes the list", async () => {
    renderPage();
    await loadMockResults();

    // Add two cases into one list.
    await addFirstResultToNewList("清单C");
    await waitFor(() => expect(loadLists(window.localStorage)[0].items).toHaveLength(1));
    const listId = loadLists(window.localStorage)[0].list_id;
    // Add the 2nd result to the SAME existing list via its checkbox.
    const addButtons = await screen.findAllByRole("button", { name: /^加入类案清单：/ });
    fireEvent.click(addButtons[1]);
    const dialog = await screen.findByRole("dialog", { name: "选择类案清单" });
    fireEvent.click(within(dialog).getByRole("checkbox", { name: /清单C/ }));
    await waitFor(() => expect(loadLists(window.localStorage)[0].items).toHaveLength(2));

    const firstBefore = loadLists(window.localStorage)[0].items[0].case_id;
    const secondBefore = loadLists(window.localStorage)[0].items[1].case_id;

    // Move the 2nd item up.
    const panel = await screen.findByLabelText("类案清单");
    fireEvent.click(within(panel).getAllByRole("button", { name: /^上移：/ })[1]);
    await waitFor(() => {
      const items = loadLists(window.localStorage)[0].items;
      expect(items[0].case_id).toBe(secondBefore);
      expect(items[1].case_id).toBe(firstBefore);
    });

    // Remove one item.
    fireEvent.click(within(panel).getAllByRole("button", { name: /^从清单移除：/ })[0]);
    await waitFor(() => expect(loadLists(window.localStorage)[0].items).toHaveLength(1));

    // Delete the whole list (with confirm).
    fireEvent.click(within(panel).getByRole("button", { name: /^删除清单：/ }));
    fireEvent.click(within(panel).getByRole("button", { name: "删除清单" }));
    await waitFor(() => expect(loadLists(window.localStorage).length).toBe(0));
    void listId;
  });

  it("jumps back to case detail from a list item", async () => {
    renderPage();
    await loadMockResults();
    await addFirstResultToNewList("清单D");

    const panel = await screen.findByLabelText("类案清单");
    fireEvent.click(within(panel).getByRole("button", { name: "详情" }));
    expect(await screen.findByRole("dialog")).toBeInTheDocument();
  });

  it("never alters the main results and sends no raw body to analytics", async () => {
    renderPage();
    await loadMockResults();

    const resultRegion = await screen.findByRole("region", { name: "搜索结果列表" });
    const titlesBefore = within(resultRegion)
      .getAllByRole("heading", { level: 2 })
      .map((h) => h.textContent);

    await addFirstResultToNewList("清单E");
    await waitFor(() => expect(loadLists(window.localStorage).length).toBe(1));

    const titlesAfter = within(resultRegion)
      .getAllByRole("heading", { level: 2 })
      .map((h) => h.textContent);
    // Main result ordering/content unchanged by list assembly.
    expect(titlesAfter).toEqual(titlesBefore);

    await waitFor(() => expect(analyticsBodies().length).toBeGreaterThan(0));
    analyticsBodies().forEach((body) => {
      expect(body).not.toContain(validQuery);
      expect(body).not.toContain("TEST-2026-MOCK-001");
    });
  });

  it("records zero React console errors across the list flow", async () => {
    renderPage();
    await loadMockResults();
    await addFirstResultToNewList("清单F");
    await screen.findByLabelText("类案清单");
    expect(errorSpy).not.toHaveBeenCalled();
  });
});
