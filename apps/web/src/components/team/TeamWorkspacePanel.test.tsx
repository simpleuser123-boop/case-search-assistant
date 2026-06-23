import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";

import { TeamWorkspacePanel } from "./TeamWorkspacePanel";
import { clearSession, setSession } from "../../lib/sessionState";

// flag mock：默认关闭，单测内按用例切换。
vi.mock("../../config/featureFlags", () => ({
  isTeamWorkspaceEnabled: vi.fn(() => false),
}));
// teamApi mock：避免真实网络；listTeams 返回空。
vi.mock("../../services/teamApi", () => ({
  listTeams: vi.fn(async () => ({ ok: true, data: { teams: [] } })),
  createTeam: vi.fn(async () => ({ ok: true, data: { team: {} } })),
}));

import { isTeamWorkspaceEnabled } from "../../config/featureFlags";
import { listTeams } from "../../services/teamApi";

const flagMock = vi.mocked(isTeamWorkspaceEnabled);
const listTeamsMock = vi.mocked(listTeams);

beforeEach(() => {
  flagMock.mockReturnValue(false);
  listTeamsMock.mockClear();
  clearSession();
});

afterEach(() => {
  cleanup();
  clearSession();
});

describe("TeamWorkspacePanel (flag-gated)", () => {
  it("renders nothing when team workspace is disabled (M5-2/M4 end state)", () => {
    flagMock.mockReturnValue(false);
    setSession({
      account: { user_id: "u_1", display_name: "A", account_status: "active", auth_provider: "local" },
      sessionToken: "tok",
      expiresAt: null,
    });
    const { container } = render(<TeamWorkspacePanel />);
    expect(container.firstChild).toBeNull();
    expect(screen.queryByLabelText("团队空间")).toBeNull();
  });

  it("renders read-only entry when enabled but not logged in", () => {
    flagMock.mockReturnValue(true);
    clearSession();
    render(<TeamWorkspacePanel />);
    expect(screen.getByLabelText("团队空间")).toBeInTheDocument();
    expect(screen.getByText(/请先登录账号/)).toBeInTheDocument();
    expect(screen.queryByPlaceholderText("新团队名称")).toBeNull();
    expect(listTeamsMock).not.toHaveBeenCalled();
  });

  it("renders team switcher with personal-private default when enabled and logged in", async () => {
    flagMock.mockReturnValue(true);
    setSession({
      account: { user_id: "u_1", display_name: "A", account_status: "active", auth_provider: "local" },
      sessionToken: "tok",
      expiresAt: null,
    });
    render(<TeamWorkspacePanel />);
    expect(screen.getByLabelText("团队空间")).toBeInTheDocument();
    // 默认提供「个人私有（无团队）」入口 = 单用户私有态
    expect(screen.getByText("个人私有（无团队）")).toBeInTheDocument();
    await waitFor(() => expect(listTeamsMock).toHaveBeenCalledTimes(1));
  });
});
