import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

vi.mock("../config/featureFlags", () => ({
  isStatuteSearchEnabled: vi.fn(() => false),
}));
vi.mock("../services/statuteApi", async () => {
  const actual = await vi.importActual<typeof import("../services/statuteApi")>(
    "../services/statuteApi",
  );
  return {
    ...actual,
    submitStatuteSearch: vi.fn(),
    fetchCasesByStatute: vi.fn(),
  };
});

import { StatutePage } from "./StatutePage";
import { isStatuteSearchEnabled } from "../config/featureFlags";
import { submitStatuteSearch, fetchCasesByStatute } from "../services/statuteApi";

const flagMock = vi.mocked(isStatuteSearchEnabled);
const searchMock = vi.mocked(submitStatuteSearch);
const casesMock = vi.mocked(fetchCasesByStatute);

// 短假法条 + 假 case_id（不含真实正文 / PII）。
const STATUTE_WITH_ANCHOR = {
  statute_id: "statute_刑法_266",
  law_name: "中华人民共和国刑法",
  article_no: "第二百六十六条",
  statute_anchors: [
    { text_id: "law_刑法_266_0", law_name: "中华人民共和国刑法", article_no: "第二百六十六条" },
  ],
  article_text: "诈骗公私财物，数额较大的，处三年以下有期徒刑……（短假条文）",
  source_corpus: "JuDGE law_corpus",
  effective_status: "现行有效",
  related_case_refs: [],
};

const STATUTE_NO_ANCHOR = {
  statute_id: "statute_无锚点_999",
  law_name: "无锚点法",
  article_no: "第一条",
  statute_anchors: [],
  article_text: "这条没有锚点，不应被展示。",
  source_corpus: null,
  effective_status: null,
  related_case_refs: [],
};

function renderPage() {
  return render(
    <MemoryRouter>
      <StatutePage />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  flagMock.mockReturnValue(false);
  searchMock.mockReset();
  casesMock.mockReset();
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("StatutePage flag gating", () => {
  it("renders nothing when VITE_ENABLE_STATUTE_SEARCH is off (DOM 无法条入口)", () => {
    flagMock.mockReturnValue(false);
    const { container } = renderPage();
    expect(container.firstChild).toBeNull();
    expect(screen.queryByLabelText("法条法规检索")).toBeNull();
  });

  it("renders the workspace when the flag is on", () => {
    flagMock.mockReturnValue(true);
    renderPage();
    expect(screen.getByLabelText("法条法规检索")).toBeTruthy();
    expect(screen.getByText("检索法条")).toBeTruthy();
  });
});

describe("StatutePage statute hits require anchors", () => {
  beforeEach(() => {
    flagMock.mockReturnValue(true);
  });

  it("renders article text only for hits with a text_id anchor; drops anchorless hits", async () => {
    searchMock.mockResolvedValue({
      ok: true,
      data: {
        query_session_id: "qs_1",
        statute_refs: [STATUTE_WITH_ANCHOR, STATUTE_NO_ANCHOR],
        statute_count: 2,
        degraded: false,
        degraded_reasons: [],
        search_mode: "standard",
      },
    });
    renderPage();

    fireEvent.change(screen.getByLabelText(/检索内容/), {
      target: { value: "诈骗的认定" },
    });
    fireEvent.click(screen.getByText("检索法条"));

    await waitFor(() => {
      expect(screen.getByText(/诈骗公私财物/)).toBeTruthy();
    });
    // 带锚点命中展示其 text_id 来源。
    expect(screen.getByText("law_刑法_266_0")).toBeTruthy();
    // 命中计数只算带锚点的（2 个里只保留 1 个）。
    expect(screen.getByText("法条命中（1）")).toBeTruthy();
    // 无锚点命中的条文绝不渲染。
    expect(screen.queryByText(/这条没有锚点/)).toBeNull();
    expect(screen.queryByText("无锚点法")).toBeNull();
  });
});

describe("StatutePage crosslink (法条→类案) shows CandidateRef without judgment body", () => {
  beforeEach(() => {
    flagMock.mockReturnValue(true);
  });

  it("loads related cases with source anchors and no body text", async () => {
    searchMock.mockResolvedValue({
      ok: true,
      data: {
        query_session_id: "qs_1",
        statute_refs: [STATUTE_WITH_ANCHOR],
        statute_count: 1,
        degraded: false,
        degraded_reasons: [],
        search_mode: "standard",
      },
    });
    casesMock.mockResolvedValue({
      ok: true,
      data: {
        query_session_id: "qs_1",
        candidate_refs: [
          {
            case_id: "case_001",
            case_number: "(2021)京01刑终123号",
            court: "北京一中院",
            trial_level: "二审",
            case_cause: "诈骗",
            judgment_date: "2021-06-01",
            source_anchors: [
              { case_id: "case_001", source_chunk_id: "chunk_7", anchor_type: "holding" },
            ],
          },
        ],
        candidate_count: 1,
        degraded: false,
        degraded_reasons: [],
        search_mode: "standard",
      },
    });
    renderPage();

    fireEvent.change(screen.getByLabelText(/检索内容/), {
      target: { value: "诈骗的认定" },
    });
    fireEvent.click(screen.getByText("检索法条"));

    await waitFor(() => {
      expect(screen.getByText("查看引用本法条的类案")).toBeTruthy();
    });
    fireEvent.click(screen.getByText("查看引用本法条的类案"));

    await waitFor(() => {
      expect(screen.getByText("case_001")).toBeTruthy();
    });
    // 互跳 CandidateRef 展示来源锚点 + 跳检索助手入口。
    expect(screen.getByText(/来源 chunk_7/)).toBeTruthy();
    expect(screen.getByText("在检索助手中查看")).toBeTruthy();
    expect(casesMock).toHaveBeenCalledWith("statute_刑法_266", expect.any(Object));
  });
});

describe("StatutePage does not touch browser storage", () => {
  beforeEach(() => {
    flagMock.mockReturnValue(true);
  });

  it("never calls localStorage / sessionStorage during a search flow", async () => {
    const lsSet = vi.spyOn(Storage.prototype, "setItem");
    const lsGet = vi.spyOn(Storage.prototype, "getItem");
    searchMock.mockResolvedValue({
      ok: true,
      data: {
        query_session_id: "qs_1",
        statute_refs: [STATUTE_WITH_ANCHOR],
        statute_count: 1,
        degraded: false,
        degraded_reasons: [],
        search_mode: "standard",
      },
    });
    renderPage();
    fireEvent.change(screen.getByLabelText(/检索内容/), {
      target: { value: "诈骗的认定" },
    });
    fireEvent.click(screen.getByText("检索法条"));
    await waitFor(() => {
      expect(screen.getByText(/诈骗公私财物/)).toBeTruthy();
    });
    expect(lsSet).not.toHaveBeenCalled();
    expect(lsGet).not.toHaveBeenCalled();
  });
});
