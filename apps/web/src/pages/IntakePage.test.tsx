import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

vi.mock("../config/featureFlags", () => ({
  isIntakeEnabled: vi.fn(() => false),
  isIntakeAiExtractionEnabled: vi.fn(() => false),
}));
vi.mock("../services/intakeApi", async () => {
  const actual = await vi.importActual<typeof import("../services/intakeApi")>(
    "../services/intakeApi",
  );
  return {
    ...actual,
    submitIntakeSearch: vi.fn(),
  };
});

import { IntakePage } from "./IntakePage";
import { isIntakeEnabled } from "../config/featureFlags";
import { submitIntakeSearch } from "../services/intakeApi";

const flagMock = vi.mocked(isIntakeEnabled);
const submitMock = vi.mocked(submitIntakeSearch);

// 短假案情（含假 PII）：用于验证脱敏 + 零上送，绝不是真实个人信息。
const RAW_CASE =
  "原告张三与被告李四就买卖合同的货款付款义务发生纠纷，张三电话13800138000，住北京市朝阳区测试路1号。";

function renderPage() {
  return render(
    <MemoryRouter>
      <IntakePage />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  flagMock.mockReturnValue(false);
  submitMock.mockReset();
  submitMock.mockResolvedValue({
    ok: true,
    data: {
      query_session_id: "qs_1",
      candidate_refs: [
        {
          case_id: "case_001",
          case_number: "(2021)京01民终123号",
          court: "北京一中院",
          trial_level: "二审",
          case_cause: "买卖合同纠纷",
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
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("IntakePage (flag-gated)", () => {
  it("renders nothing when VITE_ENABLE_INTAKE is off (default)", () => {
    flagMock.mockReturnValue(false);
    const { container } = renderPage();
    expect(container.firstChild).toBeNull();
    expect(screen.queryByLabelText("案情录入端")).toBeNull();
    expect(screen.queryByLabelText("原始案情")).toBeNull();
  });

  it("renders the local-input stage when enabled", () => {
    flagMock.mockReturnValue(true);
    renderPage();
    expect(screen.getByLabelText("案情录入端")).toBeInTheDocument();
    expect(screen.getByLabelText("原始案情")).toBeInTheDocument();
    // 默认无「服务端 AI 增强」UI（子开关默认 off、无 on 路径）。
    expect(screen.queryByText(/AI\s*增强|服务端.*增强/)).toBeNull();
  });

  it("does not send anything before the user confirms the preview", () => {
    flagMock.mockReturnValue(true);
    renderPage();
    fireEvent.change(screen.getByLabelText("原始案情"), {
      target: { value: RAW_CASE },
    });
    fireEvent.click(screen.getByText("本地脱敏预览"));
    // 预览阶段：展示脱敏后内容，但尚未发送任何请求。
    expect(screen.getByLabelText("脱敏预览")).toBeInTheDocument();
    expect(submitMock).not.toHaveBeenCalled();
  });

  it("shows desensitized preview without raw PII, then sends only the SearchProfile on confirm", async () => {
    flagMock.mockReturnValue(true);
    renderPage();

    fireEvent.change(screen.getByLabelText("原始案情"), {
      target: { value: RAW_CASE },
    });
    fireEvent.click(screen.getByText("本地脱敏预览"));

    // 预览 DOM 不得出现原始 PII。
    const previewSection = screen.getByLabelText("脱敏预览");
    const previewText = previewSection.textContent || "";
    expect(previewText).not.toContain("张三");
    expect(previewText).not.toContain("李四");
    expect(previewText).not.toContain("13800138000");
    expect(previewText).not.toContain("朝阳区测试路1号");

    fireEvent.click(screen.getByText("确认并仅发送脱敏内容"));

    await waitFor(() => expect(submitMock).toHaveBeenCalledTimes(1));

    // 发送给 API 层的 profile：只含白名单五字段，且无原始案情 / PII。
    const sentProfile = submitMock.mock.calls[0][0];
    expect(Object.keys(sentProfile).sort()).toEqual(
      [
        "case_cause",
        "dispute_focus_keywords",
        "query_text",
        "region",
        "trial_level_preference",
      ].sort(),
    );
    const serialized = JSON.stringify(sentProfile);
    expect(serialized).not.toContain("张三");
    expect(serialized).not.toContain("李四");
    expect(serialized).not.toContain("13800138000");
    expect(serialized).not.toContain("朝阳区测试路1号");
    expect(serialized).not.toContain("raw_case");

    // 结果阶段展示 CandidateRef（无正文，带来源锚点）。
    await waitFor(() =>
      expect(screen.getByLabelText("类案候选结果")).toBeInTheDocument(),
    );
    expect(screen.getByText("case_001")).toBeInTheDocument();
    expect(screen.getByText(/来源 chunk_7/)).toBeInTheDocument();
  });

  it("keeps raw case in memory only — no browser storage writes", () => {
    flagMock.mockReturnValue(true);
    const localSet = vi.spyOn(window.localStorage.__proto__, "setItem");
    const sessionSet = vi.spyOn(window.sessionStorage.__proto__, "setItem");

    renderPage();
    fireEvent.change(screen.getByLabelText("原始案情"), {
      target: { value: RAW_CASE },
    });
    fireEvent.click(screen.getByText("本地脱敏预览"));
    fireEvent.click(screen.getByText("确认并仅发送脱敏内容"));

    expect(localSet).not.toHaveBeenCalled();
    expect(sessionSet).not.toHaveBeenCalled();
    expect(JSON.stringify(window.localStorage)).not.toContain(RAW_CASE);
    expect(JSON.stringify(window.sessionStorage)).not.toContain(RAW_CASE);
  });

  it("surfaces a disabled message when the backend returns INTAKE_DISABLED", async () => {
    flagMock.mockReturnValue(true);
    submitMock.mockResolvedValue({
      ok: false,
      reason: "disabled",
      status: 403,
      reasonCode: "INTAKE_DISABLED",
    });
    renderPage();
    fireEvent.change(screen.getByLabelText("原始案情"), {
      target: { value: RAW_CASE },
    });
    fireEvent.click(screen.getByText("本地脱敏预览"));
    fireEvent.click(screen.getByText("确认并仅发送脱敏内容"));

    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
    expect(screen.getByRole("alert").textContent).toContain("未启用");
  });
});
