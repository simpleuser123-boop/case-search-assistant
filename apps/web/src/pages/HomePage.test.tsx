import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SearchComposer } from "../components/search/SearchComposer";
import { HomePage } from "../pages/HomePage";

beforeEach(() => {
  vi.restoreAllMocks();
  vi.stubEnv("VITE_ENABLE_M1_M5_ACCEPTANCE", "false");
  window.localStorage.clear();
});

afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

describe("HomePage", () => {
  it("renders the app title", () => {
    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>
    );
    expect(screen.getByText("类案检索助手")).toBeInTheDocument();
    expect(screen.getByLabelText("案情描述")).toBeInTheDocument();
  });
});

describe("SearchComposer", () => {
  it("renders the default empty state and focuses the textarea", () => {
    render(<SearchComposer onSubmit={vi.fn()} />);

    const textarea = screen.getByLabelText("案情描述");
    const submit = screen.getByRole("button", { name: "开始检索" });

    expect(textarea).toHaveValue("");
    expect(textarea).toHaveAttribute(
      "placeholder",
      expect.stringContaining("买卖合同约定分批交付设备")
    );
    expect(screen.getByText("0/500")).toBeInTheDocument();
    expect(submit).toBeDisabled();

    textarea.focus();
    expect(textarea).toHaveFocus();
  });

  it("fills an example case description and keeps keyboard focus in the input", () => {
    render(<SearchComposer onSubmit={vi.fn()} />);

    const textarea = screen.getByLabelText("案情描述");
    fireEvent.click(
      screen.getByRole("button", {
        name: /车辆低速变道时对方突然倒地/,
      })
    );

    expect(textarea).toHaveValue(
      "车辆低速变道时对方突然倒地并主张高额修车和误工损失，现场视频显示接触轻微。需要检索碰瓷、交通事故责任认定相关类案。"
    );
    expect(textarea).toHaveFocus();
    expect(screen.getByRole("button", { name: "开始检索" })).toBeEnabled();
  });

  it("does not persist raw case description to browser storage", () => {
    const rawQuery =
      "买卖合同约定交付设备，卖方迟延交付并拒绝返还预付款，原始案情不得进入浏览器持久化 storage。";
    const setItemSpy = vi.spyOn(window.localStorage.__proto__, "setItem");
    const sessionSetItemSpy = vi.spyOn(window.sessionStorage.__proto__, "setItem");

    render(<SearchComposer onSubmit={vi.fn()} />);

    fireEvent.change(screen.getByLabelText("案情描述"), {
      target: { value: rawQuery },
    });

    expect(setItemSpy).not.toHaveBeenCalled();
    expect(sessionSetItemSpy).not.toHaveBeenCalled();
    expect(JSON.stringify(window.localStorage)).not.toContain(rawQuery);
    expect(JSON.stringify(window.sessionStorage)).not.toContain(rawQuery);
  });

  it("disables submit for blank, punctuation-only, and too-short input", () => {
    render(<SearchComposer onSubmit={vi.fn()} />);

    const textarea = screen.getByLabelText("案情描述");
    const submit = screen.getByRole("button", { name: "开始检索" });

    expect(submit).toBeDisabled();

    fireEvent.change(textarea, { target: { value: "，。！？" } });
    expect(submit).toBeDisabled();
    expect(screen.getByText("输入内容不能只有标点符号。")).toBeInTheDocument();

    fireEvent.change(textarea, { target: { value: "合同纠纷" } });
    expect(submit).toBeDisabled();
    expect(
      screen.getByText("请至少输入 10 个可识别的文字或数字，描述事实经过或争议焦点。")
    ).toBeInTheDocument();
  });

  it("allows over-500-character input and shows a weak warning", () => {
    render(<SearchComposer onSubmit={vi.fn()} />);

    fireEvent.change(screen.getByLabelText("案情描述"), {
      target: { value: "案".repeat(501) },
    });

    expect(
      screen.getByText("已超过 500 字，仍可提交；建议保留关键事实和争议焦点。")
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "开始检索" })).toBeEnabled();
  });

  it("submits a valid query by button and emits only desensitized analytics fields", async () => {
    const onSubmit = vi.fn();
    const analyticsListener = vi.fn();
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 202,
      json: async () => ({ accepted: true }),
    });
    const query =
      "买卖合同约定分批交付设备，买方已付款但卖方多次延期交货并拒绝退还预付款。需要检索合同履行、迟延交付和解除责任。";
    vi.stubGlobal("fetch", fetchMock);
    window.addEventListener("case-search:analytics", analyticsListener);

    render(<SearchComposer onSubmit={onSubmit} />);
    fireEvent.change(screen.getByLabelText("案情描述"), {
      target: { value: query },
    });
    fireEvent.click(screen.getByRole("button", { name: "开始检索" }));

    expect(onSubmit).toHaveBeenCalledWith(
      query,
      expect.objectContaining({
        inputLength: Array.from(query).length,
        trigger: "button",
      })
    );
    expect(analyticsListener).toHaveBeenCalledTimes(1);
    const event = analyticsListener.mock.calls[0][0] as CustomEvent;
    expect(event.detail).toEqual({
      event_name: "search_submit",
      timestamp: expect.any(String),
      metadata: {
        input_length: Array.from(query).length,
        trigger: "button",
        has_draft_restored: false,
      },
    });
    expect(event.detail.query_session_id).toBeUndefined();
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/events",
        expect.objectContaining({ method: "POST" })
      )
    );
    expect(
      JSON.parse(String((fetchMock.mock.calls[0][1] as RequestInit).body))
    ).toEqual(event.detail);
    expect(JSON.stringify(event.detail)).not.toContain(query);

    window.removeEventListener("case-search:analytics", analyticsListener);
  });

  it("disables the textarea and submit button while submit is pending", async () => {
    let resolveSubmit: (() => void) | undefined;
    const onSubmit = vi.fn(
      () =>
        new Promise<void>((resolve) => {
          resolveSubmit = resolve;
        })
    );
    const query =
      "买卖合同约定分批交付设备，买方已付款但卖方多次延期交货并拒绝退还预付款。";

    render(<SearchComposer onSubmit={onSubmit} />);
    fireEvent.change(screen.getByLabelText("案情描述"), {
      target: { value: query },
    });
    fireEvent.click(screen.getByRole("button", { name: "开始检索" }));

    await waitFor(() => {
      expect(screen.getByLabelText("案情描述")).toBeDisabled();
      expect(screen.getByRole("button", { name: "检索中..." })).toBeDisabled();
    });

    resolveSubmit?.();

    await waitFor(() => {
      expect(screen.getByLabelText("案情描述")).toBeEnabled();
      expect(screen.getByRole("button", { name: "开始检索" })).toBeEnabled();
    });
  });

  it("submits a valid query with Ctrl+Enter", async () => {
    const onSubmit = vi.fn();
    const query =
      "消费者购买电热水壶后出现漏电受伤，商家称系使用不当，双方争议产品缺陷与赔偿责任。";

    render(<SearchComposer onSubmit={onSubmit} />);
    fireEvent.change(screen.getByLabelText("案情描述"), {
      target: { value: query },
    });
    fireEvent.keyDown(screen.getByLabelText("案情描述"), {
      key: "Enter",
      ctrlKey: true,
    });

    expect(onSubmit).toHaveBeenCalledWith(
      query,
      expect.objectContaining({ trigger: "keyboard" })
    );
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "开始检索" })).toBeEnabled()
    );
  });
});
