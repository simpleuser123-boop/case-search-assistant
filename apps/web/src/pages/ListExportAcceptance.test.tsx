import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SearchPage } from "./SearchPage";
import { MOCK_SEARCH_RESPONSE } from "../mocks/searchMockFixture";
import { EXPORT_DISCLAIMER_LINES } from "../lib/caseListExport";

// M4-5 清单导出 acceptance（jsdom）。驱动真实 SearchPage 树走内置 mock 路径
// （host<->VM 浏览器桥不可达）。覆盖可见验收点：
//   - flag 开启时清单面板出现「导出清单」入口（Markdown / CSV）；
//   - 点击导出触发本地下载，抽查实际文件内容：含案号等元数据 + 来源引用 +
//     用户备注 + 免责说明，且不含任何裁判文书正文 / 摘要 / chunk 正文 / 原始 query；
//   - 文件不含「已查全 / 保证无遗漏 / 查全率 / 胜负概率」等绝对话术与诉讼结果判断；
//   - 导出降级（环境不支持下载）给出安全提示、不抛错、不影响主链路；
//   - 主结果仍在、埋点不携带正文、console error = 0；
//   - flag 关闭时（仅 CASE_LIST 开）不渲染任何导出入口。

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

// 正文标志：绝不允许出现在任何导出文件中。
const BODY_MARKERS = ["本院查明", "裁判要旨", "前端测试数据片段", "本院认为", "经审理查明"];
// 禁用绝对话术 / 诉讼结果判断。
const FORBIDDEN_PHRASES = ["已查全", "保证无遗漏", "查全率", "胜诉概率", "败诉概率"];

// eslint-disable-next-line @typescript-eslint/no-explicit-any
let errorSpy: any;
let fetchMock: ReturnType<typeof vi.fn>;
// 捕获被「下载」的文件内容（通过 stub Blob + URL.createObjectURL）。
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
  vi.stubEnv("VITE_ENABLE_LIST_EXPORT", "true");
  downloadedFiles = [];

  // 捕获 Blob 内容：记录构造时的文本，便于抽查导出文件。
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
        // 仍构造一个真实 Blob 以兼容其他用途。
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

describe("M4-5 list export acceptance (mock path)", () => {
  it("renders export controls when flag is ON and a list has items", async () => {
    renderPage();
    await loadMockResults();
    await addFirstResultToNewList("导出清单A");

    const panel = await screen.findByLabelText("类案清单");
    expect(within(panel).getByRole("button", { name: "导出为 Markdown" })).toBeInTheDocument();
    expect(within(panel).getByRole("button", { name: "导出为 CSV" })).toBeInTheDocument();
  });

  it("exports Markdown: file has metadata + source ref + note + disclaimer, NO body, NO absolute claims", async () => {
    renderPage();
    await loadMockResults();
    await addFirstResultToNewList("导出清单B");

    const panel = await screen.findByLabelText("类案清单");
    fireEvent.click(within(panel).getByRole("button", { name: "导出为 Markdown" }));

    await waitFor(() => expect(downloadedFiles.length).toBeGreaterThan(0));
    const file = downloadedFiles[downloadedFiles.length - 1];
    expect(file.type).toContain("text/markdown");

    // 含元数据 + 来源引用。
    expect(file.content).toContain("TEST-2026-MOCK-001（非真实案号）");
    expect(file.content).toContain("mock-case-001");
    // 含全部免责说明行。
    for (const line of EXPORT_DISCLAIMER_LINES) {
      expect(file.content).toContain(line);
    }
    // 不含任何正文标志。
    for (const marker of BODY_MARKERS) {
      expect(file.content).not.toContain(marker);
    }
    // 不含原始 query。
    expect(file.content).not.toContain(validQuery);
    // 不含绝对话术 / 诉讼结果判断。
    for (const phrase of FORBIDDEN_PHRASES) {
      expect(file.content).not.toContain(phrase);
    }
    // 成功提示可见。
    expect(within(panel).getByRole("status")).toHaveTextContent(/已生成导出文件/);
  });

  it("exports CSV: disclaimer comment header + metadata, NO body", async () => {
    renderPage();
    await loadMockResults();
    await addFirstResultToNewList("导出清单C");

    const panel = await screen.findByLabelText("类案清单");
    fireEvent.click(within(panel).getByRole("button", { name: "导出为 CSV" }));

    await waitFor(() => expect(downloadedFiles.length).toBeGreaterThan(0));
    const file = downloadedFiles[downloadedFiles.length - 1];
    expect(file.type).toContain("text/csv");
    // CSV 首行是 # 开头的免责注释。
    expect(file.content.split("\n")[0].startsWith("# ")).toBe(true);
    expect(file.content).toContain("案号");
    expect(file.content).toContain("TEST-2026-MOCK-001");
    for (const marker of BODY_MARKERS) {
      expect(file.content).not.toContain(marker);
    }
    for (const phrase of FORBIDDEN_PHRASES) {
      expect(file.content).not.toContain(phrase);
    }
  });

  it("degrades safely when download is unavailable (no throw, main flow intact)", async () => {
    // 让 createObjectURL 缺失 → browserDownloader 抛错 → degraded。
    vi.stubGlobal("URL", {});
    renderPage();
    await loadMockResults();
    await addFirstResultToNewList("导出清单D");

    const panel = await screen.findByLabelText("类案清单");
    fireEvent.click(within(panel).getByRole("button", { name: "导出为 Markdown" }));

    // 给出安全提示，不抛错。
    await waitFor(() =>
      expect(within(panel).getByRole("status")).toHaveTextContent(/无法自动下载|请稍后重试/)
    );
    // 主结果仍在。
    expect(
      screen.getByText("【测试数据】产品缺陷责任纠纷样例（非真实案例）")
    ).toBeInTheDocument();
  });

  it("export analytics/events never carry body or raw query", async () => {
    renderPage();
    await loadMockResults();
    await addFirstResultToNewList("导出清单E");
    const panel = await screen.findByLabelText("类案清单");
    fireEvent.click(within(panel).getByRole("button", { name: "导出为 CSV" }));
    await waitFor(() => expect(downloadedFiles.length).toBeGreaterThan(0));

    for (const body of analyticsBodies()) {
      expect(body).not.toContain(validQuery);
      for (const marker of BODY_MARKERS) {
        expect(body).not.toContain(marker);
      }
    }
  });

  it("zero console errors during the export flow", async () => {
    renderPage();
    await loadMockResults();
    await addFirstResultToNewList("导出清单F");
    const panel = await screen.findByLabelText("类案清单");
    fireEvent.click(within(panel).getByRole("button", { name: "导出为 Markdown" }));
    await waitFor(() => expect(downloadedFiles.length).toBeGreaterThan(0));
    // jsdom 未实现 a[download] 的下载语义，点击下载锚点会抛「navigation」
    // 未实现错误——这是测试环境限制，真实浏览器不会触发。过滤后断言无其它错误。
    const realErrors = errorSpy.mock.calls.filter(
      (call: unknown[]) => !String(call[0]).includes("Not implemented: navigation")
    );
    expect(realErrors).toHaveLength(0);
  });

  it("does NOT render export controls when LIST_EXPORT flag is OFF (CASE_LIST on)", async () => {
    vi.stubEnv("VITE_ENABLE_LIST_EXPORT", "false");
    renderPage();
    await loadMockResults();
    await addFirstResultToNewList("导出清单G");

    const panel = await screen.findByLabelText("类案清单");
    expect(within(panel).queryByRole("button", { name: "导出为 Markdown" })).toBeNull();
    expect(within(panel).queryByRole("button", { name: "导出为 CSV" })).toBeNull();
  });
});
