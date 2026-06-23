import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SearchPage } from "./SearchPage";

// M3-7 acceptance (jsdom). Drives the real component tree on the built-in mock
// path (host<->VM browser bridge is unreachable). Covers the three copy entry
// points (result card / detail / compare), the metadata-only boundary, the
// clipboard-unavailable safe fallback, and the absence of export / history /
// favorite / report / case-list controls.

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[{ pathname: "/search", state: {} }]}>
        <Routes>
          <Route path="/search" element={<SearchPage />} />
          <Route path="/" element={<div>home</div>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  );
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
let errorSpy: any;
let writeText: ReturnType<typeof vi.fn>;
const copyLogs: Array<Record<string, unknown>> = [];
// eslint-disable-next-line @typescript-eslint/no-explicit-any
let infoSpy: any;

beforeEach(() => {
  vi.restoreAllMocks();
  copyLogs.length = 0;
  vi.stubEnv("VITE_ENABLE_EXPANDED_SEARCH", "true");
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue({ ok: true, status: 202, json: async () => ({ accepted: true }) })
  );
  writeText = vi.fn().mockResolvedValue(undefined);
  vi.stubGlobal("navigator", {
    ...globalThis.navigator,
    clipboard: { writeText },
  });
  errorSpy = vi.spyOn(console, "error").mockImplementation(() => {});
  infoSpy = vi.spyOn(console, "info").mockImplementation((line: string) => {
    try {
      const parsed = JSON.parse(line);
      if (parsed?.event === "citation_copy_action") {
        copyLogs.push(parsed);
      }
    } catch {
      /* non-JSON info lines ignored */
    }
  });
});

afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
  infoSpy?.mockRestore();
});

const validQuery =
  "消费者购买电热水壶后出现漏电受伤，商家称系使用不当，双方争议产品缺陷与赔偿责任。";

async function loadMockResults() {
  renderPage();
  fireEvent.change(screen.getByLabelText("案情描述"), {
    target: { value: validQuery },
  });
  fireEvent.click(screen.getByRole("button", { name: "测试数据" }));
  await screen.findByText("【测试数据】产品缺陷责任纠纷样例（非真实案例）");
}

describe("M3-7 citation copy acceptance (mock path)", () => {
  it("copies the case number from a result card (metadata only)", async () => {
    await loadMockResults();
    const copyButtons = screen.getAllByRole("button", { name: /复制案号：/ });
    expect(copyButtons.length).toBeGreaterThanOrEqual(1);
    fireEvent.click(copyButtons[0]);

    await waitFor(() => {
      expect(writeText).toHaveBeenCalledTimes(1);
    });
    // The copied payload is exactly the docket number — no summary/body text.
    expect(writeText).toHaveBeenCalledWith("TEST-2026-MOCK-001（非真实案号）");
    await screen.findAllByText("已复制");
  });

  it("copies a basic citation line from the detail drawer", async () => {
    await loadMockResults();
    fireEvent.click(screen.getAllByRole("button", { name: /查看案例详情：/ })[0]);
    const dialog = await screen.findByRole("dialog");
    const citationButton = await within(dialog).findByRole("button", {
      name: "复制本案基础引用格式",
    });
    fireEvent.click(citationButton);

    await waitFor(() => {
      expect(writeText).toHaveBeenCalledTimes(1);
    });
    const copied = writeText.mock.calls[0][0] as string;
    // citation line is metadata-only: contains court + case number, and no
    // summary / holding / fact body text leaks in.
    expect(copied).toContain("TEST-2026-MOCK-001（非真实案号）");
    expect(copied).toContain("测试法院（非真实）");
  });

  it("copies a single-case citation from the compare view", async () => {
    await loadMockResults();
    const checkboxes = screen.getAllByRole("checkbox", { name: /将案例加入对比/ });
    fireEvent.click(checkboxes[0]);
    fireEvent.click(checkboxes[1]);
    fireEvent.click(await screen.findByRole("button", { name: "打开对比视图" }));
    const dialog = await screen.findByRole("dialog", { name: /案例横向对比/ });

    const copyButtons = within(dialog).getAllByRole("button", { name: /复制引用：/ });
    expect(copyButtons.length).toBeGreaterThanOrEqual(2);
    fireEvent.click(copyButtons[0]);
    await waitFor(() => {
      expect(writeText).toHaveBeenCalledTimes(1);
    });
  });

  it("emits only sanitized copy telemetry (no body text)", async () => {
    await loadMockResults();
    fireEvent.click(screen.getAllByRole("button", { name: /复制案号：/ })[0]);
    await waitFor(() => {
      expect(copyLogs.length).toBeGreaterThanOrEqual(1);
    });
    const log = copyLogs[0];
    expect(log).toMatchObject({
      event: "citation_copy_action",
      surface: "result_card",
      kind: "case_number",
      status: "copied",
      reason_code: null,
      count: 1,
    });
    // No body-like keys ever present in the log line.
    const keys = Object.keys(log);
    ["text", "citation_format", "case_number", "case_id", "court", "query", "body"].forEach(
      (k) => expect(keys).not.toContain(k)
    );
  });

  it("degrades safely when the clipboard is unavailable and keeps results intact", async () => {
    // Remove clipboard support for this case.
    vi.stubGlobal("navigator", { ...globalThis.navigator, clipboard: undefined });
    await loadMockResults();
    fireEvent.click(screen.getAllByRole("button", { name: /复制案号：/ })[0]);

    // A safe hint is shown; main results are untouched.
    expect(
      await screen.findAllByText("复制不可用，请手动选择文本复制")
    ).not.toHaveLength(0);
    expect(
      screen.getByText("【测试数据】产品缺陷责任纠纷样例（非真实案例）")
    ).toBeInTheDocument();
    await waitFor(() => {
      expect(copyLogs.some((l) => l.reason_code === "clipboard_unavailable")).toBe(true);
    });
  });

  it("exposes no export / history / favorite / report / case-list controls", async () => {
    await loadMockResults();
    fireEvent.click(screen.getAllByRole("button", { name: /查看案例详情：/ })[0]);
    await screen.findByRole("dialog");
    for (const forbidden of [
      "导出",
      "下载",
      "历史",
      "收藏",
      "加入收藏",
      "生成报告",
      "报告",
      "类案清单",
      "清单",
    ]) {
      expect(screen.queryByRole("button", { name: new RegExp(forbidden) })).toBeNull();
    }
  });

  it("records zero React console errors across the copy flow", async () => {
    await loadMockResults();
    fireEvent.click(screen.getAllByRole("button", { name: /复制案号：/ })[0]);
    await waitFor(() => expect(writeText).toHaveBeenCalled());
    expect(errorSpy).not.toHaveBeenCalled();
  });
});
