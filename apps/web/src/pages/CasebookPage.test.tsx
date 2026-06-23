import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
// E7-4 casebook 共享前端测试（owner-only 共享切换 + 非 owner 只读）。

vi.mock("../config/featureFlags", () => ({
  isCasebookEnabled: vi.fn(() => false),
}));
vi.mock("../services/casebookApi", async () => {
  const actual = await vi.importActual<typeof import("../services/casebookApi")>(
    "../services/casebookApi",
  );
  return {
    ...actual,
    createCaseFolder: vi.fn(),
    updateCaseFolder: vi.fn(),
    listCaseFolders: vi.fn(),
    getCaseFolder: vi.fn(),
    shareCaseFolder: vi.fn(),
  };
});

import { CasebookPage } from "./CasebookPage";
import { isCasebookEnabled } from "../config/featureFlags";
import { createCaseFolder, listCaseFolders } from "../services/casebookApi";

const flagMock = vi.mocked(isCasebookEnabled);
const createMock = vi.mocked(createCaseFolder);
const listMock = vi.mocked(listCaseFolders);

function renderPage() {
  return render(
    <MemoryRouter>
      <CasebookPage />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  flagMock.mockReturnValue(false);
  createMock.mockReset();
  listMock.mockReset();
  listMock.mockResolvedValue({ ok: true, data: { folders: [], folder_count: 0 } });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("CasebookPage flag gating", () => {
  it("renders nothing when VITE_ENABLE_CASEBOOK is off (DOM 无协作工作台入口)", () => {
    flagMock.mockReturnValue(false);
    const { container } = renderPage();
    expect(container.firstChild).toBeNull();
    expect(screen.queryByLabelText("案件协作工作台")).toBeNull();
  });

  it("renders the workspace when the flag is on", () => {
    flagMock.mockReturnValue(true);
    renderPage();
    expect(screen.getByLabelText("案件协作工作台")).toBeTruthy();
    expect(screen.getByLabelText("归集区")).toBeTruthy();
  });
});

describe("CasebookPage collects, never drafts / summarizes / predicts", () => {
  beforeEach(() => {
    flagMock.mockReturnValue(true);
  });

  it("exposes no AI summary / conclusion / verdict-prediction entry", () => {
    renderPage();
    // 用 actionable 短语 + 按钮正则断言（不用裸 substring：免责文案里含「不…归纳结论…胜负预测」否定式）。
    const html = document.body.innerHTML;
    expect(html).not.toContain("AI 生成");
    expect(html).not.toContain("一键综述");
    expect(html).not.toContain("生成案件综述");
    expect(html).not.toContain("胜诉概率");
    // 无任何生成/起草/综述/归纳/预测 actionable 按钮。
    const buttons = Array.from(document.querySelectorAll("button")).map((b) => b.textContent ?? "");
    expect(buttons.some((t) => /生成|起草|综述|归纳|预测/.test(t))).toBe(false);
    // 无任何 placeholder 暗示自动生成正文（用 affirmative 短语，避免命中「不起草正文」否定式）。
    const placeholders = Array.from(document.querySelectorAll("input, textarea")).map(
      (el) => el.getAttribute("placeholder") ?? "",
    );
    expect(placeholders.some((p) => /自动生成|一键|AI 生成|生成综述|生成结论|预测胜负/.test(p))).toBe(
      false,
    );
  });

  it("blocks adding a candidate ref without an anchor (前端拦截无锚点)", () => {
    renderPage();
    fireEvent.change(screen.getByLabelText("类案 案件ID"), { target: { value: "case_1" } });
    // 不填来源片段 ID（锚点）。
    fireEvent.click(screen.getByText("加入类案引用"));
    expect(screen.getByRole("alert").textContent).toContain("来源锚点");
  });

  it("blocks adding a draft skeleton ref without any title", () => {
    renderPage();
    fireEvent.change(screen.getByLabelText("文书 草稿ID"), { target: { value: "draft_1" } });
    // 不填段落标题。
    fireEvent.click(screen.getByText("加入文书骨架引用"));
    expect(screen.getByRole("alert").textContent).toContain("段落标题");
  });

  it("non-owner sees read-only, no sharing control", async () => {
    // 当前会话用户与 folder owner 不一致 -> 只读，无共享控件。
    const { setSession } = await import("../lib/sessionState");
    setSession({
      account: { user_id: "viewer", display_name: "v", account_status: "active", auth_provider: "local" },
      sessionToken: "tok",
      expiresAt: null,
    });
    listMock.mockResolvedValue({
      ok: true,
      data: {
        folders: [
          {
            case_folder_id: "folder_1",
            owner_user_id: "owner_other",
            team_id: "team_1",
            visibility: "team",
            candidate_refs: [],
            draft_descriptors: [],
            title: "测试协作夹",
            status: "active",
          },
        ],
        folder_count: 1,
      },
    });
    renderPage();
    await waitFor(() => expect(screen.getByText("团队可见")).toBeTruthy());
    expect(screen.getByText("由协作夹所有者管理共享，你当前为只读访问。")).toBeTruthy();
    // 非 owner 无「共享给团队」/「改回仅本人可见」控件。
    const buttons = Array.from(document.querySelectorAll("button")).map((b) => b.textContent ?? "");
    expect(buttons.some((t) => /共享给团队|改回仅本人可见/.test(t))).toBe(false);
    setSession(null);
  });

  it("owner can toggle private -> team via shareCaseFolder (gated, only visibility)", async () => {
    const { setSession } = await import("../lib/sessionState");
    const { shareCaseFolder } = await import("../services/casebookApi");
    const shareMock = vi.mocked(shareCaseFolder);
    setSession({
      account: { user_id: "owner_me", display_name: "o", account_status: "active", auth_provider: "local" },
      sessionToken: "tok",
      expiresAt: null,
    });
    listMock.mockResolvedValue({
      ok: true,
      data: {
        folders: [
          {
            case_folder_id: "folder_9",
            owner_user_id: "owner_me",
            team_id: null,
            visibility: "private",
            candidate_refs: [],
            draft_descriptors: [],
            title: "我的私有夹",
            status: "active",
          },
        ],
        folder_count: 1,
      },
    });
    shareMock.mockResolvedValue({
      ok: true,
      data: {
        case_folder_id: "folder_9",
        owner_user_id: "owner_me",
        team_id: "team_42",
        visibility: "team",
        candidate_refs: [],
        draft_descriptors: [],
        status: "active",
      },
    });
    renderPage();
    await waitFor(() => expect(screen.getByText("仅本人可见")).toBeTruthy());
    fireEvent.change(screen.getByLabelText("协作夹 folder_9 共享团队ID"), {
      target: { value: "team_42" },
    });
    fireEvent.click(screen.getByText("共享给团队"));
    await waitFor(() => expect(shareMock).toHaveBeenCalledTimes(1));
    expect(shareMock.mock.calls[0][0]).toBe("folder_9");
    expect(shareMock.mock.calls[0][1]).toEqual({ visibility: "team", team_id: "team_42" });
    setSession(null);
  });

  it("saves via createCaseFolder with anchored refs collected in memory", async () => {
    createMock.mockResolvedValue({
      ok: true,
      data: {
        case_folder_id: "folder_new",
        owner_user_id: "u1",
        visibility: "private",
        candidate_refs: [],
        draft_descriptors: [],
        status: "active",
      },
    });
    renderPage();
    // 加入一条带锚点类案引用。
    fireEvent.change(screen.getByLabelText("类案 案件ID"), { target: { value: "case_1" } });
    fireEvent.change(screen.getByLabelText("类案 来源片段ID"), { target: { value: "chunk_1" } });
    fireEvent.click(screen.getByText("加入类案引用"));
    fireEvent.change(screen.getByLabelText("协作夹标题（可选，短）"), {
      target: { value: "我的协作夹" },
    });
    fireEvent.click(screen.getByText("保存协作夹"));
    await waitFor(() => expect(createMock).toHaveBeenCalledTimes(1));
    const input = createMock.mock.calls[0][0];
    expect(input.candidate_refs).toHaveLength(1);
    expect(input.candidate_refs[0].case_id).toBe("case_1");
    expect(input.title).toBe("我的协作夹");
  });
});
