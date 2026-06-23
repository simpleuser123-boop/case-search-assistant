import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import { SharingPanel } from "./SharingPanel";
import { clearSession, setSession } from "../../lib/sessionState";

vi.mock("../../config/featureFlags", () => ({
  isTeamSharingEnabled: vi.fn(() => false),
}));
vi.mock("../../services/sharingApi", () => ({
  shareToTeam: vi.fn(async () => ({ ok: true, data: { share_id: "s_1", visibility: "team", anchor_count: 1 } })),
  unshare: vi.fn(async () => ({ ok: true, data: { visibility: "private" } })),
}));

import { isTeamSharingEnabled } from "../../config/featureFlags";
import { shareToTeam, unshare } from "../../services/sharingApi";

const flagMock = vi.mocked(isTeamSharingEnabled);
const shareToTeamMock = vi.mocked(shareToTeam);
const unshareMock = vi.mocked(unshare);

const SESSION = {
  account: { user_id: "u_1", display_name: "A", account_status: "active", auth_provider: "local" as const },
  sessionToken: "tok",
  expiresAt: null,
};

beforeEach(() => {
  flagMock.mockReturnValue(false);
  shareToTeamMock.mockClear();
  unshareMock.mockClear();
  clearSession();
});

afterEach(() => {
  cleanup();
  clearSession();
});

describe("SharingPanel (flag-gated)", () => {
  it("renders nothing when team sharing is disabled (M4 local-sediment end state)", () => {
    flagMock.mockReturnValue(false);
    setSession(SESSION);
    const { container } = render(<SharingPanel />);
    expect(container.firstChild).toBeNull();
    expect(screen.queryByLabelText("沉淀同步与团队共享")).toBeNull();
  });

  it("renders read-only entry when enabled but not logged in", () => {
    flagMock.mockReturnValue(true);
    clearSession();
    render(<SharingPanel />);
    expect(screen.getByLabelText("沉淀同步与团队共享")).toBeInTheDocument();
    expect(screen.getByText(/请先登录账号/)).toBeInTheDocument();
    expect(screen.queryByPlaceholderText("对象 ID（已同步的收藏/清单/报告）")).toBeNull();
    expect(shareToTeamMock).not.toHaveBeenCalled();
    expect(unshareMock).not.toHaveBeenCalled();
  });

  it("renders sharing controls when enabled and logged in", () => {
    flagMock.mockReturnValue(true);
    setSession(SESSION);
    render(<SharingPanel />);
    expect(screen.getByLabelText("沉淀同步与团队共享")).toBeInTheDocument();
    // 默认私有说明文案存在
    expect(screen.getByText(/默认私有/)).toBeInTheDocument();
    expect(screen.getByText("共享给团队")).toBeInTheDocument();
  });
});
