import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

import { AccountPanel } from "./AccountPanel";
import { clearSession } from "../../lib/sessionState";

// flag mock：默认关闭，单测内按用例切换。
vi.mock("../../config/featureFlags", () => ({
  isAccountSystemEnabled: vi.fn(() => false),
}));

import { isAccountSystemEnabled } from "../../config/featureFlags";

const flagMock = vi.mocked(isAccountSystemEnabled);

beforeEach(() => {
  flagMock.mockReturnValue(false);
  clearSession();
});

afterEach(() => {
  cleanup();
  clearSession();
});

describe("AccountPanel (flag-gated)", () => {
  it("renders nothing when account system is disabled (M4 end state)", () => {
    flagMock.mockReturnValue(false);
    const { container } = render(<AccountPanel />);
    expect(container.firstChild).toBeNull();
    // 关闭态不暴露任何登录/注册入口
    expect(screen.queryByLabelText("账号")).toBeNull();
  });

  it("renders login/register entry when enabled", () => {
    flagMock.mockReturnValue(true);
    render(<AccountPanel />);
    expect(screen.getByLabelText("账号")).toBeInTheDocument();
    // "登录" 同时是 tab 与提交按钮文案，故用 getAllByText 断言至少出现一次
    expect(screen.getAllByText("登录").length).toBeGreaterThan(0);
    expect(screen.getByText("注册")).toBeInTheDocument();
    // 密码输入框存在但为空（不预填、不代填）
    const pw = screen.getByLabelText("密码") as HTMLInputElement;
    expect(pw.value).toBe("");
    expect(pw.type).toBe("password");
  });

  it("shows local validation errors before calling auth API", () => {
    flagMock.mockReturnValue(true);
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    render(<AccountPanel />);
    fireEvent.click(screen.getByText("注册"));
    fireEvent.change(screen.getByLabelText("登录名"), { target: { value: "alice" } });
    fireEvent.change(screen.getByLabelText("密码"), { target: { value: "1234567" } });
    const registerButtons = screen.getAllByRole("button", { name: "注册" });
    fireEvent.click(registerButtons[registerButtons.length - 1]);

    expect(screen.getByText("密码至少 8 位。")).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
