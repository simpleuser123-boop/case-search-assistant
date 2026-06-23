import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SearchPage } from "./SearchPage";
import { MOCK_SEARCH_RESPONSE } from "../mocks/searchMockFixture";
import { REPORT_DISCLAIMER_LINES } from "../lib/reportTemplate";

// M4-6 轻量报告模板 acceptance（jsdom）。驱动真实 SearchPage 树走内置 mock 路径
// （host<->VM 浏览器桥不可达）。覆盖可见验收点：
//   - flag 开启时清单面板出现「生成类案报告模板」入口；
//   - 生成后预览出现：清单概览、逐案元数据 + 来源锚点、待复核要点、免责说明；
//   - 报告不含裁判正文 / 摘要 / chunk 正文 / 原始 query，也不含胜负结论与确定性话术；
//   - 导出报告触发本地下载，文件含模板结构 + 元数据 + 来源引用 + 免责，不含正文；
//   - 生成失败 / 下载不可用安全降级，不抛错、不影响主链路；
//   - flag 关闭时（仅 CASE_LIST 开）不渲染任何报告入口。

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

const BODY_MARKERS = ["本院查明", "裁判要旨", "前端测试数据片段", "本院认为", "经审理查明"];
const FORBIDDEN_PHRASES = ["已查全", "保证无遗漏", "查全率", "胜诉概率", "败诉概率", "胜诉率"];

// eslint-disable-next-line @typescript-eslint/no-explicit-any
let errorSpy: any;
let fetchMock: ReturnType<typeof vi.fn>;
let downloadedFiles: Array<{ content: string; type: string }>;

function analyticsBodies(): string[] {
  return fetchMock.mock.calls
    .filter(([input]) => String(input) === "/api/events")
    .map(([, init]) => String((init as RequestInit).body));
}

beforeEach(() => {
  vi.restoreAllMocks();
  window.localStorage.clear();
  vi.stubEnv("VITE_ENABLE_CASE_LIST", "true");
  vi.stubEnv("VITE_ENABLE_REPORT_TEMPLATE", "true");
  downloadedFiles = [];

  const RealBlob = globalThis.Blob;
  vi.stubGlobal(
    "Blob",
    class StubBlob {
      content: string;
      type: string;
      constructor(parts: unknown[], options?: { type?: string }) {
        this.content = (parts || []).map((p) => String(p)).join("");
        this.type = options?.type || "";
        downloadedFiles.push({ content: this.content, type: this.type });
        void RealBlob;
      }
    }
  );
  vi.stubGlobal("URL", {
    createObjectURL: vi.fn(() => "blob:mock-url"),
    revokeObjectURL: vi.fn(),
  });

  fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL) => {
    const url = String(input);
    if (url === "/api/search") {
      return Promise.resolve({ ok: true, status: 200, json: async () => MOCK_SEARCH_RESPONSE });
    }
    return Promise.resolve({ ok: true, status: 202, json: async () => ({ accepted: true }) });
  });
  vi.stubGlobal("fetch", fetchMock);
  errorSpy = vi.spyOn(console, "error").mockImplementation(() => {});
});

afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

async function loadMockResults() {
  fireEvent.change(screen.getByLabelText("案情描述"), { target: { value: validQuery } });
  fireEvent.click(screen.getByRole("button", { name: "测试数据" }));
  await screen.findByText("【测试数据】产品缺陷责任纠纷样例（非真实案例）");
}

async function addFirstResultToNewList(title: string) {
  const addButtons = await screen.findAllByRole("button", { name: /^加入类案清单：/ });
  fireEvent.click(addButtons[0]);
  const dialog = await screen.findByRole("dialog", { name: "选择类案清单" });
  fireEvent.change(within(dialog).getByPlaceholderText("新建清单名称"), {
    target: { value: title },
  });
  fireEvent.click(within(dialog).getByRole("button", { name: "新建并加入" }));
  await screen.findByLabelText("类案清单");
}

async function openReportAndGenerate(panel: HTMLElement) {
  fireEvent.click(within(panel).getByRole("button", { name: "生成类案报告模板" }));
  const preview = await within(panel).findByLabelText("类案报告模板预览");
  fireEvent.click(within(preview).getByRole("button", { name: "生成报告" }));
  return preview;
}

describe("M4-6 report template acceptance (mock path)", () => {
  it("renders the report entry when flag is ON and a list has items", async () => {
    renderPage();
    await loadMockResults();
    await addFirstResultToNewList("报告清单A");

    const panel = await screen.findByLabelText("类案清单");
    expect(
      within(panel).getByRole("button", { name: "生成类案报告模板" })
    ).toBeInTheDocument();
  });

  it("preview shows overview, per-case metadata + source anchor, review points and disclaimer", async () => {
    renderPage();
    await loadMockResults();
    await addFirstResultToNewList("报告清单B");

    const panel = await screen.findByLabelText("类案清单");
    const preview = await openReportAndGenerate(panel);

    // 清单概览 + 逐案元数据。
    expect(within(preview).getByText("清单概览")).toBeInTheDocument();
    expect(within(preview).getByText(/TEST-2026-MOCK-001/)).toBeInTheDocument();
    // 逐案来源锚点（case_id#chunk_id）。
    expect(within(preview).getByText(/mock-case-001/)).toBeInTheDocument();
    // 待复核要点 + 免责说明。
    expect(within(preview).getByText("待人工复核要点")).toBeInTheDocument();
    expect(within(preview).getByText("免责说明")).toBeInTheDocument();
    expect(
      within(preview).getByText(/本报告由「类案检索助手」/)
    ).toBeInTheDocument();
  });

  it("report preview contains NO body text and NO win/lose conclusion phrases", async () => {
    renderPage();
    await loadMockResults();
    await addFirstResultToNewList("报告清单C");

    const panel = await screen.findByLabelText("类案清单");
    const preview = await openReportAndGenerate(panel);

    const text = preview.textContent || "";
    for (const marker of BODY_MARKERS) {
      expect(text.includes(marker)).toBe(false);
    }
    for (const phrase of FORBIDDEN_PHRASES) {
      expect(text.includes(phrase)).toBe(false);
    }
    // 不含原始 query。
    expect(text.includes(validQuery)).toBe(false);
  });

  it("exports report Markdown: structure + metadata + source ref + disclaimer, NO body, NO absolute claims", async () => {
    renderPage();
    await loadMockResults();
    await addFirstResultToNewList("报告清单D");

    const panel = await screen.findByLabelText("类案清单");
    const preview = await openReportAndGenerate(panel);
    fireEvent.click(within(preview).getByRole("button", { name: "导出报告为 Markdown" }));

    await waitFor(() => expect(downloadedFiles.length).toBeGreaterThan(0));
    const file = downloadedFiles[downloadedFiles.length - 1];
    expect(file.type).toContain("text/markdown");

    // 模板结构。
    expect(file.content).toContain("## 清单概览");
    expect(file.content).toContain("## 待人工复核要点");
    expect(file.content).toContain("## 免责说明");
    // 元数据 + 来源引用。
    expect(file.content).toContain("TEST-2026-MOCK-001（非真实案号）");
    expect(file.content).toContain("mock-case-001");
    // 免责说明全行。
    for (const line of REPORT_DISCLAIMER_LINES) {
      expect(file.content).toContain(line);
    }
    // 无正文 / 无原始 query / 无绝对话术。
    for (const marker of BODY_MARKERS) {
      expect(file.content).not.toContain(marker);
    }
    expect(file.content).not.toContain(validQuery);
    for (const phrase of FORBIDDEN_PHRASES) {
      expect(file.content).not.toContain(phrase);
    }
    expect(within(preview).getByRole("status")).toHaveTextContent(/已生成报告文件/);
  });

  it("degrades safely when download is unavailable (no throw, main flow intact)", async () => {
    vi.stubGlobal("URL", {});
    renderPage();
    await loadMockResults();
    await addFirstResultToNewList("报告清单E");

    const panel = await screen.findByLabelText("类案清单");
    const preview = await openReportAndGenerate(panel);
    fireEvent.click(within(preview).getByRole("button", { name: "导出报告为 Markdown" }));

    await waitFor(() =>
      expect(within(preview).getByRole("status")).toHaveTextContent(
        /无法自动下载|请稍后重试/
      )
    );
    expect(
      screen.getByText("【测试数据】产品缺陷责任纠纷样例（非真实案例）")
    ).toBeInTheDocument();
  });

  it("report flow analytics never carry body or raw query", async () => {
    renderPage();
    await loadMockResults();
    await addFirstResultToNewList("报告清单F");
    const panel = await screen.findByLabelText("类案清单");
    const preview = await openReportAndGenerate(panel);
    fireEvent.click(within(preview).getByRole("button", { name: "导出报告为 Markdown" }));
    await waitFor(() => expect(downloadedFiles.length).toBeGreaterThan(0));

    for (const body of analyticsBodies()) {
      expect(body).not.toContain(validQuery);
      for (const marker of BODY_MARKERS) {
        expect(body).not.toContain(marker);
      }
    }
  });

  it("zero console errors during the report flow", async () => {
    renderPage();
    await loadMockResults();
    await addFirstResultToNewList("报告清单G");
    const panel = await screen.findByLabelText("类案清单");
    const preview = await openReportAndGenerate(panel);
    fireEvent.click(within(preview).getByRole("button", { name: "导出报告为 Markdown" }));
    await waitFor(() => expect(downloadedFiles.length).toBeGreaterThan(0));
    const realErrors = errorSpy.mock.calls.filter(
      (call: unknown[]) => !String(call[0]).includes("Not implemented: navigation")
    );
    expect(realErrors).toHaveLength(0);
  });

  it("does NOT render report entry when REPORT_TEMPLATE flag is OFF (CASE_LIST on)", async () => {
    vi.stubEnv("VITE_ENABLE_REPORT_TEMPLATE", "false");
    renderPage();
    await loadMockResults();
    await addFirstResultToNewList("报告清单H");

    const panel = await screen.findByLabelText("类案清单");
    expect(
      within(panel).queryByRole("button", { name: "生成类案报告模板" })
    ).toBeNull();
  });
});
