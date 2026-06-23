import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

vi.mock("../config/featureFlags", () => ({
  isDraftingEnabled: vi.fn(() => false),
}));
vi.mock("../services/draftingApi", async () => {
  const actual = await vi.importActual<typeof import("../services/draftingApi")>(
    "../services/draftingApi",
  );
  return {
    ...actual,
    createDraft: vi.fn(),
    updateDraft: vi.fn(),
    listDrafts: vi.fn(),
    getDraft: vi.fn(),
  };
});

import { DraftingPage } from "./DraftingPage";
import { isDraftingEnabled } from "../config/featureFlags";
import { createDraft, listDrafts } from "../services/draftingApi";

const flagMock = vi.mocked(isDraftingEnabled);
const createMock = vi.mocked(createDraft);
const listMock = vi.mocked(listDrafts);

function renderPage() {
  return render(
    <MemoryRouter>
      <DraftingPage />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  flagMock.mockReturnValue(false);
  createMock.mockReset();
  listMock.mockReset();
  listMock.mockResolvedValue({ ok: true, data: { drafts: [], draft_count: 0 } });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("DraftingPage flag gating", () => {
  it("renders nothing when VITE_ENABLE_DRAFTING is off (DOM 无文书工作台入口)", () => {
    flagMock.mockReturnValue(false);
    const { container } = renderPage();
    expect(container.firstChild).toBeNull();
    expect(screen.queryByLabelText("文书工作台")).toBeNull();
  });

  it("renders the workspace when the flag is on", () => {
    flagMock.mockReturnValue(true);
    renderPage();
    expect(screen.getByLabelText("文书工作台")).toBeTruthy();
    expect(screen.getByText("结构骨架（段落标题）")).toBeTruthy();
  });
});

describe("DraftingPage assembles, never drafts", () => {
  beforeEach(() => {
    flagMock.mockReturnValue(true);
  });

  it("exposes no AI drafting / generation / prediction entry", () => {
    renderPage();
    const html = document.body.innerHTML;
    expect(html).not.toContain("自动起草");
    expect(html).not.toContain("一键起草");
    expect(html).not.toContain("AI 起草");
    expect(html).not.toContain("生成段落");
    expect(html).not.toContain("生成正文");
    expect(html).not.toContain("生成结论");
    expect(html).not.toContain("自动预测");
    expect(html).not.toContain("预测胜负");
    expect(html).not.toContain("预测裁判结果（");
    const buttonLabels = Array.from(document.querySelectorAll("button")).map(
      (b) => b.textContent ?? "",
    );
    expect(buttonLabels.some((t) => /起草|生成|预测|胜负|胜率/.test(t))).toBe(false);
    expect(screen.getByText(/不起草法律文书/)).toBeTruthy();
  });

  it("saves a draft with skeleton titles + anchored refs only (no body fields)", async () => {
    createMock.mockResolvedValue({
      ok: true,
      data: {
        draft_id: "draft_1",
        structure_skeleton: ["一、基本案情"],
        candidate_refs: [],
        statute_refs: [],
        note: null,
        tag: null,
        owner_user_id: "u1",
        team_id: null,
        visibility: "private",
        status: "active",
      },
    });
    renderPage();

    fireEvent.change(screen.getByLabelText("段落标题 1"), {
      target: { value: "一、基本案情" },
    });
    fireEvent.click(screen.getByText("保存草稿"));

    await waitFor(() => {
      expect(createMock).toHaveBeenCalledTimes(1);
    });
    const input = createMock.mock.calls[0][0];
    expect(input.structure_skeleton).toContain("一、基本案情");
  });
});

describe("DraftingPage rejects anchorless refs (前端拦截)", () => {
  beforeEach(() => {
    flagMock.mockReturnValue(true);
  });

  it("blocks adding a candidate ref without an anchor", () => {
    renderPage();
    fireEvent.change(screen.getByLabelText("类案 案件ID"), {
      target: { value: "case_001" },
    });
    fireEvent.click(screen.getByText("加入类案引用"));
    expect(screen.getByText(/必须带来源锚点/)).toBeTruthy();
    expect(screen.queryByText("case_001")).toBeNull();
  });

  it("adds a candidate ref once an anchor is provided", () => {
    renderPage();
    fireEvent.change(screen.getByLabelText("类案 案件ID"), {
      target: { value: "case_001" },
    });
    fireEvent.change(screen.getByLabelText("类案 来源片段ID"), {
      target: { value: "chunk_7" },
    });
    fireEvent.click(screen.getByText("加入类案引用"));
    expect(screen.getByText("case_001")).toBeTruthy();
    expect(screen.getByText(/来源 chunk_7/)).toBeTruthy();
  });

  it("blocks a statute ref without a text_id anchor", () => {
    renderPage();
    fireEvent.change(screen.getByLabelText("法条 法条ID"), {
      target: { value: "statute_刑法_266" },
    });
    fireEvent.change(screen.getByLabelText("法条 法律名称"), {
      target: { value: "中华人民共和国刑法" },
    });
    fireEvent.click(screen.getByText("加入法条引用"));
    expect(screen.getByText(/必须带 text_id 来源锚点/)).toBeTruthy();
  });
});

describe("DraftingPage does not touch browser storage", () => {
  beforeEach(() => {
    flagMock.mockReturnValue(true);
  });

  it("never calls localStorage / sessionStorage during edit + save flow", async () => {
    const lsSet = vi.spyOn(Storage.prototype, "setItem");
    const lsGet = vi.spyOn(Storage.prototype, "getItem");
    createMock.mockResolvedValue({
      ok: true,
      data: {
        draft_id: "draft_1",
        structure_skeleton: ["一、基本案情"],
        candidate_refs: [],
        statute_refs: [],
        owner_user_id: "u1",
        visibility: "private",
        status: "active",
      },
    });
    renderPage();
    fireEvent.change(screen.getByLabelText("段落标题 1"), {
      target: { value: "一、基本案情" },
    });
    fireEvent.change(screen.getByLabelText("备注（可选，短）"), {
      target: { value: "内部讨论备注" },
    });
    fireEvent.click(screen.getByText("保存草稿"));
    await waitFor(() => {
      expect(createMock).toHaveBeenCalled();
    });
    expect(lsSet).not.toHaveBeenCalled();
    expect(lsGet).not.toHaveBeenCalled();
  });
});
